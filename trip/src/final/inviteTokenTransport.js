/**
 * Path invite routes stay for shipped clients. A body/header call is used
 * only after a transport failure that is not a real invite answer.
 *
 * Fallback is once per page load, sticky, and never follows a known invalid,
 * expired, revoked, or closed invite, nor 410, 401, 403, 409, or 422.
 */

const KNOWN_INVITE_DETAILS = new Set([
  "invite_invalid",
  "invite_revoked",
  "invite_expired",
  "invite_closed",
  "Invite not found",
])

let bodyTransportEnabled = false

export function resetInviteTokenTransport() {
  bodyTransportEnabled = false
}

export function inviteTokenTransportUsesBody() {
  return bodyTransportEnabled
}

export function shouldFallbackInviteTokenTransport(error) {
  if (bodyTransportEnabled || !error) return false
  if (error.status === 405) return true
  if (error.status === 404 && !KNOWN_INVITE_DETAILS.has(error.message)) return true
  return false
}

export async function requestInviteWithTokenFallback({ pathRequest, bodyRequest, token }) {
  if (bodyTransportEnabled) return bodyRequest(token)
  try {
    return await pathRequest(token)
  } catch (error) {
    if (!shouldFallbackInviteTokenTransport(error)) throw error
    bodyTransportEnabled = true
    return bodyRequest(token)
  }
}
