import { buildTripPreviewFrameSrc } from "../tripsync-preview-contract.js";

const INTERNAL_RETURN_BASE = "https://cadensy.invalid";
const INTERNAL_RETURN_ORIGIN = new URL(INTERNAL_RETURN_BASE).origin;

/**
 * Returns a normalized, same-origin internal path, or null when value is unsafe.
 *
 * Return paths must be slash-relative and contain no ASCII controls, DEL,
 * whitespace, or backslashes. They are parsed against a fixed internal base
 * and accepted only when the parsed origin remains that base's origin. The
 * normalized pathname, search, and hash are then revalidated for a leading
 * double slash, backslashes, and encoded slashes in the pathname, and parsed
 * against the same base again to confirm the origin remains internal.
 */
export function isSafeInternalReturnPath(value) {
  if (typeof value !== "string" || /[\x00-\x20\x7F\\]|\s/u.test(value)) {
    return null;
  }
  if (!value.startsWith("/") || value.startsWith("//")) {
    return null;
  }

  try {
    const parsed = new URL(value, INTERNAL_RETURN_BASE);
    if (parsed.origin !== INTERNAL_RETURN_ORIGIN) {
      return null;
    }

    const candidate = `${parsed.pathname}${parsed.search}${parsed.hash}`;
    if (
      !candidate.startsWith("/") ||
      candidate.startsWith("//") ||
      candidate.includes("\\") ||
      parsed.pathname.includes("//") ||
      /%2f/i.test(parsed.pathname)
    ) {
      return null;
    }

    if (new URL(candidate, INTERNAL_RETURN_BASE).origin !== INTERNAL_RETURN_ORIGIN) {
      return null;
    }
    return candidate;
  } catch {
    return null;
  }
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
