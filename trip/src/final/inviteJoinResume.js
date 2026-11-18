/**
 * One-shot flag so login or signup can finish the invite join without an
 * extra click. This is not a tracking identifier.
 */

const STORAGE_KEY = "cadensy.inviteJoinResume.v1"

const storage = () => {
  if (typeof window === "undefined" || !window.sessionStorage) return null
  return window.sessionStorage
}

export function markInviteJoinResume(token) {
  const current = storage()
  if (!current || !token) return
  current.setItem(STORAGE_KEY, token)
}

export function inviteJoinResumeMatches(token) {
  const current = storage()
  if (!current || !token) return false
  return current.getItem(STORAGE_KEY) === token
}

export function clearInviteJoinResume() {
  storage()?.removeItem(STORAGE_KEY)
}

export const INVITE_JOIN_RESUME_KEY = STORAGE_KEY
