import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { baseUpdates as fallbackBaseUpdates, demoDataEnabled, initialDays as fallbackDays, personalUpdates as fallbackPersonalUpdates, trip as fallbackTrip } from './tripContent.js'
import { restoreTripAppBootstrapState } from './technicalSessionBootstrap.js'
import { createSessionRuntime, SESSION_RUNTIME_CODES } from '../../../shared/session-runtime/index.js'
import { classifyTechnicalSessionInvalidation } from './technicalSessionInvalidation.js'

const TripAppContext = createContext(null)

export const useTripApp = () => useContext(TripAppContext)

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || 'http://127.0.0.1:8000').replace(/\/$/, '')
const loginUrl = () => `${window.location.origin}/login?next=/trip`

const emptyPreferences = {
  preferredRange: { start: null, end: null },
  availableRange: { start: null, end: null },
  idealBudget: '',
  maxBudget: '',
  budgetVisibility: 'planning',
  pace: 'Balanced',
  interests: [],
  essentialNeeds: [],
  avoid: '',
}

const makeDefaultDate = (month, day) => new Date(2026, month, day)

const parseHour = text => {
  const match = /(\d{1,2})(?::(\d{2}))?\s*(a\.?m\.?|p\.?m\.?)/i.exec(text || '')
  if (!match) return null
  const hour = Number(match[1]) % 12
  const minutes = Number(match[2] || 0) / 60
  return (/p/i.test(match[3]) ? hour + 12 : hour) + minutes
}

const formatHour = value => {
  if (value === null || value === undefined) return '—'
  const whole = Math.floor(Number(value))
  const minutes = Math.round((Number(value) - whole) * 60)
  const suffix = whole >= 12 ? 'PM' : 'AM'
  const displayHour = whole % 12 || 12
  return `${displayHour}:${String(minutes).padStart(2, '0')} ${suffix}`
}

const formatDayDate = value => {
  if (!value) return ''
  const date = new Date(`${value}T00:00:00`)
  return date.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
}

const parseISODate = value => value ? new Date(`${value}T00:00:00`) : null

const titleForDay = items => {
  const sightseeing = items.filter(item => !item.isMeal)
  if (!sightseeing.length) return 'Open day'
  if (sightseeing.length === 1) return sightseeing[0].title
  return `${sightseeing[0].title} and ${sightseeing[sightseeing.length - 1].title}`
}

const formatTripDateRange = raw => {
  const start = raw.preferred_start_date ? formatDayDate(raw.preferred_start_date) : ''
  const end = raw.preferred_end_date ? formatDayDate(raw.preferred_end_date) : ''
  if (start && end) return `${start} - ${end}`
  return start || end || ''
}

const normalizeTrip = raw => ({
  id: raw.id,
  name: raw.name || 'Untitled trip',
  destination: raw.destination || 'Destination not set',
  dates: raw.dates || formatTripDateRange(raw),
  preferredStartDate: raw.preferred_start_date || raw.preferredStartDate || null,
  preferredEndDate: raw.preferred_end_date || raw.preferredEndDate || null,
  status: raw.status?.replace(/^\w/, c => c.toUpperCase()) || 'Planning',
  people: raw.member_count || 1,
  coverImageUrl: raw.cover_image_url || raw.coverImageUrl || null,
  coverImageSource: raw.cover_image_source || raw.coverImageSource || null,
  coverAttributionName: raw.cover_attribution_name || raw.coverAttributionName || null,
  coverAttributionUrl: raw.cover_attribution_url || raw.coverAttributionUrl || null,
  coverSourceUrl: raw.cover_source_url || raw.coverSourceUrl || null,
  onboarding: raw.onboarding || {},
  organizerPreference: raw.organizerPreference || raw.organizer_preference || null,
})

const normalizeItem = item => ({
  id: item.id,
  kind: item.source || 'plan',
  time: formatHour(item.start_hour),
  title: item.title,
  localTitle: item.local_title || null,
  place: item.place,
  description: item.description || item.note || null,
  status: item.settledness === 'booked' ? 'Booked' : '',
  locked: item.settledness === 'booked',
  // Do not display item prices on the itinerary. Backend still returns price_per_person and uses it for budget classification.
  // Money impact is clearer on the change itself than spread across every plan item.
  note: '',
  coords: item.coords,
  dayDate: item.day_date,
  startHour: item.start_hour,
  durationMin: item.duration_min,
  pricePerPerson: item.price_per_person,
  settledness: item.settledness,
  photoUrl: item.photoUrl,
  tags: item.tags || [],
  isMeal: Boolean(item.is_meal),
  mealType: item.meal_type || null,
})

const normalizePlan = raw => {
  const days = raw.days || []
  return days.map(day => {
    const items = (day.items || []).map(normalizeItem)
    const dayDate = day.day_date || items[0]?.dayDate
    return {
      id: `day${day.day_index}`,
      label: `Day ${day.day_index}`,
      date: formatDayDate(dayDate),
      title: titleForDay(items),
      summary: `${items.filter(item => !item.isMeal).length} activities · ${items.filter(item => item.isMeal).length} meals`,
      items,
    }
  })
}

const normalizeUpdate = update => ({
  id: update.id,
  kind: update.kind || 'plan',
  icon: update.kind === 'round' ? '◇' : update.kind === 'proposal' ? '!' : '↻',
  title: update.title,
  body: update.body,
  canObject: Boolean(update.can_object),
  itemId: update.plan_item_id,
  time: 'Just now',
})

const normalizeCurrentUser = user => ({
  membershipId: user.membership_id,
  id: user.id || user.membership_id,
  name: user.name || 'Guest',
  initials: user.initials || '??',
  email: user.email || null,
  role: user.role,
  tripId: user.trip_id,
  isGuest: Boolean(user.is_guest),
})

const normalizeAccountUser = user => ({
  membershipId: null,
  id: user.id,
  name: user.name || 'Traveler',
  initials: user.initials || initialsFor(user.name),
  email: user.email || null,
  role: 'account',
  tripId: null,
  isGuest: false,
})

const initialsFor = name => {
  const parts = String(name || '').trim().split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
  return (parts[0]?.slice(0, 2) || '??').toUpperCase()
}

const normalizeRound = (round, myVotes = {}, fallback = {}) => {
  const deadline = round.deadline ? new Date(round.deadline).getTime() : Date.now()
  const windowMs = Math.max(1, deadline - Date.now())
  return {
    id: round.id,
    kind: round.kind || 'normal',
    itemId: round.plan_item_id || fallback.itemId,
    itemTitle: round.item_title || fallback.itemTitle,
    options: round.options || [],
    reason: round.reason,
    status: round.status,
    winningOptionId: round.winning_option_id,
    totalMembers: round.total_members || 0,
    responded: round.responded || 0,
    tally: round.tally || {},
    myVote: myVotes[round.id] || round.my_vote || fallback.myVote || null,
    closesAt: deadline,
    windowMs,
  }
}

const normalizeProposal = (proposal, fallback = {}) => {
  const before = proposal.before || {}
  const after = { ...before, ...(proposal.after || {}) }
  return {
    id: proposal.id,
    status: proposal.status,
    sourceItemId: proposal.plan_item_id || fallback.sourceItemId,
    myStatus: proposal.my_status || fallback.myStatus || null,
    canDecide: Boolean(proposal.can_decide),
    headline: fallback.headline || 'Needs confirmation',
    detail: fallback.detail || 'The Current Plan stays unchanged until every affected member confirms.',
    createdAt: 'Just now',
    before: {
      title: before.title || fallback.itemTitle || 'Current plan',
      time: formatHour(before.start_hour),
      place: before.place || '',
      dayLabel: before.day_date || '',
    },
    after: {
      title: after.title || before.title || fallback.itemTitle || 'Proposed change',
      time: formatHour(after.start_hour),
      place: after.place || before.place || '',
      dayLabel: after.day_date || before.day_date || '',
    },
    affectedMembers: (proposal.members || []).map((member, index) => ({
      id: `${proposal.id}-${index}`,
      label: member.label,
      status: member.status,
      isMe: Boolean(member.is_me),
    })),
    privacyNote: proposal.privacy_note,
  }
}

const requestPatch = (actionType, item, request) => {
  const hour = parseHour(request)
  switch (actionType) {
    case 'editTime':
      return hour === null ? {} : { start_hour: hour }
    case 'moveDay':
      return { request }
    case 'replacePlace':
      return { title: request?.slice(0, 60) || item.title, request }
    case 'removePlan':
      return {
        remove: true,
        request,
      }
    default:
      return {}
  }
}

const friendlyError = err => {
  if (err?.status === 409) return 'A vote is already open for this time block.'
  if (err?.status === 422) return err.message || 'Reopening this block needs a written reason.'
  if (err?.status >= 500) return err.message || 'The backend hit an error while processing this request.'
  if (!err?.status) return 'Could not reach the backend.'
  return err.message || 'Something went wrong.'
}

const mergeRounds = (incoming, current) => {
  const incomingIds = new Set(incoming.map(round => round.id))
  return [
    ...incoming.map(round => {
      const existing = current.find(item => item.id === round.id)
      return { ...round, myVote: existing?.myVote || round.myVote }
    }),
    ...current.filter(round => !incomingIds.has(round.id) && round.status !== 'open'),
  ]
}

const mergeProposals = (incoming, current) => {
  const incomingIds = new Set(incoming.map(proposal => proposal.id))
  return [
    ...incoming.map(proposal => {
      const existing = current.find(item => item.id === proposal.id)
      return { ...existing, ...proposal }
    }),
    ...current.filter(proposal => !incomingIds.has(proposal.id) && ['waiting_affected_members', 'escalated'].includes(proposal.status)),
  ]
}

const technicalSessionFactsFromState = ({ hasAccountSession, membershipId, activeTripId }) => {
  if (hasAccountSession) {
    return {
      kind: 'account',
      accountAuth: true,
      activeTripId: activeTripId || null,
      membershipId: membershipId || null,
    }
  }

  if (membershipId && activeTripId) {
    return {
      kind: 'guest',
      activeTripId,
      membershipId,
    }
  }

  return { kind: 'none' }
}

const missingContextError = (message, code) => {
  const error = new Error(message)
  error.code = code
  return error
}

export function TripAppProvider({ children }) {
  const [sessionRuntime] = useState(() => createSessionRuntime())
  const [bootstrapSession] = useState(() => restoreTripAppBootstrapState({
    sessionRuntime,
  }))
  const [hasAccountSession, setHasAccountSession] = useState(() => bootstrapSession.hasAccountSession)
  const [membershipId, setMembershipId] = useState(() => bootstrapSession.membershipId)
  const [restoredTripId] = useState(() => bootstrapSession.restoredTripId)
  const [activeTripId, setActiveTripId] = useState(() => bootstrapSession.activeTripId)
  const [trip, setTrip] = useState(fallbackTrip)
  const [currentUser, setCurrentUser] = useState(null)
  const [days, setDays] = useState(fallbackDays)
  const [planId, setPlanId] = useState(null)
  const [planBlockedReason, setPlanBlockedReason] = useState('')
  const [planNeedsRefresh, setPlanNeedsRefresh] = useState(false)
  const [notices, setNotices] = useState([])
  const [baseUpdates, setBaseUpdates] = useState(fallbackBaseUpdates)
  const [personalUpdates] = useState(fallbackPersonalUpdates)
  const [activeRounds, setActiveRounds] = useState([])
  const [activeProposals, setActiveProposals] = useState([])
  const [myVotes, setMyVotes] = useState({})
  const [decisionResolved, setDecisionResolved] = useState(false)
  const [updateFilter, setUpdateFilter] = useState('actions')
  const [toast, setToast] = useState('')
  const [loading, setLoading] = useState({ initial: true, action: false })
  const [error, setError] = useState('')
  const [trips, setTrips] = useState([])
  const [tripSummaries, setTripSummaries] = useState([])
  const [tripSummariesStatus, setTripSummariesStatus] = useState(() => (bootstrapSession.hasAccountSession ? 'idle' : 'not-needed'))
  const [inviteCopied, setInviteCopied] = useState(false)
  const [preferencesSubmittedFor, setPreferencesSubmittedFor] = useState([])
  const [preferences, setPreferences] = useState(() => demoDataEnabled ? {
    preferredRange: { start: makeDefaultDate(7, 14), end: makeDefaultDate(7, 17) },
    availableRange: { start: makeDefaultDate(7, 13), end: makeDefaultDate(7, 18) },
    idealBudget: '$500',
    maxBudget: '$650',
    budgetVisibility: 'organizer',
    pace: 'Relaxed',
    interests: ['Food', 'Culture', 'Relaxed'],
    essentialNeeds: [
      { id: 'need-1', text: 'No activities before 9:00 AM', importance: 'required', visibility: 'planning' },
    ],
    avoid: 'Very crowded nightlife venues',
  } : emptyPreferences)
  const pollTimerRef = useRef(null)
  const pollFailuresRef = useRef(0)
  const pollDelayRef = useRef(5000)
  const technicalSessionFacts = useMemo(() => technicalSessionFactsFromState({
    hasAccountSession,
    membershipId,
    activeTripId,
  }), [activeTripId, hasAccountSession, membershipId])

  const resolveActiveTripId = useCallback(() => {
    const tripId = activeTripId || trip?.id
    if (!tripId) {
      throw missingContextError(
        'Missing trip session',
        SESSION_RUNTIME_CODES.missingContext.MISSING_ACTIVE_TRIP_CONTEXT,
      )
    }
    return tripId
  }, [activeTripId, trip?.id])

  const notify = useCallback(message => {
    setToast(message)
    window.setTimeout(() => setToast(''), 2400)
  }, [])

  const publicRequestJson = useCallback(async (path, options = {}) => {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      const message = typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`
      const error = new Error(message)
      error.status = response.status
      throw error
    }
    return response.json()
  }, [])

  const identityHeadersFor = useCallback(scope => {
    const identity = sessionRuntime.requestIdentityFor(scope, technicalSessionFacts)
    if (identity.ok) return identity.headers

    switch (identity.code) {
      case SESSION_RUNTIME_CODES.missingContext.MISSING_ACCOUNT_AUTH:
        throw missingContextError('Missing account session', identity.code)
      case SESSION_RUNTIME_CODES.missingContext.MISSING_ACTIVE_TRIP_CONTEXT:
        throw missingContextError('Missing trip session', identity.code)
      case SESSION_RUNTIME_CODES.missingContext.MISSING_MEMBERSHIP_IDENTITY:
        throw missingContextError('Missing membership session', identity.code)
      default:
        throw missingContextError('Missing session context', identity.code)
    }
  }, [sessionRuntime, technicalSessionFacts])

  const applyTechnicalSessionInvalidation = useCallback((cause, message) => {
    const invalidated = sessionRuntime.invalidateTechnicalSession(technicalSessionFacts, cause)
    const nextFacts = invalidated.facts

    if (nextFacts.kind !== 'account') {
      setHasAccountSession(false)
      setTripSummaries([])
      setTripSummariesStatus('not-needed')
    } else {
      setHasAccountSession(true)
      setTripSummariesStatus('idle')
    }

    setMembershipId(nextFacts.kind === 'none' ? '' : (nextFacts.membershipId || ''))
    setActiveTripId(nextFacts.kind === 'none' ? '' : (nextFacts.activeTripId || ''))
    setCurrentUser(null)
    setError(message)
    return invalidated
  }, [sessionRuntime, technicalSessionFacts])

  const sessionRequestJson = useCallback(async (scope, path, options = {}) => {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        Accept: 'application/json',
        'Content-Type': 'application/json',
        ...identityHeadersFor(scope),
        ...(options.headers || {}),
      },
    })
    if (!response.ok) {
      const body = await response.json().catch(() => ({}))
      const message = typeof body.detail === 'string' ? body.detail : `Request failed (${response.status})`
      const invalidationCause = classifyTechnicalSessionInvalidation({
        scope,
        facts: technicalSessionFacts,
        status: response.status,
      })
      if (invalidationCause) {
        applyTechnicalSessionInvalidation(invalidationCause, message)
      }
      const error = new Error(message)
      error.status = response.status
      if (invalidationCause) {
        error.invalidationCause = invalidationCause
      }
      throw error
    }
    return response.json()
  }, [applyTechnicalSessionInvalidation, identityHeadersFor, technicalSessionFacts])

  const accountRequestJson = useCallback(async (path, options = {}) => {
    return sessionRequestJson('account', path, options)
  }, [sessionRequestJson])

  const refreshAccountUser = useCallback(async () => {
    const raw = await accountRequestJson('/api/account')
    setCurrentUser(normalizeAccountUser(raw))
    return raw
  }, [accountRequestJson])

  const requestJson = useCallback(async (path, options = {}) => {
    return sessionRequestJson('trip', path, options)
  }, [sessionRequestJson])

  const refreshTrip = useCallback(async () => {
    if (!activeTripId) throw new Error('Missing trip session')
    const raw = await requestJson(`/api/trips/${activeTripId}`)
    const normalizedTrip = normalizeTrip(raw)
    setTrip(normalizedTrip)
    setTrips(current => current.map(item => item.id === normalizedTrip.id ? normalizedTrip : item))
    return raw
  }, [activeTripId, requestJson])

  const refreshCurrentUser = useCallback(async () => {
    const raw = await requestJson('/api/me')
    setCurrentUser(normalizeCurrentUser(raw))
    return raw
  }, [requestJson])

  const refreshPlan = useCallback(async () => {
    if (!activeTripId) throw new Error('Missing trip session')
    try {
      const raw = await requestJson(`/api/trips/${activeTripId}/plans/current`)
      setPlanId(raw.plan_id)
      setPlanBlockedReason(raw.blocked_reason || '')
      setPlanNeedsRefresh(Boolean(raw.needs_refresh))
      setDays(normalizePlan(raw))
      return raw
    } catch (err) {
      if (err.status === 404) {
        setPlanId(null)
        setPlanBlockedReason('')
        setPlanNeedsRefresh(false)
        setDays([])
        return null
      }
      throw err
    }
  }, [activeTripId, requestJson])

  const refreshUpdates = useCallback(async () => {
    if (!activeTripId) throw new Error('Missing trip session')
    const raw = await requestJson(`/api/trips/${activeTripId}/updates`)
    const normalized = raw.map(normalizeUpdate)
    setNotices(normalized)
    setBaseUpdates(normalized)
    return raw
  }, [activeTripId, requestJson])

  const refreshActions = useCallback(async () => {
    if (!activeTripId) throw new Error('Missing trip session')
    const raw = await requestJson(`/api/trips/${activeTripId}/actions`)
    const rounds = (raw.rounds || []).map(round => normalizeRound(round, myVotes))
    const proposals = (raw.proposals || []).map(proposal => normalizeProposal(proposal))
    setActiveRounds(current => mergeRounds(rounds, current))
    setActiveProposals(current => mergeProposals(proposals, current))
    return raw
  }, [activeTripId, myVotes, requestJson])

  const refreshTripSummaries = useCallback(async () => {
    if (!hasAccountSession) {
      setTripSummaries([])
      setTripSummariesStatus('not-needed')
      return []
    }
    setTripSummariesStatus('loading')
    const priorityQuery = activeTripId ? `?priority_trip_id=${encodeURIComponent(activeTripId)}` : ''
    const raw = await accountRequestJson(`/api/trips${priorityQuery}`)
    setTripSummaries(raw)
    setTrip(current => {
      if (!current) return current
      const summary = raw.find(candidate => candidate.id === current.id)
      return summary ? { ...current, ...normalizeTrip(summary) } : current
    })
    setTripSummariesStatus('ready')
    return raw
  }, [accountRequestJson, activeTripId, hasAccountSession])

  const refreshAll = useCallback(async ({ background = false } = {}) => {
    if (!hasAccountSession && (!membershipId || !activeTripId)) {
      if (!background) setLoading(current => ({ ...current, initial: false }))
      return false
    }
    if (!background) setLoading(current => ({ ...current, initial: true }))
    try {
      if (hasAccountSession && !activeTripId) {
        await Promise.all([refreshAccountUser(), refreshTripSummaries()])
        setError('')
        return true
      }
      await Promise.all([
        refreshCurrentUser(),
        refreshTrip(),
        refreshPlan(),
        refreshUpdates(),
        refreshActions(),
        hasAccountSession
          ? refreshTripSummaries().catch(() => {
            setTripSummariesStatus('failed')
            return null
          })
          : Promise.resolve([]),
      ])
      setError('')
      return true
    } catch (err) {
      // User-initiated failures are shown immediately.
      // Background polling failures are tolerated twice so brief network blips do not flash banners repeatedly.
      if (!background || pollFailuresRef.current >= 2) {
        setError(err.message || 'Failed to load trip data')
      }
      return false
    } finally {
      if (!hasAccountSession) setTripSummariesStatus('not-needed')
      if (!background) setLoading(current => ({ ...current, initial: false }))
    }
  }, [activeTripId, hasAccountSession, membershipId, refreshAccountUser, refreshActions, refreshCurrentUser, refreshPlan, refreshTrip, refreshTripSummaries, refreshUpdates])

  useEffect(() => {
    if (!hasAccountSession) {
      setTripSummaries([])
      setTripSummariesStatus('not-needed')
    }
  }, [hasAccountSession])

  useEffect(() => {
    refreshAll()
  }, [refreshAll])

  useEffect(() => {
    if ((!hasAccountSession && !membershipId) || !activeTripId) return undefined
    let cancelled = false

    const clearTimer = () => {
      if (pollTimerRef.current) window.clearTimeout(pollTimerRef.current)
      pollTimerRef.current = null
    }
    const schedule = delay => {
      clearTimer()
      if (!cancelled && document.visibilityState === 'visible') {
        pollTimerRef.current = window.setTimeout(poll, delay)
      }
    }
    const poll = async () => {
      if (cancelled || document.visibilityState !== 'visible') return
      const ok = await refreshAll({ background: true })
      if (ok) {
        pollFailuresRef.current = 0
        pollDelayRef.current = 5000
      } else {
        pollFailuresRef.current += 1
        pollDelayRef.current = pollFailuresRef.current === 1 ? 10000 : 30000
      }
      schedule(pollDelayRef.current)
    }
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') {
        poll()
      } else {
        clearTimer()
      }
    }

    schedule(pollDelayRef.current)
    document.addEventListener('visibilitychange', handleVisibility)
    return () => {
      cancelled = true
      clearTimer()
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [activeTripId, hasAccountSession, membershipId, refreshAll])

  const readInviteAdoption = useCallback(token => {
    if (!token) return null
    const { record } = sessionRuntime.readInviteAdoption(token)
    if (!record) return null
    return {
      membershipId: record.membershipId,
      tripId: record.activeTripId,
    }
  }, [sessionRuntime])

  const adoptTechnicalTripContext = useCallback(({ membershipId: nextMembershipId, tripId: nextTripId, guestToken, inviteToken, profile }) => {
    const forceGuest = Boolean(profile?.isGuest)
    if (forceGuest) {
      sessionRuntime.adoptGuestAuth({
        token: guestToken,
        membershipId: nextMembershipId,
        activeTripId: nextTripId,
        ...(inviteToken ? { inviteToken } : {}),
      })
    } else {
      sessionRuntime.adoptTechnicalTripContext({
        membershipId: nextMembershipId,
        activeTripId: nextTripId,
        ...(inviteToken ? { inviteToken } : {}),
      })
    }
    if (forceGuest) {
      setHasAccountSession(false)
      setTripSummaries([])
      setTripSummariesStatus('not-needed')
    }
    setMembershipId(nextMembershipId)
    setActiveTripId(nextTripId)
    if (profile) {
      setCurrentUser({
        membershipId: nextMembershipId,
        id: profile.id || nextMembershipId,
        name: profile.name || 'Guest',
        initials: profile.initials || initialsFor(profile.name),
        email: profile.email || null,
        role: profile.role || 'participant',
        tripId: nextTripId,
        isGuest: forceGuest,
      })
    }
  }, [sessionRuntime])

  const logout = useCallback(async () => {
    const result = await sessionRuntime.logoutTechnicalSession(technicalSessionFacts, {
      revoke: async () => publicRequestJson('/api/auth/logout', {
        method: 'POST',
        headers: identityHeadersFor(technicalSessionFacts.kind === 'guest' ? 'trip' : 'account'),
      }),
    })
    setHasAccountSession(false)
    setMembershipId(result.facts.kind === 'none' ? '' : (result.facts.membershipId || ''))
    setActiveTripId(result.facts.kind === 'none' ? '' : (result.facts.activeTripId || ''))
    setCurrentUser(null)
    setTripSummaries([])
    setTripSummariesStatus('not-needed')
    window.top.location.href = loginUrl()
  }, [identityHeadersFor, publicRequestJson, sessionRuntime, technicalSessionFacts])

  // Create a real backend trip. This used to create an in-memory draft id.
  // The backend did not know that trip existed, so generation, invites, and preferences could not work.
  const createTrip = useCallback(async payload => {
    setLoading(current => ({ ...current, action: true }))
    try {
      const created = await accountRequestJson('/api/trips', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      const normalizedTrip = {
        ...normalizeTrip(created),
        isCreated: true,
        membership_id: created.membership_id,
        my_role: 'organizer',
      }
      setTrip(normalizedTrip)
      setPlanId(null)
      setPlanBlockedReason('')
      setDays([])
      setNotices([])
      setBaseUpdates([])
      setActiveRounds([])
      setActiveProposals([])
      setTrips(current => [normalizedTrip, ...current.filter(item => item.id !== normalizedTrip.id)])
      // You have a different membership in the new trip. If the frontend does not switch to it,
      // invites, generation, and preferences are rejected because the identity belongs to another trip.
      if (created.membership_id) {
        adoptTechnicalTripContext({
          membershipId: created.membership_id,
          tripId: created.id,
          profile: created.member || {
            id: currentUser?.id,
            name: currentUser?.name,
            initials: currentUser?.initials,
            email: currentUser?.email,
            role: 'organizer',
            tripId: created.id,
            isGuest: false,
          },
        })
      }
      if (hasAccountSession) {
        refreshTripSummaries().catch(() => setTripSummariesStatus('failed'))
      }
      return created
    } catch (err) {
      setError(friendlyError(err))
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [accountRequestJson, adoptTechnicalTripContext, currentUser, hasAccountSession, refreshTripSummaries])

  const createInvite = useCallback(async tripId => {
    return requestJson(`/api/trips/${tripId}/invite`, { method: 'POST' })
  }, [requestJson])

  const revokeInvite = useCallback(async inviteId => {
    return requestJson(`/api/invites/${inviteId}/revoke`, { method: 'POST' })
  }, [requestJson])

  const getInvite = useCallback(async token => {
    return publicRequestJson(`/api/invites/${token}`)
  }, [publicRequestJson])

  const joinInvite = useCallback(async (token, body) => {
    return publicRequestJson(`/api/invites/${token}/join`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
  }, [publicRequestJson])

  const fetchRound = useCallback(async (roundId, fallback = {}) => {
    const raw = await requestJson(`/api/rounds/${roundId}`)
    const normalized = normalizeRound(raw, myVotes, fallback)
    setActiveRounds(current => [normalized, ...current.filter(round => round.id !== normalized.id)])
    return normalized
  }, [myVotes, requestJson])

  const fetchProposal = useCallback(async (proposalId, fallback) => {
    const raw = await requestJson(`/api/proposals/${proposalId}`)
    const normalized = normalizeProposal(raw, fallback)
    setActiveProposals(current => [normalized, ...current.filter(proposal => proposal.id !== normalized.id)])
    return normalized
  }, [requestJson])

  const classify = useCallback(async ({ item, actionType, request, patch = null }) => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const body = patch ? { ...patch, request } : { ...requestPatch(actionType, item, request), request }
      return await requestJson(`/api/plans/items/${item.id}/classify`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
    } catch (err) {
      const message = friendlyError(err)
      if (err.status === 409 || err.status === 422) notify(message)
      else setError(message)
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [notify, requestJson])

  // -------------------- Preferences and six constraint kinds --------------------
  // There is no "someone else" slot in the route; backend has the same shape.

  const loadMyPreferences = useCallback(async () => {
    const tripId = resolveActiveTripId()
    return requestJson(`/api/trips/${tripId}/preferences/me`)
  }, [requestJson, resolveActiveTripId])

  const saveMyPreferences = useCallback(async payload => {
    const tripId = resolveActiveTripId()
    setLoading(current => ({ ...current, action: true }))
    try {
      const saved = await requestJson(`/api/trips/${tripId}/preferences/me`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      })
      const pref = saved.preference || {}
      setPreferences(current => ({
        ...current,
        preferredRange: {
          start: parseISODate(pref.preferred_start_date) || current.preferredRange.start,
          end: parseISODate(pref.preferred_end_date) || current.preferredRange.end,
        },
        availableRange: {
          start: parseISODate(pref.available_start_date) || current.availableRange.start,
          end: parseISODate(pref.available_end_date) || current.availableRange.end,
        },
        idealBudget: pref.ideal_budget ?? current.idealBudget,
        maxBudget: pref.maximum_budget ?? current.maxBudget,
        budgetVisibility: pref.budget_visibility || current.budgetVisibility,
        pace: pref.travel_style || current.pace,
        interests: pref.top_interests?.length ? pref.top_interests : current.interests,
      }))
      await Promise.all([
        refreshCurrentUser(),
        refreshTrip(),
        refreshPlan(),
        refreshUpdates(),
        refreshActions(),
        hasAccountSession
          ? refreshTripSummaries().catch(() => {
            setTripSummariesStatus('failed')
            return null
          })
          : Promise.resolve([]),
      ])
      return saved
    } catch (err) {
      setError(friendlyError(err))
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [hasAccountSession, refreshActions, refreshCurrentUser, refreshPlan, refreshTrip, refreshTripSummaries, refreshUpdates, requestJson, resolveActiveTripId])

  // Adding a constraint returns conflicts showing which plan items it hits.
  // Backend reports only and does not auto-edit; the user may prefer relaxing the requirement.
  const addConstraint = useCallback(async payload => {
    const tripId = resolveActiveTripId()
    const result = await requestJson(`/api/trips/${tripId}/constraints`, {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    await refreshPlan()
    return result
  }, [refreshPlan, requestJson, resolveActiveTripId])

  const updateConstraint = useCallback(async (constraintId, payload) => {
    const result = await requestJson(`/api/constraints/${constraintId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    })
    await refreshPlan()
    return result
  }, [refreshPlan, requestJson])

  const deleteConstraint = useCallback(async constraintId => {
    const result = await requestJson(`/api/constraints/${constraintId}`, { method: 'DELETE' })
    await refreshPlan()
    return result
  }, [refreshPlan, requestJson])

  const loadMembers = useCallback(async () => {
    const tripId = resolveActiveTripId()
    return requestJson(`/api/trips/${tripId}/members`)
  }, [requestJson, resolveActiveTripId])

  const loadComments = useCallback(async () => {
    const tripId = resolveActiveTripId()
    return requestJson(`/api/trips/${tripId}/comments`)
  }, [requestJson, resolveActiveTripId])

  const loadChangeLog = useCallback(async () => {
    if (!planId) return []
    return requestJson(`/api/plans/${planId}/changes`)
  }, [planId, requestJson])

  const addComment = useCallback(async (itemId, text) => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      return await requestJson(`/api/plans/items/${itemId}/comments`, {
        method: 'POST',
        body: JSON.stringify({ text }),
      })
    } catch (err) {
      const message = friendlyError(err)
      if (err.status === 422) notify(message)
      else setError(message)
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [notify, requestJson])

  const setItemBooked = useCallback(async (itemId, booked) => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const item = await requestJson(`/api/plans/items/${itemId}/booking`, {
        method: 'PATCH',
        body: JSON.stringify({ booked }),
      })
      await Promise.all([refreshPlan(), refreshUpdates()])
      return item
    } catch (err) {
      const message = friendlyError(err)
      if (err.status === 409 || err.status === 422) notify(message)
      else setError(message)
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [notify, refreshPlan, refreshUpdates, requestJson])

  const addPlanItem = useCallback(async ({ title, afterItemId, beforeItemId, startHour }) => {
    if (!planId) throw new Error('No current plan')
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const item = await requestJson(`/api/plans/${planId}/items`, {
        method: 'POST',
        body: JSON.stringify({
          title: title.trim(),
          after_item_id: afterItemId,
          before_item_id: beforeItemId,
          start_hour: startHour,
        }),
      })
      await refreshPlan()
      return item
    } catch (err) {
      const message = friendlyError(err)
      if (err.status === 409 || err.status === 422) notify(message)
      else setError(message)
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [notify, planId, refreshPlan, requestJson])

  const generatePlan = useCallback(async () => {
    const tripId = resolveActiveTripId()
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const result = await requestJson(`/api/trips/${tripId}/plans/generate`, { method: 'POST' })
      await Promise.all([refreshPlan(), refreshTrip(), refreshUpdates(), refreshActions()])
      return result
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [refreshActions, refreshPlan, refreshTrip, refreshUpdates, requestJson, resolveActiveTripId])

  const remindMember = useCallback(async targetMembershipId => {
    const tripId = resolveActiveTripId()
    return requestJson(`/api/trips/${tripId}/members/${targetMembershipId}/remind`, { method: 'POST' })
  }, [requestJson, resolveActiveTripId])

  const extendRound = useCallback(async roundId => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const result = await requestJson(`/api/rounds/${roundId}/extend`, { method: 'POST' })
      const deadline = result.deadline ? new Date(result.deadline).getTime() : Date.now()
      setActiveRounds(current => current.map(round => round.id === roundId
        ? { ...round, closesAt: deadline, windowMs: Math.max(1, deadline - Date.now()) }
        : round))
      await refreshUpdates()
      return result
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [refreshUpdates, requestJson])

  const escalateProposal = useCallback(async proposalId => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const result = await requestJson(`/api/proposals/${proposalId}/escalate`, { method: 'POST' })
      setActiveProposals(current => current.map(proposal => proposal.id === proposalId ? { ...proposal, status: result.status } : proposal))
      setUpdateFilter('actions')
      await Promise.all([refreshUpdates(), refreshActions()])
      return result
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [refreshActions, refreshUpdates, requestJson])

  const resolveDeadlock = useCallback(async (proposalId, action) => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const result = await requestJson(`/api/proposals/${proposalId}/deadlock`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      setActiveProposals(current => current.filter(proposal => proposal.id !== proposalId))
      setDecisionResolved(true)
      await Promise.all([refreshPlan(), refreshUpdates(), refreshActions()])
      return result
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [refreshActions, refreshPlan, refreshUpdates, requestJson])

  const chatWithTrip = useCallback(async ({ message, itemId, history = [] }) => {
    const tripId = resolveActiveTripId()
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      return await requestJson(`/api/trips/${tripId}/chat`, {
        method: 'POST',
        body: JSON.stringify({
          message,
          ...(itemId ? { item_id: itemId } : {}),
          history: history.slice(-10),
        }),
      })
    } catch (err) {
      const message = friendlyError(err)
      if (err.status === 409 || err.status === 422) notify(message)
      else setError(message)
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [notify, requestJson, resolveActiveTripId])

  const handleOutcome = useCallback(async (outcome, item) => {
    if (outcome.round_id) {
      await fetchRound(outcome.round_id, { itemId: item?.id, itemTitle: item?.title })
      setUpdateFilter('actions')
    }
    if (outcome.proposal_id) {
      await fetchProposal(outcome.proposal_id, {
        headline: outcome.headline,
        detail: outcome.detail,
        sourceItemId: item?.id,
        itemTitle: item?.title,
      })
      setDecisionResolved(false)
      setUpdateFilter('actions')
    }
    await Promise.all([refreshPlan(), refreshUpdates()])
    return outcome
  }, [fetchProposal, fetchRound, refreshPlan, refreshUpdates])

  const submitChange = useCallback(async ({ item, actionType, request, verdict, patch, options }) => {
    let reason = null
    if (verdict?.needs_reason) {
      reason = actionType === 'removePlan'
        ? (request?.trim() || `Remove ${item.title} from the plan`)
        : window.prompt('Please write a reason for reopening this settled block:')
      if (!reason?.trim()) {
        notify('Reopening this block needs a written reason.')
        return null
      }
    }
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const body = { ...(patch || requestPatch(actionType, item, request)), request, reason }
      if (options?.length) body.options = options
      const outcome = await requestJson(`/api/plans/items/${item.id}/changes`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
      await handleOutcome(outcome, item)
      return outcome
    } catch (err) {
      const message = friendlyError(err)
      if (err.status === 409 || err.status === 422) notify(message)
      else setError(message)
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [handleOutcome, notify, requestJson])

  const searchReplacementPlaces = useCallback(async ({ itemId, query = '' }) => {
    const params = query.trim() ? `?q=${encodeURIComponent(query.trim())}` : ''
    return requestJson(`/api/plans/items/${itemId}/replacement-places${params}`)
  }, [requestJson])

  const objectToNotice = useCallback(async notice => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const outcome = await requestJson(`/api/updates/${notice.id}/object`, { method: 'POST' })
      await handleOutcome(outcome, { id: notice.itemId, title: notice.title })
      return outcome
    } catch (err) {
      const message = friendlyError(err)
      if (err.status === 409 || err.status === 422) notify(message)
      else setError(message)
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [handleOutcome, notify, requestJson])

  const castVote = useCallback(async (roundId, optionId) => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const raw = await requestJson(`/api/rounds/${roundId}/votes`, {
        method: 'POST',
        body: JSON.stringify({ option_id: optionId }),
      })
      setMyVotes(current => ({ ...current, [roundId]: optionId }))
      const existing = activeRounds.find(round => round.id === roundId)
      const normalized = normalizeRound(raw, { ...myVotes, [roundId]: optionId }, existing)
      setActiveRounds(current => [normalized, ...current.filter(round => round.id !== normalized.id)])
      if (normalized.status === 'closed') {
        await refreshAll({ background: true })
      }
      return normalized
    } catch (err) {
      setError(err.message || 'Could not vote')
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [activeRounds, myVotes, refreshAll, requestJson])

  const resolveProposal = useCallback(async (proposalId, status = 'accepted') => {
    setLoading(current => ({ ...current, action: true }))
    setError('')
    try {
      const result = await requestJson(`/api/proposals/${proposalId}/decisions`, {
        method: 'POST',
        body: JSON.stringify({ status }),
      })
      const normalized = normalizeProposal(result)
      setActiveProposals(current => [normalized, ...current.filter(proposal => proposal.id !== normalized.id)])
      await refreshPlan()
      await refreshUpdates()
      if (result.applied) setDecisionResolved(true)
      return result
    } catch (err) {
      setError(err.message || 'Could not update confirmation')
      throw err
    } finally {
      setLoading(current => ({ ...current, action: false }))
    }
  }, [fetchProposal, refreshPlan, refreshUpdates, requestJson])

  const withdrawProposal = useCallback(proposalId => {
    setActiveProposals(current => proposalId ? current.filter(proposal => proposal.id !== proposalId) : current.slice(1))
    setDecisionResolved(false)
  }, [])

  const resetDemo = useCallback(() => {
    setActiveRounds([])
    setActiveProposals([])
    setDecisionResolved(false)
    refreshAll()
  }, [refreshAll])

  const activeRound = activeRounds.find(round => round.status === 'open') || activeRounds[0] || null
  const activeProposal = activeProposals.find(proposal => ['waiting_affected_members', 'escalated'].includes(proposal.status)) || activeProposals[0] || null

  const value = useMemo(() => ({
    trip, currentUser, days, planId, planBlockedReason, planNeedsRefresh,
    hasAccountSession,
    membershipId,
    activeTripId,
    restoredTripId,
    appliedPatches: {},
    contestedSlots: [],
    notices,
    baseUpdates,
    personalUpdates,
    activeRounds,
    activeProposals,
    activeRound,
    activeProposal,
    decisionResolved,
    conflictCreated: activeProposals.some(proposal => ['waiting_affected_members', 'escalated'].includes(proposal.status)),
    classify,
    chatWithTrip,
    logout,
    submitChange,
    searchReplacementPlaces,
    castVote,
    resolveProposal,
    generatePlan,
    remindMember,
    extendRound,
    escalateProposal,
    resolveDeadlock,
    withdrawProposal,
    objectToNotice,
    resetDemo,
    refreshAll,
    adoptTechnicalTripContext,
    readInviteAdoption,
    createInvite,
    revokeInvite,
    getInvite,
    joinInvite,
    loading,
    error,
    clearError: () => setError(''),
    updateFilter,
    setUpdateFilter,
    trips,
    tripSummaries,
    tripSummariesStatus,
    createTrip,
    inviteCopied,
    setInviteCopied,
    preferences,
    setPreferences,
    loadMyPreferences,
    saveMyPreferences,
    addConstraint,
    updateConstraint,
    deleteConstraint,
    loadMembers,
    loadComments,
    loadChangeLog,
    addComment,
    addPlanItem,
    setItemBooked,
    preferencesSubmittedFor,
    submitPreferencesFor: tripId => setPreferencesSubmittedFor(current => current.includes(tripId) ? current : [...current, tripId]),
    notify,
  }), [createTrip, activeProposal, activeProposals, activeRound, activeRounds, activeTripId, addPlanItem, adoptTechnicalTripContext, baseUpdates, castVote, chatWithTrip, classify, createInvite, currentUser, days, decisionResolved, error, getInvite, hasAccountSession, inviteCopied, joinInvite, loading, logout, membershipId, notices, objectToNotice, personalUpdates, planBlockedReason, planNeedsRefresh, planId, loadMembers, loadComments, loadChangeLog, addComment, readInviteAdoption, setItemBooked, generatePlan, remindMember, extendRound, escalateProposal, resolveDeadlock, loadMyPreferences, preferences, preferencesSubmittedFor, refreshAll, restoredTripId, saveMyPreferences, addConstraint, updateConstraint, deleteConstraint, resetDemo, resolveProposal, revokeInvite, searchReplacementPlaces, submitChange, trip, trips, tripSummaries, tripSummariesStatus, updateFilter, withdrawProposal])

  if (!currentUser) {
    const isJoinRoute = window.location.hash.startsWith('#/join/')
    if (isJoinRoute) {
      return <TripAppContext.Provider value={value}>{children}{toast && <div className="toast">{toast}</div>}</TripAppContext.Provider>
    }
    const missingSession = (!hasAccountSession && !membershipId) || !activeTripId
    const loadError = error || (missingSession ? 'Join from an invite link or configure a membership session.' : '')
    return <TripAppContext.Provider value={value}>
      <main className="homePage">
        <section className="homeContent">
          <div className="emptyState quietEmptyState">
            <span></span>
            <h2>{loadError ? 'Could not load your profile' : 'Loading Cadensy'}</h2>
            <p>{loadError || 'Fetching your membership and trip data from the backend.'}</p>
            {loadError && <a className="btn btnSecondary" href={loginUrl()} target="_top">Sign in</a>}
            {loadError && !missingSession && <button className="btn btnSecondary" type="button" onClick={refreshAll}>Retry</button>}
          </div>
        </section>
      </main>
      {toast && <div className="toast">{toast}</div>}
    </TripAppContext.Provider>
  }

  return <TripAppContext.Provider value={value}>{children}{toast && <div className="toast">{toast}</div>}</TripAppContext.Provider>
}
