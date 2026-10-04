import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const tripAppStateUrl = new URL("../../trip/src/final/TripAppState.jsx", import.meta.url);

test("phase 5 TripAppState delegates request identity ownership to session-runtime while keeping scope selection local", async () => {
  const source = await readFile(tripAppStateUrl, "utf8");

  assert.match(source, /import \{ createSessionRuntime, SESSION_RUNTIME_CODES \} from '\.\.\/\.\.\/\.\.\/shared\/session-runtime\/index\.js'/);
  assert.match(source, /const \[sessionRuntime\] = useState\(\(\) => createSessionRuntime\(\)\)/);
  assert.match(source, /const identityHeadersFor = useCallback\(scope => \{\s*const identity = sessionRuntime\.requestIdentityFor\(scope, technicalSessionFacts\)/s);
  assert.match(source, /const sessionRequestJson = useCallback\(async \(scope, path, options = \{\}\) => \{/);
  assert.match(source, /\.\.\.identityHeadersFor\(scope\),/);
  assert.match(source, /const accountRequestJson = useCallback\(async \(path, options = \{\}\) => \{\s*return sessionRequestJson\('account', path, options\)/s);
  assert.match(source, /const requestJson = useCallback\(async \(path, options = \{\}\) => \{\s*return sessionRequestJson\('trip', path, options\)/s);
});

test("phase 5 scope mapping keeps trip summaries and createTrip on account scope while workspace fetches remain trip scoped", async () => {
  const source = await readFile(tripAppStateUrl, "utf8");

  assert.match(source, /const raw = await accountRequestJson\(`\/api\/trips\$\{priorityQuery\}`\)/);
  assert.match(source, /const created = await accountRequestJson\('\/api\/trips', \{/);
  assert.match(source, /const raw = await requestJson\(`\/api\/trips\/\$\{activeTripId\}`\)/);
  assert.match(source, /const raw = await requestJson\('\/api\/me'\)/);
  assert.match(source, /return requestJson\(`\/api\/trips\/\$\{tripId\}\/preferences\/me`\)/);
  assert.doesNotMatch(source, /requestIdentityFor\('membership-compat'/);
});

test("phase 5 removes manual request header assembly from TripAppState helpers", async () => {
  const source = await readFile(tripAppStateUrl, "utf8");
  const authorizationMatches = source.match(/Authorization: `Bearer \$\{/g) || [];

  assert.equal(authorizationMatches.length, 0);
  assert.doesNotMatch(source, /"X-Trip-Id"/);
  assert.doesNotMatch(source, /"X-Membership-Id"/);
});
