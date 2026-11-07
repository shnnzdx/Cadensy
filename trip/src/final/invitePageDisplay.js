import { serializeWorkspaceRoute } from '../../../shared/trip-navigation-route/index.js'

const SECTION_LABELS = {
  plan: 'Plan',
  chat: 'Chat',
  updates: 'Updates',
  preferences: 'Preferences',
  members: 'Members',
  invite: 'Invite',
}

const MEMBER_SECTIONS = ['plan', 'chat', 'updates', 'preferences']
const ORGANIZER_SECTIONS = [...MEMBER_SECTIONS, 'members', 'invite']

const tripHref = (tripId, section) => serializeWorkspaceRoute({ kind: 'trip', tripId, section })

const routeIdOf = value => (typeof value === 'string' && value.length > 0 ? value : '')

const matchById = (records, routeTripId) => (
  (records || []).find(item => item && item.id === routeTripId) || null
)

export function resolveInvitePageDisplay({
  routeTripId,
  trips,
  activeTrip,
  tripSummaries,
  tripSummariesStatus,
  demoTrip = null,
}) {
  const routeId = routeIdOf(routeTripId)
  const shellTrip = matchById(trips, routeId) || activeTrip || demoTrip || null
  const shellMatchesRoute = Boolean(
    routeId
    && shellTrip
    && shellTrip !== demoTrip
    && shellTrip.id === routeId,
  )
  const fromTrips = matchById(trips, routeId)
  const fromActive = activeTrip && activeTrip !== demoTrip && activeTrip.id === routeId ? activeTrip : null
  const fromSummary = matchById(tripSummaries, routeId)
  const matched = fromTrips || fromActive || fromSummary || null
  const waiting = tripSummariesStatus === 'idle' || tripSummariesStatus === 'loading'

  if (!routeId || !matched || matched.id !== routeId) {
    return {
      status: routeId && waiting ? 'loading' : 'unavailable',
      routeTripId: routeId,
      trip: null,
      shellMode: 'neutral',
      shouldCreateInvite: false,
      navigation: [],
      startPlanningHref: null,
      heading: routeId && waiting ? 'Loading' : 'Unavailable',
    }
  }

  const sections = matched.my_role === 'organizer' ? ORGANIZER_SECTIONS : MEMBER_SECTIONS
  return {
    status: 'ready',
    routeTripId: routeId,
    trip: matched,
    shellMode: shellMatchesRoute ? 'shared' : 'route',
    shouldCreateInvite: true,
    navigation: sections.map(section => ({
      id: section,
      label: SECTION_LABELS[section],
      href: tripHref(routeId, section),
    })),
    startPlanningHref: tripHref(routeId, 'preferences'),
    heading: matched.name || 'Trip',
  }
}

export function visibleInviteForRoute(routeTripId, inviteState) {
  if (!inviteState || inviteState.routeTripId !== routeTripId) return null
  return inviteState.invite || null
}

export function invitePageRenderModel(input, inviteState = null) {
  const display = resolveInvitePageDisplay(input)
  const invite = display.status === 'ready' ? visibleInviteForRoute(display.routeTripId, inviteState) : null
  const error = display.status === 'ready' && inviteState && inviteState.routeTripId === display.routeTripId
    ? (inviteState.error || '')
    : ''
  return {
    ...display,
    invite,
    error,
    name: display.trip?.name || null,
    destination: display.trip?.destination || null,
    dates: display.trip?.dates || null,
    coverTripId: display.trip?.id || null,
  }
}
