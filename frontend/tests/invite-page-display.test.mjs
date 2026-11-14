import assert from "node:assert/strict";
import test from "node:test";

import {
  invitePageRenderModel,
  resolveInvitePageDisplay,
  visibleInviteForRoute,
} from "../../trip/src/final/invitePageDisplay.js";

const tripA = {
  id: "trip-a",
  name: "Route Alpine",
  destination: "Alpine Valley",
  dates: "June 1-4",
  my_role: "organizer",
  membership_id: "membership-a",
}
const tripB = {
  id: "trip-b",
  name: "Cached Harbor",
  destination: "Harbor City",
  dates: "July 9-12",
  my_role: "organizer",
  membership_id: "membership-b",
}
const demoTrip = {
  id: "demo-chicago",
  name: "Chicago Demo",
  destination: "Chicago, Illinois",
  dates: "August 1-3",
}

function renderedText(model) {
  return JSON.stringify({
    status: model.status,
    heading: model.heading,
    name: model.name,
    destination: model.destination,
    dates: model.dates,
    coverTripId: model.coverTripId,
    navigation: model.navigation,
    startPlanningHref: model.startPlanningHref,
    invite: model.invite,
    error: model.error,
  })
}

function assertNoTripB(model) {
  const text = renderedText(model)
  assert.equal(text.includes(tripB.id), false)
  assert.equal(text.includes(tripB.name), false)
  assert.equal(text.includes(tripB.destination), false)
  assert.equal(text.includes(tripB.dates), false)
  assert.equal(text.includes("Chicago Demo"), false)
  assert.equal(text.includes("demo-chicago"), false)
}

test("route A with summaries only and loaded trip B shows A and does not show B", () => {
  const model = invitePageRenderModel({
    routeTripId: "trip-a",
    trips: [],
    activeTrip: tripB,
    tripSummaries: [tripA],
    tripSummariesStatus: "ready",
    demoTrip,
  })

  assert.equal(model.status, "ready")
  assert.equal(model.shellMode, "route")
  assert.equal(model.shouldCreateInvite, true)
  assert.equal(model.coverTripId, "trip-a")
  assert.equal(model.name, "Route Alpine")
  assert.equal(model.destination, "Alpine Valley")
  assert.equal(model.dates, "June 1-4")
  assert.equal(model.startPlanningHref, "/trip/trip-a/preferences")
  assert.ok(model.navigation.length > 0)
  for (const entry of model.navigation) {
    assert.equal(entry.href.startsWith("/trip/trip-a/"), true)
  }
  assert.equal(model.navigation.some(entry => entry.href === "/trip/trip-a/invite"), true)
  assertNoTripB(model)
})

test("summaries still loading does not show trip B and does not create an invite", () => {
  const model = invitePageRenderModel({
    routeTripId: "trip-a",
    trips: [],
    activeTrip: tripB,
    tripSummaries: [],
    tripSummariesStatus: "loading",
    demoTrip,
  })

  assert.equal(model.status, "loading")
  assert.equal(model.heading, "Loading")
  assert.equal(model.shouldCreateInvite, false)
  assert.equal(model.shellMode, "neutral")
  assert.equal(model.name, null)
  assert.equal(model.coverTripId, null)
  assert.equal(model.startPlanningHref, null)
  assert.deepEqual(model.navigation, [])
  assert.equal(model.invite, null)
  assertNoTripB(model)
})

test("settled lists without route A are unavailable and do not fall back to trip B or the demo trip", () => {
  const model = invitePageRenderModel({
    routeTripId: "trip-a",
    trips: [tripB],
    activeTrip: tripB,
    tripSummaries: [tripB],
    tripSummariesStatus: "ready",
    demoTrip,
  })

  assert.equal(model.status, "unavailable")
  assert.equal(model.heading, "Unavailable")
  assert.equal(model.shouldCreateInvite, false)
  assert.equal(model.shellMode, "neutral")
  assert.equal(model.name, null)
  assert.equal(model.startPlanningHref, null)
  assertNoTripB(model)
})

test("a loaded route trip A is shown and the shared shell is safe", () => {
  const model = invitePageRenderModel({
    routeTripId: "trip-a",
    trips: [tripA],
    activeTrip: tripB,
    tripSummaries: [],
    tripSummariesStatus: "ready",
    demoTrip,
  })

  assert.equal(model.status, "ready")
  assert.equal(model.shellMode, "shared")
  assert.equal(model.shouldCreateInvite, true)
  assert.equal(model.name, "Route Alpine")
  assert.equal(model.coverTripId, "trip-a")
  assert.equal(model.startPlanningHref, "/trip/trip-a/preferences")
  assertNoTripB(model)
})

test("active trip A is shown when the route cache does not contain it", () => {
  const model = resolveInvitePageDisplay({
    routeTripId: "trip-a",
    trips: [],
    activeTrip: tripA,
    tripSummaries: [],
    tripSummariesStatus: "ready",
    demoTrip,
  })

  assert.equal(model.status, "ready")
  assert.equal(model.shellMode, "shared")
  assert.equal(model.trip.id, "trip-a")
  assert.equal(model.shouldCreateInvite, true)
  assert.equal(model.startPlanningHref, "/trip/trip-a/preferences")
})

test("the demo trip is not a display or navigation fallback", () => {
  const model = invitePageRenderModel({
    routeTripId: "demo-chicago",
    trips: [],
    activeTrip: null,
    tripSummaries: [],
    tripSummariesStatus: "ready",
    demoTrip,
  })

  assert.equal(model.status, "unavailable")
  assert.equal(model.shouldCreateInvite, false)
  assert.equal(model.name, null)
  assert.equal(model.coverTripId, null)
  assert.equal(renderedText(model).includes("Chicago Demo"), false)
})

test("a previous invite is not visible after the route changes", () => {
  const previous = { routeTripId: "trip-a", invite: { token: "secret-token-a", invite_id: "invite-a" }, error: "old error" }
  assert.equal(visibleInviteForRoute("trip-c", previous), null)

  const model = invitePageRenderModel({
    routeTripId: "trip-c",
    trips: [],
    activeTrip: tripB,
    tripSummaries: [],
    tripSummariesStatus: "ready",
    demoTrip,
  }, previous)

  assert.equal(model.invite, null)
  assert.equal(model.error, "")
  assert.equal(model.shouldCreateInvite, false)
  assert.equal(renderedText(model).includes("secret-token-a"), false)
  assert.equal(renderedText(model).includes("old error"), false)
  assertNoTripB(model)
})

test("invite creation stays off until the route display is confirmed", () => {
  const base = {
    routeTripId: "trip-a",
    trips: [],
    activeTrip: tripB,
    demoTrip,
  }
  const loading = invitePageRenderModel({ ...base, tripSummaries: [], tripSummariesStatus: "loading" })
  const ready = invitePageRenderModel({ ...base, tripSummaries: [tripA], tripSummariesStatus: "ready" })

  assert.equal(loading.shouldCreateInvite, false)
  assert.equal(ready.shouldCreateInvite, true)
  assert.equal(ready.routeTripId, "trip-a")
  assertNoTripB(loading)
  assertNoTripB(ready)
})
