import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resolveRestoredWorkspaceDestination } from "../../trip/src/final/workspace-navigation-model.js";

function accountUser(overrides = {}) {
  return {
    tripId: "t1",
    role: "participant",
    isGuest: false,
    ...overrides,
  };
}

function resolveRestoration(overrides = {}) {
  return resolveRestoredWorkspaceDestination({
    currentRoutePath: "/",
    hasAccountSession: true,
    membershipId: "m-1",
    currentUser: accountUser(),
    tripSummaries: [
      { id: "t1", status: "Planning", my_role: "participant" },
    ],
    activeTrip: {
      id: "t1",
      status: "Planning",
    },
    activeTripId: "t1",
    restoredTripId: "t1",
    ...overrides,
  });
}

test("account restoration leaves the My Trips dashboard as the login landing page", () => {
  const resolution = resolveRestoration({
    tripSummaries: [
      { id: "t1", status: "Planning", my_role: "participant" },
      { id: "t2", status: "Traveling", my_role: "organizer" },
    ],
    explain: true,
  });

  assert.equal(resolution.disposition, "allow");
  assert.equal(resolution.destinationHref, "/");
  assert.equal(resolution.diagnostics?.acceptedCode, "current-route-allowed");
});

test("phase 9 restoration keeps a valid restored trip eligible when the current route is already that trip", () => {
  const resolution = resolveRestoration({
    currentRoutePath: "/trip/t1/plan",
    tripSummaries: [
      { id: "t1", status: "Planning", my_role: "participant" },
      { id: "t2", status: "Traveling", my_role: "participant" },
    ],
    restoredTripId: "t1",
    explain: true,
  });

  assert.equal(resolution.disposition, "allow");
  assert.equal(resolution.destinationHref, "/trip/t1/plan");
  assert.equal(resolution.diagnostics?.acceptedCode, "restored-selection-accepted");
});

test("phase 9 restoration rejects stale restored selections through policy when authoritative trip facts disagree", () => {
  const resolution = resolveRestoration({
    currentRoutePath: "/trip/t-old/plan",
    currentUser: accountUser({ tripId: "t2" }),
    tripSummaries: [
      { id: "t2", status: "Planning", my_role: "participant" },
    ],
    activeTrip: {
      id: "t2",
      status: "Planning",
    },
    activeTripId: "t2",
    restoredTripId: "t-old",
    explain: true,
  });

  assert.equal(resolution.disposition, "redirect");
  assert.equal(resolution.destinationHref, "/");
  assert.equal(resolution.diagnostics?.acceptedCode, "default-home");
  assert.equal(resolution.diagnostics?.rejectedIntents?.[0]?.code, "stale-restored-selection");
});

test("phase 9 restoration preserves unknown trip state when the current route is already the restored trip", () => {
  const resolution = resolveRestoration({
    currentRoutePath: "/trip/t1/plan",
    tripSummaries: [
      { id: "t1", my_role: "participant" },
    ],
    activeTrip: {
      id: "t1",
    },
    explain: true,
  });

  assert.equal(resolution.disposition, "allow");
  assert.equal(resolution.destinationHref, "/trip/t1/plan");
  assert.equal(resolution.diagnostics?.acceptedCode, "restored-selection-accepted");
});

test("phase 9 runtime resolves restoration inside the workspace guard while leaving join outside that bootstrap flow", async () => {
  const source = await readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8");

  assert.match(source, /resolveRestoredWorkspaceDestination/);
  assert.match(source, /const initialPathRef = useRef\(location\.pathname\)/);
  assert.match(source, /app\.tripSummariesStatus === 'loading'/);
  assert.match(source, /if \(restorationResolution\?\.disposition === 'redirect'\)/);
  assert.match(source, /<Route element={<WorkspaceRouteGuard\/>}>/);
  assert.match(source, /<Route path="\/join\/:token" element={<JoinInvitePage\/>}\/>/);
});

test("phase 9 TripAppState keeps storage and request-header ownership while adding authoritative trip summaries for restoration", async () => {
  const source = await readFile(new URL("../../trip/src/final/TripAppState.jsx", import.meta.url), "utf8");
  const bootstrapSource = await readFile(new URL("../../trip/src/final/technicalSessionBootstrap.js", import.meta.url), "utf8");

  assert.match(source, /import \{ restoreTripAppBootstrapState \} from '\.\/technicalSessionBootstrap\.js'/);
  assert.match(source, /const \[hasAccountSession, setHasAccountSession\] = useState\(\(\) => bootstrapSession\.hasAccountSession\)/);
  assert.match(source, /const \[restoredTripId\] = useState\(\(\) => bootstrapSession\.restoredTripId\)/);
  assert.match(source, /const \[activeTripId, setActiveTripId\] = useState\(\(\) => bootstrapSession\.activeTripId\)/);
  assert.match(source, /const clearMembershipScopedState = useCallback\(\(\) => \{/);
  assert.match(source, /const adoptTechnicalTripContext = useCallback\(\(\{ membershipId: nextMembershipId, tripId: nextTripId, guestToken, inviteToken, profile \}\) => \{/);
  assert.match(source, /sessionRuntime\.adoptTechnicalTripContext\(\{\s*membershipId: nextMembershipId,\s*activeTripId: nextTripId,/s);
  assert.match(source, /const identityHeadersFor = useCallback\(scope => \{\s*const identity = sessionRuntime\.requestIdentityFor\(scope, technicalSessionFacts\)/s);
  assert.match(source, /const raw = await accountRequestJson\(`\/api\/trips\$\{priorityQuery\}`\)/);
  assert.match(source, /const created = await accountRequestJson\('\/api\/trips', \{/);
  assert.match(source, /setTripSummariesStatus\('failed'\)/);
  assert.match(source, /const readInviteAdoption = useCallback\(token => \{/);
  assert.match(bootstrapSource, /const \{ facts, restorationHint \} = sessionRuntime\.restoreTechnicalSession\(\)/);
  assert.match(bootstrapSource, /hasAccountSession: facts\.kind === 'account'/);
  assert.doesNotMatch(bootstrapSource, /requestIdentityFor\('account'/);
  assert.doesNotMatch(bootstrapSource, /Authorization/);
  assert.doesNotMatch(bootstrapSource, /Bearer /);
  assert.match(bootstrapSource, /restoredTripId: restorationHint\?\.tripId \|\| ''/);
  assert.match(bootstrapSource, /activeTripId: facts\.kind === 'none' \? '' : facts\.activeTripId \|\| ''/);
});
