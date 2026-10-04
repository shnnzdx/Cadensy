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
});
