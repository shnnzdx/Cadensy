import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const tripAppStateUrl = new URL("../../trip/src/final/TripAppState.jsx", import.meta.url);
const finalAppUrl = new URL("../../trip/src/final/FinalApp.jsx", import.meta.url);

test("phase 6 TripAppState routes technical trip-context adoption through session-runtime", async () => {
  const source = await readFile(tripAppStateUrl, "utf8");

  assert.match(source, /const readInviteAdoption = useCallback\(token => \{/);
  assert.match(source, /const \{ record \} = sessionRuntime\.readInviteAdoption\(token\)/);
  assert.match(source, /const clearMembershipScopedState = useCallback\(\(\) => \{/);
  assert.match(source, /const adoptTechnicalTripContext = useCallback\(\(\{ membershipId: nextMembershipId, tripId: nextTripId, guestToken, inviteToken, profile \}\) => \{/);
  assert.match(source, /sessionRuntime\.adoptTechnicalTripContext\(\{\s*membershipId: nextMembershipId,\s*activeTripId: nextTripId,/s);
  assert.doesNotMatch(source, /writeLocal\('tripsync:membershipId'/);
  assert.doesNotMatch(source, /writeLocal\('tripsync:tripId'/);
  assert.doesNotMatch(source, /tripsync:invite:/);
});

test("phase 6 createTrip and invite flows use shared technical trip adoption without moving navigation policy", async () => {
  const [tripAppStateSource, finalAppSource] = await Promise.all([
    readFile(tripAppStateUrl, "utf8"),
    readFile(finalAppUrl, "utf8"),
  ]);

  assert.match(tripAppStateSource, /if \(created\.membership_id\) \{\s*adoptTechnicalTripContext\(\{\s*membershipId: created\.membership_id,\s*tripId: created\.id,\s*profile:/s);
  assert.match(finalAppSource, /const savedInviteSession = useMemo\(\(\) => app\.readInviteAdoption\(token\), \[app, token\]\)/);
  assert.doesNotMatch(finalAppSource, /app\.adoptTechnicalTripContext\(\{\s*membershipId: savedInviteSession\.membershipId/s);
  assert.match(finalAppSource, /const completeJoin = \(joined, \{ isGuest, profile \}\) => \{\s*app\.adoptTechnicalTripContext\(\{\s*membershipId: joined\.membership_id,\s*tripId: joined\.trip_id,/s);
  assert.match(finalAppSource, /resolveInviteJoinRoute\(\{/);
  assert.doesNotMatch(finalAppSource, /navigate\(`\/trip\/\$\{saved\.tripId\}\/plan`/);
  assert.doesNotMatch(finalAppSource, /navigate\(`\/trip\/\$\{joined\.trip_id\}\/preferences`/);
});
