import assert from "node:assert/strict";
import test from "node:test";

import {
  requestInviteWithTokenFallback,
  resetInviteTokenTransport,
  shouldFallbackInviteTokenTransport,
} from "../../trip/src/final/inviteTokenTransport.js";

test("invite token transport falls back once on 405 and unknown 404", async () => {
  resetInviteTokenTransport();
  const calls = [];
  const missing = Object.assign(new Error("Not Found"), { status: 404 });
  const result = await requestInviteWithTokenFallback({
    token: "secret-token",
    pathRequest: () => {
      calls.push("path");
      throw missing;
    },
    bodyRequest: (token) => {
      calls.push(`body:${token}`);
      return { ok: true };
    },
  });
  assert.deepEqual(result, { ok: true });
  assert.deepEqual(calls, ["path", "body:secret-token"]);

  await requestInviteWithTokenFallback({
    token: "secret-token",
    pathRequest: () => {
      calls.push("path-again");
      return { skipped: true };
    },
    bodyRequest: () => {
      calls.push("body-again");
      return { sticky: true };
    },
  });
  assert.deepEqual(calls, ["path", "body:secret-token", "body-again"]);
  resetInviteTokenTransport();
});

test("invite token transport does not fall back on known invite results", async () => {
  resetInviteTokenTransport();
  for (const [status, message] of [
    [404, "invite_invalid"],
    [404, "Invite not found"],
    [410, "invite_expired"],
    [410, "invite_revoked"],
    [410, "invite_closed"],
    [401, "Login required"],
    [403, "invite_join_forbidden"],
    [409, "membership_change_pending_decision"],
    [422, "token is required"],
  ]) {
    const error = Object.assign(new Error(message), { status });
    assert.equal(shouldFallbackInviteTokenTransport(error), false);
    await assert.rejects(
      () => requestInviteWithTokenFallback({
        token: "secret-token",
        pathRequest: () => { throw error; },
        bodyRequest: () => ({ used: true }),
      }),
      (caught) => caught === error,
    );
  }
  const method = Object.assign(new Error("Method Not Allowed"), { status: 405 });
  assert.equal(shouldFallbackInviteTokenTransport(method), true);
  resetInviteTokenTransport();
});
