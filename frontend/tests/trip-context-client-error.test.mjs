import assert from "node:assert/strict";
import test from "node:test";

import {
  addStopWarningText,
  assistantChangeErrorText,
  extendRoundFailureNotice,
  friendlyError,
  generateItineraryErrorText,
  messageFromResponseBody,
  tripContextClientText,
} from "../../trip/src/final/tripContextClientError.js";

test("response detail is the string body.detail and not detail.code", () => {
  assert.equal(messageFromResponseBody({ detail: "trip_context_required" }, 400), "trip_context_required");
  assert.equal(messageFromResponseBody({ detail: "trip_context_mismatch" }, 409), "trip_context_mismatch");
  assert.equal(
    messageFromResponseBody({ detail: { code: "trip_context_required" } }, 400),
    "Request failed (400)",
  );
  assert.equal(messageFromResponseBody({ detail: ["trip_context_mismatch"] }, 409), "Request failed (409)");
  assert.equal(messageFromResponseBody({}, 400), "Request failed (400)");
  assert.equal(messageFromResponseBody(null, 409), "Request failed (409)");
});

test("only the decided status and string pairs become trip-context copy", () => {
  assert.equal(
    tripContextClientText({ status: 400, message: "trip_context_required" }),
    "This action needs a selected trip.",
  );
  assert.equal(
    tripContextClientText({ status: 409, message: "trip_context_mismatch" }),
    "The selected trip does not match this page.",
  );
  assert.equal(tripContextClientText({ status: 400, message: "trip_context_mismatch" }), null);
  assert.equal(tripContextClientText({ status: 409, message: "trip_context_required" }), null);
  assert.equal(tripContextClientText({ status: 403, message: "trip_context_mismatch" }), null);
  assert.equal(tripContextClientText({ status: 401, message: "trip_context_required" }), null);
  assert.equal(tripContextClientText({ status: 400, message: "Request failed (400)" }), null);
});

test("friendlyError keeps generic 409 and 400 copy for every other string", () => {
  assert.equal(
    friendlyError({ status: 409, message: "trip_context_mismatch" }),
    "The selected trip does not match this page.",
  );
  assert.equal(
    friendlyError({ status: 400, message: "trip_context_required" }),
    "This action needs a selected trip.",
  );
  assert.equal(
    friendlyError({ status: 409, message: "vote_already_open" }),
    "A vote is already open for this time block.",
  );
  assert.equal(friendlyError({ status: 400, message: "budget_too_low" }), "budget_too_low");
  assert.equal(friendlyError({ status: 422, message: "Need a reason" }), "Need a reason");
  assert.equal(friendlyError({ status: 500, message: "db down" }), "db down");
  assert.equal(friendlyError(new Error("offline")), "Could not reach the backend.");
});

test("both extend presenters keep the domain sentence for other 409 details", () => {
  assert.equal(
    extendRoundFailureNotice({ status: 409, message: "trip_context_mismatch" }),
    "The selected trip does not match this page.",
  );
  assert.notEqual(
    extendRoundFailureNotice({ status: 409, message: "trip_context_mismatch" }),
    "This round has already been extended once.",
  );
  assert.equal(
    extendRoundFailureNotice({ status: 409, message: "round_already_extended" }),
    "This round has already been extended once.",
  );
  assert.equal(
    extendRoundFailureNotice({ status: 400, message: "trip_context_required" }),
    "This action needs a selected trip.",
  );
  assert.equal(
    extendRoundFailureNotice({ status: 400, message: "something_else" }),
    "Could not extend this round.",
  );
});

test("assistant, add-stop, and generate handlers do not swallow the decided strings", () => {
  assert.equal(
    assistantChangeErrorText({ status: 409, message: "trip_context_mismatch" }),
    "The selected trip does not match this page.",
  );
  assert.equal(
    assistantChangeErrorText({ status: 409, message: "vote_open" }),
    "A vote is already open for this time block.",
  );
  assert.equal(
    assistantChangeErrorText({ status: 400, message: "trip_context_required" }),
    "This action needs a selected trip.",
  );
  assert.equal(
    assistantChangeErrorText({ status: 400, message: "other" }),
    "I could not reach the backend. Try again in a moment.",
  );

  assert.equal(
    addStopWarningText({ status: 409, message: "trip_context_mismatch" }),
    "The selected trip does not match this page.",
  );
  assert.equal(
    addStopWarningText({ status: 409, message: "overlaps the next stop" }),
    "overlaps the next stop",
  );
  assert.equal(addStopWarningText({ status: 409 }), "This stop may affect the next activity's time.");
  assert.equal(
    addStopWarningText({ status: 400, message: "trip_context_required" }),
    "This action needs a selected trip.",
  );
  assert.equal(addStopWarningText({ status: 400, message: "other" }), null);

  assert.equal(
    generateItineraryErrorText({ status: 409, message: "trip_context_mismatch" }),
    "The selected trip does not match this page.",
  );
  assert.equal(
    generateItineraryErrorText({ status: 409, message: "plan_exists" }),
    "An itinerary already exists.",
  );
  assert.equal(
    generateItineraryErrorText({ status: 400, message: "trip_context_required" }),
    "This action needs a selected trip.",
  );
  assert.equal(
    generateItineraryErrorText({ status: 422, message: "missing" }),
    "Fill your preferences before generating the first itinerary.",
  );
  assert.equal(
    generateItineraryErrorText({ code: "organizer_preference_missing", status: 400, message: "missing" }),
    "Fill your preferences before generating the first itinerary.",
  );
  assert.equal(
    generateItineraryErrorText({ status: 500, message: "down" }),
    "Could not generate the itinerary. Try again in a moment.",
  );
});
