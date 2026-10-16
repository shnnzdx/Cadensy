import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  accountAuthenticationHref,
  inviteJoinReturnPath,
  isSafeInternalReturnPath,
} from "../../shared/trip-navigation-policy/invite-auth-return.js";
import {
  describeInviteJoinError,
  guestJoinPayload,
  validateInviteJoinResult,
} from "../../trip/src/final/invite-join-flow.js";

test("PR-06B.1A.3 keeps the Account invite return inside the Trip workspace", () => {
  assert.equal(inviteJoinReturnPath("a/b token"), "/trip-app/index.html#/join/a%2Fb%20token");
  assert.equal(
    accountAuthenticationHref("login", "invite-1"),
    "/login?next=%2Ftrip-app%2Findex.html%23%2Fjoin%2Finvite-1",
  );
  assert.equal(isSafeInternalReturnPath("/trip-app/index.html#/join/invite-1"), true);
  assert.equal(isSafeInternalReturnPath("//outside.example/invite"), false);
  assert.equal(isSafeInternalReturnPath("https://outside.example/invite"), false);
  assert.equal(isSafeInternalReturnPath("/\\outside.example"), false);
});

test("PR-06B.1A.3 public Guest join has a display-name-only payload", () => {
  assert.deepEqual(guestJoinPayload("  Guest Name  "), { display_name: "Guest Name" });
  assert.throws(() => guestJoinPayload("  "), /display name/i);
});

test("PR-06B.1A.3 validates Account and Guest join response separation", () => {
  const account = { membership_id: "m-account", trip_id: "t-1", role: "participant" };
  const guest = { membership_id: "m-guest", trip_id: "t-1", guest_token: "gst_1" };
  assert.equal(validateInviteJoinResult(account, "account"), account);
  assert.equal(validateInviteJoinResult(guest, "guest"), guest);
  assert.throws(() => validateInviteJoinResult({ ...account, guest_token: "gst_1" }, "account"), /Guest credential/i);
  assert.throws(() => validateInviteJoinResult(account, "guest"), /Guest credential/i);
});

test("PR-06B.1A.3 maps deterministic lifecycle denials without a retry fallback", () => {
  assert.deepEqual(describeInviteJoinError({ status: 409, message: "membership_change_pending_decision" }), {
    kind: "decision-window",
    message: "Joining is temporarily unavailable while a group decision is in progress.",
  });
  assert.equal(describeInviteJoinError({ status: 409, message: "Membership is quarantined" }).kind, "quarantined");
  assert.equal(describeInviteJoinError({ status: 401, message: "Account authentication required" }).kind, "authentication");
  assert.equal(describeInviteJoinError({ status: 404, message: "Invite not found" }).kind, "invite");
});

test("PR-06B.1A.3 wires the public and Account endpoints through separate UI actions", async () => {
  const [finalApp, state, login, signup] = await Promise.all([
    readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8"),
    readFile(new URL("../../trip/src/final/TripAppState.jsx", import.meta.url), "utf8"),
    readFile(new URL("../app/login/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../app/signup/page.tsx", import.meta.url), "utf8"),
  ]);

  assert.match(finalApp, /const joined = await app\.joinInvite\(token, name\)/);
  assert.match(finalApp, /const joined = await app\.joinAccountInvite\(token\)/);
  assert.doesNotMatch(finalApp, /email:\s*email\.trim\(\)/);
  assert.match(finalApp, /use a separate browser session so your Account session is not replaced/);
  assert.match(state, /body: JSON\.stringify\(guestJoinPayload\(displayName\)\)/);
  assert.match(state, /accountRequestJson\(`\/api\/invites\/\$\{token\}\/account-join`, \{/);
  assert.match(state, /clearMembershipScopedState\(\)/);
  assert.match(login, /isSafeInternalReturnPath\(requestedNext\)/);
  assert.match(signup, /window\.location\.href = nextPathRef\.current;/);
});
