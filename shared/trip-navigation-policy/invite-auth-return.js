import { buildTripPreviewFrameSrc } from "../tripsync-preview-contract.js";

/**
 * The login application only accepts same-origin, path-relative return URLs.
 * This keeps an invite token inside Cadensy's own Trip workspace rather than
 * treating it as an identity claim or forwarding it to another origin.
 */
export function isSafeInternalReturnPath(value) {
  return typeof value === "string" &&
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.includes("\\");
}

export function inviteJoinReturnPath(token) {
  if (typeof token !== "string" || !token.trim()) {
    throw new TypeError("A non-empty invite token is required for an invite return path.");
  }
  return buildTripPreviewFrameSrc(`/join/${encodeURIComponent(token)}`);
}

export function accountAuthenticationHref(intent, token) {
  if (intent !== "login" && intent !== "signup") {
    throw new TypeError("Account authentication intent must be login or signup.");
  }
  return `/${intent}?next=${encodeURIComponent(inviteJoinReturnPath(token))}`;
}
