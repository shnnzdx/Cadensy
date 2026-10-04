import assert from 'node:assert/strict';
import test from 'node:test';

import { createSessionRuntime } from '../../shared/session-runtime/index.js';

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

test('PR-01A legacy header characterization is retired by the PR-01B Guest bearer contract', async () => {
  const storage = createMemoryStorage();
  const joiningRuntime = createSessionRuntime({ storage });
  const adopted = joiningRuntime.adoptGuestAuth({
    token: 'gst_synthetic_credential',
    activeTripId: 'synthetic-trip',
    membershipId: 'synthetic-membership',
  });

  assert.deepEqual(adopted.facts, {
    kind: 'guest',
    guestAuth: true,
    activeTripId: 'synthetic-trip',
    membershipId: 'synthetic-membership',
  });

  const reopenedRuntime = createSessionRuntime({ storage });
  const restored = reopenedRuntime.restoreTechnicalSession();
  const requestIdentity = reopenedRuntime.requestIdentityFor('trip', restored.facts);
  const loggedOut = await reopenedRuntime.logoutTechnicalSession(restored.facts, {
    revoke: async () => {},
  });

  assert.deepEqual(restored.facts, adopted.facts);
  assert.deepEqual(requestIdentity, {
    ok: true,
    headers: {
      'X-Trip-Id': 'synthetic-trip',
      Authorization: 'Bearer gst_synthetic_credential',
    },
  });
  assert.equal(loggedOut.revokeAttempted, true);
  assert.deepEqual(loggedOut.facts, { kind: 'none' });
});
