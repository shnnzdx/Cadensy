import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const tripAppStateUrl = new URL("../../trip/src/final/TripAppState.jsx", import.meta.url);

test("phase 8 TripAppState delegates logout technical-session lifecycle to session-runtime", async () => {
  const source = await readFile(tripAppStateUrl, "utf8");

  assert.match(source, /const result = await sessionRuntime\.logoutTechnicalSession\(technicalSessionFacts, \{/);
  assert.match(source, /revoke: async \(\) => publicRequestJson\('\/api\/auth\/logout', \{/);
  assert.match(source, /headers: identityHeadersFor\(technicalSessionFacts\.kind === 'guest' \? 'trip' : 'account'\),/);
  assert.doesNotMatch(source, /removeLocal\('tripsync:authToken'\)/);
  assert.doesNotMatch(source, /removeLocal\('tripsync:membershipId'\)/);
  assert.doesNotMatch(source, /removeLocal\('tripsync:tripId'\)/);
  assert.doesNotMatch(source, /Authorization: `Bearer \$\{token\}`/);
});

test("phase 8 logout keeps domain reset and navigation ownership outside session-runtime", async () => {
  const source = await readFile(tripAppStateUrl, "utf8");

  assert.match(source, /setHasAccountSession\(false\)/);
  assert.match(source, /setMembershipId\(result\.facts\.kind === 'none' \? '' : \(result\.facts\.membershipId \|\| ''\)\)/);
  assert.match(source, /setActiveTripId\(result\.facts\.kind === 'none' \? '' : \(result\.facts\.activeTripId \|\| ''\)\)/);
  assert.match(source, /setCurrentUser\(null\)/);
  assert.match(source, /setTripSummaries\(\[\]\)/);
  assert.match(source, /setTripSummariesStatus\('not-needed'\)/);
  assert.match(source, /window\.top\.location\.href = loginUrl\(\)/);
});
