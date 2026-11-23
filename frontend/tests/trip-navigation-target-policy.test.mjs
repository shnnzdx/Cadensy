import assert from "node:assert/strict";
import test from "node:test";
import { tripNavigationPolicy } from "../../shared/trip-navigation-policy/index.js";

/**
 * Phase 2 target-policy specification.
 *
 * These cases freeze APPROVED TARGET BEHAVIOR for the future
 * `trip-navigation-policy` module. They are intentionally separate from
 * Phase 1 characterization tests, which describe CURRENT OBSERVED BEHAVIOR.
 *
 * In Phase 3, these frozen cases exercise the real runtime implementation.
 */

const home = () => ({ kind: "home" });
const account = (section) => ({ kind: "account", section });
const join = (token) => ({ kind: "join", token });
const trip = (tripId, section) => ({ kind: "trip", tripId, section });

const diagnosticCodes = new Set([
  "current-route-allowed",
  "invite-invalid",
  "invite-existing-membership",
  "invite-open-allowed",
  "invite-completion-first-membership",
  "role-not-authorized",
  "trip-not-accessible",
  "stale-restored-selection",
  "return-target-accepted",
  "return-target-rejected",
  "restored-selection-accepted",
  "default-home",
  "default-guest-trip-plan",
]);

const resolveDestinationCases = [
  {
    id: "default-organizer-account-home",
    category: "default",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "organizer", identityKind: "account" }, state: "planning" },
      },
      intent: {},
    },
    expected: { disposition: "redirect", destination: home() },
    explain: ["default-home"],
  },
  {
    id: "default-participant-account-home",
    category: "default",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {},
    },
    expected: { disposition: "redirect", destination: home() },
    explain: ["default-home"],
  },
  {
    id: "default-guest-backed-participant-plan",
    category: "default",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "guest" }, state: "planning" },
      },
      intent: {},
    },
    expected: { disposition: "redirect", destination: trip("t1", "plan") },
    explain: ["default-guest-trip-plan"],
  },
  {
    id: "current-route-allowed-participant-plan",
    category: "current-route",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: { currentRoute: trip("t1", "plan") },
    },
    expected: { disposition: "allow", destination: trip("t1", "plan") },
    explain: ["current-route-allowed"],
  },
  {
    id: "current-route-organizer-only-rejected-for-participant",
    category: "current-route",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: { currentRoute: trip("t1", "invite") },
    },
    expected: { disposition: "redirect", destination: trip("t1", "plan") },
    explain: ["role-not-authorized"],
  },
  {
    id: "current-route-organizer-only-allowed-for-organizer",
    category: "current-route",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "organizer", identityKind: "account" }, state: "planning" },
      },
      intent: { currentRoute: trip("t1", "invite") },
    },
    expected: { disposition: "allow", destination: trip("t1", "invite") },
    explain: ["current-route-allowed"],
  },
  {
    id: "invite-open-valid-with-no-membership-stays-on-join-route",
    category: "invite",
    input: {
      accessState: "signed-out",
      relevantTripsById: {
        t3: { state: "planning" },
      },
      invite: { token: "inv-1", validity: "valid", tripId: "t3" },
      intent: { inviteFlow: { token: "inv-1", step: "open" }, currentRoute: join("inv-1") },
    },
    expected: { disposition: "allow", destination: join("inv-1") },
    explain: ["invite-open-allowed"],
  },
  {
    id: "invite-completion-first-membership-goes-to-preferences",
    category: "invite",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t3: { membership: { role: "participant", identityKind: "guest" }, state: "planning" },
      },
      invite: { token: "inv-1", validity: "valid", tripId: "t3" },
      intent: { inviteFlow: { token: "inv-1", step: "complete" }, currentRoute: join("inv-1") },
    },
    expected: { disposition: "redirect", destination: trip("t3", "preferences") },
    explain: ["invite-completion-first-membership"],
  },
  {
    id: "invite-open-valid-with-existing-membership-classifies-as-repeated",
    category: "invite",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t3: { membership: { role: "participant", identityKind: "guest" }, state: "planning" },
      },
      invite: { token: "inv-1", validity: "valid", tripId: "t3" },
      intent: { inviteFlow: { token: "inv-1", step: "open" }, currentRoute: join("inv-1") },
    },
    expected: { disposition: "redirect", destination: trip("t3", "plan") },
    explain: ["invite-existing-membership"],
  },
  {
    id: "invalid-invite-does-not-force-leaving-join-route",
    category: "invite",
    input: {
      accessState: "signed-out",
      relevantTripsById: {},
      invite: { token: "inv-stale", validity: "invalid", tripId: "t3" },
      intent: { inviteFlow: { token: "inv-stale", step: "open" }, currentRoute: join("inv-stale") },
    },
    expected: { disposition: "allow", destination: join("inv-stale") },
    explain: ["invite-invalid"],
  },
  {
    id: "invalid-invite-loses-to-valid-restored-selection",
    category: "precedence",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      invite: { token: "inv-stale", validity: "invalid", tripId: "t3" },
      intent: {
        inviteFlow: { token: "inv-stale", step: "open" },
        restoredSelection: { tripId: "t1", preferredSection: "plan" },
        currentRoute: join("inv-stale"),
      },
    },
    expected: { disposition: "redirect", destination: trip("t1", "plan") },
    explain: ["invite-invalid", "restored-selection-accepted"],
  },
  {
    id: "valid-return-target-beats-restored-selection",
    category: "precedence",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        t2: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {
        returnTarget: trip("t2", "updates"),
        restoredSelection: { tripId: "t1", preferredSection: "plan" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t2", "updates") },
    explain: ["return-target-accepted"],
  },
  {
    id: "organizer-only-return-target-rejected-for-participant-falls-back-to-restored-trip",
    category: "precedence",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        t2: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {
        returnTarget: trip("t2", "invite"),
        restoredSelection: { tripId: "t1", preferredSection: "plan" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t1", "plan") },
    explain: ["return-target-rejected", "role-not-authorized", "restored-selection-accepted"],
  },
  {
    id: "reachable-current-trip-route-beats-different-restored-selection",
    category: "precedence",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        "t-mia": { membership: { role: "organizer", identityKind: "account" }, state: "planning" },
        "t-smoke": { membership: { role: "organizer", identityKind: "account" }, state: "planning" },
      },
      intent: {
        currentRoute: trip("t-smoke", "plan"),
        restoredSelection: { tripId: "t-mia", preferredSection: "plan" },
      },
    },
    expected: { disposition: "allow", destination: trip("t-smoke", "plan") },
    explain: ["current-route-allowed"],
  },
  {
    id: "inaccessible-current-trip-route-still-falls-back-to-restored-selection",
    category: "precedence",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        "t-mia": { membership: { role: "organizer", identityKind: "account" }, state: "planning" },
      },
      intent: {
        currentRoute: trip("t-foreign", "plan"),
        restoredSelection: { tripId: "t-mia", preferredSection: "plan" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t-mia", "plan") },
    explain: ["restored-selection-accepted"],
  },
  {
    id: "organizer-only-current-route-for-participant-keeps-restored-fallback",
    category: "precedence",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        "t-mia": { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        "t-smoke": { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {
        currentRoute: trip("t-smoke", "members"),
        restoredSelection: { tripId: "t-mia", preferredSection: "plan" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t-mia", "plan") },
    explain: ["restored-selection-accepted"],
  },
  {
    id: "restored-selection-used-when-no-stronger-valid-intent-exists",
    category: "restored-selection",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "organizer", identityKind: "account" }, state: "planning" },
      },
      intent: {
        restoredSelection: { tripId: "t1", preferredSection: "chat" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t1", "chat") },
    explain: ["restored-selection-accepted"],
  },
  {
    id: "stale-restored-selection-absent-from-trip-facts-falls-back-to-default",
    category: "restored-selection",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t2: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {
        restoredSelection: { tripId: "t1", preferredSection: "plan" },
      },
    },
    expected: { disposition: "redirect", destination: home() },
    explain: ["stale-restored-selection", "default-home"],
  },
  {
    id: "valid-invite-open-beats-valid-return-target-and-restored-selection",
    category: "cross-trip",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        t2: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        t3: { state: "planning" },
      },
      invite: { token: "inv-3", validity: "valid", tripId: "t3" },
      intent: {
        currentRoute: join("inv-3"),
        inviteFlow: { token: "inv-3", step: "open" },
        returnTarget: trip("t2", "updates"),
        restoredSelection: { tripId: "t1", preferredSection: "plan" },
      },
    },
    expected: { disposition: "allow", destination: join("inv-3") },
    explain: ["invite-open-allowed"],
  },
  {
    id: "cross-trip-valid-return-target-t2-beats-restored-t1",
    category: "cross-trip",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        t2: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {
        returnTarget: trip("t2", "chat"),
        restoredSelection: { tripId: "t1", preferredSection: "plan" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t2", "chat") },
    explain: ["return-target-accepted"],
  },
  {
    id: "cross-trip-inaccessible-return-target-t2-falls-back-to-valid-restored-t1",
    category: "cross-trip",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        t2: { state: "planning" },
      },
      intent: {
        returnTarget: trip("t2", "plan"),
        restoredSelection: { tripId: "t1", preferredSection: "plan" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t1", "plan") },
    explain: ["trip-not-accessible", "restored-selection-accepted"],
  },
  {
    id: "trip-facts-without-membership-do-not-make-return-target-reachable",
    category: "cross-trip",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
        t2: { state: "planning" },
      },
      intent: {
        returnTarget: trip("t2", "chat"),
      },
    },
    expected: { disposition: "redirect", destination: home() },
    explain: ["trip-not-accessible", "default-home"],
  },
  {
    id: "default-landing-with-unknown-state-organizer-account-stays-deterministic",
    category: "unknown-state",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "organizer", identityKind: "account" }, state: "unknown" },
      },
      intent: {},
    },
    expected: { disposition: "redirect", destination: home() },
    explain: ["default-home"],
  },
  {
    id: "reachable-trip-route-with-unknown-state-is-still-allowed",
    category: "unknown-state",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "unknown" },
      },
      intent: {
        currentRoute: trip("t1", "chat"),
      },
    },
    expected: { disposition: "allow", destination: trip("t1", "chat") },
    explain: ["current-route-allowed"],
  },
  {
    id: "organizer-only-route-with-unknown-state-remains-role-restricted",
    category: "unknown-state",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "unknown" },
      },
      intent: {
        currentRoute: trip("t1", "members"),
      },
    },
    expected: { disposition: "redirect", destination: trip("t1", "plan") },
    explain: ["role-not-authorized"],
  },
  {
    id: "restored-selection-with-unknown-state-is-accepted-when-trip-is-reachable",
    category: "unknown-state",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "unknown" },
      },
      intent: {
        restoredSelection: { tripId: "t1", preferredSection: "updates" },
      },
    },
    expected: { disposition: "redirect", destination: trip("t1", "updates") },
    explain: ["restored-selection-accepted"],
  },
  {
    id: "return-target-with-unknown-state-is-accepted-when-trip-is-reachable",
    category: "unknown-state",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t2: { membership: { role: "participant", identityKind: "account" }, state: "unknown" },
      },
      intent: {
        returnTarget: trip("t2", "plan"),
      },
    },
    expected: { disposition: "redirect", destination: trip("t2", "plan") },
    explain: ["return-target-accepted"],
  },
  {
    id: "invite-completion-with-unknown-state-still-goes-to-preferences",
    category: "unknown-state",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t3: { membership: { role: "participant", identityKind: "guest" }, state: "unknown" },
      },
      invite: { token: "inv-3", validity: "valid", tripId: "t3" },
      intent: {
        inviteFlow: { token: "inv-3", step: "complete" },
        currentRoute: join("inv-3"),
      },
    },
    expected: { disposition: "redirect", destination: trip("t3", "preferences") },
    explain: ["invite-completion-first-membership"],
  },
];

const describeNavigationCases = [
  {
    id: "organizer-trip-navigation-order",
    category: "describe-navigation",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "organizer", identityKind: "account" }, state: "planning" },
      },
      currentRoute: trip("t1", "plan"),
    },
    expected: {
      homeRoute: home(),
      entryIds: ["plan", "chat", "updates", "preferences", "members", "invite"],
      activeId: "plan",
    },
  },
  {
    id: "participant-trip-navigation-hides-organizer-only-entries",
    category: "describe-navigation",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      currentRoute: trip("t1", "updates"),
    },
    expected: {
      homeRoute: home(),
      entryIds: ["plan", "chat", "updates", "preferences"],
      activeId: "updates",
    },
  },
  {
    id: "guest-backed-participant-navigation-has-no-home-route",
    category: "describe-navigation",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "guest" }, state: "planning" },
      },
      currentRoute: trip("t1", "chat"),
    },
    expected: {
      homeRoute: null,
      entryIds: ["plan", "chat", "updates", "preferences"],
      activeId: "chat",
    },
  },
  {
    id: "account-navigation-order-uses-stable-semantic-ids",
    category: "describe-navigation",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      currentRoute: account("settings"),
    },
    expected: {
      homeRoute: home(),
      entryIds: ["account-profile", "account-travel", "account-notifications", "account-settings"],
      activeId: "account-settings",
    },
  },
  {
    id: "organizer-visibility-remains-explicit-when-trip-state-is-unknown",
    category: "describe-navigation",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "organizer", identityKind: "account" }, state: "unknown" },
      },
      currentRoute: trip("t1", "invite"),
    },
    expected: {
      homeRoute: home(),
      entryIds: ["plan", "chat", "updates", "preferences", "members", "invite"],
      activeId: "invite",
    },
  },
];

const invalidInputCases = [
  {
    id: "signed-out-cannot-carry-membership",
    expectedErrorCode: "invalid-access-state-membership-combination",
    input: {
      accessState: "signed-out",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {},
    },
  },
  {
    id: "session-no-trip-access-cannot-carry-membership",
    expectedErrorCode: "invalid-access-state-membership-combination",
    input: {
      accessState: "session-no-trip-access",
      relevantTripsById: {
        t1: { membership: { role: "participant", identityKind: "account" }, state: "planning" },
      },
      intent: {},
    },
  },
  {
    id: "guest-identity-cannot-have-organizer-role",
    expectedErrorCode: "invalid-membership-role-identity-combination",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { membership: { role: "organizer", identityKind: "guest" }, state: "planning" },
      },
      intent: {},
    },
  },
  {
    id: "invite-flow-token-must-match-invite-fact-token",
    expectedErrorCode: "invite-token-mismatch",
    input: {
      accessState: "signed-out",
      relevantTripsById: {},
      invite: { token: "inv-a", validity: "valid", tripId: "t3" },
      intent: {
        inviteFlow: { token: "inv-b", step: "open" },
        currentRoute: join("inv-b"),
      },
    },
  },
  {
    id: "trip-session-requires-at-least-one-membership",
    expectedErrorCode: "trip-session-without-membership",
    input: {
      accessState: "trip-session",
      relevantTripsById: {
        t1: { state: "planning" },
      },
      intent: {},
    },
  },
];

const unresolvedPolicyCells = [];

test("phase 2 target-policy matrix defines the expected case inventory", () => {
  assert.equal(resolveDestinationCases.length, 28);
  assert.equal(describeNavigationCases.length, 5);
  assert.equal(invalidInputCases.length, 5);
  assert.equal(resolveDestinationCases.length + describeNavigationCases.length + invalidInputCases.length, 38);
  assert.deepEqual(Object.keys(tripNavigationPolicy).sort(), [
    "describeNavigation",
    "resolveDestination",
  ]);

  const allIds = [
    ...resolveDestinationCases.map((item) => item.id),
    ...describeNavigationCases.map((item) => item.id),
    ...invalidInputCases.map((item) => item.id),
  ];
  assert.equal(new Set(allIds).size, allIds.length);
});

test("phase 2 target-policy matrix freezes explicit precedence and cross-trip competition", () => {
  const precedenceIds = new Set(resolveDestinationCases.filter((item) =>
    ["precedence", "cross-trip"].includes(item.category)).map((item) => item.id));

  for (const id of [
    "invalid-invite-loses-to-valid-restored-selection",
    "reachable-current-trip-route-beats-different-restored-selection",
    "inaccessible-current-trip-route-still-falls-back-to-restored-selection",
    "organizer-only-current-route-for-participant-keeps-restored-fallback",
    "valid-return-target-beats-restored-selection",
    "organizer-only-return-target-rejected-for-participant-falls-back-to-restored-trip",
    "valid-invite-open-beats-valid-return-target-and-restored-selection",
    "cross-trip-valid-return-target-t2-beats-restored-t1",
    "cross-trip-inaccessible-return-target-t2-falls-back-to-valid-restored-t1",
  ]) {
    assert(precedenceIds.has(id), `Missing explicit precedence case: ${id}`);
  }
});

test("phase 2 target-policy matrix freezes repeated-invite semantics without caller repeat flags", () => {
  const inviteCases = resolveDestinationCases.filter((item) => item.category === "invite");
  assert(inviteCases.some((item) => item.id === "invite-open-valid-with-no-membership-stays-on-join-route"));
  assert(inviteCases.some((item) => item.id === "invite-completion-first-membership-goes-to-preferences"));
  assert(inviteCases.some((item) => item.id === "invite-open-valid-with-existing-membership-classifies-as-repeated"));
  assert(inviteCases.some((item) => item.id === "invalid-invite-does-not-force-leaving-join-route"));

  for (const item of inviteCases) {
    const step = item.input.intent.inviteFlow?.step;
    assert(step === "open" || step === "complete");
    assert(!JSON.stringify(item.input).includes("repeat"));
  }
});

test("phase 2 target-policy matrix freezes organizer, participant, and guest-backed visibility", () => {
  const caseById = new Map(describeNavigationCases.map((item) => [item.id, item]));

  assert.deepEqual(caseById.get("organizer-trip-navigation-order").expected.entryIds, [
    "plan",
    "chat",
    "updates",
    "preferences",
    "members",
    "invite",
  ]);
  assert.deepEqual(caseById.get("participant-trip-navigation-hides-organizer-only-entries").expected.entryIds, [
    "plan",
    "chat",
    "updates",
    "preferences",
  ]);
  assert.equal(caseById.get("guest-backed-participant-navigation-has-no-home-route").expected.homeRoute, null);
});

test("phase 2 target-policy matrix makes unknown trip-state behavior explicit", () => {
  const unknownCases = resolveDestinationCases.filter((item) => item.category === "unknown-state");

  assert.equal(unknownCases.length, 6);
  assert(unknownCases.some((item) => item.id === "default-landing-with-unknown-state-organizer-account-stays-deterministic"));
  assert(unknownCases.some((item) => item.id === "reachable-trip-route-with-unknown-state-is-still-allowed"));
  assert(unknownCases.some((item) => item.id === "organizer-only-route-with-unknown-state-remains-role-restricted"));
  assert(unknownCases.some((item) => item.id === "restored-selection-with-unknown-state-is-accepted-when-trip-is-reachable"));
  assert(unknownCases.some((item) => item.id === "return-target-with-unknown-state-is-accepted-when-trip-is-reachable"));
  assert(unknownCases.some((item) => item.id === "invite-completion-with-unknown-state-still-goes-to-preferences"));
  assert.deepEqual(unresolvedPolicyCells, []);
});

test("phase 2 target-policy matrix freezes deterministic contract-violation behavior for invalid normalized input", () => {
  const expectedCodes = invalidInputCases.map((item) => item.expectedErrorCode);

  assert.deepEqual(expectedCodes, [
    "invalid-access-state-membership-combination",
    "invalid-access-state-membership-combination",
    "invalid-membership-role-identity-combination",
    "invite-token-mismatch",
    "trip-session-without-membership",
  ]);
});

test("phase 2 target-policy diagnostics use stable machine-readable reason codes", () => {
  for (const item of resolveDestinationCases) {
    for (const code of item.explain || []) {
      assert(diagnosticCodes.has(code), `Unexpected diagnostic code ${code} in ${item.id}`);
      assert.match(code, /^[a-z0-9-]+$/);
    }
  }
});

test("phase 3 runtime implementation satisfies all frozen resolveDestination matrix cases", () => {
  for (const item of resolveDestinationCases) {
    const actual = tripNavigationPolicy.resolveDestination(item.input, { explain: true });

    assert.deepEqual(
      { disposition: actual.disposition, destination: actual.destination },
      item.expected,
      item.id,
    );

    const observedCodes = new Set([
      actual.diagnostics?.acceptedCode,
      ...(actual.diagnostics?.rejectedIntents || []).flatMap((entry) => [entry.code, entry.detailCode]),
    ].filter(Boolean));

    for (const code of item.explain || []) {
      assert(observedCodes.has(code), `${item.id} missing diagnostic code ${code}`);
    }
  }
});

test("phase 3 runtime implementation satisfies all frozen describeNavigation matrix cases", () => {
  for (const item of describeNavigationCases) {
    const actual = tripNavigationPolicy.describeNavigation(item.input, { explain: true });

    assert.deepEqual(actual.contextRoute, item.expected.homeRoute, item.id);
    assert.deepEqual(actual.entries.map((entry) => entry.id), item.expected.entryIds, item.id);
    assert.deepEqual(
      actual.entries.find((entry) => entry.active)?.id || null,
      item.expected.activeId,
      item.id,
    );
    assert(actual.entries.every((entry) => entry.destination && typeof entry.destination.kind === "string"), item.id);
  }
});

test("phase 3 runtime implementation throws stable contract errors for invalid normalized input", () => {
  for (const item of invalidInputCases) {
    assert.throws(
      () => tripNavigationPolicy.resolveDestination(item.input),
      (error) => error?.code === item.expectedErrorCode,
      item.id,
    );
  }
});
