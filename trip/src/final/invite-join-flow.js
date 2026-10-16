export function guestJoinPayload(displayName) {
  const normalized = String(displayName || "").trim();
  if (!normalized) {
    throw new TypeError("A Guest display name is required.");
  }
  // Deliberately whitelist the public contract: email cannot be forwarded by
  // a stale caller as an Account identity claim.
  return { display_name: normalized };
}

export function validateInviteJoinResult(result, kind) {
  if (!result || typeof result !== "object" ||
      typeof result.membership_id !== "string" || !result.membership_id ||
      typeof result.trip_id !== "string" || !result.trip_id) {
    throw new TypeError("The join response did not contain a valid Trip Membership context.");
  }
  if (kind === "guest" && (typeof result.guest_token !== "string" || !result.guest_token)) {
    throw new TypeError("The Guest join response did not contain a Guest credential.");
  }
  if (kind === "account" && Object.hasOwn(result, "guest_token")) {
    throw new TypeError("An Account join response must not contain a Guest credential.");
  }
  return result;
}

export function describeInviteJoinError(error) {
  const detail = error?.message || "";
  if (error?.status === 404) return { kind: "invite", message: "This invite link is no longer active." };
  if (error?.status === 401) return { kind: "authentication", message: "Your sign-in session has expired. Sign in again to continue." };
  if (detail === "account_join_requires_authenticated_session") {
    return { kind: "authentication", message: "Sign in with an Account to join this trip as an Account member." };
  }
  if (detail === "membership_change_pending_decision") {
    return { kind: "decision-window", message: "Joining is temporarily unavailable while a group decision is in progress." };
  }
  if (detail === "Membership is quarantined") {
    return { kind: "quarantined", message: "This Membership cannot access this trip. Contact the organizer if you need help." };
  }
  if (detail === "Legacy identity is not an authenticated Account") {
    return { kind: "legacy-account", message: "This legacy identity is not an authenticated Account. Sign in with an active Account." };
  }
  return { kind: "other", message: detail || "Could not join this trip." };
}
