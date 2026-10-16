import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const loginPageUrl = new URL("../app/login/page.tsx", import.meta.url);
const finalAppUrl = new URL("../../trip/src/final/FinalApp.jsx", import.meta.url);
const tripAppStateUrl = new URL("../../trip/src/final/TripAppState.jsx", import.meta.url);
const previewContractUrl = new URL("../../shared/tripsync-preview-contract.js", import.meta.url);
const backendTripServiceUrl = new URL("../../backend/app/domain/trips/service.py", import.meta.url);
const tripPageUrl = new URL("../app/trip/page.tsx", import.meta.url);

async function loadSources() {
  const [loginPage, finalApp, tripAppState, previewContract, tripPage, backendTripService] = await Promise.all([
    readFile(loginPageUrl, "utf8"),
    readFile(finalAppUrl, "utf8"),
    readFile(tripAppStateUrl, "utf8"),
    readFile(previewContractUrl, "utf8"),
    readFile(tripPageUrl, "utf8"),
    readFile(backendTripServiceUrl, "utf8"),
  ]);

  return { loginPage, finalApp, tripAppState, previewContract, tripPage, backendTripService };
}

test("characterizes current login redirect and session persistence behavior", async () => {
  const { loginPage } = await loadSources();

  assert.match(loginPage, /const nextPathRef = useRef\("\/trip"\);/);
  assert.match(loginPage, /const requestedNext = params\.get\("next"\);/);
  assert.match(loginPage, /const resolvedNext = isSafeInternalReturnPath\(requestedNext\) \? requestedNext : "\/trip";/);
  assert.match(loginPage, /createSessionRuntime, SESSION_RUNTIME_CODES/);
  assert.match(loginPage, /const membership = result\.default_membership \|\| result\.memberships\?\.\[0\];/);
  assert.match(loginPage, /const adoption = sessionRuntime\.adoptAccountAuth\(\{\s*token: result\.token,\s*\.\.\.\(membership \? \{/s);
  assert.match(loginPage, /window\.location\.href = nextPathRef\.current;/);
});

test("characterizes current workspace host entry as a fixed preview iframe handoff", async () => {
  const { previewContract, tripPage } = await loadSources();

  assert.match(previewContract, /export const tripPreviewDefaultHashRoute = "#\/";/);
  assert.match(previewContract, /export function buildTripPreviewFrameSrc\(route = tripPreviewDefaultHashRoute\)/);
  assert.match(previewContract, /return `\$\{tripPreviewBasePath\}\/index\.html\$\{normalizeTripPreviewHashRoute\(route\)\}`;/);
  assert.match(tripPage, /<iframe/);
  assert.match(tripPage, /src=\{tripPreviewFrameSrc\}/);
});

test("characterizes current restored-trip session behavior in TripAppState", async () => {
  const { tripAppState } = await loadSources();

  assert.match(tripAppState, /const \[activeTripId, setActiveTripId\] = useState\(\(\) => bootstrapSession\.activeTripId\)/);
  assert.match(tripAppState, /const identityHeadersFor = useCallback\(scope => \{\s*const identity = sessionRuntime\.requestIdentityFor\(scope, technicalSessionFacts\)/s);
  assert.match(tripAppState, /const raw = await requestJson\(`\/api\/trips\/\$\{activeTripId\}`\)/);
  assert.match(tripAppState, /const raw = await requestJson\(`\/api\/trips\/\$\{activeTripId\}\/plans\/current`\)/);
  assert.match(tripAppState, /const loadError = error \|\| \(missingSession \? 'Join from an invite link or configure a membership session\.' : ''\)/);
});

test("characterizes current invite fact availability and policy-driven join destinations", async () => {
  const { finalApp, tripAppState, backendTripService } = await loadSources();

  assert.match(finalApp, /const savedInviteSession = useMemo\(\(\) => app\.readInviteAdoption\(token\), \[app, token\]\)/);
  assert.match(finalApp, /app\.getInvite\(token\)/);
  assert.match(finalApp, /resolveInviteJoinRoute\(\{/);
  assert.match(finalApp, /inviteTripId: savedInviteSession\?\.tripId \|\| null/);
  assert.match(finalApp, /const joined = await app\.joinInvite\(token, name\)/);
  assert.match(finalApp, /const joined = await app\.joinAccountInvite\(token\)/);
  assert.match(finalApp, /if \(mapped\.kind === 'invite'\) setInvalid\(true\)/);
  assert.match(finalApp, /This link is no longer active/);
  assert.doesNotMatch(finalApp, /navigate\(`\/trip\/\$\{saved\.tripId\}\/plan`, \{ replace: true \}\)/);
  assert.doesNotMatch(finalApp, /navigate\(`\/trip\/\$\{joined\.trip_id\}\/preferences`, \{ replace: true \}\)/);
  assert.match(tripAppState, /sessionRuntime\.adoptTechnicalTripContext\(\{/);
  assert.match(backendTripService, /def invite_preview\(db: Session, token: str\) -> dict:/);
  assert.doesNotMatch(backendTripService, /"trip_id": invite\.trip_id/);
  assert.match(backendTripService, /return JoinedInvite\(\s*membership=membership,\s*trip_id=invite\.trip_id,/s);
});

test("characterizes current centralized workspace route guarding and guest presentation split", async () => {
  const { finalApp } = await loadSources();

  assert.match(finalApp, /buildWorkspaceNavigationModel/);
  assert.match(finalApp, /resolveCurrentWorkspaceRoute/);
  assert.match(finalApp, /function WorkspaceRouteGuard\(\)/);
  assert.match(finalApp, /navigation\.entries\.map\(entry => <Link key=\{entry\.id\} className=\{entry\.active \? 'active' : ''\} to=\{entry\.href\}>/);
  assert.match(finalApp, /members: 'Members'/);
  assert.match(finalApp, /invite: 'Invite'/);
  assert.match(finalApp, /const isGuest = currentUser\.isGuest/);
  assert.match(finalApp, /<Route element={<WorkspaceRouteGuard\/>}>/);
  assert.doesNotMatch(finalApp, /if \(currentUser\.role === 'guest'\) return <Navigate to=\{`\/trip\/\$\{currentTrip\.id\}\/plan`\} replace\/>/);
});

test("characterizes current route declarations and redirects", async () => {
  const { finalApp } = await loadSources();

  assert.match(finalApp, /<Route path="\*" element={<Navigate to="\/" replace\/>}\/>/);
  assert.match(finalApp, /<Route path="\/trip\/:tripId\/plan" element={<PlanRoute\/>}\/>/);
  assert.match(finalApp, /<Route path="\/trip\/:tripId\/chat" element={<ChatWorkspace thread="personal"\/>}\/>/);
  assert.match(finalApp, /<Route path="\/trip\/:tripId\/members" element={<MembersPage\/>}\/>/);
  assert.match(finalApp, /<Route path="\/trip\/:tripId\/invite" element={<InvitePage\/>}\/>/);
  assert.match(finalApp, /<Route path="\/join\/:token" element={<JoinInvitePage\/>}\/>/);
});
