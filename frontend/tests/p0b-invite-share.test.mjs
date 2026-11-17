import assert from "node:assert/strict";
import test from "node:test";

import {
  INVITE_LINK_STORAGE_KEY,
  clearAllInviteLinks,
  clearInviteLink,
  readInviteLink,
  rememberInviteLink,
} from "../../trip/src/final/inviteLinkCache.js";
import {
  clearInviteJoinResume,
  inviteJoinResumeMatches,
  markInviteJoinResume,
} from "../../trip/src/final/inviteJoinResume.js";
import { wechatShareTemplate, writeClipboard } from "../../trip/src/final/inviteShare.js";
import { describeInviteJoinError, validateInviteJoinResult } from "../../trip/src/final/invite-join-flow.js";

const memoryStore = new Map();
const memoryStorage = {
  getItem: key => (memoryStore.has(key) ? memoryStore.get(key) : null),
  setItem: (key, value) => memoryStore.set(key, String(value)),
  removeItem: key => memoryStore.delete(key),
};
globalThis.window = { localStorage: memoryStorage, sessionStorage: memoryStorage };

test("invite link cache is per trip and invite and is not a session key", () => {
  clearAllInviteLinks();
  rememberInviteLink("trip-a", "invite-a", "token-a");
  rememberInviteLink("trip-b", "invite-b", "token-b");

  assert.equal(readInviteLink("trip-a", "invite-a"), "token-a");
  assert.equal(readInviteLink("trip-a", "invite-b"), "");
  assert.equal(readInviteLink("trip-b", "invite-b"), "token-b");
  assert.equal(INVITE_LINK_STORAGE_KEY.startsWith("tripsync:"), false);

  clearInviteLink("trip-a", "invite-a");
  assert.equal(readInviteLink("trip-a", "invite-a"), "");
  assert.equal(readInviteLink("trip-b", "invite-b"), "token-b");
  clearAllInviteLinks();
  assert.equal(readInviteLink("trip-b", "invite-b"), "");
});

test("clipboard success is reported only after writeText resolves", async () => {
  const writes = [];
  assert.deepEqual(
    await writeClipboard("https://cadensy.example/#/join/token", {
      writeText: async value => {
        writes.push(value);
      },
    }),
    { ok: true },
  );
  assert.deepEqual(writes, ["https://cadensy.example/#/join/token"]);
  assert.deepEqual(
    await writeClipboard("https://cadensy.example/#/join/token", {
      writeText: async () => {
        throw new Error("denied");
      },
    }),
    { ok: false, reason: "failed" },
  );
  assert.deepEqual(await writeClipboard("https://cadensy.example/#/join/token", {}), {
    ok: false,
    reason: "unavailable",
  });
});

test("WeChat template contains only the trip name and link", () => {
  const template = wechatShareTemplate({
    tripName: "成都周末",
    url: "https://cadensy.example/#/join/abc",
  });
  assert.match(template, /成都周末/);
  assert.match(template, /https:\/\/cadensy\.example\/#\/join\/abc/);
  assert.equal(template.includes("budget"), false);
  assert.equal(template.includes("constraint"), false);
  assert.throws(() => wechatShareTemplate({ tripName: "成都周末", url: "" }));
});

test("invite join resume is a one-shot return flag", () => {
  clearInviteJoinResume();
  markInviteJoinResume("invite-token");
  assert.equal(inviteJoinResumeMatches("invite-token"), true);
  assert.equal(inviteJoinResumeMatches("other-token"), false);
  clearInviteJoinResume();
  assert.equal(inviteJoinResumeMatches("invite-token"), false);
});

test("join messages distinguish expired, revoked, forbidden, and an open decision", () => {
  assert.equal(describeInviteJoinError({ status: 410, message: "invite_expired" }).message.includes("expired"), true);
  assert.equal(describeInviteJoinError({ status: 410, message: "invite_revoked" }).message.includes("revoked"), true);
  assert.equal(describeInviteJoinError({ status: 404, message: "invite_invalid" }).kind, "invite");
  assert.equal(describeInviteJoinError({ status: 403, message: "invite_join_forbidden" }).kind, "forbidden");
  assert.match(
    describeInviteJoinError({ status: 409, message: "membership_change_pending_decision" }).message,
    /vote or confirmation/,
  );
  const rejoined = validateInviteJoinResult(
    { membership_id: "m1", trip_id: "t1", already_member: true, role: "participant" },
    "guest",
  );
  assert.equal(rejoined.already_member, true);
  assert.throws(
    () => validateInviteJoinResult(
      { membership_id: "m1", trip_id: "t1", already_member: true, guest_token: "gst_new" },
      "guest",
    ),
    /must not mint/,
  );
});
