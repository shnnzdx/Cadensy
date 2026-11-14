import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { SESSION_RUNTIME_CODES } from "../../shared/session-runtime/index.js";
import { classifyTechnicalSessionInvalidation } from "../../trip/src/final/technicalSessionInvalidation.js";

test("phase 7 account 401 is classified as account credential invalidation", () => {
  assert.equal(
    classifyTechnicalSessionInvalidation({
      scope: "account",
      facts: {
        kind: "account",
        accountAuth: true,
        activeTripId: "t-1",
        membershipId: "m-1",
      },
      status: 401,
    }),
    SESSION_RUNTIME_CODES.invalidation.ACCOUNT_CREDENTIALS_INVALID,
  );
});

test("phase 7 Guest 401 is classified as Guest credential invalidation", () => {
  assert.equal(
    classifyTechnicalSessionInvalidation({
      scope: "trip",
      facts: {
        kind: "guest",
        guestAuth: true,
        activeTripId: "t-guest",
        membershipId: "m-guest",
      },
      status: 401,
    }),
    SESSION_RUNTIME_CODES.invalidation.MEMBERSHIP_CREDENTIALS_INVALID,
  );

});

test("phase 7 trip 403 does not invalidate session", () => {
  assert.equal(
    classifyTechnicalSessionInvalidation({
      scope: "trip",
      facts: {
        kind: "account",
        accountAuth: true,
        activeTripId: "t-1",
        membershipId: "m-1",
      },
      status: 403,
    }),
    null,
  );
});

test("phase 7 ordinary 403 does not invalidate session", () => {
  assert.equal(
    classifyTechnicalSessionInvalidation({
      scope: "account",
      facts: {
        kind: "account",
        accountAuth: true,
        activeTripId: null,
        membershipId: null,
      },
      status: 403,
    }),
    null,
  );
});

test("400 trip_context_required does not invalidate an account or guest session", () => {
  for (const facts of [
    { kind: "account", accountAuth: true, activeTripId: "t-1", membershipId: "m-1" },
    { kind: "guest", guestAuth: true, activeTripId: "t-guest", membershipId: "m-guest" },
  ]) {
    assert.equal(
      classifyTechnicalSessionInvalidation({
        scope: "trip",
        facts,
        status: 400,
        message: "trip_context_required",
      }),
      null,
    );
  }
});

test("409 trip_context_mismatch does not invalidate an account or guest session", () => {
  for (const facts of [
    { kind: "account", accountAuth: true, activeTripId: "t-1", membershipId: "m-1" },
    { kind: "guest", guestAuth: true, activeTripId: "t-guest", membershipId: "m-guest" },
  ]) {
    assert.equal(
      classifyTechnicalSessionInvalidation({
        scope: "trip",
        facts,
        status: 409,
        message: "trip_context_mismatch",
      }),
      null,
    );
  }
});

test("phase 7 404 does not invalidate session", () => {
  assert.equal(
    classifyTechnicalSessionInvalidation({
      scope: "trip",
      facts: {
        kind: "guest",
        guestAuth: true,
        activeTripId: "t-guest",
        membershipId: "m-guest",
      },
      status: 404,
    }),
    null,
  );
});

test("phase 7 TripAppState invalidates technical session on authoritative 401 without moving navigation into session-runtime", async () => {
  const source = await readFile(new URL("../../trip/src/final/TripAppState.jsx", import.meta.url), "utf8");

  assert.match(source, /import \{ classifyTechnicalSessionInvalidation \} from '\.\/technicalSessionInvalidation\.js'/);
  assert.match(source, /const applyTechnicalSessionInvalidation = useCallback\(\(cause, message\) => \{/);
  assert.match(source, /const invalidated = sessionRuntime\.invalidateTechnicalSession\(technicalSessionFacts, cause\)/);
  assert.match(source, /const invalidationCause = classifyTechnicalSessionInvalidation\(\{/);
  assert.match(source, /if \(invalidationCause\) \{\s*applyTechnicalSessionInvalidation\(invalidationCause, message\)\s*\}/s);
  assert.doesNotMatch(source, /if \(response\.status === 403\)[\s\S]*invalidateTechnicalSession/);
  assert.doesNotMatch(source, /if \(response\.status === 404\)[\s\S]*invalidateTechnicalSession/);
  assert.doesNotMatch(source, /if \(response\.status === 400\)[\s\S]*invalidateTechnicalSession/);
  assert.doesNotMatch(source, /if \(response\.status === 409\)[\s\S]*invalidateTechnicalSession/);

  const planFeature = await readFile(new URL("../../trip/src/final/plan-feature/PlanFeature.jsx", import.meta.url), "utf8");
  const finalApp = await readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8");
  assert.match(planFeature, /app\.notify\(extendRoundFailureNotice\(err\)\)/);
  assert.match(finalApp, /app\.notify\(extendRoundFailureNotice\(err\)\)/);
  assert.doesNotMatch(planFeature, /already been extended/);
  assert.doesNotMatch(finalApp, /already been extended/);
});
