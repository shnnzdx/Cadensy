import assert from "node:assert/strict";
import test from "node:test";
import { restoreTripAppBootstrapState } from "../../trip/src/final/technicalSessionBootstrap.js";

function createStubRuntime({ facts, restorationHint, authorization }) {
  return {
    restoreTechnicalSession() {
      return {
        facts,
        restorationHint,
        warnings: [],
      };
    },
    requestIdentityFor(scope, inputFacts) {
      assert.fail(`requestIdentityFor should not be called during phase 4 bootstrap: ${scope} ${JSON.stringify(inputFacts)}`);
    },
  };
}

test("phase 4 bootstrap preserves empty-session defaults", () => {
  const bootstrap = restoreTripAppBootstrapState({
    sessionRuntime: createStubRuntime({
      facts: { kind: "none" },
      restorationHint: null,
      authorization: null,
    }),
  });

  assert.deepEqual(bootstrap, {
    hasAccountSession: false,
    membershipId: "",
    restoredTripId: "",
    activeTripId: "",
  });
});

test("phase 4 bootstrap preserves token-only account session semantics without exposing bearer credentials", () => {
  const bootstrap = restoreTripAppBootstrapState({
    sessionRuntime: createStubRuntime({
      facts: {
        kind: "account",
        accountAuth: true,
        activeTripId: null,
        membershipId: null,
      },
      restorationHint: null,
      authorization: null,
    }),
  });

  assert.deepEqual(bootstrap, {
    hasAccountSession: true,
    membershipId: "",
    restoredTripId: "",
    activeTripId: "",
  });
});

test("phase 4 bootstrap restores a bounded Guest session without exposing its token", () => {
  const bootstrap = restoreTripAppBootstrapState({
    sessionRuntime: createStubRuntime({
      facts: {
        kind: "guest",
        guestAuth: true,
        activeTripId: "trip-guest",
        membershipId: "member-guest",
      },
      restorationHint: { tripId: "trip-guest" },
      authorization: null,
    }),
  });

  assert.deepEqual(bootstrap, {
    hasAccountSession: false,
    membershipId: "member-guest",
    restoredTripId: "trip-guest",
    activeTripId: "trip-guest",
  });
});

test("phase 4 bootstrap preserves account trip restoration", () => {
  const bootstrap = restoreTripAppBootstrapState({
    sessionRuntime: createStubRuntime({
      facts: {
        kind: "account",
        accountAuth: true,
        activeTripId: "trip-account",
        membershipId: "member-account",
      },
      restorationHint: { tripId: "trip-account" },
      authorization: null,
    }),
  });

  assert.deepEqual(bootstrap, {
    hasAccountSession: true,
    membershipId: "member-account",
    restoredTripId: "trip-account",
    activeTripId: "trip-account",
  });
});

test("phase 4 bootstrap never restores legacy environment Membership IDs", () => {
  const bootstrap = restoreTripAppBootstrapState({
    sessionRuntime: createStubRuntime({
      facts: { kind: "none" },
      restorationHint: null,
      authorization: null,
    }),
  });

  assert.deepEqual(bootstrap, {
    hasAccountSession: false,
    membershipId: "",
    restoredTripId: "",
    activeTripId: "",
  });
});

test("phase 4 bootstrap keeps restorationHint tripId distinct from active technical trip context", () => {
  const bootstrap = restoreTripAppBootstrapState({
    sessionRuntime: createStubRuntime({
      facts: {
        kind: "account",
        accountAuth: true,
        activeTripId: "trip-active",
        membershipId: "member-1",
      },
      restorationHint: { tripId: "trip-restored" },
      authorization: null,
    }),
  });

  assert.equal(bootstrap.hasAccountSession, true);
  assert.equal(bootstrap.activeTripId, "trip-active");
  assert.equal(bootstrap.restoredTripId, "trip-restored");
});

test("phase 4 bootstrap never reconstructs raw bearer tokens through request identity", async () => {
  const source = await import("node:fs/promises").then(({ readFile }) =>
    readFile(new URL("../../trip/src/final/technicalSessionBootstrap.js", import.meta.url), "utf8"),
  );

  assert.doesNotMatch(source, /requestIdentityFor\('account'/);
  assert.doesNotMatch(source, /Authorization/);
  assert.doesNotMatch(source, /Bearer /);
});
