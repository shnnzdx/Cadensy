import assert from "node:assert/strict";
import test from "node:test";

import { createSessionRuntime } from "../../shared/session-runtime/index.js";

function createMemoryStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.get(key) ?? null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
  };
}

test("PR-01B Guest adoption restores a bearer credential and revokes it on logout", async () => {
  const storage = createMemoryStorage();
  const joiningRuntime = createSessionRuntime({ storage });
  const adopted = joiningRuntime.adoptGuestAuth({
    token: "gst_synthetic_high_entropy_token",
    activeTripId: "synthetic-trip",
    membershipId: "synthetic-membership",
    inviteToken: "synthetic-invite",
  });

  assert.deepEqual(adopted.facts, {
    kind: "guest",
    guestAuth: true,
    activeTripId: "synthetic-trip",
    membershipId: "synthetic-membership",
  });

  const reopenedRuntime = createSessionRuntime({ storage });
  const restored = reopenedRuntime.restoreTechnicalSession();
  assert.deepEqual(reopenedRuntime.requestIdentityFor("trip", restored.facts), {
    ok: true,
    headers: {
      Authorization: "Bearer gst_synthetic_high_entropy_token",
      "X-Trip-Id": "synthetic-trip",
    },
  });

  let revokeCalls = 0;
  const loggedOut = await reopenedRuntime.logoutTechnicalSession(restored.facts, {
    revoke: async () => {
      revokeCalls += 1;
    },
  });

  assert.equal(revokeCalls, 1);
  assert.equal(loggedOut.revokeAttempted, true);
  assert.equal(loggedOut.revokeFailed, false);
  assert.deepEqual(loggedOut.facts, { kind: "none" });
  assert.deepEqual(reopenedRuntime.restoreTechnicalSession().facts, { kind: "none" });
});
