import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createSessionRuntime } from "../../shared/session-runtime/index.js";

const CURRENT_TRIP_SELECTOR = "return app.trips.find(item => item.id === tripId) || app.trip || trip || null";
const CREATE_INVITE_CALL = "return requestJson(`/api/trips/${tripId}/invite`, { method: 'POST' })";
const INVITE_PAGE_CALL = "app.createInvite(currentTrip.id)";

function createMemoryStorage() {
  const store = new Map();
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
  };
}

function selectCurrentTrip({ trips, routeTripId, appTrip, demoTrip }) {
  const tripId = routeTripId;
  const app = { trips, trip: appTrip };
  const trip = demoTrip;
  return app.trips.find(item => item.id === tripId) || app.trip || trip || null;
}

function tripScopedHeader(activeTripId) {
  const runtime = createSessionRuntime({ storage: createMemoryStorage() });
  const adopted = runtime.adoptAccountAuth({
    token: "account-token",
    activeTripId,
    membershipId: "membership-1",
  });
  const identity = runtime.requestIdentityFor("trip", adopted.facts);
  assert.equal(identity.ok, true);
  return identity.headers["X-Trip-Id"];
}

function inviteRequest(input) {
  const currentTrip = selectCurrentTrip(input);
  const pathTripId = currentTrip.id;
  return {
    path: `/api/trips/${pathTripId}/invite`,
    pathTripId,
    headerTripId: tripScopedHeader(input.activeTripId),
  };
}

test("FE-INVITE-1 production pins: path argument is currentTrip.id and X-Trip-Id is activeTripId", async () => {
  const [finalApp, tripAppState, sessionRuntime] = await Promise.all([
    readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../trip/src/final/TripAppState.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../shared/session-runtime/index.js", import.meta.url), "utf8"),
  ]);

  assert.match(finalApp, /function useCurrentTrip\(\) \{[\s\S]*?return app\.trips\.find\(item => item\.id === tripId\) \|\| app\.trip \|\| trip \|\| null/);
  assert.equal([...finalApp.matchAll(/createInvite\(/g)].length, 1);
  assert.match(finalApp, new RegExp(INVITE_PAGE_CALL.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(tripAppState, /const createInvite = useCallback\(async tripId => \{\s*return requestJson\(`\/api\/trips\/\$\{tripId\}\/invite`, \{ method: 'POST' \}\)/);
  assert.match(tripAppState, /const requestJson = useCallback\(async \(path, options = \{\}\) => \{\s*return sessionRequestJson\('trip', path, options\)/s);
  assert.equal([...sessionRuntime.matchAll(/"X-Trip-Id": facts\.activeTripId/g)].length, 2);
  assert.equal(CURRENT_TRIP_SELECTOR.includes("activeTripId"), false);
  assert.equal(CREATE_INVITE_CALL.includes("activeTripId"), false);
});

test("FE-INVITE-1 aligned current trip matches that request's X-Trip-Id", () => {
  const request = inviteRequest({
    trips: [{ id: "trip-active" }],
    routeTripId: "trip-active",
    appTrip: { id: "trip-active" },
    demoTrip: null,
    activeTripId: "trip-active",
  });

  assert.equal(request.path, "/api/trips/trip-active/invite");
  assert.equal(request.pathTripId, request.headerTripId);
});

test("FE-INVITE-1 loaded app.trip matches X-Trip-Id when the route id is not in app.trips", () => {
  const request = inviteRequest({
    trips: [],
    routeTripId: "trip-route",
    appTrip: { id: "trip-active" },
    demoTrip: null,
    activeTripId: "trip-active",
  });

  assert.equal(request.pathTripId, "trip-active");
  assert.equal(request.pathTripId, request.headerTripId);
});

// The production selectors allow this split. createTrip pushes into app.trips,
// and adoptTechnicalTripContext does not remove that entry when it selects
// another trip. useCurrentTrip prefers the cached route id. Route reachability
// is membership on the route trip, not equality with facts.activeTripId.
// Closing the split means choosing which id wins. This test stops there.
test("FE-INVITE-1 STOP: cached route trip diverges from X-Trip-Id", () => {
  const request = inviteRequest({
    trips: [{ id: "trip-route" }, { id: "trip-active" }],
    routeTripId: "trip-route",
    appTrip: { id: "trip-active" },
    demoTrip: null,
    activeTripId: "trip-active",
  });

  assert.equal(
    request.pathTripId,
    request.headerTripId,
    [
      "STOP FE-INVITE-1.",
      `path trip id ${request.pathTripId} !== X-Trip-Id ${request.headerTripId}.`,
      "useCurrentTrip() can return app.trips entry for the route while requestIdentityFor('trip') sends facts.activeTripId.",
      "createInvite(currentTrip.id) then posts /api/trips/${currentTrip.id}/invite.",
      "Making these equal requires choosing which id wins, rewriting the invite URL, or adopting the route trip.",
      "That selection rule is unresolved. Production invite wiring was not changed.",
    ].join(" "),
  );
});
