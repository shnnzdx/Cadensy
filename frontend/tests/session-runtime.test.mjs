import assert from "node:assert/strict";
import test from "node:test";
import {
  createSessionRuntime,
  SESSION_RUNTIME_CODES,
} from "../../shared/session-runtime/index.js";

function createMemoryStorage(seed = {}) {
  const store = new Map(Object.entries(seed));
  return {
    getItem(key) {
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      store.set(key, String(value));
    },
    removeItem(key) {
      store.delete(key);
    },
    dump() {
      return Object.fromEntries(store.entries());
    },
  };
}

function createThrowingStorage({ read = false, write = false, clear = false } = {}) {
  const store = new Map();
  return {
    getItem(key) {
      if (read) throw new Error("read failed");
      return store.has(key) ? store.get(key) : null;
    },
    setItem(key, value) {
      if (write) throw new Error("write failed");
      store.set(key, String(value));
    },
    removeItem(key) {
      if (clear) throw new Error("clear failed");
      store.delete(key);
    },
  };
}

test("session-runtime is SSR/import-safe without browser globals", async () => {
  const originalWindow = globalThis.window;
  try {
    delete globalThis.window;
    const module = await import("../../shared/session-runtime/index.js");
    const runtime = module.createSessionRuntime();
    const restored = runtime.restoreTechnicalSession();

    assert.equal(typeof module.createSessionRuntime, "function");
    assert.deepEqual(restored.facts, { kind: "none" });
    assert.equal(restored.restorationHint, null);
    assert.deepEqual(restored.warnings, [
      SESSION_RUNTIME_CODES.warnings.PERSISTENCE_UNAVAILABLE,
    ]);
  } finally {
    if (originalWindow === undefined) {
      delete globalThis.window;
    } else {
      globalThis.window = originalWindow;
    }
  }
});

test("restoreTechnicalSession returns none for empty storage", () => {
  const runtime = createSessionRuntime({ storage: createMemoryStorage() });
  const restored = runtime.restoreTechnicalSession();

  assert.deepEqual(restored, {
    facts: { kind: "none" },
    restorationHint: null,
    warnings: [],
  });
});

test("restoreTechnicalSession returns account session for token-only persisted state", () => {
  const runtime = createSessionRuntime({
    storage: createMemoryStorage({
      "tripsync:authToken": "token-1",
    }),
  });

  const restored = runtime.restoreTechnicalSession();

  assert.deepEqual(restored, {
    facts: {
      kind: "account",
      accountAuth: true,
      activeTripId: null,
      membershipId: null,
    },
    restorationHint: null,
    warnings: [],
  });
});

test("restoreTechnicalSession returns Guest session only when its bearer is persisted", () => {
  const runtime = createSessionRuntime({
    storage: createMemoryStorage({
      "tripsync:guestToken": "gst-token-guest",
      "tripsync:membershipId": "m-guest",
      "tripsync:tripId": "t-guest",
    }),
  });

  const restored = runtime.restoreTechnicalSession();

  assert.deepEqual(restored, {
    facts: {
      kind: "guest",
      guestAuth: true,
      membershipId: "m-guest",
      activeTripId: "t-guest",
    },
    restorationHint: { tripId: "t-guest" },
    warnings: [],
  });
});

test("restoreTechnicalSession returns account session with active trip and compatibility membership when token and trip context exist", () => {
  const runtime = createSessionRuntime({
    storage: createMemoryStorage({
      "tripsync:authToken": "token-1",
      "tripsync:membershipId": "m-1",
      "tripsync:tripId": "t-1",
    }),
  });

  const restored = runtime.restoreTechnicalSession();

  assert.deepEqual(restored, {
    facts: {
      kind: "account",
      accountAuth: true,
      activeTripId: "t-1",
      membershipId: "m-1",
    },
    restorationHint: { tripId: "t-1" },
    warnings: [],
  });
});

test("restoreTechnicalSession drops malformed persisted values while preserving valid ones and reporting warnings", () => {
  const runtime = createSessionRuntime({
    storage: createMemoryStorage({
      "tripsync:authToken": "",
      "tripsync:membershipId": "m-1",
      "tripsync:tripId": "",
    }),
  });

  const restored = runtime.restoreTechnicalSession();

  assert.deepEqual(restored.facts, { kind: "none" });
  assert.equal(restored.restorationHint, null);
  assert.deepEqual(restored.warnings, [
    SESSION_RUNTIME_CODES.warnings.PERSISTENCE_MALFORMED_DATA,
  ]);
});

test("restoreTechnicalSession reports unavailable or failing storage without throwing", () => {
  const unavailableRuntime = createSessionRuntime();
  const unavailable = unavailableRuntime.restoreTechnicalSession();
  assert.deepEqual(unavailable.facts, { kind: "none" });
  assert.deepEqual(unavailable.warnings, [
    SESSION_RUNTIME_CODES.warnings.PERSISTENCE_UNAVAILABLE,
  ]);

  const failingRuntime = createSessionRuntime({ storage: createThrowingStorage({ read: true }) });
  const failing = failingRuntime.restoreTechnicalSession();
  assert.deepEqual(failing.facts, { kind: "none" });
  assert.deepEqual(failing.warnings, [
    SESSION_RUNTIME_CODES.warnings.PERSISTENCE_READ_FAILED,
  ]);
});

test("adoptAccountAuth establishes in-memory account session and persists restorable material", () => {
  const storage = createMemoryStorage();
  const runtime = createSessionRuntime({ storage });

  const adopted = runtime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-1",
    membershipId: "m-1",
  });

  assert.deepEqual(adopted, {
    facts: {
      kind: "account",
      accountAuth: true,
      activeTripId: "t-1",
      membershipId: "m-1",
    },
    warnings: [],
  });

  const restored = createSessionRuntime({ storage }).restoreTechnicalSession();
  assert.deepEqual(restored.facts, adopted.facts);
  assert.deepEqual(restored.restorationHint, { tripId: "t-1" });
});

test("adoptAccountAuth supports token with no memberships or active trip", () => {
  const runtime = createSessionRuntime({ storage: createMemoryStorage() });
  const adopted = runtime.adoptAccountAuth({ token: "token-no-trips" });

  assert.deepEqual(adopted, {
    facts: {
      kind: "account",
      accountAuth: true,
      activeTripId: null,
      membershipId: null,
    },
    warnings: [],
  });
});

test("adoptAccountAuth keeps successful in-memory adoption when persistence writes fail", () => {
  const runtime = createSessionRuntime({
    storage: createThrowingStorage({ write: true, clear: true }),
  });

  const adopted = runtime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-1",
    membershipId: "m-1",
  });

  assert.deepEqual(adopted.facts, {
    kind: "account",
    accountAuth: true,
    activeTripId: "t-1",
    membershipId: "m-1",
  });
  assert.deepEqual(adopted.warnings, [
    SESSION_RUNTIME_CODES.warnings.PERSISTENCE_WRITE_FAILED,
    SESSION_RUNTIME_CODES.warnings.PERSISTENCE_CLEAR_FAILED,
  ]);
});

test("Guest adoption, account trip switching, and invite cache writes remain separate", () => {
  const guestStorage = createMemoryStorage();
  const guestRuntime = createSessionRuntime({ storage: guestStorage });
  const guestAdoption = guestRuntime.adoptGuestAuth({
    token: "gst-token-guest",
    activeTripId: "t-guest",
    membershipId: "m-guest",
    inviteToken: "invite-guest",
  });

  assert.deepEqual(guestAdoption, {
    facts: {
      kind: "guest",
      guestAuth: true,
      activeTripId: "t-guest",
      membershipId: "m-guest",
    },
    warnings: [],
  });
  assert.deepEqual(guestRuntime.readInviteAdoption("invite-guest"), {
    record: {
      activeTripId: "t-guest",
      membershipId: "m-guest",
    },
    warnings: [],
  });

  const accountStorage = createMemoryStorage();
  const accountRuntime = createSessionRuntime({ storage: accountStorage });
  accountRuntime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-old",
    membershipId: "m-old",
  });
  const switched = accountRuntime.adoptTechnicalTripContext({
    activeTripId: "t-new",
    membershipId: "m-new",
  });

  assert.deepEqual(switched, {
    facts: {
      kind: "account",
      accountAuth: true,
      activeTripId: "t-new",
      membershipId: "m-new",
    },
    warnings: [],
  });
});

test("adoptGuestAuth replaces an existing account token without retaining it", () => {
  const storage = createMemoryStorage();
  const runtime = createSessionRuntime({ storage });
  runtime.adoptAccountAuth({
    token: "organizer-token",
    activeTripId: "t-organizer",
    membershipId: "m-organizer",
  });

  const adopted = runtime.adoptGuestAuth({
    token: "gst-token-invite",
    activeTripId: "t-invite",
    membershipId: "m-invite",
    inviteToken: "invite-guest",
  });

  assert.deepEqual(adopted, {
    facts: {
      kind: "guest",
      guestAuth: true,
      activeTripId: "t-invite",
      membershipId: "m-invite",
    },
    warnings: [],
  });
  assert.equal(storage.dump()["tripsync:authToken"], undefined);
  assert.deepEqual(runtime.restoreTechnicalSession().facts, {
    kind: "guest",
    guestAuth: true,
    activeTripId: "t-invite",
    membershipId: "m-invite",
  });
});

test("readInviteAdoption returns null for missing or malformed cache records", () => {
  const storage = createMemoryStorage({
    "tripsync:invite:good": JSON.stringify({ tripId: "t-1", membershipId: "m-1" }),
    "tripsync:invite:bad-json": "{nope",
    "tripsync:invite:bad-shape": JSON.stringify({ tripId: "t-1" }),
  });
  const runtime = createSessionRuntime({ storage });

  assert.deepEqual(runtime.readInviteAdoption("missing"), {
    record: null,
    warnings: [],
  });
  assert.deepEqual(runtime.readInviteAdoption("good"), {
    record: {
      activeTripId: "t-1",
      membershipId: "m-1",
    },
    warnings: [],
  });
  assert.deepEqual(runtime.readInviteAdoption("bad-json"), {
    record: null,
    warnings: [SESSION_RUNTIME_CODES.warnings.PERSISTENCE_MALFORMED_DATA],
  });
  assert.deepEqual(runtime.readInviteAdoption("bad-shape"), {
    record: null,
    warnings: [SESSION_RUNTIME_CODES.warnings.PERSISTENCE_MALFORMED_DATA],
  });
});

test("requestIdentityFor derives only verified account or Guest bearer headers", () => {
  const runtime = createSessionRuntime({ storage: createMemoryStorage() });

  const missingAccount = runtime.requestIdentityFor("account", { kind: "none" });
  assert.deepEqual(missingAccount, {
    ok: false,
    code: SESSION_RUNTIME_CODES.missingContext.MISSING_ACCOUNT_AUTH,
  });

  const accountFacts = runtime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-1",
    membershipId: "m-1",
  }).facts;
  assert.deepEqual(runtime.requestIdentityFor("account", accountFacts), {
    ok: true,
    headers: {
      Authorization: "Bearer token-1",
    },
  });
  assert.deepEqual(runtime.requestIdentityFor("trip", accountFacts), {
    ok: true,
    headers: {
      Authorization: "Bearer token-1",
      "X-Trip-Id": "t-1",
    },
  });

  const guestFacts = createSessionRuntime({ storage: createMemoryStorage() })
    .adoptGuestAuth({
      token: "gst-token-guest",
      activeTripId: "t-guest",
      membershipId: "m-guest",
    }).facts;
  const guestRuntime = createSessionRuntime({ storage: createMemoryStorage() });
  guestRuntime.adoptGuestAuth({
    token: "gst-token-guest",
    activeTripId: "t-guest",
    membershipId: "m-guest",
  });
  assert.deepEqual(guestRuntime.requestIdentityFor("trip", guestFacts), {
    ok: true,
    headers: {
      Authorization: "Bearer gst-token-guest",
      "X-Trip-Id": "t-guest",
    },
  });
  const tokenOnlyFacts = createSessionRuntime({ storage: createMemoryStorage() })
    .adoptAccountAuth({ token: "token-only" }).facts;
  const tokenOnlyRuntime = createSessionRuntime({ storage: createMemoryStorage() });
  tokenOnlyRuntime.adoptAccountAuth({ token: "token-only" });
  assert.deepEqual(tokenOnlyRuntime.requestIdentityFor("trip", tokenOnlyFacts), {
    ok: false,
    code: SESSION_RUNTIME_CODES.missingContext.MISSING_ACTIVE_TRIP_CONTEXT,
  });
});

test("invalidateTechnicalSession distinguishes account invalidation from membership invalidation", () => {
  const accountStorage = createMemoryStorage();
  const accountRuntime = createSessionRuntime({ storage: accountStorage });
  const accountFacts = accountRuntime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-1",
    membershipId: "m-1",
  }).facts;

  const membershipInvalidated = accountRuntime.invalidateTechnicalSession(
    accountFacts,
    SESSION_RUNTIME_CODES.invalidation.MEMBERSHIP_CREDENTIALS_INVALID,
  );
  assert.deepEqual(membershipInvalidated, {
    facts: {
      kind: "account",
      accountAuth: true,
      activeTripId: null,
      membershipId: null,
    },
    warnings: [],
  });
  assert.deepEqual(accountRuntime.requestIdentityFor("account", membershipInvalidated.facts), {
    ok: true,
    headers: {
      Authorization: "Bearer token-1",
    },
  });

  const accountInvalidated = accountRuntime.invalidateTechnicalSession(
    membershipInvalidated.facts,
    SESSION_RUNTIME_CODES.invalidation.ACCOUNT_CREDENTIALS_INVALID,
  );
  assert.deepEqual(accountInvalidated, {
    facts: { kind: "none" },
    warnings: [],
  });
  assert.deepEqual(accountRuntime.requestIdentityFor("account", accountInvalidated.facts), {
    ok: false,
    code: SESSION_RUNTIME_CODES.missingContext.MISSING_ACCOUNT_AUTH,
  });

  const guestRuntime = createSessionRuntime({ storage: createMemoryStorage() });
  const guestFacts = guestRuntime.adoptGuestAuth({
    token: "gst-token-guest",
    activeTripId: "t-guest",
    membershipId: "m-guest",
  }).facts;
  const guestInvalidated = guestRuntime.invalidateTechnicalSession(
    guestFacts,
    SESSION_RUNTIME_CODES.invalidation.MEMBERSHIP_CREDENTIALS_INVALID,
  );
  assert.deepEqual(guestInvalidated, {
    facts: { kind: "none" },
    warnings: [],
  });
});

test("logoutTechnicalSession attempts revoke for active account or Guest sessions, preserves invite caches, and does not let revoke failure block clear", async () => {
  const storage = createMemoryStorage();
  const runtime = createSessionRuntime({ storage });
  runtime.adoptAccountAuth({
    token: "token-1",
    activeTripId: "t-1",
    membershipId: "m-1",
  });
  runtime.adoptTechnicalTripContext({
    activeTripId: "t-1",
    membershipId: "m-1",
    inviteToken: "invite-1",
  });

  let revokeCalls = 0;
  const logoutSuccess = await runtime.logoutTechnicalSession({
    kind: "account",
    accountAuth: true,
    activeTripId: "t-1",
    membershipId: "m-1",
  }, {
    revoke: async () => {
      revokeCalls += 1;
    },
  });

  assert.equal(revokeCalls, 1);
  assert.deepEqual(logoutSuccess, {
    facts: { kind: "none" },
    revokeAttempted: true,
    revokeFailed: false,
    warnings: [],
  });
  assert.deepEqual(runtime.restoreTechnicalSession(), {
    facts: { kind: "none" },
    restorationHint: null,
    warnings: [],
  });
  assert.deepEqual(runtime.readInviteAdoption("invite-1"), {
    record: {
      activeTripId: "t-1",
      membershipId: "m-1",
    },
    warnings: [],
  });

  const runtimeWithFailure = createSessionRuntime({ storage: createMemoryStorage() });
  runtimeWithFailure.adoptAccountAuth({
    token: "token-2",
    activeTripId: "t-2",
    membershipId: "m-2",
  });
  const logoutFailure = await runtimeWithFailure.logoutTechnicalSession({
    kind: "account",
    accountAuth: true,
    activeTripId: "t-2",
    membershipId: "m-2",
  }, {
    revoke: async () => {
      throw new Error("revoke failed");
    },
  });

  assert.deepEqual(logoutFailure, {
    facts: { kind: "none" },
    revokeAttempted: true,
    revokeFailed: true,
    warnings: [],
  });

  const guestRuntime = createSessionRuntime({ storage: createMemoryStorage() });
  guestRuntime.adoptGuestAuth({
    token: "gst-token-guest",
    activeTripId: "t-g",
    membershipId: "m-g",
  });
  const guestLogout = await guestRuntime.logoutTechnicalSession({
    kind: "guest",
    activeTripId: "t-g",
    membershipId: "m-g",
  }, {
    revoke: async () => {
      throw new Error("should not be called");
    },
  });
  assert.deepEqual(guestLogout, {
    facts: { kind: "none" },
    revokeAttempted: true,
    revokeFailed: true,
    warnings: [],
  });
});

test("public contract violations remain hard errors while expected missing context remains a semantic result", () => {
  const runtime = createSessionRuntime({ storage: createMemoryStorage() });

  assert.throws(
    () => runtime.adoptAccountAuth({ token: "" }),
    (error) => error?.code === SESSION_RUNTIME_CODES.errors.INVALID_ADOPTION_INPUT,
  );
  assert.throws(
    () => runtime.adoptTechnicalTripContext({ activeTripId: "t-1", membershipId: "" }),
    (error) => error?.code === SESSION_RUNTIME_CODES.errors.INVALID_ADOPTION_INPUT,
  );
  assert.throws(
    () => runtime.requestIdentityFor("public", { kind: "none" }),
    (error) => error?.code === SESSION_RUNTIME_CODES.errors.INVALID_REQUEST_SCOPE,
  );
  assert.throws(
    () => runtime.readInviteAdoption(""),
    (error) => error?.code === SESSION_RUNTIME_CODES.errors.INVALID_INVITE_TOKEN,
  );
});
