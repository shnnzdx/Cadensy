import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { createSessionRuntime } from "../../shared/session-runtime/index.js";
import { createInviteAttemptGate } from "../../trip/src/final/inviteCreation.js";

const CURRENT_TRIP_SELECTOR = "return app.trips.find(item => item.id === tripId) || app.trip || trip || null";

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

function organizer(id, membershipId = `membership-${id}`) {
  return { id, membership_id: membershipId, my_role: "organizer" };
}

function accountHarness({ activeTripId, membershipId = "membership-active", rows }) {
  const runtime = createSessionRuntime({ storage: createMemoryStorage() });
  let facts = runtime.adoptAccountAuth({
    token: "account-token",
    activeTripId,
    membershipId,
  }).facts;
  const posts = [];
  const adopts = [];
  const lists = [];
  const gate = createInviteAttemptGate();

  async function run(routeTripId, listRows = rows) {
    return gate.createVerifiedInvite({
      routeTripId,
      readSession: () => facts,
      listAccountTrips: async () => {
        lists.push({ routeTripId });
        return listRows;
      },
      adoptAccountTrip: (input) => {
        adopts.push(input);
        const adopted = runtime.adoptTechnicalTripContext({
          membershipId: input.membershipId,
          activeTripId: input.tripId,
        });
        facts = adopted.facts;
        return adopted;
      },
      postInvite: ({ tripId, facts: postFacts }) => {
        const identity = runtime.requestIdentityFor("trip", postFacts);
        posts.push({
          path: `/api/trips/${tripId}/invite`,
          pathTripId: tripId,
          headerTripId: identity.ok ? identity.headers["X-Trip-Id"] : null,
        });
        return { token: "invite-token", invite_id: "invite-1" };
      },
    });
  }

  return { run, posts, adopts, lists };
}

test("FE-INVITE-1 production pins: route trip id is the invite target and X-Trip-Id is activeTripId", async () => {
  const [finalApp, tripAppState, sessionRuntime, inviteCreation] = await Promise.all([
    readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../trip/src/final/TripAppState.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../shared/session-runtime/index.js", import.meta.url), "utf8"),
    readFile(new URL("../../trip/src/final/inviteCreation.js", import.meta.url), "utf8"),
  ]);

  assert.match(finalApp, /function useCurrentTrip\(\) \{[\s\S]*?return app\.trips\.find\(item => item\.id === tripId\) \|\| app\.trip \|\| trip \|\| null/);
  assert.equal(CURRENT_TRIP_SELECTOR.includes("activeTripId"), false);
  assert.match(finalApp, /function InvitePage\(\) \{[\s\S]*?const \{ tripId: routeTripId \} = useParams\(\)/);
  assert.match(finalApp, /app\.createInvite\(routeTripId\)/);
  assert.equal([...finalApp.matchAll(/app\.createInvite\(/g)].length, 1);
  assert.doesNotMatch(finalApp, /app\.createInvite\(currentTrip\.id\)/);
  assert.match(finalApp, /app\.invalidateInviteCreation\(\)/);
  assert.match(tripAppState, /listAccountTrips: \(\) => accountRequestJsonRef\.current\('\/api\/trips'\)/);
  assert.match(tripAppState, /sessionRuntimeRef\.current\.requestIdentityFor\('trip', facts\)/);
  assert.match(tripAppState, /identity\.headers\['X-Trip-Id'\] !== tripId/);
  assert.match(tripAppState, /sessionRequestJsonRef\.current\('trip', `\/api\/trips\/\$\{tripId\}\/invite`/);
  assert.doesNotMatch(tripAppState, /["']X-Trip-Id["']\s*:\s*routeTripId/);
  assert.doesNotMatch(tripAppState, /["']X-Trip-Id["']\s*:\s*tripId/);
  assert.doesNotMatch(inviteCreation, /app\.trips|demoTrip|trips\[0\]/);
  assert.equal([...sessionRuntime.matchAll(/"X-Trip-Id": facts\.activeTripId/g)].length, 2);
});

test("FE-INVITE-1 aligned current trip matches that request's X-Trip-Id", async () => {
  const harness = accountHarness({
    activeTripId: "trip-active",
    rows: [organizer("trip-active")],
  });
  await harness.run("trip-active");

  assert.equal(harness.lists.length, 1);
  assert.equal(harness.adopts.length, 0);
  assert.equal(harness.posts.length, 1);
  assert.equal(harness.posts[0].path, "/api/trips/trip-active/invite");
  assert.equal(harness.posts[0].pathTripId, harness.posts[0].headerTripId);
});

test("FE-INVITE-1 aligned ids do not post without a current organizer row", async () => {
  const missing = accountHarness({
    activeTripId: "trip-active",
    rows: [],
  });
  await assert.rejects(() => missing.run("trip-active"));
  assert.equal(missing.lists.length, 1);
  assert.equal(missing.posts.length, 0);

  const participant = accountHarness({
    activeTripId: "trip-active",
    rows: [{ id: "trip-active", membership_id: "membership-active", my_role: "participant" }],
  });
  await assert.rejects(() => participant.run("trip-active"));
  assert.equal(participant.posts.length, 0);
  assert.equal(participant.adopts.length, 0);
});

test("FE-INVITE-1 does not invite the loaded app.trip when the route id is not verified", async () => {
  const harness = accountHarness({
    activeTripId: "trip-active",
    rows: [organizer("trip-active", "membership-active")],
  });
  await assert.rejects(() => harness.run("trip-route"));

  assert.equal(harness.posts.length, 0);
  assert.equal(harness.adopts.length, 0);
  assert.equal(harness.posts.some(call => call.pathTripId === "trip-active"), false);
});

test("FE-INVITE-1 cached route trip diverges from X-Trip-Id", async () => {
  const cachedTrips = [{ id: "trip-route" }, { id: "trip-active" }];
  const routeTripId = "trip-route";
  const activeTripId = "trip-active";
  assert.equal(cachedTrips.some(item => item.id === routeTripId), true);
  assert.notEqual(routeTripId, activeTripId);

  const harness = accountHarness({
    activeTripId,
    rows: [organizer("trip-route", "membership-route"), organizer("trip-active", "membership-active")],
  });
  await harness.run(routeTripId);

  assert.equal(harness.lists.length, 1);
  assert.equal(harness.adopts.length, 1);
  assert.equal(harness.adopts[0].membershipId, "membership-route");
  assert.equal(harness.adopts[0].tripId, "trip-route");
  assert.equal(harness.posts.length, 1);
  assert.equal(harness.posts[0].path, "/api/trips/trip-route/invite");
  assert.equal(
    harness.posts[0].pathTripId,
    harness.posts[0].headerTripId,
  );
  assert.equal(harness.posts[0].headerTripId, "trip-route");
  assert.notEqual(harness.posts[0].headerTripId, "trip-active");
});
