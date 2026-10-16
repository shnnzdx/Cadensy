import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";
import { normalizeWorkspaceSessionFacts } from "../../trip/src/final/navigation-normalizers.js";

function loginMembership(tripId, role = "participant") {
  return {
    membership_id: `m-${tripId}`,
    trip_id: tripId,
    role,
  };
}

test("phase 10 successful account login with memberships can construct trip-session policy facts without guessing", () => {
  const loginResponse = {
    token: "token-1",
    memberships: [
      loginMembership("t1", "organizer"),
      loginMembership("t2", "participant"),
    ],
    default_membership: loginMembership("t1", "organizer"),
  };

  const normalized = normalizeWorkspaceSessionFacts({
    hasAccountSession: true,
    memberships: loginResponse.memberships,
  });

  assert.equal(normalized.accessState, "trip-session");
  assert.deepEqual(normalized.relevantTripsById, {
    t1: {
      membership: { role: "organizer", identityKind: "account" },
    },
    t2: {
      membership: { role: "participant", identityKind: "account" },
    },
  });
  assert.deepEqual(normalized.intent, {});
});

test("phase 10 successful account login with no memberships constructs session-no-trip-access from authoritative response", () => {
  const normalized = normalizeWorkspaceSessionFacts({
    hasAccountSession: true,
    memberships: [],
  });

  assert.equal(normalized.accessState, "session-no-trip-access");
  assert.deepEqual(normalized.relevantTripsById, {});
  assert.deepEqual(normalized.intent, {});
});

test("phase 10 missing trip state can remain unknown without inventing planning", () => {
  const normalized = normalizeWorkspaceSessionFacts({
    hasAccountSession: true,
    memberships: [loginMembership("t1", "participant")],
    tripSummaries: [{ id: "t1", status: null }],
  });

  assert.equal(normalized.accessState, "trip-session");
  assert.deepEqual(normalized.relevantTripsById, {
    t1: {
      membership: { role: "participant", identityKind: "account" },
      state: "unknown",
    },
  });
});

test("phase 10 default_membership is not required as a pre-resolved destination winner to construct policy facts", () => {
  const loginResponse = {
    token: "token-2",
    memberships: [
      loginMembership("t-created-first", "participant"),
      loginMembership("t-created-second", "organizer"),
    ],
    default_membership: loginMembership("t-created-first", "participant"),
  };

  const normalized = normalizeWorkspaceSessionFacts({
    hasAccountSession: true,
    memberships: loginResponse.memberships,
  });

  assert.deepEqual(Object.keys(normalized.relevantTripsById).sort(), [
    "t-created-first",
    "t-created-second",
  ]);
  assert.equal(normalized.intent.returnTarget, undefined);
  assert.equal(normalized.intent.restoredSelection, undefined);
});

test("phase 11 login source keeps current destination behavior without fake workspace-policy runtime scaffolding", async () => {
  const loginPage = await readFile(new URL("../app/login/page.tsx", import.meta.url), "utf8");

  await assert.rejects(
    access(new URL("../app/login/navigation-import-proof.ts", import.meta.url)),
  );

  assert.doesNotMatch(loginPage, /navigation-import-proof/);
  assert.doesNotMatch(loginPage, /resolveDestination/);
  assert.doesNotMatch(loginPage, /tripNavigationPolicy/);
  assert.doesNotMatch(loginPage, /serializeWorkspaceRoute/);
  assert.doesNotMatch(loginPage, /parseWorkspaceRoute/);
  assert.doesNotMatch(loginPage, /structured workspace return intent/i);
});

test("phase 11 login source preserves a validated same-origin return path before navigation", async () => {
  const loginPage = await readFile(new URL("../app/login/page.tsx", import.meta.url), "utf8");

  assert.match(loginPage, /const nextPathRef = useRef\("\/trip"\);/);
  assert.match(loginPage, /const requestedNext = params\.get\("next"\);/);
  assert.match(loginPage, /isSafeInternalReturnPath\(requestedNext\)/);
  assert.match(loginPage, /const membership = result\.default_membership \|\| result\.memberships\?\.\[0\];/);
  assert.match(loginPage, /const adoption = sessionRuntime\.adoptAccountAuth\(\{\s*token: result\.token,\s*\.\.\.\(membership \? \{/s);
  assert.match(loginPage, /if \(hasPersistenceWarning\(adoption\.warnings\)\) \{/);
  assert.match(loginPage, /window\.location\.href = nextPathRef\.current;/);
  assert.doesNotMatch(loginPage, /window\.location\.href = `\/trip\/\$\{.*\}\/.*/);
});
