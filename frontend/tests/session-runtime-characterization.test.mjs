import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// Phase 1 characterization only:
// These tests describe CURRENT observed session/storage/header behavior.
// They intentionally do not assert Candidate 2 target behavior.

const loginPageUrl = new URL("../app/login/page.tsx", import.meta.url);
const finalAppUrl = new URL("../../trip/src/final/FinalApp.jsx", import.meta.url);
const tripAppStateUrl = new URL("../../trip/src/final/TripAppState.jsx", import.meta.url);
const backendApiUrl = new URL("../../backend/app/api/main.py", import.meta.url);
const backendAuthUrl = new URL("../../backend/app/domain/auth.py", import.meta.url);

async function loadSources() {
  const [loginPage, finalApp, tripAppState, backendApi, backendAuth] = await Promise.all([
    readFile(loginPageUrl, "utf8"),
    readFile(finalAppUrl, "utf8"),
    readFile(tripAppStateUrl, "utf8"),
    readFile(backendApiUrl, "utf8"),
    readFile(backendAuthUrl, "utf8"),
  ]);

  return { loginPage, finalApp, tripAppState, backendApi, backendAuth };
}

test("phase 1 current login persists auth token, membership id, and trip id before host navigation", async () => {
  const { loginPage } = await loadSources();

  assert.match(loginPage, /const membership = result\.default_membership \|\| result\.memberships\?\.\[0\];/);
  assert.match(loginPage, /const adoption = sessionRuntime\.adoptAccountAuth\(\{\s*token: result\.token,\s*\.\.\.\(membership \? \{/s);
  assert.match(loginPage, /window\.location\.href = nextPath;/);
});

test("phase 1 current login treats storage exceptions as the same generic failure bucket as backend reachability", async () => {
  const { loginPage } = await loadSources();

  assert.match(loginPage, /try \{/);
  assert.match(loginPage, /function hasPersistenceWarning\(warnings: string\[\]\): boolean \{/);
  assert.match(loginPage, /if \(hasPersistenceWarning\(adoption\.warnings\)\) \{\s*setError\("Could not reach the backend\. Make sure the API is running\."\);\s*return;\s*\}/s);
  assert.match(loginPage, /\} catch \{/);
  assert.match(loginPage, /setError\("Could not reach the backend\. Make sure the API is running\."\);/);
  assert.doesNotMatch(loginPage, /window\.localStorage\.setItem\(/);
  assert.doesNotMatch(loginPage, /\bquota\b|private browsing|storage unavailable/i);
});

test("phase 1 current restore bootstrap now delegates core technical session restore to the shared adapter without direct raw-key reads in TripAppState", async () => {
  const { tripAppState } = await loadSources();

  assert.match(tripAppState, /import \{ restoreTripAppBootstrapState \} from '\.\/technicalSessionBootstrap\.js'/);
  assert.match(tripAppState, /const \[bootstrapSession\] = useState\(\(\) => restoreTripAppBootstrapState\(\{/);
  assert.match(tripAppState, /const \[hasAccountSession, setHasAccountSession\] = useState\(\(\) => bootstrapSession\.hasAccountSession\)/);
  assert.match(tripAppState, /const \[membershipId, setMembershipId\] = useState\(\(\) => bootstrapSession\.membershipId\)/);
  assert.match(tripAppState, /const \[restoredTripId\] = useState\(\(\) => bootstrapSession\.restoredTripId\)/);
  assert.match(tripAppState, /const \[activeTripId, setActiveTripId\] = useState\(\(\) => bootstrapSession\.activeTripId\)/);
  assert.match(tripAppState, /const \[tripSummariesStatus, setTripSummariesStatus\] = useState\(\(\) => \(bootstrapSession\.hasAccountSession \? 'idle' : 'not-needed'\)\)/);
  assert.doesNotMatch(tripAppState, /readLocal\(/);
});

test("phase 1 current malformed or partial storage remains a technical-session concern, including invite-adoption cache parsing", async () => {
  const { finalApp, tripAppState } = await loadSources();

  assert.match(tripAppState, /const readInviteAdoption = useCallback\(token => \{/);
  assert.match(tripAppState, /const \{ record \} = sessionRuntime\.readInviteAdoption\(token\)/);
  assert.match(finalApp, /const savedInviteSession = useMemo\(\(\) => app\.readInviteAdoption\(token\), \[app, token\]\)/);
  assert.match(tripAppState, /bootstrapSession\.membershipId/);
  assert.match(tripAppState, /bootstrapSession\.restoredTripId/);
  assert.match(tripAppState, /bootstrapSession\.hasAccountSession/);
  assert.doesNotMatch(tripAppState, /JSON\.parse\(.*tripsync:authToken/);
  assert.doesNotMatch(tripAppState, /JSON\.parse\(.*tripsync:membershipId/);
  assert.doesNotMatch(tripAppState, /JSON\.parse\(.*tripsync:tripId/);
});

test("phase 1 current request identity behavior keeps scope selection in TripAppState while delegating ready-to-apply identity headers to session-runtime", async () => {
  const { tripAppState } = await loadSources();

  assert.match(tripAppState, /const \[sessionRuntime\] = useState\(\(\) => createSessionRuntime\(\)\)/);
  assert.match(tripAppState, /const identityHeadersFor = useCallback\(scope => \{\s*const identity = sessionRuntime\.requestIdentityFor\(scope, technicalSessionFacts\)/s);
  assert.match(tripAppState, /const accountRequestJson = useCallback\(async \(path, options = \{\}\) => \{\s*return sessionRequestJson\('account', path, options\)/s);
  assert.match(tripAppState, /const requestJson = useCallback\(async \(path, options = \{\}\) => \{\s*return sessionRequestJson\('trip', path, options\)/s);
  assert.doesNotMatch(tripAppState, /\.\.\.\(authToken \? \{ Authorization: `Bearer \$\{authToken\}` \} : \{\}\),/);
  assert.doesNotMatch(tripAppState, /\.\.\.\(activeTripId \? \{ 'X-Trip-Id': activeTripId \} : \{\}\),/);
  assert.doesNotMatch(tripAppState, /\.\.\.\(DEV_ALLOW_MEMBERSHIP_HEADER && membershipId \? \{ 'X-Membership-Id': membershipId \} : \{\}\),/);
});

test("phase 1 invite adoption cache remains a routing hint and cannot restore Guest credentials", async () => {
  const { finalApp, tripAppState } = await loadSources();

  assert.match(tripAppState, /const \{ record \} = sessionRuntime\.readInviteAdoption\(token\)/);
  assert.match(tripAppState, /sessionRuntime\.adoptGuestAuth\(\{\s*token: guestToken,\s*membershipId: nextMembershipId,\s*activeTripId: nextTripId,/s);
  assert.match(finalApp, /const savedInviteSession = useMemo\(\(\) => app\.readInviteAdoption\(token\), \[app, token\]\)/);
  assert.match(finalApp, /savedInviteSession\?\.tripId/);
  assert.doesNotMatch(finalApp, /app\.adoptTechnicalTripContext\(\{\s*membershipId: savedInviteSession\.membershipId/);
});

test("phase 1 current createTrip and invite join both adopt technical trip context through shared session-runtime adoption", async () => {
  const { finalApp, tripAppState } = await loadSources();

  assert.match(tripAppState, /if \(created\.membership_id\) \{\s*adoptTechnicalTripContext\(\{\s*membershipId: created\.membership_id,\s*tripId: created\.id,\s*profile:/s);
  assert.match(finalApp, /const joined = await app\.joinInvite\(token, \{/);
  assert.match(finalApp, /app\.adoptTechnicalTripContext\(\{\s*membershipId: joined\.membership_id,\s*tripId: joined\.trip_id,\s*\.\.\.\(!withAccount \? \{ guestToken: joined\.guest_token \} : \{\}\),/s);
});

test("phase 1 current logout delegates technical-session clear to session-runtime, preserves invite adoption caches, and keeps outer reset/navigation in TripAppState", async () => {
  const { tripAppState } = await loadSources();

  assert.match(tripAppState, /sessionRuntime\.logoutTechnicalSession\(technicalSessionFacts, \{/);
  assert.match(tripAppState, /revoke: async \(\) => publicRequestJson\('\/api\/auth\/logout', \{/);
  assert.match(tripAppState, /headers: identityHeadersFor\(technicalSessionFacts\.kind === 'guest' \? 'trip' : 'account'\),/);
  assert.doesNotMatch(tripAppState, /readInviteAdoption\(.*removeItem/i);
  assert.match(tripAppState, /setHasAccountSession\(false\)/);
  assert.match(tripAppState, /setCurrentUser\(null\)/);
  assert.match(tripAppState, /window\.top\.location\.href = loginUrl\(\)/);
});

test("phase 1 current authoritative invalid credential 401 clears the local technical session while leaving UI handling in TripAppState", async () => {
  const { tripAppState, backendApi, backendAuth } = await loadSources();

  assert.match(backendAuth, /raise AuthRequired\("Invalid or expired session"\)/);
  assert.match(backendApi, /except auth_service\.AuthRequired as exc:\s*raise HTTPException\(401, str\(exc\)\)/s);
  assert.match(tripAppState, /const invalidationCause = classifyTechnicalSessionInvalidation\(\{/);
  assert.match(tripAppState, /sessionRuntime\.invalidateTechnicalSession\(technicalSessionFacts, cause\)/);
  assert.match(tripAppState, /if \(!response\.ok\) \{\s*const body = await response\.json\(\)\.catch\(\(\) => \(\{\}\)\)\s*const message = typeof body\.detail === 'string' \? body\.detail : `Request failed \(\$\{response\.status\}\)`/s);
  assert.match(tripAppState, /setError\(err\.message \|\| 'Failed to load trip data'\)/);
  assert.match(tripAppState, /const loadError = error \|\| \(missingSession \? 'Join from an invite link or configure a membership session\.' : ''\)/);
  assert.match(tripAppState, /<a className="btn btnSecondary" href=\{loginUrl\(\)\} target="_top">Sign in<\/a>/);
  assert.match(tripAppState, /if \(invalidationCause\) \{\s*applyTechnicalSessionInvalidation\(invalidationCause, message\)\s*\}/s);
  assert.doesNotMatch(tripAppState, /if \(response\.status === 403\)[\s\S]*invalidateTechnicalSession/);
  assert.doesNotMatch(tripAppState, /if \(response\.status === 404\)[\s\S]*invalidateTechnicalSession/);
});
