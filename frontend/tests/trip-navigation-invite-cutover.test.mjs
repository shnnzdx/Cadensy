import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { resolveInviteJoinRoute } from "../../trip/src/final/workspace-navigation-model.js";

function invitePreview() {
  return {
    name: "Chicago birthday",
    destination: "Chicago",
    preferred_start_date: "2026-08-14",
    preferred_end_date: "2026-08-17",
  };
}

test("phase 8 valid fresh invite open stays on the join route through policy", () => {
  const resolution = resolveInviteJoinRoute({
    currentRoutePath: "/join/inv-open",
    token: "inv-open",
    step: "open",
    invitePreview: invitePreview(),
    explain: true,
  });

  assert.equal(resolution.disposition, "allow");
  assert.equal(resolution.destinationHref, "/join/inv-open");
  assert.equal(resolution.diagnostics?.acceptedCode, "invite-open-allowed");
});

test("phase 8 repeated invite redirect is derived from invite facts plus existing membership, not caller repeat flags", () => {
  const resolution = resolveInviteJoinRoute({
    currentRoutePath: "/join/inv-repeat",
    currentUser: {
      tripId: "t-repeat",
      role: "guest",
      isGuest: true,
    },
    activeTrip: {
      id: "t-repeat",
      status: "Planning",
    },
    activeTripId: "t-repeat",
    membershipId: "m-repeat",
    token: "inv-repeat",
    step: "open",
    invitePreview: invitePreview(),
    inviteTripId: "t-repeat",
    explain: true,
  });

  assert.equal(resolution.disposition, "redirect");
  assert.equal(resolution.destinationHref, "/trip/t-repeat/plan");
  assert.equal(resolution.diagnostics?.acceptedCode, "invite-existing-membership");
});

test("phase 8 fresh invite completion redirects to preferences through policy", () => {
  const resolution = resolveInviteJoinRoute({
    currentRoutePath: "/join/inv-complete",
    currentUser: {
      tripId: "t-complete",
      role: "guest",
      isGuest: true,
    },
    activeTrip: {
      id: "t-complete",
    },
    activeTripId: "t-complete",
    membershipId: "m-complete",
    token: "inv-complete",
    step: "complete",
    joinResult: {
      membership_id: "m-complete",
      trip_id: "t-complete",
      role: "participant",
    },
    explain: true,
  });

  assert.equal(resolution.disposition, "redirect");
  assert.equal(resolution.destinationHref, "/trip/t-complete/preferences");
  assert.equal(resolution.diagnostics?.acceptedCode, "invite-completion-first-membership");
});

test("phase 8 invalid invite stays on the join route and leaves unavailable UI ownership outside policy", () => {
  const resolution = resolveInviteJoinRoute({
    currentRoutePath: "/join/inv-stale",
    token: "inv-stale",
    step: "open",
    inviteErrorStatus: 404,
    explain: true,
  });

  assert.equal(resolution.disposition, "allow");
  assert.equal(resolution.destinationHref, "/join/inv-stale");
  assert.equal(resolution.diagnostics?.acceptedCode, "invite-invalid");
});

test("phase 8 adapter does not fabricate invite trip identity from current session when preview lacks tripId", () => {
  const resolution = resolveInviteJoinRoute({
    currentRoutePath: "/join/inv-no-trip-id",
    currentUser: {
      tripId: "t-session",
      role: "participant",
      isGuest: false,
    },
    activeTrip: {
      id: "t-session",
      status: "Planning",
    },
    activeTripId: "t-session",
    membershipId: "m-session",
    token: "inv-no-trip-id",
    step: "open",
    invitePreview: invitePreview(),
    explain: true,
  });

  assert.equal(resolution.disposition, "allow");
  assert.equal(resolution.destinationHref, "/join/inv-no-trip-id");
  assert.equal(resolution.diagnostics?.acceptedCode, "invite-open-allowed");
});

test("phase 8 runtime keeps preview tripId absent in backend preview and consumes token-scoped saved invite facts instead of hard-coded destinations", async () => {
  const [backendSource, finalAppSource, tripAppStateSource] = await Promise.all([
    readFile(new URL("../../backend/app/domain/trips/service.py", import.meta.url), "utf8"),
    readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../trip/src/final/TripAppState.jsx", import.meta.url), "utf8"),
  ]);

  assert.match(
    backendSource,
    /def invite_preview\(\s*db: Session, token: str, \*, opener: InviteOpenContext \| None = None\s*\) -> dict:/,
  );
  assert.match(backendSource, /"organizer_name": organizer_user\.name if organizer_user else "Organizer",/);
  assert.doesNotMatch(backendSource, /"trip_id": invite\.trip_id/);
  assert.match(finalAppSource, /const savedInviteSession = useMemo\(\(\) => app\.readInviteAdoption\(token\), \[app, token\]\)/);
  assert.match(finalAppSource, /resolveInviteJoinRoute\(\{/);
  assert.match(finalAppSource, /inviteTripId: savedInviteSession\?\.tripId \|\| null/);
  assert.doesNotMatch(finalAppSource, /navigate\(`\/trip\/\$\{saved\.tripId\}\/plan`, \{ replace: true \}\)/);
  assert.doesNotMatch(finalAppSource, /navigate\(`\/trip\/\$\{joined\.trip_id\}\/preferences`, \{ replace: true \}\)/);
  assert.match(tripAppStateSource, /const readInviteAdoption = useCallback\(token => \{/);
  assert.match(tripAppStateSource, /sessionRuntime\.adoptTechnicalTripContext\(\{/);
});
