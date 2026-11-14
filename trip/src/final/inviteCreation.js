/**
 * Invite creation for the page the user is actually on.
 *
 * The route id is the only target. A fresh account trip list must show an
 * organizer membership for that id before any invite POST. Guest sessions
 * cannot produce that evidence. Headers come from the verified session facts,
 * never from the raw route id.
 */

export const INVITE_UNAVAILABLE_MESSAGE = 'Could not create an invite link'

const unavailable = () => {
  const error = new Error(INVITE_UNAVAILABLE_MESSAGE)
  error.code = 'invite-unavailable'
  return error
}

export const verifiedOrganizerMembership = (rows, routeTripId) => {
  if (!Array.isArray(rows)) return null
  const row = rows.find(candidate => candidate && candidate.id === routeTripId)
  if (!row) return null
  if (typeof row.membership_id !== 'string' || row.membership_id.length === 0) return null
  if (row.my_role !== 'organizer') return null
  return row
}

export function createInviteAttemptGate() {
  let generation = 0

  return {
    invalidate() {
      generation += 1
    },
    async createVerifiedInvite({
      routeTripId,
      readSession,
      listAccountTrips,
      adoptAccountTrip,
      postInvite,
    }) {
      const attempt = ++generation
      const current = () => generation === attempt
      if (typeof routeTripId !== 'string' || routeTripId.length === 0) {
        throw unavailable()
      }

      const started = readSession()
      if (!started || started.kind !== 'account') {
        throw unavailable()
      }

      let rows
      try {
        rows = await listAccountTrips()
      } catch (error) {
        if (!current()) throw unavailable()
        throw error
      }
      if (!current()) throw unavailable()

      const membership = verifiedOrganizerMembership(rows, routeTripId)
      if (!membership) throw unavailable()

      const midway = readSession()
      if (!current() || !midway || midway.kind !== 'account') throw unavailable()

      let facts = midway
      if (midway.activeTripId !== routeTripId) {
        let adopted
        try {
          adopted = await Promise.resolve(adoptAccountTrip({
            membershipId: membership.membership_id,
            tripId: routeTripId,
            role: membership.my_role,
          }))
        } catch {
          throw unavailable()
        }
        if (!current()) throw unavailable()
        facts = adopted?.facts
        if (!facts || facts.kind !== 'account' || facts.activeTripId !== routeTripId) {
          throw unavailable()
        }
      }

      const latest = readSession()
      if (!current() || !latest || latest.kind !== 'account') throw unavailable()
      if (
        latest.activeTripId
        && latest.activeTripId !== routeTripId
        && latest.activeTripId !== midway.activeTripId
      ) {
        throw unavailable()
      }
      if (!facts || facts.kind !== 'account' || facts.activeTripId !== routeTripId) {
        throw unavailable()
      }
      if (!current()) throw unavailable()

      return postInvite({ tripId: routeTripId, facts })
    },
  }
}
