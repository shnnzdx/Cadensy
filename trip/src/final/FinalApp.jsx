import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, Navigate, Outlet, Route, Routes, useLocation, useNavigate, useParams } from 'react-router-dom'
import { TripAppProvider, useTripApp } from './TripAppState.jsx'
import { trip, tripMembers, tripStyles } from './tripContent.js'
import PlanFeature from './plan-feature/PlanFeature.jsx'
import { serializeWorkspaceRoute } from '../../../shared/trip-navigation-route/index.js'
import { buildTripPreviewAbsoluteUrl } from '../../../shared/tripsync-preview-contract.js'
import {
  buildWorkspaceNavigationModel,
  resolveCurrentWorkspaceRoute,
  resolveInviteJoinRoute,
  resolveRestoredWorkspaceDestination,
} from './workspace-navigation-model.js'
import { resolveTripCover, tripCoverImageUrlForVariant } from './trip-cover.js'

const dayKey = date => date ? `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}` : ''
const sameDay = (a, b) => a && b && dayKey(a) === dayKey(b)
const isBefore = (a, b) => a.getTime() < b.getTime()
const isWithin = (day, range) => range.start && range.end && !isBefore(day, range.start) && !isBefore(range.end, day)
const nightsBetween = range => range.start && range.end ? Math.max(0, Math.round((range.end - range.start) / 86400000)) : 0
const formatShortDate = date => date ? date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }) : 'Select'
const formatDateRange = range => {
  if (!range.start || !range.end) return 'Select dates'
  const sameYear = range.start.getFullYear() === range.end.getFullYear()
  const start = range.start.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: sameYear ? undefined : 'numeric' })
  const end = range.end.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' })
  return `${start} – ${end}`
}
const formatInviteDate = value => value ? new Date(`${value}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) : null
// Date shown on change cards. Use "Sat, Aug 15" because time alone cannot distinguish date changes.
// Date picker returns a Date object; the backend expects YYYY-MM-DD.
const toISODate = value => {
  if (!value) return null
  const d = new Date(value)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

const fromISODate = value => value ? new Date(`${value}T00:00:00`) : null

const formatChangeDay = value => value
  ? new Date(`${value}T00:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
  : null

const tripNavigationLabels = {
  plan: 'Plan',
  chat: 'Chat',
  updates: 'Updates',
  preferences: 'Preferences',
  members: 'Members',
  invite: 'Invite',
}
const accountNavigationLabels = {
  'account-profile': 'Profile',
  'account-travel': 'Travel profile',
  'account-notifications': 'Notifications',
  'account-settings': 'Settings',
}
const workspaceHomeHref = () => serializeWorkspaceRoute({ kind: 'home' })
const workspaceCreateHref = () => serializeWorkspaceRoute({ kind: 'create-trip' })
const accountHref = section => serializeWorkspaceRoute({ kind: 'account', section })
const tripHref = (tripId, section) => serializeWorkspaceRoute({ kind: 'trip', tripId, section })
const joinHref = token => serializeWorkspaceRoute({ kind: 'join', token })
const tripPlanHref = (tripId, focusItemId) => {
  const href = tripHref(tripId, 'plan')
  return focusItemId ? `${href}?focus=${encodeURIComponent(focusItemId)}` : href
}

function ScrollToTop() {
  const { pathname } = useLocation()
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' })
  }, [pathname])
  return null
}

// Shared overlay behavior: click outside to close.
function useClickOutside(active, onClose) {
  const ref = useRef(null)
  useEffect(() => {
    if (!active) return
    const handle = event => {
      if (ref.current && !ref.current.contains(event.target)) onClose()
    }
    document.addEventListener('pointerdown', handle)
    return () => document.removeEventListener('pointerdown', handle)
  }, [active, onClose])
  return ref
}

// Return the user-created trip when route tripId matches; otherwise fall back to the Chicago demo trip.
function useCurrentTrip() {
  const { tripId } = useParams()
  const app = useTripApp()
  return app.trips.find(item => item.id === tripId) || app.trip || trip || null
}

const todayAtMidnight = () => {
  const now = new Date()
  return new Date(now.getFullYear(), now.getMonth(), now.getDate())
}
const calendarMonth = (anchor, offset) => {
  const date = new Date(anchor.getFullYear(), anchor.getMonth() + offset, 1)
  return { key: `${date.getFullYear()}-${date.getMonth()}`, label: date.toLocaleDateString('en-US', { month: 'long', year: 'numeric' }), year: date.getFullYear(), month: date.getMonth() }
}
const eachDay = range => {
  if (!range.start || !range.end) return []
  const days = []
  for (let day = new Date(range.start); !isBefore(range.end, day); day = new Date(day.getFullYear(), day.getMonth(), day.getDate() + 1)) days.push(day)
  return days
}

function DateRangePicker({ value, onChange, allowedRange = null, minDate = todayAtMidnight(), maxDate = null, required = false, label = 'Trip dates' }) {
  const [monthOffset, setMonthOffset] = useState(0)
  const resolvedMinDate = allowedRange?.start || minDate
  const resolvedMaxDate = allowedRange?.end || maxDate
  const calendarMonths = [calendarMonth(resolvedMinDate, monthOffset), calendarMonth(resolvedMinDate, monthOffset + 1)]
  const unavailable = day => isBefore(day, resolvedMinDate) || (resolvedMaxDate && isBefore(resolvedMaxDate, day))
  const chooseDay = day => {
    if (unavailable(day)) return
    if (!value.start || value.end) return onChange({ start: day, end: null })
    if (isBefore(day, value.start)) return onChange({ start: day, end: null })
    onChange({ start: value.start, end: day })
  }
  return <div className="rangeCalendar">
    <div className="rangeCalendarSummary"><div><span>{label}</span><strong>{formatDateRange(value)}</strong></div><small>{value.start && value.end ? `${nightsBetween(value)} nights` : 'Choose a start and end date'}</small></div>
    {required && <div className="requiredDateLabels"><span>Start date · Required</span><span>End date · Required</span></div>}
    <div className="calendarNav"><button type="button" disabled={monthOffset === 0} onClick={() => setMonthOffset(current => Math.max(0, current - 1))}>← Previous</button><button type="button" onClick={() => setMonthOffset(current => current + 1)}>Next →</button></div>
    <div className="calendarMonths">{calendarMonths.map(month => {
      const first = new Date(month.year, month.month, 1).getDay()
      const count = new Date(month.year, month.month + 1, 0).getDate()
      return <section className="calendarMonth" key={month.key}><h3>{month.label}</h3><div className="weekdayRow">{['S','M','T','W','T','F','S'].map((d,i) => <span key={`${d}-${i}`}>{d}</span>)}</div><div className="calendarGrid">
        {Array.from({ length: first }, (_, i) => <span className="calendarBlank" key={`b-${i}`}/>) }
        {Array.from({ length: count }, (_, i) => { const day = new Date(month.year, month.month, i + 1); const disabled = unavailable(day); return <button type="button" key={dayKey(day)} disabled={disabled} className={cx(disabled && 'disabledDay', sameDay(day,value.start) && 'rangeStart', sameDay(day,value.end) && 'rangeEnd', isWithin(day,value) && 'inRange')} onClick={() => chooseDay(day)}>{i + 1}</button> })}
      </div></section>
    })}</div>
  </div>
}

function AvailabilityPicker({ tripRange, value, onChange, readOnly = false }) {
  const days = eachDay(tripRange)
  const chooseDay = day => {
    if (!value.start || value.end) return onChange({ start: day, end: null })
    if (isBefore(day, value.start)) return onChange({ start: day, end: null })
    onChange({ start: value.start, end: day })
  }
  return <div className={cx('availabilityPicker', readOnly && 'readOnly')} aria-readonly={readOnly || undefined}>
    <div><strong>My Availability</strong><small>Select one continuous window within the Trip Dates.</small></div>
    <div className="availabilityDays">{days.map(day => <button type="button" key={dayKey(day)} disabled={readOnly} className={cx(sameDay(day, value.start) && 'rangeStart', sameDay(day, value.end) && 'rangeEnd', isWithin(day, value) && 'inRange')} onClick={readOnly ? undefined : () => chooseDay(day)}><span>{day.toLocaleDateString('en-US', { weekday: 'short' })}</span><strong>{day.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}</strong></button>)}</div>
    <small className="availabilitySummary">{value.start && value.end ? `Available ${formatDateRange(value)}` : 'Choose the first and last date you are available.'}</small>
  </div>
}

const cx = (...classes) => classes.filter(Boolean).join(' ')

function Logo() {
  const location = useLocation()
  const isWorkspaceHome = location.pathname === workspaceHomeHref()

  if (isWorkspaceHome) {
    return <a href="/" target="_top" className="logo brandLogoLink" aria-label="CADENSY home">
      <img className="brandLogoMark" src="/images/cadensy-mark.png" alt="" />
      <img className="brandLogoWordmark" src="/images/cadensy-wordmark.png" alt="CADENSY" />
    </a>
  }

  return <Link to="/" className="logo brandLogoLink" aria-label="CADENSY home">
    <img className="brandLogoMark" src="/images/cadensy-mark.png" alt="" />
    <img className="brandLogoWordmark" src="/images/cadensy-wordmark.png" alt="CADENSY" />
  </Link>
}

function BrandAssets({ className = '' }) {
  return <span className={`brandAssets ${className}`.trim()} aria-hidden="true">
    <img className="brandLogoMark" src="/images/cadensy-mark.png" alt="" />
    <img className="brandLogoWordmark" src="/images/cadensy-wordmark.png" alt="" />
  </span>
}

function Badge({ children, tone = 'neutral' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

function Button({ children, secondary, ghost, className, ...props }) {
  return <button className={cx('btn', secondary && 'btnSecondary', ghost && 'btnGhost', className)} {...props}>{children}</button>
}

function CustomSelect({ label, value, options, onChange, className, readOnly = false }) {
  const [open, setOpen] = useState(false)
  const ref = useClickOutside(open, () => setOpen(false))
  const selected = options.find(option => option.value === value) || options[0]
  return <div className={cx('customSelectField', className, readOnly && 'readOnly')} ref={ref} aria-readonly={readOnly || undefined}>
    <label>{label}</label>
    <button type="button" className={cx('customSelectButton', open && 'open')} aria-readonly={readOnly || undefined} onClick={readOnly ? undefined : () => setOpen(current => !current)}>
      <span>{selected.label}</span>
      {!readOnly && <i aria-hidden="true">⌄</i>}
    </button>
    {!readOnly && open && <div className="customSelectMenu">
      {options.map(option => <button type="button" key={option.value} className={cx(option.value === value && 'selected')} onClick={() => { onChange(option.value); setOpen(false) }}>
        <span>{option.label}</span>
        {option.value === value && <b>✓</b>}
      </button>)}
    </div>}
  </div>
}

function Account() {
  return <div className="account"><ProfileMenu/></div>
}

function ActionBell() {
  const app = useTripApp()
  const [open, setOpen] = useState(false)
  const ref = useClickOutside(open, () => setOpen(false))
  const currentTrip = app.trip || trip || null
  const updatesHref = currentTrip ? tripHref(currentTrip.id, 'updates') : workspaceHomeHref()
  const actions = []
  if (currentTrip) {
    app.activeRounds?.filter(round => round.status === 'open').forEach(round => actions.push({ trip: currentTrip.name, text: `${round.itemTitle || 'A block'} has a group round open`, to: updatesHref }))
    app.activeProposals?.filter(proposal => ['waiting_affected_members', 'escalated'].includes(proposal.status)).forEach(proposal => actions.push({ trip: currentTrip.name, text: proposal.status === 'escalated' ? `${proposal.before?.title || 'A proposal'} is with the organizer` : `${proposal.before?.title || 'A proposal'} is waiting for confirmation`, to: updatesHref }))
  }
  return <div className="actionBellWrap" ref={ref}>
    <button className={cx('actionBell', actions.length && 'hasActions')} type="button" onClick={() => setOpen(current => !current)} aria-label="Action inbox">🔔</button>
    {open && <div className="actionInbox">
      <div className="actionInboxHead"><span>Actions</span><small>{actions.length ? `${actions.length} waiting` : 'Clear'}</small></div>
      {actions.length === 0 && <div className="actionInboxEmpty">No trip actions right now.</div>}
      {actions.map(action => <Link key={action.text} to={action.to} className="actionInboxItem">
        <strong>{action.trip}</strong>
        <span>{action.text}</span>
      </Link>)}
      {actions.length > 0 && <Link className="actionInboxFooter" to={updatesHref}>Open trip actions →</Link>}
    </div>}
  </div>
}

function ProfileMenu() {
  const app = useTripApp()
  const currentUser = app.currentUser
  const [open, setOpen] = useState(false)
  const ref = useClickOutside(open, () => setOpen(false))
  const menuLinks = [
    { to: accountHref('profile'), icon: '◌', label: 'Profile', detail: 'Name, email, account' },
    { to: accountHref('travel'), icon: '✈', label: 'Travel profile', detail: 'Defaults for new trips' },
    { to: accountHref('notifications'), icon: '◍', label: 'Notifications', detail: 'Trip alerts and reminders' },
    { to: accountHref('settings'), icon: '⚙', label: 'Settings', detail: 'Privacy and appearance' },
  ]
  return <div className="profileMenuWrap" ref={ref}>
    <button className="profileButton" type="button" onClick={() => setOpen(current => !current)} aria-label="Profile menu">{currentUser.initials}</button>
    {open && <div className="profileMenu">
      <div className="profileMenuHead"><span className="profilePhoto">{currentUser.initials}</span><div><strong>{currentUser.name}</strong><small>{currentUser.email || (currentUser.isGuest ? 'Guest' : 'No email')}</small></div></div>
      <div className="profileMenuSection">
        {menuLinks.map(link => <Link key={link.to} to={link.to} className="profileMenuItem" onClick={() => setOpen(false)}>
          <span>{link.icon}</span><div><strong>{link.label}</strong><small>{link.detail}</small></div>
        </Link>)}
      </div>
      <button type="button" className="profileMenuSignOut" onClick={() => { setOpen(false); app.logout() }}><span>↪</span><div><strong>Sign out</strong><small>End this session</small></div></button>
    </div>}
  </div>
}

function DashboardCard({ title, location, dates, status, tone, tripCover, detail, to, onOpen, variant = 'compact', action = 'Open trip' }) {
  const app = useTripApp()
  const currentTrip = app.trip || trip || null
  const cover = resolveTripCover({ destination: location, ...(tripCover || {}) })
  const imageUrl = tripCoverImageUrlForVariant(cover.imageUrl, variant)
  const destinationHref = to || (currentTrip ? tripHref(currentTrip.id, 'plan') : workspaceHomeHref())
  return <article className={`dashboardTripCard dashboardTripCard--${variant}`}>
      <div className={cx('tripPhoto', `tripPhoto--${cover.kind}`)} aria-label={cover.label}>
        <Link className="dashboardTripCoverLink" to={destinationHref} onClick={onOpen} aria-label={`Open ${title}`}>
        {imageUrl && <img className="tripCoverImage" src={imageUrl} alt="" loading={variant === 'featured' ? 'eager' : 'lazy'} fetchPriority={variant === 'featured' ? 'high' : 'auto'} decoding="async"/>}
        <Badge tone={tone}>{status}</Badge>
        {!imageUrl && <span className="tripCoverPlaceholder" aria-hidden="true"><i>✦</i><small>Travel cover</small></span>}
        </Link>
        {cover.attribution && <small className="tripCoverAttribution">Photo by <a href={cover.attribution.photographerUrl} target="_blank" rel="noreferrer">{cover.attribution.name}</a> on <a href={cover.attribution.sourceUrl} target="_blank" rel="noreferrer">Unsplash</a></small>}
      </div>
      <Link className="dashboardTripBody dashboardTripBodyLink" to={destinationHref} onClick={onOpen} aria-label={`Open ${title}`}>
        <div className="tripTitle"><h2>{title}</h2>{detail && <span className="attentionDot">{detail}</span>}</div>
        <p>{location} · {dates}</p>
        <div className="cardFooter">{variant !== 'compact' && <span>{detail || 'No action needed'}</span>}<strong>{action} →</strong></div>
      </Link>
  </article>
}

function TripCoverHero({ trip: coverTrip, className, children }) {
  const cover = resolveTripCover(coverTrip || {})
  const imageUrl = tripCoverImageUrlForVariant(cover.imageUrl, 'featured')
  return <section className={cx(className, `tripPhoto--${cover.kind}`)} aria-label={cover.label}>
    {imageUrl && <img className="tripCoverImage" src={imageUrl} alt="" loading="eager" fetchPriority="high" decoding="async"/>}
    {!imageUrl && <span className="tripCoverPlaceholder" aria-hidden="true"><i>✦</i><small>Travel cover</small></span>}
    <div className="tripCoverHeroContent">{children}</div>
    {cover.attribution && <small className="inviteCoverAttribution">Photo by <a href={cover.attribution.photographerUrl} target="_blank" rel="noreferrer">{cover.attribution.name}</a> on <a href={cover.attribution.sourceUrl} target="_blank" rel="noreferrer">Unsplash</a></small>}
  </section>
}

function WorkspaceRouteGuard() {
  const location = useLocation()
  const app = useTripApp()
  const initialPathRef = useRef(location.pathname)
  const restorationResolvedRef = useRef(false)
  const restorationFactsPending = Boolean(
    !restorationResolvedRef.current &&
    location.pathname === initialPathRef.current &&
    app.currentUser &&
    app.hasAccountSession &&
    app.tripSummariesStatus === 'loading',
  )
  const restorationResolution = useMemo(() => {
    if (restorationResolvedRef.current) return null
    if (location.pathname !== initialPathRef.current) return null
    if (!app.currentUser || app.loading.initial) return null
    if (app.hasAccountSession && app.tripSummariesStatus !== 'ready') return null
    return resolveRestoredWorkspaceDestination({
      currentRoutePath: location.pathname,
      hasAccountSession: app.hasAccountSession,
      membershipId: app.membershipId,
      currentUser: app.currentUser,
      tripSummaries: app.tripSummaries,
      activeTrip: app.trip || trip || null,
      activeTripId: app.activeTripId,
      restoredTripId: app.restoredTripId,
    })
  }, [
    app.activeTripId,
    app.hasAccountSession,
    app.currentUser,
    app.loading.initial,
    app.membershipId,
    app.restoredTripId,
    app.trip,
    app.tripSummaries,
    app.tripSummariesStatus,
    location.pathname,
  ])
  const resolution = useMemo(() => resolveCurrentWorkspaceRoute({
    currentRoutePath: location.pathname,
    currentUser: app.currentUser,
    tripSummaries: app.tripSummaries,
    activeTrip: app.trip || trip || null,
    activeTripId: app.activeTripId,
  }), [app.activeTripId, app.currentUser, app.trip, app.tripSummaries, location.pathname])

  useEffect(() => {
    if (restorationResolvedRef.current) return
    if (location.pathname !== initialPathRef.current) {
      restorationResolvedRef.current = true
      return
    }
    if (!app.currentUser || app.loading.initial) return
    if (!app.hasAccountSession || ['ready', 'failed', 'not-needed'].includes(app.tripSummariesStatus)) {
      restorationResolvedRef.current = true
    }
  }, [app.hasAccountSession, app.currentUser, app.loading.initial, app.tripSummariesStatus, location.pathname])

  if (app.loading.initial || (app.hasAccountSession && !app.currentUser)) {
    return null
  }

  if (restorationFactsPending) {
    return null
  }

  if (restorationResolution?.disposition === 'redirect') {
    return <Navigate to={restorationResolution.destinationHref} replace/>
  }

  if (resolution?.disposition === 'redirect') {
    return <Navigate to={resolution.destinationHref} replace/>
  }

  return <Outlet/>
}

function Home() {
  const app = useTripApp()
  const isEmptyAccount = app.hasAccountSession && !app.activeTripId && app.tripSummariesStatus === 'ready' && app.tripSummaries.length === 0
  if (isEmptyAccount) {
    return <main className="homePage">
      <header className="editorialNav"><Logo/><nav><Link className="active" to={workspaceHomeHref()}>MY TRIPS</Link><Link to={workspaceCreateHref()}>NEW TRIP</Link></nav><div className="editorialActions"><ProfileMenu/></div></header>
      <section className="homeContent">
        <div className="dashboardMasthead">
          <div><span className="eyebrow">My trips</span><h1>Start your first shared plan</h1><p>Your account is ready. Create a trip frame, then invite the people planning with you.</p></div>
          <Link className="btn dashboardNewTrip" to={workspaceCreateHref()}>Create first trip</Link>
        </div>
      </section>
    </main>
  }
  // Guests have no account or cross-trip dashboard; direct links send them back to their trip.
  const currentTrip = app.trip || trip || null
  const roundOpen = Boolean(currentTrip && app.activeRounds?.some(round => round.status === 'open'))
  const proposalPending = Boolean(currentTrip && app.activeProposals?.some(proposal => ['waiting_affected_members', 'escalated'].includes(proposal.status)))
  const dashboardTrips = [...(app.tripSummaries || []), ...(app.trips || [])]
    .filter((item, index, all) => item?.id && all.findIndex(candidate => candidate?.id === item.id) === index)
    .filter(item => !currentTrip || item.id !== currentTrip.id)
  const openDashboardTrip = tripSummary => {
    if (!tripSummary?.id || !tripSummary?.membership_id) return
    app.adoptTechnicalTripContext({
      membershipId: tripSummary.membership_id,
      tripId: tripSummary.id,
      profile: {
        id: app.currentUser?.id,
        name: app.currentUser?.name,
        initials: app.currentUser?.initials,
        email: app.currentUser?.email,
        role: tripSummary.my_role || 'participant',
        tripId: tripSummary.id,
        isGuest: false,
      },
    })
  }
  return <main className="homePage">
    <header className="editorialNav"><Logo/><nav><Link className="active" to={workspaceHomeHref()}>MY TRIPS</Link><Link to={workspaceCreateHref()}>NEW TRIP</Link></nav><div className="editorialActions"><ActionBell/><ProfileMenu/></div></header>
    <section className="homeContent">
      <div className="dashboardMasthead">
        <div><span className="eyebrow">My trips</span><h1>Your shared plans</h1><p>Continue the trip that needs you, or revisit another plan.</p></div>
        <Link className="btn dashboardNewTrip" to={workspaceCreateHref()}>New trip</Link>
      </div>
      {currentTrip && <section className="dashboardSection dashboardContinue">
        <div className="dashboardSectionHead"><span>Continue planning</span><small>Current workspace</small></div>
        <DashboardCard title={currentTrip.name} location={currentTrip.destination} dates={currentTrip.dates || 'Dates not set'} status={currentTrip.status} tone="purple" tripCover={currentTrip} detail={roundOpen ? 'Round open' : proposalPending ? 'Confirmation needed' : 'Current plan'} action={roundOpen ? 'Choose an option' : proposalPending ? 'Review change' : 'Review current plan'} variant="featured" to={tripHref(currentTrip.id, 'plan')} />
      </section>}
      {currentTrip && (roundOpen || proposalPending) && <section className="dashboardSection dashboardAttention">
        <div className="dashboardSectionHead"><span>Needs your attention</span><small>Open decisions</small></div>
        <div className="dashboardAttentionList">
          {roundOpen && <Link className="dashboardAlert" to={tripHref(currentTrip.id, 'updates')}><span>01</span><div><strong>A group round is open</strong><p>One block is contested. Pick an option before the round closes.</p></div><b>Choose →</b></Link>}
          {proposalPending && <Link className="dashboardAlert" to={tripHref(currentTrip.id, 'updates')}><span>02</span><div><strong>Your confirmation is needed</strong><p>The Current Plan stays active until affected members respond.</p></div><b>Review →</b></Link>}
        </div>
      </section>}
      <section className="dashboardSection dashboardOthers">
        <div className="dashboardSectionHead"><span>Other trips</span><small>{dashboardTrips.length} workspaces</small></div>
        {dashboardTrips.length > 0
          ? <div className="dashboardGrid">
            {dashboardTrips.map(created => <DashboardCard
              key={created.id}
              title={created.name}
              location={created.destination}
              dates={created.dates || 'Dates not set'}
              status={created.status || 'Planning'}
              tone="orange"
              tripCover={created}
              detail={created.next_item_title && created.next_item_title !== 'Open workspace' ? created.next_item_title : null}
              action="View trip"
              to={tripHref(created.id, 'plan')}
              onOpen={() => openDashboardTrip(created)}
            />)}
          </div>
          : <div className="dashboardEmptyTrips">
            <div className="dashboardEmptyTripsCopy">
              <span className="eyebrow">No saved workspaces</span>
              <h2>No other trips yet</h2>
              <p>Create a trip here, then it will stay on this page for you to reopen later.</p>
            </div>
          </div>}
      </section>
    </section>
  </main>
}

function TripShell({ children }) {
  const location = useLocation()
  const app = useTripApp()
  const currentUser = app.currentUser
  const currentTrip = useCurrentTrip()
  if (!currentTrip) {
    return <MissingTripShell/>
  }
  return <LoadedTripShell app={app} currentUser={currentUser} currentTrip={currentTrip} location={location}>{children}</LoadedTripShell>
}

function MissingTripShell() {
  return <div className="tripPage">
    <header className="tripUnifiedHeader">
      <div className="tripUnifiedBrand"><Link className="brandBack brandBack--return" to={workspaceHomeHref()}><BrandAssets /><span className="backArrow">←</span><span>My Trips</span></Link></div>
      <div className="tripUnifiedCenter"><div className="tripUnifiedTitleRow"><h1>Trip not found</h1></div></div>
      <div className="tripUnifiedRight"><Account/></div>
    </header>
    <main className="workspaceContent">
      <div className="emptyState quietEmptyState"><span></span><h2>No trip loaded</h2><p>This workspace needs a real trip from your account or an invite session.</p><Link className="btn btnSecondary" to={workspaceCreateHref()}>Create trip</Link></div>
    </main>
  </div>
}

function LoadedTripShell({ children, app, currentUser, currentTrip, location }) {
  const pending = (app.activeRounds || []).filter(round => round.status === 'open').length +
    (app.activeProposals || []).filter(proposal => ['waiting_affected_members', 'escalated'].includes(proposal.status)).length
  const navigation = useMemo(() => buildWorkspaceNavigationModel({
    currentRoutePath: location.pathname,
    currentUser,
    activeTrip: currentTrip,
    activeTripId: app.activeTripId,
  }), [app.activeTripId, currentTrip, currentUser, location.pathname])
  // The organizer is not a super-user; they only get extra controls for maintaining the shared frame.
  // Plan, Chat, Updates, and Preferences are identical across the three roles.
  const isGuest = currentUser.isGuest
  return <div className="tripPage">
    <header className="tripUnifiedHeader">
      {/* The trip logo and My Trips used to link to the same place; merge them into one return entry. */}
      <div className="tripUnifiedBrand">
        {!navigation.contextHref
          ? <span className="brandBack" aria-label="Cadensy"><BrandAssets /></span>
          : <Link className="brandBack brandBack--return" to={navigation.contextHref}><BrandAssets /><span className="backArrow">←</span><span>My Trips</span></Link>}
      </div>
      <div className="tripUnifiedCenter">
        <div className="tripUnifiedTitleRow tripUnifiedTitleRow--tabsOnly"><nav className="tripUnifiedTabs" aria-label={`${currentTrip.name || 'Trip'} sections`}>
          {navigation.entries.map(entry => <Link key={entry.id} className={entry.active ? 'active' : ''} to={entry.href}>
            {tripNavigationLabels[entry.id] || entry.id}
            {entry.id === 'updates' && pending > 0 && <i>{pending}</i>}
          </Link>)}
        </nav></div>
      </div>
      <div className="tripUnifiedRight">
        {isGuest ? <SaveToAccount/> : <Account/>}
      </div>
    </header>
    <main className="workspaceContent">
      {children}
    </main>
  </div>
}

// Guest account binding: keep the existing membership, do not create a member, and keep submitted preferences.
function SaveToAccount() {
  const app = useTripApp()
  return <Button secondary onClick={() => app.notify('Account signup connects to the backend later — your membership and preferences carry over')}>Save to account</Button>
}

// Organizer-only. Show only joined/submitted status, never preference content.
function MembersPage() {
  const app = useTripApp()
  const [roster, setRoster] = useState(null)
  const [reminded, setReminded] = useState({})
  const [remindingId, setRemindingId] = useState('')

  useEffect(() => {
    let cancelled = false
    app.loadMembers()
      .then(data => {
        if (cancelled) return
        // Backend answers only whether someone submitted, not what they submitted; this page never sees preference content.
        setRoster((data.members || []).map(member => ({
          id: member.membership_id,
          name: member.name,
          initials: (member.name || '?').split(' ').filter(Boolean).slice(0, 2).map(part => part[0]).join('').toUpperCase(),
          role: member.role,
          isGuest: member.role === 'guest',
          joined: member.joined,
          preferencesSubmitted: member.preferences_submitted,
          isMe: member.is_me,
        })))
      })
      .catch(() => { if (!cancelled) setRoster([]) })
    return () => { cancelled = true }
  }, [app.loadMembers])

  const list = roster || []
  const submitted = list.filter(member => member.preferencesSubmitted).length
  const joined = list.filter(member => member.joined).length
  const remind = async member => {
    setRemindingId(member.id)
    try {
      await app.remindMember(member.id)
      setReminded(current => ({ ...current, [member.id]: true }))
      app.notify('Reminder sent')
    } catch (err) {
      app.notify(err.status === 429 ? 'You can remind each person once every 24 hours.' : 'Could not send the reminder.')
    } finally {
      setRemindingId('')
    }
  }
  return <TripShell>
    <div className="pageHeading editorialPageHeading"><div><span className="eyebrow">Members</span><h1>Who is on this trip</h1></div></div>
    <div className="memberStats">
      <div><strong>{joined}</strong><span>joined</span></div>
      <div><strong>{submitted}</strong><span>preferences in</span></div>
      <div><strong>{Math.max(0, list.length - submitted)}</strong><span>still waiting</span></div>
    </div>
    <section className="memberList">
      {roster === null && <p className="needsHint">Loading the roster...</p>}
      {list.map(member => <article className="memberRow" key={member.id}>
        <span className={cx('memberAvatar', !member.joined && 'pendingAvatar')}>{member.initials}</span>
        <div>
          <h3>{member.isMe ? `${member.name} (you)` : member.name}</h3>
          <p>{member.role === 'organizer' ? 'Organizer' : 'Participant'}{member.isGuest && ' · guest, no account'}{!member.joined && ' · invite not opened yet'}</p>
        </div>
        <span className={cx('memberState', member.preferencesSubmitted ? 'done' : 'waiting')}>{member.preferencesSubmitted ? 'Preferences in' : reminded[member.id] ? 'Reminded' : member.joined ? 'No preferences yet' : 'Not joined'}</span>
        {!member.preferencesSubmitted && !member.isMe && <button className="memberRemind" disabled={reminded[member.id] || remindingId === member.id} onClick={() => remind(member)}>{remindingId === member.id ? 'Sending...' : reminded[member.id] ? 'Reminded' : 'Remind'}</button>}
      </article>)}
    </section>
    <div className="organizerLimits">
      <h3>What you cannot do here</h3>
      <ul>
        <li>Fill in or edit anyone else's preferences</li>
        <li>Treat a non-reply as agreement</li>
        <li>Read hidden preference text — the same rule applies to the organizer</li>
        <li>Confirm a proposal on someone else's behalf</li>
      </ul>
    </div>
  </TripShell>
}

// Countdown ring: remaining time as a share of the full window.
function DeadlineRing({ round, closed }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    if (closed) return
    const timer = window.setInterval(() => setNow(Date.now()), 30000)
    return () => window.clearInterval(timer)
  }, [closed])
  const remaining = Math.max(0, (round.closesAt || now) - now)
  const fraction = closed ? 0 : Math.max(0, Math.min(1, remaining / round.windowMs))
  const hours = Math.floor(remaining / 3600000)
  const minutes = Math.floor((remaining % 3600000) / 60000)
  const label = closed ? 'Applied' : hours > 0 ? `${hours}h ${minutes}m` : `${minutes}m`
  const radius = 15
  const circumference = 2 * Math.PI * radius
  return <div className={cx('deadlineRing', closed && 'done')}>
    <svg viewBox="0 0 36 36" aria-hidden="true">
      <circle className="ringTrack" cx="18" cy="18" r={radius}/>
      <circle className="ringFill" cx="18" cy="18" r={radius} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - fraction)}/>
    </svg>
    <div><small>{closed ? 'Round' : 'Closes in'}</small><strong>{label}</strong></div>
  </div>
}

// Path B UI: a parallel response card, not chat.
function DecisionRoundCard({ round, compact }) {
  const app = useTripApp()
  const navigate = useNavigate()
  const currentTrip = useCurrentTrip()
  const isOrganizer = app.currentUser.role === 'organizer'
  const myVote = round.myVote
  const voteCount = round.responded || 0
  const closed = round.status === 'closed'
  const winner = closed ? round.options.find(option => option.id === round.winningOptionId) : null
  const tally = round.tally || {}
  const leading = Math.max(1, ...Object.values(tally))
  const isReopen = round.kind === 'reopen'
  const planTarget = tripPlanHref(currentTrip.id, round.itemId)
  const extend = async () => {
    try {
      await app.extendRound(round.id)
      app.notify('Round extended')
    } catch (err) {
      app.notify(err.status === 409 ? 'This round has already been extended once.' : 'Could not extend this round.')
    }
  }
  return <article id={`round-card-${round.id}`} data-round-item-id={round.itemId || ''} className={cx('roundCard', compact && 'roundCardCompact', closed && 'roundClosed')}>
    <div className="roundHead">
      <div>
        <Badge tone={closed ? 'green' : 'blue'}>{closed ? 'Round closed' : isReopen ? 'Reopen round' : 'Group round'}</Badge>
        <h3>{closed ? `Settled: ${winner?.title}` : `This block is contested: ${round.itemTitle}`}</h3>
        <p>{closed
          ? 'Applied to the Current Plan. Members who did not respond are recorded as no preference, never as agreement.'
          : isReopen ? 'No response counts as keeping the current decision, so a change needs a clear majority.' : 'Vote on the option, not the person. Ideas stay anonymous while the group chooses what happens to this block.'}</p>
        {isReopen && round.reason && <p><strong>Reason:</strong> {round.reason}</p>}
      </div>
      <DeadlineRing round={round} closed={closed}/>
    </div>
    <div className="roundTally">
      <div className="voterDots" aria-label={`${voteCount} of ${round.totalMembers} responded`}>
        {Array.from({ length: round.totalMembers }, (_, i) => <i key={i} className={cx(i < voteCount && 'filled')}/>)}
      </div>
      <span>{voteCount} of {round.totalMembers} responded{!closed && (isReopen ? ' · no response counts as keeping the current decision' : ' · silence counts as no preference')}</span>
    </div>
    <div className="roundOptions">
      {round.options.map(option => {
        const count = tally[option.id] || 0
        return <button key={option.id} type="button" className={cx('roundOption', myVote === option.id && 'chosen', closed && round.winningOptionId === option.id && 'won')} disabled={closed || app.loading.action} onClick={() => app.castVote(round.id, option.id)}>
          <span className="roundOptionTop"><span className="roundOptionLabel">{option.label}</span>{myVote === option.id && <em>Your pick</em>}</span>
          <strong>{option.title}</strong>
          <small>{option.body}</small>
          <span className="voteBar"><i style={{ width: `${(count / leading) * 100}%` }}/></span>
          <span className="voteCount">{count === 0 ? 'No votes yet' : count === 1 ? '1 vote' : `${count} votes`}</span>
        </button>
      })}
    </div>
    {!closed && <div className="roundFooter">
      <span>Anonymous — cards are choices, not members.</span>
      <div className="roundFooterActions">
        {isOrganizer && <button type="button" className="roundDiscuss" disabled={app.loading.action} onClick={extend}>{app.loading.action ? 'Extending...' : 'Extend'}</button>}
        <button type="button" className="roundDiscuss" onClick={() => navigate(tripHref(currentTrip.id, 'conflict'))}>None of these work — discuss instead</button>
      </div>
    </div>}
    {closed && <div className="roundFooter">
      <span>The round has been settled.</span>
      <Link className="btn btnSecondary" to={planTarget}>View result in plan →</Link>
    </div>}
  </article>
}

function PlanRoute() {
  const navigate = useNavigate()
  const handlePlanFeatureCommand = useCallback(command => {
    if (!command || command.type !== 'navigate' || !command.to) return
    if (command.delayMs) {
      window.setTimeout(() => navigate(command.to), command.delayMs)
      return
    }
    navigate(command.to)
  }, [navigate])

  return <TripShell><PlanFeature onCommand={handlePlanFeatureCommand}/></TripShell>
}

function ChatBubble({ from, children }) {
  const app = useTripApp()
  const currentUser = app.currentUser
  const isUser = from === 'you'
  return <div className={cx('chatBubbleRow', isUser && 'mine')}>
    {!isUser && <span className="chatAvatar tripSync">C</span>}
    <div className={cx('drawerMessage', isUser ? 'mine' : 'assistant')}><strong>{isUser ? 'You' : 'Cadensy'}</strong><p>{children}</p></div>
    {isUser && <span className="chatAvatar user">{currentUser.initials}</span>}
  </div>
}

function ChatWorkspace({ thread }) {
  const app = useTripApp()
  const currentTrip = useCurrentTrip()
  const showTradeoff = Boolean(app.activeProposal && ['waiting_affected_members', 'escalated'].includes(app.activeProposal.status))
  return <TripShell>
    <div className="chatLayout">
      <aside className="conversationList">
        <div className="conversationHead"><span className="eyebrow">Conversations</span><h2>Chat</h2></div>
        <Link className={cx('conversation', thread === 'personal' && 'active')} to={tripHref(currentTrip.id, 'chat')}><span className="aiAvatar">C</span><div><strong>Cadensy</strong><small>Personal planning assistant</small></div></Link>
        {showTradeoff && <Link className={cx('conversation', thread === 'tradeoff' && 'active')} to={tripHref(currentTrip.id, 'conflict')}><span className="pairAvatar anon">◍</span><div><strong>Decision needed</strong><small>Private constraints · affected members only</small></div></Link>}
      </aside>
      {thread === 'tradeoff' && showTradeoff ? <TradeoffThread/> : thread === 'tradeoff' ? <EmptyTradeoffPanel tripId={currentTrip.id}/> : <PersonalThread/>}
    </div>
  </TripShell>
}

function EmptyTradeoffPanel({ tripId }) {
  return <section className="chatPanel">
    <header><div><span className="pairAvatar anon">◍</span><div><h2>Decision needed</h2><p>No active decision</p></div></div></header>
    <div className="messages">
      <div className="emptyState quietEmptyState"><span></span><h2>Nothing to resolve</h2><p>Most changes never need this panel. It opens only when a change touches a hard constraint that needs confirmation or organizer resolution.</p><Link className="btn btnSecondary" to={tripHref(tripId, 'plan')}>Back to plan</Link></div>
    </div>
  </section>
}

function PersonalThread() {
  const app = useTripApp()
  const [draft, setDraft] = useState('')
  const [messages, setMessages] = useState([])
  const [sending, setSending] = useState(false)
  const send = async () => {
    const text = draft.trim()
    if (!text || sending) return
    const loadingId = `ai-loading-${Date.now()}`
    setMessages(current => [...current, { from: 'you', text }, { id: loadingId, from: 'tripSync', text: 'Thinking...', loading: true }])
    setDraft('')
    setSending(true)
    try {
      const history = messages
        .filter(message => !message.loading && message.text)
        .map(message => ({
          role: message.from === 'you' ? 'user' : 'assistant',
          text: message.text,
        }))
      const result = await app.chatWithTrip({ message: text, itemId: null, history })
      setMessages(current => current.map(message => message.id === loadingId ? {
        ...message,
        loading: false,
        from: 'tripSync',
        text: result.proposed_change
          ? `${result.reply} I matched this to ${result.proposed_change.item_title}. Open that item in the plan to review and submit the change.`
          : result.reply,
      } : message))
    } catch (err) {
      const text = err?.status === 409
        ? 'A vote is already open for this time block.'
        : err?.status === 422
          ? 'Reopening this block needs a written reason.'
          : err?.status >= 500
            ? 'The backend hit an error while sending this message. Try again or check the backend logs.'
            : 'I could not reach the backend. Try again in a moment.'
      setMessages(current => current.map(message => message.id === loadingId ? { ...message, text, loading: false, error: true } : message))
    } finally {
      setSending(false)
    }
  }
  return <section className="chatPanel">
    <header><div><span className="aiAvatar">C</span><div><h2>Cadensy</h2><p>Just for you · not shared with the group</p></div></div><Badge>My AI</Badge></header>
    <div className="messages">
      <ChatBubble from="tripSync">This conversation is just between us. Ask about the plan, flag fatigue or weather, or request a change — nothing reaches the group until I have checked what it affects.</ChatBubble>
      {messages.map((message, index) => <ChatBubble from={message.from} key={message.id || `${message.from}-${index}`}>{message.text}</ChatBubble>)}
    </div>
    <div className="chatComposer"><button>＋</button><input value={draft} onChange={event => setDraft(event.target.value)} onKeyDown={event => event.key === 'Enter' && send()} placeholder="Message Cadensy..."/><button className="sendBtn" disabled={sending || !draft.trim()} onClick={send}>{sending ? '…' : '↑'}</button></div>
  </section>
}

// Path C: only affected members plus AI. Everyone except the current user is anonymous.
function TradeoffThread() {
  const app = useTripApp()
  const currentTrip = useCurrentTrip()
  const navigate = useNavigate()
  const isOrganizer = app.currentUser.role === 'organizer'
  const proposal = app.activeProposal
  if (!proposal) return null
  const { before, after, affectedMembers } = proposal
  const planTarget = tripPlanHref(currentTrip.id, proposal.sourceItemId)
  const applied = app.decisionResolved || proposal.status === 'applied'
  const unchanged = ['declined', 'withdrawn', 'expired'].includes(proposal.status)
  const escalated = proposal.status === 'escalated'
  const pending = !applied && !unchanged && proposal.status === 'waiting_affected_members'
  const canRespond = pending && proposal.canDecide && proposal.myStatus !== 'accepted'
  const alreadyAccepted = pending && proposal.myStatus === 'accepted'
  const resolveDeadlock = async action => {
    try {
      await app.resolveDeadlock(proposal.id, action)
      const labels = {
        keep: 'Current plan kept',
        split: 'Block split',
        clear: 'Activity removed',
      }
      app.notify(labels[action] || 'Block resolved')
      navigate(tripHref(currentTrip.id, 'updates'))
    } catch {
      app.notify('Could not resolve this block.')
    }
  }
  return <section className="chatPanel">
    <header><div><span className="pairAvatar anon">◍</span><div><h2>Decision needed</h2><p>{affectedMembers.length} affected members · private constraints hidden</p></div></div><Badge tone={applied ? 'green' : unchanged ? 'blue' : 'orange'}>{applied ? 'Resolved' : unchanged ? 'Closed' : escalated ? 'With organizer' : 'Awaiting confirmation'}</Badge></header>
    <div className="messages conflictMessages">
      <div className="anonBanner"><span>◍</span><p>{proposal.privacyNote}</p></div>
      <div className="message ai"><span>✦</span><div><p>{proposal.headline}. {proposal.detail}</p><p>This is a confirmation step, not a group chat. The Current Plan stays unchanged until the affected members accept or the organizer chooses a resolution.</p></div></div>
      <div className="changeCompare conflictCompare"><div><small>Current{before.dayLabel ? ` · ${before.dayLabel}` : ''}</small><strong>{before.time} · {before.title}</strong><span>{before.place}</span></div><b>→</b><div className="new"><small>Proposed{after.dayLabel ? ` · ${after.dayLabel}` : ''}</small><strong>{after.time} · {after.title}</strong><span>{after.place}</span></div></div>
      <div className="impactRow conflictImpactRow">{affectedMembers.map(member => <span key={member.id}>{applied || member.status === 'accepted' ? `${member.label}: accepted` : unchanged ? `${member.label}: closed` : `${member.label}: needs decision`}{member.isMe ? ' (you)' : ''}</span>)}<span>Names hidden</span><span>Personal reasons hidden</span></div>
      {canRespond && <div className="message ai"><span>✦</span><div><p>Choose one response. Your private reason is not shown to the group.</p><div className="messageActions conflictDecisionActions"><Button secondary disabled={app.loading.action} onClick={() => app.resolveProposal(proposal.id, 'accepted')}>Accept</Button><Button ghost disabled={app.loading.action} onClick={async () => { await app.resolveProposal(proposal.id, 'declined'); app.notify('Current plan kept') }}>Decline change</Button></div></div></div>}
      {alreadyAccepted && <div className="message ai"><span>✦</span><div><p>Your response is accepted. The Current Plan will update after the remaining affected members accept.</p></div></div>}
      {pending && !proposal.canDecide && <div className="message ai"><span>✦</span><div><p>This confirmation is waiting on another affected member. You can review it, but you cannot accept on their behalf.</p></div></div>}
      {escalated && isOrganizer && <div className="message ai"><span>✦</span><div><p>The affected members could not agree. Resolve this without accepting the blocked proposal for anyone else.</p><div className="messageActions conflictDecisionActions"><Button secondary disabled={app.loading.action} onClick={() => resolveDeadlock('keep')}>Keep current</Button><Button ghost disabled={app.loading.action} onClick={() => resolveDeadlock('split')}>Split group</Button><Button ghost disabled={app.loading.action} onClick={() => resolveDeadlock('clear')}>Clear activity</Button></div></div></div>}
      {escalated && !isOrganizer && <div className="message ai"><span>✦</span><div><p>Waiting for the organizer to choose keep current, split group, or clear this activity.</p></div></div>}
      {applied && <div className="message ai resolvedMessage"><span>✓</span><div><p>Every affected member confirmed. The Current Plan is updated and the booking is unchanged.</p><Link className="inlineAction" to={planTarget}>Back to updated plan →</Link></div></div>}
      {unchanged && <div className="message ai resolvedMessage"><span>↩</span><div><p>The proposal is closed. The Current Plan did not change.</p><Link className="inlineAction" to={planTarget}>Back to Current Plan →</Link></div></div>}
    </div>
  </section>
}

function UpdatesPage() {
  const app = useTripApp()
  const navigate = useNavigate()
  const currentTrip = useCurrentTrip()
  const openRounds = (app.activeRounds || []).filter(round => round.status === 'open')
  const pendingProposals = (app.activeProposals || []).filter(proposal => ['waiting_affected_members', 'escalated'].includes(proposal.status))
  const hasActions = openRounds.length > 0 || pendingProposals.length > 0
  return <TripShell>
      <div className="pageHeading editorialPageHeading"><div><h1>Trip notes</h1></div></div>
    <div className="updateFilters editorialUpdateTabs">
      <button className={app.updateFilter === 'all' ? 'active' : ''} onClick={() => app.setUpdateFilter('all')}>All</button>
      <button className={app.updateFilter === 'forYou' ? 'active' : ''} onClick={() => app.setUpdateFilter('forYou')}>For you</button>
      <button className={app.updateFilter === 'actions' ? 'active' : ''} onClick={() => app.setUpdateFilter('actions')}>Actions {hasActions && <i>{openRounds.length + pendingProposals.length}</i>}</button>
    </div>
    {app.loading.initial && <div className="planNotice"><span>…</span><div><strong>Loading updates</strong><p>Fetching actions and notices from the backend.</p></div></div>}
    {app.error && <div className="planNotice"><span>!</span><div><strong>Backend request failed</strong><p>{app.error}</p></div><button type="button" onClick={app.refreshAll}>Retry</button></div>}
    <section className="updatesList">
      {app.updateFilter === 'actions' && <>
        {!hasActions && <div className="emptyState quietEmptyState"><span></span><h2>You're all caught up.</h2><p>No decisions need your attention right now.</p><small>New votes, confirmations, or conflicts will appear here.</small></div>}
        {openRounds.map(round => <DecisionRoundCard key={round.id} round={round}/>)}
        {pendingProposals.map(proposal => <article className="decisionCard" key={proposal.id}>
          <div className="decisionTop"><div><Badge tone="orange">{proposal.status === 'escalated' ? 'With organizer' : 'Needs confirmation'}</Badge><h2>{proposal.headline}</h2><p>{proposal.status === 'escalated' ? 'The affected members could not agree. The organizer can keep current, split group, or clear this activity.' : `${proposal.detail} You proposed this, so you already count as accepted.`}</p></div><span>{proposal.createdAt}</span></div>
          <div className="changeCompare"><div><small>Current{proposal.before.dayLabel ? ` · ${proposal.before.dayLabel}` : ''}</small><strong>{proposal.before.time} · {proposal.before.title}</strong><span>{proposal.before.place}</span></div><b>→</b><div className="new"><small>Proposed{proposal.after.dayLabel ? ` · ${proposal.after.dayLabel}` : ''}</small><strong>{proposal.after.time} · {proposal.after.title}</strong><span>{proposal.after.place}</span></div></div>
          <div className="impactRow">{proposal.affectedMembers.map(member => <span key={member.id}>{member.label}: {member.status === 'accepted' ? 'accepted' : 'needs decision'}</span>)}<span>Names hidden</span></div>
          <div className="decisionActions"><Button onClick={() => navigate(tripHref(currentTrip.id, 'conflict'))}>Review decision</Button>{proposal.status !== 'escalated' && <Button ghost onClick={() => { app.withdrawProposal(proposal.id); app.notify('Hidden — current plan kept') }}>Hide</Button>}</div>
        </article>)}
      </>}
      {app.updateFilter === 'all' && <>
        {(app.baseUpdates || []).length ? (app.baseUpdates || []).map(item => <article className="updateRow" key={item.id}><span className={`updateIcon ${item.kind}`}>{item.icon}</span><div><h3>{item.title}</h3><p>{item.body}</p>{item.canObject && <button className="objectLink" onClick={async () => { await app.objectToNotice(item); app.setUpdateFilter('actions'); app.notify('Escalated to a group round') }}>I have a different idea →</button>}</div><time>{item.time}</time></article>)
          : <div className="emptyState quietEmptyState"><span></span><h2>No updates yet</h2><p>Activity for this trip will appear here once members join and preferences arrive.</p></div>}
      </>}
      {app.updateFilter === 'forYou' && <>
        {(app.personalUpdates || []).length ? <>
          {app.personalUpdates.map(item => <article className="updateRow" key={item.id}><span className={`updateIcon ${item.kind}`}>{item.icon}</span><div><h3>{item.title}</h3><p>{item.body}</p></div><time>{item.time}</time></article>)}
        </> : <div className="emptyState quietEmptyState"><span></span><h2>Nothing for you yet</h2><p>Mentions and replies that involve you will appear here.</p></div>}
      </>}
    </section>
  </TripShell>
}

// Six constraint kinds, but users see natural wording; nobody needs to know what "time_window" means.
// Keep these entry points after AI is ready; add a free-text input that lets AI prefill parameters.
const CONSTRAINT_KINDS = [
  { kind: 'time_window', label: "I can't do early mornings", hint: 'Or late nights' },
  { kind: 'budget_ceiling', label: 'I have a spending limit', hint: 'A ceiling, not a target' },
  { kind: 'walk_limit', label: "I can't walk far", hint: 'Per day' },
  { kind: 'dietary', label: 'I have a dietary requirement', hint: 'Meals must work for me' },
  { kind: 'date_range', label: "I'm only free on certain days", hint: 'Outside this I cannot come' },
  { kind: 'avoid_tag', label: 'There is something I want to avoid', hint: 'Not just a dislike' },
]

const DIET_TAGS = ['vegetarian', 'vegan', 'halal', 'gluten_free']
const AVOID_TAGS = ['museum', 'nightlife', 'outdoor', 'shopping', 'family', 'music', 'seafood']
const VISIBILITY_OPTIONS = [
  { value: 'planning_only', label: 'Only Cadensy' },
  { value: 'organizer', label: 'Organizer too' },
  { value: 'everyone', label: 'Whole group' },
]

const defaultParams = kind => ({
  time_window: { earliest_hour: 9 },
  budget_ceiling: { max_total_per_person: 650 },
  walk_limit: { max_km_per_day: 3 },
  dietary: { required_tags: [] },
  date_range: { start: null, end: null },
  avoid_tag: { tags: [] },
}[kind] || {})

const labelFor = kind => CONSTRAINT_KINDS.find(entry => entry.kind === kind)?.label || kind
const visibilityLabel = value => VISIBILITY_OPTIONS.find(option => option.value === value)?.label || 'Only Cadensy'
const hourText = value => {
  if (value === null || value === undefined || value === '') return null
  const hour = Number(value)
  const suffix = hour >= 12 ? 'PM' : 'AM'
  return `${hour % 12 || 12}:00 ${suffix}`
}
const constraintSummary = entry => {
  const params = entry.params || {}
  if (entry.kind === 'time_window') {
    const parts = []
    const earliest = hourText(params.earliest_hour)
    const latest = hourText(params.latest_hour)
    if (earliest) parts.push(`No activities before ${earliest}`)
    if (latest) parts.push(`No activities after ${latest}`)
    return parts.join('. ') || 'Time limit set'
  }
  if (entry.kind === 'budget_ceiling') return `Trip total must stay at or below $${params.max_total_per_person ?? 0}`
  if (entry.kind === 'walk_limit') return `No more than ${params.max_km_per_day ?? 0} km of walking per day`
  if (entry.kind === 'dietary') return (params.required_tags || []).length ? `Meals must support ${(params.required_tags || []).map(tag => tag.replace('_', ' ')).join(', ')}` : 'Dietary requirement set'
  if (entry.kind === 'date_range') return [params.start, params.end].filter(Boolean).length ? `Available from ${params.start || 'any start'} to ${params.end || 'any end'}` : 'Date limit set'
  if (entry.kind === 'avoid_tag') return (params.tags || []).length ? `Avoid ${(params.tags || []).join(', ')}` : 'Avoidance rule set'
  return 'Requirement set'
}

const constraintDraftProblem = draft => {
  const params = draft?.params || {}
  if (draft?.kind === 'time_window') {
    const earliest = params.earliest_hour
    const latest = params.latest_hour
    if (earliest === null && latest === null) return 'Choose a not-before or not-after time.'
    if (earliest !== null && earliest !== undefined && latest !== null && latest !== undefined && latest < earliest) {
      return 'The end of the time window cannot be earlier than the start.'
    }
    return ''
  }
  if (draft?.kind === 'budget_ceiling') return params.max_total_per_person === null || params.max_total_per_person === undefined ? 'Choose a maximum total budget.' : ''
  if (draft?.kind === 'walk_limit') return params.max_km_per_day === null || params.max_km_per_day === undefined ? 'Choose a daily walking limit.' : ''
  if (draft?.kind === 'dietary') return (params.required_tags || []).length ? '' : 'Choose at least one dietary tag.'
  if (draft?.kind === 'date_range') return params.start || params.end ? '' : 'Choose an available date range.'
  if (draft?.kind === 'avoid_tag') return (params.tags || []).length ? '' : 'Choose at least one thing to avoid.'
  return ''
}

function ConstraintParams({ kind, params, onChange, allowedRange = null }) {
  const set = (key, value) => onChange({ ...params, [key]: value })
  const minDate = toISODate(allowedRange?.start)
  const maxDate = toISODate(allowedRange?.end)
  const toggle = (key, tag) => {
    const list = params[key] || []
    set(key, list.includes(tag) ? list.filter(item => item !== tag) : [...list, tag])
  }
  if (kind === 'time_window') return <div className="constraintParams">
    <label>Not before<input type="number" min="0" max="23" value={params.earliest_hour ?? ''} onChange={e => set('earliest_hour', e.target.value === '' ? null : Number(e.target.value))}/></label>
    <label>Not after<input type="number" min="0" max="24" value={params.latest_hour ?? ''} onChange={e => set('latest_hour', e.target.value === '' ? null : Number(e.target.value))}/></label>
  </div>
  if (kind === 'budget_ceiling') return <div className="constraintParams">
    <label>No more than<input type="number" min="0" value={params.max_total_per_person ?? ''} onChange={e => set('max_total_per_person', Number(e.target.value))}/></label>
  </div>
  if (kind === 'walk_limit') return <div className="constraintParams">
    <label>Km per day<input type="number" min="0" step="0.5" value={params.max_km_per_day ?? ''} onChange={e => set('max_km_per_day', Number(e.target.value))}/></label>
  </div>
  if (kind === 'dietary') return <div className="constraintParams tagRow">
    {DIET_TAGS.map(tag => <button type="button" key={tag} className={cx('tagChip', (params.required_tags || []).includes(tag) && 'on')} onClick={() => toggle('required_tags', tag)}>{tag.replace('_', ' ')}</button>)}
  </div>
  if (kind === 'avoid_tag') return <div className="constraintParams tagRow">
    {AVOID_TAGS.map(tag => <button type="button" key={tag} className={cx('tagChip', (params.tags || []).includes(tag) && 'on')} onClick={() => toggle('tags', tag)}>{tag}</button>)}
  </div>
  return <div className="constraintParams">
    <label>From<input type="date" min={minDate || undefined} max={maxDate || undefined} value={params.start || ''} onChange={e => set('start', e.target.value || null)}/></label>
    <label>Until<input type="date" min={minDate || undefined} max={maxDate || undefined} value={params.end || ''} onChange={e => set('end', e.target.value || null)}/></label>
  </div>
}

function PreferencesPage() {
  const app = useTripApp()
  const currentTrip = useCurrentTrip()
  const navigate = useNavigate()
  const [form, setForm] = useState(app.preferences)
  const [availabilityMode, setAvailabilityMode] = useState('full')
  const [constraints, setConstraints] = useState([])
  const [conflicts, setConflicts] = useState([])
  const [picking, setPicking] = useState(false)
  const [draft, setDraft] = useState(null)
  const [loaded, setLoaded] = useState(false)
  // A plan record can exist before the first generation attempt. Preferences
  // become read-only only once the current plan has actual itinerary items.
  const planGenerated = Boolean(app.planId && (app.days || []).some(day => (day.items || []).length > 0))
  const tripRange = useMemo(() => ({
    start: fromISODate(currentTrip?.preferredStartDate),
    end: fromISODate(currentTrip?.preferredEndDate),
  }), [currentTrip?.preferredEndDate, currentTrip?.preferredStartDate])

  useEffect(() => {
    if (!currentTrip) {
      setLoaded(true)
      return undefined
    }
    let cancelled = false
    app.loadMyPreferences()
      .then(data => {
        if (cancelled) return
        setConstraints(data.constraints || [])
        const pref = data.preference
        const savedAvailability = {
          start: fromISODate(pref?.available_start_date) || tripRange.start,
          end: fromISODate(pref?.available_end_date) || tripRange.end,
        }
        setAvailabilityMode(
          savedAvailability.start && savedAvailability.end
          && sameDay(savedAvailability.start, tripRange.start)
          && sameDay(savedAvailability.end, tripRange.end)
            ? 'full'
            : 'limited'
        )
        setForm(current => ({
          ...current,
          preferredRange: tripRange,
          availableRange: savedAvailability,
          idealBudget: pref?.ideal_budget ?? current.idealBudget,
          maxBudget: pref?.maximum_budget ?? current.maxBudget,
          budgetVisibility: pref?.budget_visibility || current.budgetVisibility,
          pace: pref?.travel_style || current.pace,
          interests: pref?.top_interests?.length ? pref.top_interests : current.interests,
        }))
      })
      .catch(() => {})
      .finally(() => { if (!cancelled) setLoaded(true) })
    return () => { cancelled = true }
  }, [app.loadMyPreferences, currentTrip, tripRange])

  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const toggleStyle = style => setForm(current => {
    const selected = current.interests || []
    if (selected.includes(style)) return { ...current, interests: selected.filter(item => item !== style) }
    if (selected.length >= 3) return { ...current, interests: [...selected.slice(1), style] }
    return { ...current, interests: [...selected, style] }
  })

  const startDraft = kind => {
    setPicking(false)
    setDraft({ kind, params: defaultParams(kind), importance: 'required', original_text: '', visibility: 'planning_only' })
  }

  const commitDraft = async () => {
    if (!draft) return
    const problem = constraintDraftProblem(draft)
    if (problem) {
      app.notify(problem)
      return
    }
    try {
      const result = await app.addConstraint(draft)
      setConstraints(current => [...current, { id: result.id, ...draft }])
      setConflicts(result.conflicts || [])
      setDraft(null)
      app.notify(result.conflicts?.length ? 'Saved — it clashes with parts of the plan' : 'Saved')
    } catch { /* the provider already surfaced it */ }
  }

  const drop = async id => {
    try {
      await app.deleteConstraint(id)
      setConstraints(current => current.filter(entry => entry.id !== id))
      setConflicts(current => current.filter(entry => entry.constraint_id !== id))
    } catch { /* handled upstream */ }
  }

  const save = async () => {
    const availability = availabilityMode === 'full' ? tripRange : form.availableRange
    if (!tripRange.start || !tripRange.end || !availability.start || !availability.end) {
      app.notify('Choose your availability within the Trip Dates')
      return
    }
    try {
      await app.saveMyPreferences({
        preferred_start_date: toISODate(tripRange.start),
        preferred_end_date: toISODate(tripRange.end),
        available_start_date: toISODate(availability.start),
        available_end_date: toISODate(availability.end),
        ideal_budget: Number(String(form.idealBudget).replace(/[^0-9.]/g, '')) || null,
        maximum_budget: Number(String(form.maxBudget).replace(/[^0-9.]/g, '')) || null,
        budget_visibility: form.budgetVisibility === 'planning' ? 'planning_only' : form.budgetVisibility,
        travel_style: form.pace,
        top_interests: form.interests || [],
      })
      app.submitPreferencesFor(currentTrip.id)
      app.notify('Preferences saved · shared anonymously')
      navigate(tripHref(currentTrip.id, 'plan'), { replace: true })
    } catch { /* handled upstream */ }
  }

  if (!currentTrip) return <TripShell />

  return <TripShell>
    <div className={cx('preferenceWrap', 'editorialForm', planGenerated && 'preferenceReadOnly')} aria-readonly={planGenerated || undefined}>
      <div className="pageHeading"><div><span className="eyebrow">My preferences</span><h1>Share only what matters.</h1></div></div>
      <section className="preferenceCard preferenceFlow">
        <div className="wide tripDatesCard"><span>Trip Dates</span><strong>{formatDateRange(tripRange)}</strong><small>Set by trip organizer</small></div>
        <fieldset className="wide availabilityChoice" disabled={planGenerated}><legend>Are you available for the full trip?</legend>
          <label><input type="radio" name="availability" checked={availabilityMode === 'full'} onChange={() => { setAvailabilityMode('full'); set('availableRange', tripRange) }}/><span><strong>Yes, all dates</strong><small>I am available for the full Trip Dates.</small></span></label>
          <label><input type="radio" name="availability" checked={availabilityMode === 'limited'} onChange={() => { setAvailabilityMode('limited'); set('availableRange', { start: null, end: null }) }}/><span><strong>No, I have limited availability</strong><small>I will choose a window inside the Trip Dates.</small></span></label>
        </fieldset>
        {availabilityMode === 'limited' && <div className="wide"><AvailabilityPicker tripRange={tripRange} value={form.availableRange} readOnly={planGenerated} onChange={range => set('availableRange', range)}/></div>}
        <div className="wide fieldPair">
          <label>Ideal total budget<input readOnly={planGenerated} value={form.idealBudget} onChange={e => set('idealBudget', e.target.value)}/></label>
          <label>Maximum acceptable budget<input readOnly={planGenerated} value={form.maxBudget} onChange={e => set('maxBudget', e.target.value)}/></label>
        </div>
        <CustomSelect className="wide" label="Who can see my budget" value={form.budgetVisibility} readOnly={planGenerated} onChange={value => set('budgetVisibility', value)} options={[{ value: 'planning', label: 'Only Cadensy' }, { value: 'organizer', label: 'Organizer too' }, { value: 'everyone', label: 'Whole group' }]}/>
        <CustomSelect className="wide" label="Preferred pace" value={form.pace} readOnly={planGenerated} onChange={value => set('pace', value)} options={['Relaxed', 'Balanced', 'Full schedule'].map(option => ({ value: option, label: option }))}/>
        <div className="wide"><label>Top interests — up to 3</label><div className="styleGrid">{tripStyles.map(style => <button type="button" disabled={planGenerated} key={style} className={cx('styleTile', form.interests?.includes(style) && 'selected')} onClick={planGenerated ? undefined : () => toggleStyle(style)}><span>{style}</span><small>{style === 'Food' ? 'better meals' : style === 'Nature' ? 'parks and views' : style === 'Relaxed' ? 'slower days' : style === 'Culture' ? 'museums and neighborhoods' : 'more active plans'}</small><b className="styleTileCheck" aria-hidden="true">✓</b></button>)}</div></div>

        <div className="wide needsPanel">
          <label>Things that are not negotiable</label>
          <p className="needsHint">Only these structured selections are checked against the plan. Anything else, say it to the group.</p>
          {/* State the privacy promise once, directly above the most private inputs. Repeating it sounds defensive; call out the budget exception explicitly. */}
          <div className="privacyBox"><div><strong>Private by default</strong><p>Nobody sees this — not even the organizer.</p></div><Badge tone="green">Protected</Badge></div>
          {constraints.map(entry => <div className="needRow savedNeed" key={entry.id}>
            <div><strong>{labelFor(entry.kind)}</strong><small>{constraintSummary(entry)}</small><small className="needVisibility">Visible to: {visibilityLabel(entry.visibility)}</small></div>
            <Badge tone={entry.importance === 'required' ? 'orange' : 'blue'}>{entry.importance}</Badge>
            {!planGenerated && <button type="button" className="needRemove" aria-label="Remove" onClick={() => drop(entry.id)}>×</button>}
          </div>)}

          {!planGenerated && draft && <div className="needDraft">
            <strong>{labelFor(draft.kind)}</strong>
            <ConstraintParams kind={draft.kind} params={draft.params} allowedRange={tripRange} onChange={params => setDraft(current => ({ ...current, params }))}/>
            <label>In your own words
              <input value={draft.original_text} placeholder="Optional" onChange={e => setDraft(current => ({ ...current, original_text: e.target.value }))}/>
            </label>
            <CustomSelect label="How strict is this?" value={draft.importance} onChange={value => setDraft(current => ({ ...current, importance: value }))} options={[{ value: 'required', label: 'Not negotiable' }, { value: 'flexible', label: 'Prefer, but flexible' }]}/>
            <CustomSelect label="Who can see this?" value={draft.visibility} onChange={value => setDraft(current => ({ ...current, visibility: value }))} options={VISIBILITY_OPTIONS}/>
            <div className="needDraftActions"><Button disabled={Boolean(constraintDraftProblem(draft))} onClick={commitDraft}>Add</Button><Button ghost onClick={() => setDraft(null)}>Cancel</Button></div>
          </div>}

          {!planGenerated && picking && !draft && <div className="needPicker">
            {CONSTRAINT_KINDS.map(entry => <button type="button" key={entry.kind} onClick={() => startDraft(entry.kind)}>
              <strong>{entry.label}</strong><small>{entry.hint}</small>
            </button>)}
          </div>}

          {!planGenerated && !draft && <button type="button" className="needAdd" onClick={() => setPicking(current => !current)}>{picking ? 'Close' : '＋ Add something that is not negotiable'}</button>}
        </div>

        {conflicts.length > 0 && <div className="wide conflictPanel">
          <strong>This clashes with {conflicts.length} {conflicts.length === 1 ? 'part' : 'parts'} of the plan</strong>
          <p>Nothing was changed. You can adjust those blocks, or relax this requirement — that call is yours.</p>
          <ul>{conflicts.map((entry, index) => <li key={`${entry.constraint_id}-${index}`}>
            <span>{entry.item_title || 'The trip total'}</span>
            <small>{entry.day_date ? formatChangeDay(entry.day_date) : 'Whole trip'}{entry.settledness === 'booked' ? ' · already booked' : entry.settledness === 'settled' ? ' · already settled' : ''}</small>
          </li>)}</ul>
        </div>}

        <div className="formFooter"><span>{loaded ? (planGenerated ? 'Read-only: these preferences were used to generate the current plan.' : '') : 'Loading your preferences...'}</span>{!planGenerated && <Button disabled={app.loading.action} onClick={save}>{app.loading.action ? 'Saving...' : 'Save preferences'}</Button>}</div>
      </section>
    </div>
  </TripShell>
}

function AccountPage({ section = 'profile' }) {
  const app = useTripApp()
  const currentUser = app.currentUser
  const location = useLocation()
  const tabs = useMemo(() => buildWorkspaceNavigationModel({
    currentRoutePath: location.pathname,
    currentUser,
    activeTrip: app.trip || trip || null,
    activeTripId: app.activeTripId,
  }).entries, [app.activeTripId, app.trip, currentUser, location.pathname])
  const pages = {
    profile: {
      eyebrow: 'Account',
      title: 'Profile',
      body: 'Your account identity is used for trip membership and private planning context.',
      cards: [
        ['Display name', currentUser.name],
        ['Email', currentUser.email || 'No email on this account'],
        ['Account type', currentUser.isGuest ? 'Guest access' : 'Cadensy account'],
      ],
    },
    travel: {
      eyebrow: 'Defaults',
      title: 'Travel profile',
      body: 'These defaults will prefill future preference forms without showing private notes to the group.',
      cards: [
        ['Pace', 'Balanced days with room to rest'],
        ['Budget style', 'Mid-range by default'],
        ['Privacy', 'Only Cadensy unless you choose otherwise'],
      ],
    },
    notifications: {
      eyebrow: 'Alerts',
      title: 'Notifications',
      body: 'Choose which trip moments should ask for your attention.',
      cards: [
        ['Decision rounds', 'On'],
        ['Confirmations', 'On'],
        ['Daily summaries', 'Off'],
      ],
    },
    settings: {
      eyebrow: 'Preferences',
      title: 'Settings',
      body: 'Control privacy, display, and account-level defaults for Cadensy.',
      cards: [
        ['Privacy mode', 'Anonymous impact by default'],
        ['Appearance', 'System default'],
        ['Data controls', 'Connects when login is added'],
      ],
    },
  }
  const content = pages[section] || pages.profile
  return <div className="simplePage accountPage">
    <header className="editorialNav glassTop"><Logo/><nav><Link to={workspaceHomeHref()}>MY TRIPS</Link><Link to={workspaceCreateHref()}>NEW TRIP</Link></nav><div className="editorialActions"><ActionBell/><ProfileMenu/></div></header>
    <main className="accountShell">
      <aside className="accountSide">
        <span className="profilePhoto accountPhoto">{currentUser.initials}</span>
        <h2>{currentUser.name}</h2>
        <p>{currentUser.email || 'Guest account'}</p>
        <nav>{tabs.map(tab => <Link key={tab.id} className={tab.active ? 'active' : ''} to={tab.href}>{accountNavigationLabels[tab.id] || tab.id}</Link>)}</nav>
      </aside>
      <section className="accountPanel">
        <span className="eyebrow">{content.eyebrow}</span>
        <h1>{content.title}</h1>
        <p>{content.body}</p>
        <div className="accountCards">
          {content.cards.map(([label, value]) => <div className="accountCard" key={label}><span>{label}</span><strong>{value}</strong></div>)}
        </div>
        <div className="accountNotice"><strong>Coming next</strong><p>These pages are ready for the frontend flow. Saved account settings can connect when login and account APIs are added.</p></div>
      </section>
    </main>
  </div>
}

function CreateTrip() {
  const navigate = useNavigate()
  const app = useTripApp()
  const [dateRange, setDateRange] = useState({ start: null, end: null })
  const [form, setForm] = useState({ name: '', destination: '', theme: '', groupSize: '', currency: 'USD', budget: '', assumptions: '', deadline: '' })
  const set = (key, value) => setForm(current => ({ ...current, [key]: value }))
  const canCreate = form.name.trim() && form.destination.trim() && dateRange.start && dateRange.end
  const [creating, setCreating] = useState(false)
  const createTrip = async () => {
    if (creating) return
    setCreating(true)
    try {
      // Backend currently expects only these fields. It does not store theme or assumptions yet, so do not send them.
      // Avoid implying that unused fields will affect anything.
      const created = await app.createTrip({
        name: form.name.trim(),
        destination: form.destination.trim(),
        preferred_start_date: toISODate(dateRange.start),
        preferred_end_date: toISODate(dateRange.end),
        expected_group_size: Number(form.groupSize) || 1,
        currency: form.currency,
      })
      app.setInviteCopied(false)
      app.notify('Trip created')
      navigate(tripHref(created.id, 'invite'))
    } catch {
      /* provider already surfaced it */
    } finally {
      setCreating(false)
    }
  }
  return <div className="simplePage createPage"><header className="editorialNav glassTop"><Logo/><nav><Link to={workspaceHomeHref()}>MY TRIPS</Link><Link className="active" to={workspaceCreateHref()}>NEW TRIP</Link></nav><div className="editorialActions"><ActionBell/><ProfileMenu/></div></header><main className="createEditorial">
    <section className="createHero"><div><span className="roleChip">Organizer</span><h1>Create the trip frame.</h1><p>Set the destination, date window, group size, and shared assumptions. Guests add their preferences after joining.</p></div><div className="createHeroPhoto"><Badge tone="blue">New trip</Badge></div></section>
    <section className="preferenceCard createGrid createFlow">
      <div className="formChapter wide"><span>01</span><h2>Where and why</h2></div>
      <label>Trip name <span className="requiredMark">· Required</span><input required value={form.name} placeholder="e.g. Summer city weekend" onChange={e => set('name', e.target.value)}/></label><label>Destination <span className="requiredMark">· Required</span><input required value={form.destination} placeholder="e.g. Chicago, Illinois" onChange={e => set('destination', e.target.value)}/></label>
      <label>Trip theme<input value={form.theme} placeholder="e.g. Birthday weekend" onChange={e => set('theme', e.target.value)}/></label><label>Expected group size<input inputMode="numeric" pattern="[0-9]*" value={form.groupSize} placeholder="6" onChange={e => set('groupSize', e.target.value.replace(/[^0-9]/g, ''))}/></label>
      <div className="formChapter wide"><span>02</span><h2>Date window</h2></div>
      <div className="wide dateField"><DateRangePicker required value={dateRange} onChange={setDateRange}/></div>
      <div className="formChapter wide"><span>03</span><h2>Budget and assumptions</h2></div>
      <label>Currency<select value={form.currency} onChange={e => set('currency', e.target.value)}><option>USD</option><option>CAD</option><option>CNY</option><option>EUR</option></select></label><label>Approximate budget<input value={form.budget} placeholder="e.g. $600 per person" onChange={e => set('budget', e.target.value)}/></label>
      <label className="wide">Shared trip assumptions<textarea rows="4" value={form.assumptions} placeholder="e.g. Relaxed pace, central stay, shared dinners." onChange={e => set('assumptions', e.target.value)}/></label>
      <label className="wide">Preferences deadline<input value={form.deadline} placeholder="e.g. Friday, August 7 at 6:00 PM" onChange={e => set('deadline', e.target.value)}/></label>
      <div className="formFooter wide"><span>Guests will choose dates and styles from this frame.</span><Button disabled={!canCreate || creating} onClick={createTrip}>{creating ? "Creating..." : "Create trip"}</Button></div>
    </section>
  </main></div>
}

function InvitePage() {
  const app = useTripApp()
  const navigate = useNavigate()
  const currentTrip = useCurrentTrip()
  const [invite, setInvite] = useState(null)
  const [loadingInvite, setLoadingInvite] = useState(false)
  const [inviteError, setInviteError] = useState('')
  const inviteUrl = invite ? buildTripPreviewAbsoluteUrl(window.location.origin, joinHref(invite.token)) : ''
  useEffect(() => {
    let cancelled = false
    setLoadingInvite(true)
    setInviteError('')
    app.createInvite(currentTrip.id)
      .then(nextInvite => {
        if (!cancelled) setInvite(nextInvite)
      })
      .catch(err => {
        if (!cancelled) setInviteError(err.message || 'Could not create an invite link')
      })
      .finally(() => {
        if (!cancelled) setLoadingInvite(false)
      })
    return () => {
      cancelled = true
    }
  }, [app.createInvite, currentTrip.id])
  const copyLink = () => {
    if (!inviteUrl) return
    if (navigator.clipboard?.writeText) navigator.clipboard.writeText(inviteUrl).catch(() => {})
    app.setInviteCopied(true)
    app.notify('Invite link copied')
  }
  const revokeLink = async () => {
    if (!invite?.invite_id) return
    await app.revokeInvite(invite.invite_id)
    setInvite(null)
    app.setInviteCopied(false)
    app.notify('Invite link revoked')
  }
  return <TripShell>
    <div className="inviteManager">
      <TripCoverHero className="shareHero" trip={currentTrip}>
        <span className="eyebrow">Invite link</span>
        <h1>Share this trip with the group.</h1>
      </TripCoverHero>
      <section className="linkPanel">
        <div><span className="roleChip">{app.inviteCopied ? 'Link copied' : 'Ready to share'}</span><h2>{currentTrip.name}</h2><p>{currentTrip.destination} · {currentTrip.dates}</p></div>
        <label>Invite link<textarea className="inviteLinkField" readOnly rows={2} value={loadingInvite ? 'Creating link...' : inviteError || inviteUrl}/></label>
        <div className="copyActions"><Button disabled={!inviteUrl} onClick={copyLink}>{app.inviteCopied ? 'Copied' : 'Copy link'}</Button><Button secondary onClick={() => navigate(tripHref(currentTrip.id, 'preferences'))}>Start planning</Button>{invite && <Button ghost onClick={revokeLink}>Revoke link</Button>}</div>
        {app.inviteCopied && <div className="copiedState"><strong>Link ready to share</strong></div>}
        {inviteError && <div className="copiedState"><strong>{inviteError}</strong></div>}
      </section>
    </div>
  </TripShell>
}

function JoinInvitePage() {
  const app = useTripApp()
  const navigate = useNavigate()
  const { token } = useParams()
  const [preview, setPreview] = useState(null)
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [loadingInvite, setLoadingInvite] = useState(true)
  const [joining, setJoining] = useState(false)
  const [invalid, setInvalid] = useState(false)
  const [error, setError] = useState('')
  const savedInviteSession = useMemo(() => app.readInviteAdoption(token), [app, token])
  const dateText = preview ? [formatInviteDate(preview.preferred_start_date), formatInviteDate(preview.preferred_end_date)].filter(Boolean).join(' – ') : ''
  useEffect(() => {
    let cancelled = false
    setLoadingInvite(true)
    setInvalid(false)
    app.getInvite(token)
      .then(data => {
        if (cancelled) return
        setPreview(data)
        // Invite cache is only a routing hint. It never contains a credential
        // and therefore must not restore or mint Guest authentication.
      })
      .catch(err => {
        if (!cancelled) {
          setInvalid(err.status === 404)
          setError(err.status === 404 ? '' : (err.message || 'Could not load this invite'))
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingInvite(false)
      })
    return () => {
      cancelled = true
    }
  }, [app.activeTripId, app.adoptTechnicalTripContext, app.getInvite, app.membershipId, savedInviteSession, token])
  const inviteOpenResolution = useMemo(() => {
    if (!token || (!preview && !invalid)) return null
    return resolveInviteJoinRoute({
      currentRoutePath: joinHref(token),
      currentUser: app.currentUser,
      activeTrip: app.trip || trip || null,
      activeTripId: app.activeTripId,
      membershipId: app.membershipId,
      token,
      step: 'open',
      invitePreview: preview,
      inviteErrorStatus: invalid ? 404 : null,
      inviteTripId: savedInviteSession?.tripId || null,
    })
  }, [app.activeTripId, app.currentUser, app.membershipId, app.trip, invalid, preview, savedInviteSession, token])
  useEffect(() => {
    if (inviteOpenResolution?.disposition === 'redirect') {
      navigate(inviteOpenResolution.destinationHref, { replace: true })
    }
  }, [inviteOpenResolution, navigate])
  const join = async withAccount => {
    if (!name.trim()) {
      setError('Enter your name to join this trip.')
      return
    }
    if (withAccount && !email.trim()) {
      setError('Enter an email address to join with an account.')
      return
    }
    setJoining(true)
    setError('')
    try {
      const joined = await app.joinInvite(token, {
        display_name: name.trim(),
        ...(withAccount ? { email: email.trim() } : {}),
      })
      app.adoptTechnicalTripContext({
        membershipId: joined.membership_id,
        tripId: joined.trip_id,
        ...(!withAccount ? { guestToken: joined.guest_token } : {}),
        inviteToken: token,
        profile: {
          name: name.trim(),
          email: withAccount ? email.trim() : null,
          role: joined.role || 'participant',
          isGuest: !withAccount,
        },
      })
      const completionResolution = resolveInviteJoinRoute({
        currentRoutePath: joinHref(token),
        currentUser: {
          tripId: joined.trip_id,
          role: joined.role || 'participant',
          isGuest: !withAccount,
        },
        activeTrip: {
          id: joined.trip_id,
        },
        activeTripId: joined.trip_id,
        membershipId: joined.membership_id,
        token,
        step: 'complete',
        joinResult: joined,
      })
      navigate(completionResolution.destinationHref, { replace: true })
    } catch (err) {
      if (err.status === 404) setInvalid(true)
      else setError(err.message || 'Could not join this trip')
    } finally {
      setJoining(false)
    }
  }
  if (invalid) {
    return <div className="invitePage">
      <header className="inviteGlass"><Logo/></header>
      <main className="inviteLayout">
        <section className="invitePanel">
          <span className="eyebrow">Invite unavailable</span><h2>This link is no longer active</h2>
          <p>Ask the organizer for a new invite link.</p>
        </section>
      </main>
    </div>
  }
  return <div className="invitePage">
    <header className="inviteGlass"><Logo/><div><strong>{preview?.name || 'Cadensy invite'}</strong></div></header>
    <main className="inviteLayout">
      <TripCoverHero className="invitePhoto" trip={preview || {}}><Badge tone="blue">{dateText || 'Trip invite'}</Badge><h1>{preview?.destination || 'Join the group trip'} with the group.</h1></TripCoverHero>
      <section className="invitePanel">
        <span className="eyebrow">Join Cadensy</span><h2>{loadingInvite ? 'Loading invite' : preview?.name || 'You have been invited'}</h2>
        {preview && <p>{preview.destination} · {dateText || 'Dates to be confirmed'} · {preview.member_count} members · Organized by {preview.organizer_name}</p>}
        <label>Your name<input value={name} onChange={event => setName(event.target.value)} placeholder="Name shown in this trip"/></label>
        <Button disabled={joining || loadingInvite || !preview} onClick={() => join(false)}>{joining ? 'Joining...' : 'Continue as guest'}</Button>
        <div className="dividerText">or</div>
        <label>Email<input value={email} onChange={event => setEmail(event.target.value)} placeholder="you@example.com"/></label>
        <Button secondary disabled={joining || loadingInvite || !preview} onClick={() => join(true)}>Join with an account</Button>
        {error && <p className="formError">{error}</p>}
      </section>
    </main>
  </div>
}

export default function FinalApp() {
  return <TripAppProvider><Routes>
    <Route path="*" element={<Navigate to="/" replace/>}/>
    <Route element={<WorkspaceRouteGuard/>}>
      <Route path="/" element={<Home/>}/>
      <Route path="/create" element={<CreateTrip/>}/>
      <Route path="/account/profile" element={<AccountPage section="profile"/>}/>
      <Route path="/account/travel" element={<AccountPage section="travel"/>}/>
      <Route path="/account/notifications" element={<AccountPage section="notifications"/>}/>
      <Route path="/account/settings" element={<AccountPage section="settings"/>}/>
      <Route path="/trip/:tripId/plan" element={<PlanRoute/>}/>
      <Route path="/trip/:tripId/chat" element={<ChatWorkspace thread="personal"/>}/>
      <Route path="/trip/:tripId/conflict" element={<ChatWorkspace thread="tradeoff"/>}/>
      <Route path="/trip/:tripId/updates" element={<UpdatesPage/>}/>
      <Route path="/trip/:tripId/preferences" element={<PreferencesPage/>}/>
      <Route path="/trip/:tripId/members" element={<MembersPage/>}/>
      <Route path="/trip/:tripId/invite" element={<InvitePage/>}/>
    </Route>
    <Route path="/join/:token" element={<JoinInvitePage/>}/>
  </Routes><ScrollToTop/></TripAppProvider>
}
