/**
 * Organizer-only copy of a raw invite link.
 *
 * The server stores the token hash. This cache is the only place the raw link
 * can be shown again. It is not a tripsync session key and it is not an
 * analytics identifier.
 */

const STORAGE_KEY = "cadensy.inviteLink.v1"

const memory = () => {
  if (typeof window === "undefined" || !window.localStorage) return null
  return window.localStorage
}

const readAll = storage => {
  try {
    const parsed = JSON.parse(storage.getItem(STORAGE_KEY) || "{}")
    return parsed && typeof parsed === "object" ? parsed : {}
  } catch {
    return {}
  }
}

const writeAll = (storage, value) => {
  storage.setItem(STORAGE_KEY, JSON.stringify(value))
}

export function rememberInviteLink(tripId, inviteId, token) {
  const storage = memory()
  if (!storage || !tripId || !inviteId || !token) return
  const all = readAll(storage)
  const trip = all[tripId] && typeof all[tripId] === "object" ? all[tripId] : {}
  trip[inviteId] = token
  all[tripId] = trip
  writeAll(storage, all)
}

export function readInviteLink(tripId, inviteId) {
  const storage = memory()
  if (!storage || !tripId || !inviteId) return ""
  const token = readAll(storage)[tripId]?.[inviteId]
  return typeof token === "string" ? token : ""
}

export function clearInviteLink(tripId, inviteId) {
  const storage = memory()
  if (!storage || !tripId || !inviteId) return
  const all = readAll(storage)
  if (!all[tripId]) return
  delete all[tripId][inviteId]
  writeAll(storage, all)
}

export function clearInviteLinksForTrip(tripId) {
  const storage = memory()
  if (!storage || !tripId) return
  const all = readAll(storage)
  delete all[tripId]
  writeAll(storage, all)
}

export function clearAllInviteLinks() {
  const storage = memory()
  if (!storage) return
  storage.removeItem(STORAGE_KEY)
}

export const INVITE_LINK_STORAGE_KEY = STORAGE_KEY
