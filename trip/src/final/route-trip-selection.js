/**
 * The workspace route selects the trip a member is looking at.
 * A cached active trip is only a display fallback when the route does not
 * name a trip. It must not stand in for a different route id.
 *
 * @param {{
 *   routeTripId?: string | null,
 *   trips?: Array<Record<string, any>> | null,
 *   activeTrip?: Record<string, any> | null,
 *   tripSummaries?: Array<Record<string, any>> | null,
 * }} input
 */
export function selectDisplayedTrip(input) {
  const routeTripId = input?.routeTripId || null
  const activeTrip = input?.activeTrip || null
  if (!routeTripId) return activeTrip

  const listed = (input?.trips || []).find(item => item?.id === routeTripId) || null
  if (listed) return listed
  if (activeTrip?.id === routeTripId) return activeTrip
  return (input?.tripSummaries || []).find(item => item?.id === routeTripId) || null
}

/**
 * Membership to adopt so trip requests follow a route the user is allowed to open.
 * Returns null when the route is already active, or when no membership is known.
 * Guest sessions are left untouched: switching them needs the guest credential,
 * which this helper does not carry.
 *
 * @param {{
 *   routeTripId?: string | null,
 *   activeTripId?: string | null,
 *   membershipId?: string | null,
 *   currentUser?: Record<string, any> | null,
 *   tripSummaries?: Array<Record<string, any>> | null,
 * }} input
 */
export function membershipAdoptionForRouteTrip(input) {
  const routeTripId = input?.routeTripId || null
  if (!routeTripId || routeTripId === input?.activeTripId) return null
  if (input?.currentUser?.isGuest || input?.currentUser?.is_guest) return null

  const summary = (input?.tripSummaries || []).find(item => item?.id === routeTripId && item?.membership_id) || null
  if (summary) {
    return {
      tripId: routeTripId,
      membershipId: summary.membership_id,
      role: summary.my_role || 'participant',
    }
  }

  const userTripId = input?.currentUser?.tripId || input?.currentUser?.trip_id || null
  if (userTripId === routeTripId && input?.membershipId) {
    return {
      tripId: routeTripId,
      membershipId: input.membershipId,
      role: input.currentUser?.role || 'participant',
    }
  }

  return null
}
