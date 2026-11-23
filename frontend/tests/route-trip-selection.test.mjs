import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import {
  membershipAdoptionForRouteTrip,
  selectDisplayedTrip,
} from "../../trip/src/final/route-trip-selection.js";

test("the route trip is shown when the cached workspace is a different trip", () => {
  const selected = selectDisplayedTrip({
    routeTripId: "t-smoke",
    trips: [],
    activeTrip: { id: "t-mia", name: "mia" },
    tripSummaries: [{ id: "t-smoke", name: "SMOKE-P0-2026-11-23" }],
  });

  assert.equal(selected.id, "t-smoke");
  assert.equal(selected.name, "SMOKE-P0-2026-11-23");
});

test("a route trip does not fall back to a different cached workspace", () => {
  const selected = selectDisplayedTrip({
    routeTripId: "t-smoke",
    trips: [{ id: "t-mia", name: "mia" }],
    activeTrip: { id: "t-mia", name: "mia" },
    tripSummaries: [{ id: "t-mia", name: "mia" }],
  });

  assert.equal(selected, null);
});

test("the loaded trip is used when it is the route trip", () => {
  const selected = selectDisplayedTrip({
    routeTripId: "t-smoke",
    trips: [],
    activeTrip: { id: "t-smoke", name: "SMOKE-P0-2026-11-23" },
    tripSummaries: [],
  });

  assert.equal(selected.name, "SMOKE-P0-2026-11-23");
});

test("a direct trip URL adopts that trip's membership instead of the cached workspace", () => {
  const adoption = membershipAdoptionForRouteTrip({
    routeTripId: "t-smoke",
    activeTripId: "t-mia",
    tripSummaries: [
      { id: "t-mia", membership_id: "m-mia", my_role: "organizer" },
      { id: "t-smoke", membership_id: "m-smoke", my_role: "organizer" },
    ],
    currentUser: { tripId: "t-mia", role: "organizer", isGuest: false },
  });

  assert.deepEqual(adoption, {
    tripId: "t-smoke",
    membershipId: "m-smoke",
    role: "organizer",
  });
});

test("an already active route trip is not adopted again", () => {
  assert.equal(membershipAdoptionForRouteTrip({
    routeTripId: "t-smoke",
    activeTripId: "t-smoke",
    membershipId: "m-smoke",
    tripSummaries: [{ id: "t-smoke", membership_id: "m-smoke", my_role: "participant" }],
  }), null);
});

test("a route trip with no known membership is not adopted", () => {
  assert.equal(membershipAdoptionForRouteTrip({
    routeTripId: "t-foreign",
    activeTripId: "t-mia",
    membershipId: "m-mia",
    tripSummaries: [{ id: "t-mia", membership_id: "m-mia", my_role: "organizer" }],
    currentUser: { tripId: "t-mia", role: "organizer", isGuest: false },
  }), null);
});

test("guest sessions are not switched from this helper", () => {
  assert.equal(membershipAdoptionForRouteTrip({
    routeTripId: "t-smoke",
    activeTripId: "t-mia",
    membershipId: "m-mia",
    tripSummaries: [{ id: "t-smoke", membership_id: "m-smoke", my_role: "participant" }],
    currentUser: { tripId: "t-mia", role: "participant", isGuest: true },
  }), null);
});

test("workspace screens use the route trip and show its name", async () => {
  const [finalApp, planFeature] = await Promise.all([
    readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../trip/src/final/plan-feature/PlanFeature.jsx", import.meta.url), "utf8"),
  ]);

  assert.match(finalApp, /membershipAdoptionForRouteTrip/);
  assert.match(finalApp, /selectDisplayedTrip/);
  assert.match(finalApp, /function LoadedTripShell[\s\S]*?<h1>\{currentTrip\.name \|\| 'Untitled trip'\}<\/h1>/);
  assert.match(finalApp, /function PreferencesPage[\s\S]*?\{currentTrip\.name \|\| 'My preferences'\}/);
  assert.match(planFeature, /selectDisplayedTrip/);
  assert.match(planFeature, /function NewTripPlan[\s\S]*?<h1>\{currentTrip\.name \|\| 'Untitled trip'\}<\/h1>/);
  assert.match(planFeature, /No itinerary yet/);
});
