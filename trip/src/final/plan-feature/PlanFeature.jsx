import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTripApp } from '../TripAppState.jsx'
import { trip } from '../tripContent.js'
import TripMap from '../TripMap.jsx'
import { resolveTripCover, tripCoverImageUrlForVariant } from '../trip-cover.js'
import { serializeWorkspaceRoute } from '../../../../shared/trip-navigation-route/index.js'
import { useAssistantChangeRequestFlow } from './useAssistantChangeRequestFlow.js'
import { usePlanInteractionRuntime } from './usePlanInteractionRuntime.js'

const visibleStatus = status => ['Booked', 'Updated'].includes(status) ? status : ''
const statusTone = status => status === 'Booked' ? 'purple' : status === 'Updated' ? 'green' : 'blue'
const cx = (...classes) => classes.filter(Boolean).join(' ')
const tripHref = (tripId, section) => serializeWorkspaceRoute({ kind: 'trip', tripId, section })
const tripPlanHref = (tripId, focusItemId) => {
  const href = tripHref(tripId, 'plan')
  return focusItemId ? `${href}?focus=${encodeURIComponent(focusItemId)}` : href
}

const placeDisplayName = value => ({
  'charlemagne et ses leudes': 'Charlemagne Monument',
}[value?.trim().toLowerCase()] || value)

const usesOnlyLatinLetters = value => {
  const letters = Array.from(value || '').filter(character => /\p{Letter}/u.test(character))
  return letters.length > 0 && letters.every(character => /\p{Script=Latin}/u.test(character))
}

const usefulLocalName = item => {
  const local = item.localTitle?.trim()
  const english = placeDisplayName(item.title)?.trim()
  if (!local || !english || local.toLocaleLowerCase() === english.toLocaleLowerCase()) return null
  return usesOnlyLatinLetters(local) ? null : local
}

const landmarkAnchor = value => {
  const displayed = placeDisplayName(value)
  if (/notre[- ]dame/i.test(displayed)) return 'Notre-Dame'
  return displayed
}

const compactAddress = (value, title, destination, localTitle) => {
  if (!value) return 'Location unavailable'
  const destinationParts = (destination || '').split(',').map(part => part.trim().toLowerCase()).filter(Boolean)
  const city = destinationParts[0] || ''
  const country = destinationParts[destinationParts.length - 1] || ''
  const parts = value.split(',').map(part => part.trim()).filter(Boolean)
  if ([title, localTitle].filter(Boolean).some(name => parts[0]?.toLowerCase() === name.toLowerCase())) parts.shift()
  const inferredCountry = destinationParts.length === 1 && parts.length >= 3
    ? parts[parts.length - 1].toLowerCase()
    : ''
  const conciseParts = parts.filter(part => {
    const normalized = part.toLowerCase()
    return normalized !== city
      && normalized !== country
      && normalized !== inferredCountry
      && !/\b(?:\d{4,6}|\d{3}-\d{4})\b/.test(normalized)
  })
  return conciseParts.slice(0, 2).join(', ') || value
}

const dayDisplayTitle = (day, destination) => {
  if (!day.items.length) return 'Open day'
  const tags = new Set(day.items.flatMap(item => item.tags || []))
  const city = (destination || '').split(',')[0].trim().replace(/\b\w/g, letter => letter.toUpperCase())
  const englishItems = day.items.filter(item => usesOnlyLatinLetters(placeDisplayName(item.title)))
  const titleText = englishItems.map(item => item.title).join(' ')
  const historicItem = englishItems.find(item => /notre[- ]dame|charlemagne|crypt|cathedral|monument|temple|shrine|church|castle|palace|heritage/i.test(item.title))
  const anchor = landmarkAnchor(historicItem?.title || englishItems.find(item => !item.tags?.includes('catering'))?.title)
  const hasWaterfront = tags.has('aquarium') || tags.has('water') || tags.has('marina')
  const hasArts = tags.has('museum') || tags.has('culture')
  if (hasWaterfront && hasArts) return city ? `${city} Waterfront & Arts` : 'Waterfront & Arts'
  if (hasWaterfront) return city ? `${city} Waterfront` : 'Waterfront'
  let theme = city ? `${city} Highlights` : 'City Highlights'
  if (tags.has('heritage') || tags.has('religion') || tags.has('sights') || /notre[- ]dame|charlemagne|crypt|cathedral|monument|temple|shrine|church|castle|palace|heritage/i.test(titleText)) theme = city ? `Historic ${city}` : 'Historic Quarter'
  else if (tags.has('museum') || tags.has('culture')) theme = 'Arts & Culture'
  else if (tags.has('park') || tags.has('garden') || tags.has('natural')) theme = 'Parks & Gardens'
  else if (day.items.filter(item => item.tags?.includes('catering')).length >= 2) theme = city ? `${city} Local Flavors` : 'Local Flavors'
  if (theme.endsWith('Local Flavors')) return theme
  return anchor ? `${anchor} & ${theme}` : theme
}

const formatChangeDay = value => value
  ? new Date(`${value}T00:00:00`).toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' })
  : null

const formatPlanHour = value => {
  if (value === null || value === undefined || value === '') return '—'
  const numeric = Number(value)
  const whole = Math.floor(numeric)
  const minutes = Math.round((numeric - whole) * 60)
  const suffix = whole >= 12 ? 'PM' : 'AM'
  return `${whole % 12 || 12}:${String(minutes).padStart(2, '0')} ${suffix}`
}

const formatPlanEndHour = item => {
  if (item?.startHour === null || item?.startHour === undefined || !item?.durationMin) return null
  return formatPlanHour(Number(item.startHour) + Number(item.durationMin) / 60)
}

const addStopDurationMin = 30
const defaultBlockDurationMin = 90
const quarterHour = 0.25

const roundToQuarter = value => Math.round(value / quarterHour) * quarterHour
const ceilToQuarter = value => Math.ceil((value - Number.EPSILON) / quarterHour) * quarterHour
const floorToQuarter = value => Math.floor((value + Number.EPSILON) / quarterHour) * quarterHour

const addStopWindow = context => {
  if (!context) return { earliest: null, latest: null, options: [] }
  const afterStart = Number(context.afterItem?.startHour)
  const beforeStart = Number(context.beforeItem?.startHour)
  if (!Number.isFinite(afterStart) || !Number.isFinite(beforeStart) || beforeStart <= afterStart) {
    return { earliest: null, latest: null, options: [] }
  }
  const afterEnd = afterStart + (Number(context.afterItem?.durationMin) || defaultBlockDurationMin) / 60
  const latest = beforeStart - addStopDurationMin / 60
  const first = ceilToQuarter(afterEnd)
  const last = floorToQuarter(latest)
  const options = []
  for (let hour = first; hour <= last + Number.EPSILON; hour += quarterHour) {
    options.push(Number(hour.toFixed(2)))
  }
  return { earliest: afterEnd, latest, options }
}

const coordsFor = item => {
  if (!Array.isArray(item?.coords) || item.coords.length < 2) return null
  const [lat, lng] = item.coords.map(Number)
  return Number.isFinite(lat) && Number.isFinite(lng) ? { lat, lng } : null
}

const straightLineMiles = (from, to) => {
  const start = coordsFor(from)
  const end = coordsFor(to)
  if (!start || !end) return null
  const radiusMiles = 3958.8
  const toRad = degrees => degrees * Math.PI / 180
  const dLat = toRad(end.lat - start.lat)
  const dLng = toRad(end.lng - start.lng)
  const a = Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(start.lat)) * Math.cos(toRad(end.lat)) *
    Math.sin(dLng / 2) ** 2
  return radiusMiles * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
}

const formatStraightLineDistance = (from, to) => {
  const miles = straightLineMiles(from, to)
  if (miles === null) return 'Distance unavailable'
  if (miles < 0.1) return 'About 0.1 mi to next stop'
  return `About ${miles.toFixed(miles < 10 ? 1 : 0)} mi to next stop`
}

function AddStopDialog({ context, app, onClose }) {
  const [title, setTitle] = useState('')
  const [startHour, setStartHour] = useState('')
  const [saving, setSaving] = useState(false)
  const [warning, setWarning] = useState('')
  const timeWindow = addStopWindow(context)
  const canChooseTime = timeWindow.options.length > 0

  useEffect(() => {
    if (!context) return
    const currentWindow = addStopWindow(context)
    const preferred = roundToQuarter((currentWindow.earliest + currentWindow.latest) / 2)
    const defaultHour = currentWindow.options.includes(preferred)
      ? preferred
      : currentWindow.options[Math.floor(currentWindow.options.length / 2)]
    setStartHour(defaultHour === undefined ? '' : String(defaultHour))
    setWarning('')
  }, [context?.afterItem?.id, context?.beforeItem?.id])

  if (!context) return null

  const add = async event => {
    event.preventDefault()
    const value = title.trim()
    if (!value || !canChooseTime || startHour === '' || saving) return
    setSaving(true)
    setWarning('')
    try {
      await app.addPlanItem({
        title: value,
        afterItemId: context.afterItem.id,
        beforeItemId: context.beforeItem.id,
        startHour: Number(startHour),
      })
      app.notify(`${value} added to the plan`)
      onClose()
    } catch (error) {
      if (error?.status === 409) {
        setWarning(error.message || "This stop may affect the next activity's time.")
      }
    } finally {
      setSaving(false)
    }
  }

  return <div className="addStopOverlay" role="presentation" onMouseDown={event => event.target === event.currentTarget && onClose()}>
    <form className="addStopDialog" onSubmit={add} role="dialog" aria-modal="true" aria-labelledby="add-stop-title" aria-describedby="add-stop-helper">
      <div className="addStopDialogHead"><div><span className="eyebrow">Add to itinerary</span><h2 id="add-stop-title">Add a stop</h2><p id="add-stop-helper">Add a place or activity between these two stops.</p></div><button type="button" className="addStopClose" aria-label="Close" onClick={onClose}>×</button></div>
      <label className="addStopField">What would you like to add?<input autoFocus value={title} onChange={event => { setTitle(event.target.value); setWarning('') }} placeholder="Search for a place or enter an activity" /></label>
      <label className="addStopField addStopTimeField">Start time
        <select value={startHour} disabled={!canChooseTime} onChange={event => { setStartHour(event.target.value); setWarning('') }}>
          {timeWindow.options.map(hour => <option key={hour} value={hour}>{formatPlanHour(hour)}</option>)}
        </select>
      </label>
      <div className="addStopPosition"><span>Between</span><strong>{context.afterItem.time}</strong><i>→</i><strong>{context.beforeItem.time}</strong><small>{context.day.label}</small></div>
      <p className="addStopTimeHint">{canChooseTime ? `Available times keep a ${addStopDurationMin}-minute stop inside this gap.` : `There is not enough open time for a ${addStopDurationMin}-minute stop here.`}</p>
      {warning && <p className="addStopWarning" role="alert">{warning}</p>}
      <div className="addStopActions"><button type="button" className="btn btnGhost" onClick={onClose}>Cancel</button><button type="submit" className="btn" disabled={!title.trim() || !canChooseTime || startHour === '' || saving}>{saving ? 'Adding...' : 'Add to plan'}</button></div>
    </form>
  </div>
}

const totalRouteMiles = items => items.reduce((total, item, index) => {
  if (index === items.length - 1) return total
  return total + (straightLineMiles(item, items[index + 1]) || 0)
}, 0)

const fieldLabels = {
  title: 'Title',
  place: 'Place',
  start_hour: 'Time',
  day_date: 'Date',
  duration_min: 'Duration',
  price_per_person: 'Price',
  settledness: 'Status',
}

const formatPatchValue = (key, value) => {
  if (key === 'start_hour') return formatPlanHour(value)
  if (key === 'day_date') return formatChangeDay(value) || value
  if (key === 'duration_min') return `${value} min`
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

const formatChangeSummary = change => {
  const entries = Object.entries(change.patch || {})
    .filter(([key]) => !['lat', 'lng', 'photo_url'].includes(key))
  if (!entries.length) return 'Recorded the decision without changing visible fields.'
  return entries
    .map(([key, value]) => `${fieldLabels[key] || key}: ${formatPatchValue(key, value)}`)
    .join(' · ')
}

const formatChangeTime = value => {
  if (!value) return ''
  return new Date(value).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
}

const originLabel = origin => ({
  notice: 'Direct change',
  round: 'Vote result',
  reopen_round: 'Reopened vote',
  confirm: 'Confirmed change',
  booking: 'Booking status',
  organizer_resolution: 'Organizer resolution',
}[origin] || origin || 'Change')

const decisionPresentation = {
  notice: {
    summary: 'No conflicts found. This change can apply now.',
    status: 'NOTICE · No approval needed',
    action: 'Apply change',
  },
  round: {
    summary: 'There is another preference affecting this change. A group decision is needed.',
    status: 'GROUP DECISION · Group input needed',
    action: 'Start group decision',
  },
  reopen_round: {
    summary: 'This settled choice needs group input before it can change.',
    status: 'GROUP DECISION · Group input needed',
    action: 'Start group decision',
  },
  confirm: {
    summary: 'This change affects a confirmed booking or required constraint. Approval is needed before the plan changes.',
    status: 'CONFIRMATION NEEDED · Affected member approval required',
    action: 'Request approval',
  },
}

const decisionPresentationFor = (path, memberCount) => {
  const presentation = decisionPresentation[path] || decisionPresentation.notice
  if (path !== 'confirm' || Number(memberCount || 0) > 1) return presentation
  return {
    ...presentation,
    summary: 'This affects a confirmed booking or required constraint. Because this trip only has one member, applying it updates the plan now.',
    status: 'BOOKING CHANGE · Solo trip applies directly',
    action: 'Apply change',
  }
}

function Badge({ children, tone = 'neutral' }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

function Button({ children, secondary, ghost, className, ...props }) {
  return <button className={cx('btn', secondary && 'btnSecondary', ghost && 'btnGhost', className)} {...props}>{children}</button>
}

function usePlanCurrentTrip() {
  const { tripId } = useParams()
  const app = useTripApp()
  return app.trips.find(item => item.id === tripId) || app.trip || trip || null
}

const tripCityLabel = currentTrip => (currentTrip?.destination || 'Destination not set').split(',')[0].trim() || currentTrip?.destination || 'Destination not set'

function PlanCoverMasthead({ currentTrip }) {
  const cover = resolveTripCover(currentTrip || {})
  const imageUrl = tripCoverImageUrlForVariant(cover.imageUrl, 'featured')
  const label = cover.label || `${currentTrip.destination || 'Trip'} cover`
  return <section className={cx('planCoverMasthead', imageUrl && 'hasImage')} aria-label={label}>
    {imageUrl && <img className="planCoverImage" src={imageUrl} alt="" loading="eager" />}
    <div className="planCoverOverlay" aria-hidden="true"/>
    {cover.attribution && <small className="planCoverAttribution">Photo by <a href={cover.attribution.photographerUrl} target="_blank" rel="noreferrer">{cover.attribution.name}</a> on <a href={cover.attribution.sourceUrl} target="_blank" rel="noreferrer">Unsplash</a></small>}
  </section>
}

const categoryPresentation = item => {
  if (item.isMeal) return { label: (item.mealType || 'meal').toUpperCase(), icon: 'utensils' }
  const text = `${item.title || ''} ${item.place || ''} ${(item.tags || []).join(' ')}`.toLowerCase()
  if (/aquarium|seabed/.test(text)) return { label: 'Aquarium', icon: 'wave' }
  if (/water|river|waterfront|marina/.test(text)) return { label: 'Attraction', icon: 'wave' }
  if (/museum/.test(text)) return { label: 'Museum', icon: 'museum' }
  if (/park|garden|arboretum|botanic|nature|leaf/.test(text)) return { label: 'Park / Garden', icon: 'leaf' }
  if (/gallery|art/.test(text)) return { label: 'Art / Gallery', icon: 'gallery' }
  if (/historic|history|heritage|monument|memorial|landmark|cathedral|church|temple|palace|castle|shrine/.test(text)) return { label: 'Historic Site', icon: 'landmark' }
  if (/tourism|attraction|sights|viewpoint|zoo|planetarium/.test(text)) return { label: 'Attraction', icon: 'landmark' }
  return { label: 'Place', icon: 'pin' }
}

function CategoryGlyph({ icon }) {
  if (icon === 'utensils') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 3v7M4.8 3v7M9.2 3v7M4.8 10h4.4M7 10v11M16.5 3v18M14 3h5v8.5a2.5 2.5 0 0 1-2.5 2.5"/></svg>
  if (icon === 'museum') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9h16M5 20h14M6 9l6-4 6 4M7 10v8M12 10v8M17 10v8"/></svg>
  if (icon === 'leaf') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19c9 0 14-5 14-14-9 0-14 5-14 14Z"/><path d="M5 19c3-5 7-8 14-14"/></svg>
  if (icon === 'gallery') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4z"/><path d="m7 16 3.5-4 2.5 3 2-2.2 2.5 3.2"/><circle cx="9" cy="9" r="1.2"/></svg>
  if (icon === 'wave') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 15c2.2 0 2.2-2 4.4-2s2.2 2 4.4 2 2.2-2 4.4-2 2.2 2 4.4 2"/><path d="M3 19c2.2 0 2.2-2 4.4-2s2.2 2 4.4 2 2.2-2 4.4-2 2.2 2 4.4 2"/></svg>
  if (icon === 'landmark') return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 21h14M7 18h10M8 10v8M12 10v8M16 10v8M4 9h16l-8-5Z"/></svg>
  return <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s6-5.4 6-11a6 6 0 1 0-12 0c0 5.6 6 11 6 11Z"/><circle cx="12" cy="10" r="2"/></svg>
}

function StopCategoryIcon({ item }) {
  const presentation = categoryPresentation(item)
  return <div className={cx('stopCategoryIcon', item.isMeal && 'mealCategoryIcon')} aria-hidden="true"><CategoryGlyph icon={presentation.icon}/></div>
}

function NewTripPlan({ currentTrip }) {
  const app = useTripApp()
  const [progress, setProgress] = useState(null)
  const [generateError, setGenerateError] = useState('')
  const [blockedReason, setBlockedReason] = useState('')
  const [blockedCode, setBlockedCode] = useState('')
  const isOrganizer = app.currentUser.role === 'organizer'
  const onboarding = currentTrip.onboarding || {}
  const organizerPreferenceStatus = currentTrip.organizerPreference?.status || onboarding.organizer_preference?.status

  useEffect(() => {
    let cancelled = false
    app.loadMembers()
      .then(data => {
        if (cancelled) return
        const members = data.members || []
        setProgress({
          total: data.total ?? members.length,
          submitted: data.submitted ?? members.filter(member => member.preferences_submitted).length,
          meSubmitted: Boolean(members.find(member => member.is_me)?.preferences_submitted),
        })
      })
      .catch(() => {
        if (!cancelled) setProgress({ total: currentTrip.people || 1, submitted: 0, meSubmitted: false })
      })
    return () => { cancelled = true }
  }, [app.loadMembers, currentTrip.people])

  const total = Math.max(1, progress?.total || currentTrip.people || 1)
  const submitted = Math.min(total, progress?.submitted || 0)
  const missing = Math.max(0, total - submitted)
  const meSubmitted = Boolean(progress?.meSubmitted) || (isOrganizer && organizerPreferenceStatus === 'complete')
  const canGenerate = isOrganizer && meSubmitted && !app.loading.action
  const progressText = `${submitted} of ${total} people have shared what they need.`
  const visibleBlockedReason = blockedReason || app.planBlockedReason || ''
  const visibleBlockedCode = blockedCode || (
    visibleBlockedReason.toLowerCase().includes('no usable places')
      ? 'NO_PLACE_CANDIDATES'
      : visibleBlockedReason.toLowerCase().includes('destination')
        ? 'DESTINATION_NOT_FOUND'
        : visibleBlockedReason ? 'CONSTRAINTS_BLOCKED' : ''
  )
  const generationBlocked = Boolean(visibleBlockedReason)
  const destinationBlocked = visibleBlockedCode === 'DESTINATION_NOT_FOUND'
  const placesBlocked = visibleBlockedCode === 'NO_PLACE_CANDIDATES'
  const budgetBlocked = visibleBlockedReason.toLowerCase().includes('budget')
  const dateBlocked = visibleBlockedReason.toLowerCase().startsWith('trip dates are missing or invalid')
  const blockedHelp = destinationBlocked
    ? 'Check the destination name and try again.'
    : placesBlocked
      ? 'Try again when place data is available for this destination.'
      : budgetBlocked
        ? 'Raise the maximum budget, remove the budget ceiling, or choose cheaper places.'
        : dateBlocked
          ? 'Set a valid trip date range, then try generating again.'
          : 'Review the required constraints and available places, then try generating again.'
  const headline = generationBlocked
    ? destinationBlocked
      ? "We couldn't find this destination"
      : placesBlocked
        ? "We couldn't find usable places for this destination"
        : 'The requirements blocked this itinerary'
    : isOrganizer
      ? meSubmitted ? 'Ready to generate' : 'Share your preferences first'
      : meSubmitted ? 'You are ready' : 'Share your preferences first'
  const body = generationBlocked
    ? 'Cadensy could not build a valid plan from the current requirements.'
    : isOrganizer
      ? meSubmitted
        ? missing > 0
          ? `${progressText} You can generate now, but missing hard requirements will not be checked.`
          : 'Everyone has shared preferences. You can generate the first itinerary.'
        : 'Your own requirements need to be included before Cadensy creates the group plan.'
      : meSubmitted
        ? 'Your preferences are saved. The organizer will generate the itinerary when the group is ready.'
        : 'Add your preferences so the organizer can generate a plan that checks your requirements.'
  const statusLabel = generationBlocked ? 'Blocked' : missing === 0 ? 'Ready' : `${missing} waiting`
  const primaryAction = isOrganizer
    ? meSubmitted
      ? { kind: 'generate', label: app.loading.action ? 'Generating...' : 'Generate itinerary' }
      : { kind: 'preferences', label: 'Fill my preferences' }
    : { kind: 'preferences', label: meSubmitted ? 'Review preferences' : 'Fill my preferences' }

  const generate = async () => {
    setGenerateError('')
    setBlockedReason('')
    setBlockedCode('')
    try {
      const result = await app.generatePlan()
      if (result.status === 'blocked') {
        setBlockedReason(result.blocked_reason || 'The required budget limit is too low for the available places.')
        setBlockedCode(result.blocked_code || '')
        return
      }
      app.notify('Itinerary generated')
    } catch (err) {
      if (err.code === 'organizer_preference_missing' || err.status === 422) setGenerateError('Fill your preferences before generating the first itinerary.')
      else if (err.status === 409) setGenerateError('An itinerary already exists.')
      else setGenerateError('Could not generate the itinerary. Try again in a moment.')
    }
  }

  return <>
    <div className="pageHeading setupPageHeading compactSetupHeading">
      <div>
        <span className="eyebrow">Current Plan</span>
        <h1>No itinerary yet</h1>
      </div>
    </div>
    <section className="setupCompactPanel">
      <div className="setupCompactTop">
        <div>
          <Badge tone={generationBlocked ? 'orange' : missing === 0 ? 'green' : 'blue'}>{isOrganizer ? 'Organizer step' : 'Waiting for organizer'}</Badge>
          <h2>{headline}</h2>
          <p>{submitted} of {total} preferences in</p>
        </div>
        <span className={cx('setupCompactCount', missing === 0 && !generationBlocked && 'done', generationBlocked && 'blocked')}>{statusLabel}</span>
      </div>

      <p className="compactLead">{body}</p>

      <div className="compactActionRow">
        {primaryAction.kind === 'generate'
          ? <Button disabled={!canGenerate} onClick={generate}>{primaryAction.label}</Button>
          : <Link className="btn" to={tripHref(currentTrip.id, 'preferences')}>{primaryAction.label}</Link>}
        {primaryAction.kind === 'generate' && <Link className="btn btnSecondary" to={tripHref(currentTrip.id, 'preferences')}>Edit preferences</Link>}
        {isOrganizer && <Link className="btn btnSecondary" to={tripHref(currentTrip.id, 'invite')}>Invite people</Link>}
        {isOrganizer && meSubmitted && <Link className="btn btnSecondary" to={tripHref(currentTrip.id, 'members')}>Check members</Link>}
      </div>

      {isOrganizer && !meSubmitted && <p className="compactHint">Only the organizer can generate the first itinerary, but your preferences must be in first.</p>}
      {(visibleBlockedReason || generateError) && <div className="setupError compactError">
        {visibleBlockedReason && <><strong>{visibleBlockedReason}</strong><p>{blockedHelp}</p></>}
        {generateError && <p className="formError">{generateError}</p>}
      </div>}
    </section>
  </>
}

function PlanDecisionRoundCard({ round, compact, onCommand }) {
  const app = useTripApp()
  const currentTrip = usePlanCurrentTrip()
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
        <button type="button" className="roundDiscuss" onClick={() => onCommand?.({ type: 'navigate', to: tripHref(currentTrip.id, 'conflict') })}>None of these work — discuss instead</button>
      </div>
    </div>}
    {closed && <div className="roundFooter">
      <span>The round has been settled.</span>
      <Link className="btn btnSecondary" to={planTarget}>View result in plan →</Link>
    </div>}
  </article>
}

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

function PlanChatBubble({ from, children }) {
  const app = useTripApp()
  const currentUser = app.currentUser
  const isUser = from === 'you'
  return <div className={cx('chatBubbleRow', isUser && 'mine')}>
    {!isUser && <span className="chatAvatar tripSync">C</span>}
    <div className={cx('drawerMessage', isUser ? 'mine' : 'assistant')}><strong>{isUser ? 'You' : 'Cadensy'}</strong><p>{children}</p></div>
    {isUser && <span className="chatAvatar user">{currentUser.initials}</span>}
  </div>
}

function ChangeConfirmCard({ message, proposedChange, currentItem, showRecognizedItem, onApply, onDismiss }) {
  const app = useTripApp()
  const memberCount = app.trip?.people || 1
  const verdict = proposedChange.verdict
  const patch = proposedChange.patch || {}
  const before = {
    title: currentItem?.title || proposedChange.item_title,
    place: currentItem?.place || '',
    time: currentItem?.time || formatPlanHour(currentItem?.startHour),
    day: formatChangeDay(currentItem?.dayDate),
  }
  const after = {
    title: patch.title || before.title,
    place: patch.place || before.place,
    time: patch.start_hour !== undefined ? formatPlanHour(patch.start_hour) : before.time,
    day: formatChangeDay(patch.day_date || currentItem?.dayDate),
  }
  const presentation = decisionPresentationFor(verdict.path, memberCount)
  const isRemoval = patch.remove === true
  const changedField = isRemoval
    ? { label: 'Remove', before: before.title, after: 'Removed from itinerary' }
    : patch.start_hour !== undefined
    ? { label: 'Time', before: before.time, after: after.time }
    : patch.day_date
      ? { label: 'Day', before: before.day || 'Current day', after: after.day || 'Proposed day' }
      : patch.title
        ? { label: 'Activity', before: before.title, after: after.title }
        : patch.place
          ? { label: 'Place', before: before.place || before.title, after: after.place || after.title }
          : { label: 'Change', before: before.time, after: after.time }

  return <div className={cx('changeConfirmCard', message.applied && 'done')}>
    {showRecognizedItem && <div className="recognizedItem"><span>Cadensy matched this to</span><strong>{proposedChange.item_title}</strong></div>}
    <div className="changeConfirmHead">
      <div><span>{changedField.label} change</span><h3>{proposedChange.item_title}</h3>{before.place && <p>{before.place}</p>}</div>
      {message.applied && <Badge tone="green">Done</Badge>}
    </div>
    <div className="changeCompare assistantChangeCompare">
      <div><small>Current</small><strong>{changedField.before}</strong></div>
      <b>→</b>
      <div className="new"><small>Proposed</small><strong>{changedField.after}</strong></div>
    </div>
    <div className={cx('decisionStatus', `decisionStatus--${verdict.path}`)}>{presentation.status}</div>
    {message.applyError && <p className="assistantError">{message.applyError}</p>}
    <div className="changeDecisionActions">
      <button className="changePrimaryAction" onClick={onApply} disabled={message.applied || message.applying}>{message.applied ? 'Applied' : message.applying ? 'Applying...' : presentation.action}</button>
      {!message.applied && <button className="changeCancelAction" onClick={onDismiss}>Cancel</button>}
    </div>
  </div>
}

// Compromise options the assistant drafted alongside the requested change.
// Picking one re-checks it on its own before anything is submitted, so a member
// can find a change that needs no group decision without opening a round first.
function CandidateOptionList({ message, onSelect }) {
  const options = message.candidateOptions || []
  if (!options.length || message.applied) return null
  const busy = message.applying || message.classifyingCandidate
  return <div className="assistantOptions">
    <span className="assistantOptionsLabel">Cadensy also drafted</span>
    <div className="assistantOptionList">
      {options.map(option => <button
        key={option.id}
        type="button"
        className={cx('roundOption', 'assistantOption', message.selectedCandidateId === option.id && 'chosen')}
        disabled={busy}
        onClick={() => onSelect(message, option)}
      >
        <strong>{option.title || option.label || 'Alternative'}</strong>
        {(option.tradeoff || option.body) && <span className="assistantOptionTradeoff">{option.tradeoff || option.body}</span>}
      </button>)}
    </div>
    {message.classifyingCandidate && <p className="assistantOptionNote">Checking that option...</p>}
    {message.selectionNote && <p className="assistantOptionNote">{message.selectionNote}</p>}
  </div>
}

function replacementPatch(candidate) {
  return {
    title: candidate.title,
    local_title: candidate.local_title || null,
    place: candidate.place,
    ...(candidate.lat !== null && candidate.lat !== undefined ? { lat: candidate.lat } : {}),
    ...(candidate.lng !== null && candidate.lng !== undefined ? { lng: candidate.lng } : {}),
    ...(candidate.photo_url ? { photo_url: candidate.photo_url } : {}),
    ...(candidate.price_per_person !== null && candidate.price_per_person !== undefined ? { price_per_person: candidate.price_per_person } : {}),
    ...(Array.isArray(candidate.tags) && candidate.tags.length ? { tags: candidate.tags } : {}),
  }
}

function ReplacePlacePanel({ item, onCommand, onResolvedOutcome }) {
  const app = useTripApp()
  const [query, setQuery] = useState('')
  const [candidates, setCandidates] = useState([])
  const [selectedId, setSelectedId] = useState('')
  const [loading, setLoading] = useState(false)
  const [applying, setApplying] = useState(false)
  const [error, setError] = useState('')
  const selected = candidates.find(candidate => candidate.candidate_id === selectedId) || null

  const search = async (nextQuery = query) => {
    setLoading(true)
    setError('')
    try {
      const result = await app.searchReplacementPlaces({ itemId: item.id, query: nextQuery })
      const nextCandidates = result.candidates || []
      setCandidates(nextCandidates)
      setSelectedId(current => nextCandidates.some(candidate => candidate.candidate_id === current) ? current : nextCandidates[0]?.candidate_id || '')
    } catch (err) {
      setError(err.message || 'Could not load replacement places.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    search('')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item.id])

  const applyReplacement = async () => {
    if (!selected || applying) return
    const patch = replacementPatch(selected)
    setApplying(true)
    setError('')
    try {
      const request = `Replace ${item.title} with ${selected.title}`
      const verdict = await app.classify({ item, actionType: 'replacePlace', request, patch })
      const outcome = await app.submitChange({ item, actionType: 'replacePlace', request, verdict, patch })
      if (!outcome) return
      if (outcome.applied) {
        app.notify('Place replaced')
        onResolvedOutcome?.({ kind: 'focus-item', itemId: item.id, outcome, targetItem: item })
      } else if (outcome.path === 'round' || outcome.path === 'reopen_round') {
        app.notify('Vote opened')
        onResolvedOutcome?.({ kind: 'focus-round', itemId: item.id, outcome, targetItem: item })
      } else {
        app.setUpdateFilter?.('actions')
        const tripId = app.activeTripId || app.trip?.id || trip?.id
        if (tripId) onCommand?.({ type: 'navigate', to: tripHref(tripId, 'updates'), delayMs: 850 })
      }
    } catch (err) {
      setError(err.message || 'Could not replace this place.')
    } finally {
      setApplying(false)
    }
  }

  return <div className="replacePlacePanel">
    <div className="replaceSearchRow">
      <input value={query} onChange={event => setQuery(event.target.value)} onKeyDown={event => event.key === 'Enter' && search()} placeholder="Search coffee, museum, park..."/>
      <Button secondary disabled={loading} onClick={() => search()}>{loading ? 'Searching...' : 'Search'}</Button>
    </div>
    <p className="replacePlaceHint">Candidates come from the place provider/cache and are checked against this stop's current time when hours are known.</p>
    {error && <p className="formError">{error}</p>}
    <div className="replaceCandidateList">
      {candidates.map(candidate => <button type="button" key={candidate.candidate_id} className={cx('replaceCandidate', selectedId === candidate.candidate_id && 'selected')} onClick={() => setSelectedId(candidate.candidate_id)}>
        {candidate.photo_url ? <img src={candidate.photo_url} alt=""/> : <span className="replaceCandidateFallback">{candidate.title?.slice(0, 1) || 'P'}</span>}
        <div><strong>{candidate.title}</strong><small>{candidate.place || 'Location unavailable'}</small>{candidate.tags?.length > 0 && <em>{candidate.tags.slice(0, 3).join(' · ')}</em>}</div>
      </button>)}
      {!loading && candidates.length === 0 && <div className="emptyState quietEmptyState replaceEmpty"><span></span><h2>No candidates found</h2><p>Try a broader keyword, or ask Cadensy for ideas from the chat button.</p></div>}
    </div>
    <div className="replacePlaceFooter">
      <span>{selected ? `Selected: ${selected.title}` : 'Select a replacement place'}</span>
      <Button disabled={!selected || applying || app.loading.action} onClick={applyReplacement}>{applying ? 'Replacing...' : 'Replace place'}</Button>
    </div>
  </div>
}

function AssistantDrawer({ item, mode, onClose, onCommand, onResolvedOutcome, inline = false }) {
  const app = useTripApp()
  const memberCount = app.trip?.people || 1
  const actionLabels = {
    global: 'Ask Cadensy',
    ask: 'Ask Cadensy',
    editTime: 'Edit time',
    moveDay: 'Move to another day',
    replacePlace: 'Replace place',
    removePlan: 'Remove from plan',
    details: 'Explain',
  }
  const {
    view,
    actions,
  } = useAssistantChangeRequestFlow({
    item,
    mode,
    onCommand,
    onResolvedOutcome,
  })

  const detailRows = mode === 'details' ? [
    { section: 'Schedule', label: 'Date', value: item.dayDate ? formatChangeDay(item.dayDate) : null },
    { section: 'Schedule', label: 'Starts', value: item.time },
    { section: 'Schedule', label: 'Ends', value: formatPlanEndHour(item) },
    { section: 'Details', label: 'Type', value: categoryPresentation(item).label },
    { section: 'Details', label: 'Address', value: item.place },
    { section: 'Details', label: 'Duration', value: item.durationMin ? `${item.durationMin} min` : null },
    { section: 'Details', label: 'Description', value: item.description || item.note },
    { section: 'Status', label: 'Status', value: item.status || (item.locked ? 'Existing reservation' : 'Not booked') },
  ].filter(row => row.value) : []
  const detailSections = ['Schedule', 'Details', 'Status'].map(section => ({
    section,
    rows: detailRows.filter(row => row.section === section),
  })).filter(group => group.rows.length)

  const drawer = <aside className={cx('assistantDrawer', inline && 'inlineAssistant')} onClick={event => event.stopPropagation()}>
      <header><div><span className="eyebrow">{actionLabels[mode]}</span><h2>{item.title}</h2><p>{item.place} · {item.time}</p></div><button type="button" onClick={onClose}>×</button></header>
      <div className="drawerThread" ref={view.threadRef}>
        {mode !== 'details' && mode !== 'replacePlace' && <><div className="assistantBubbleRail"><i/><i/><i/></div><PlanChatBubble from="tripSync">{mode === 'global' ? 'Ask me about the itinerary, or tell me what you want to adjust. If I can identify the item, I will show the change before anything is submitted.' : 'Ask me about this item, or tell me a change in your own words. I will check it first and show exactly what would be submitted.'}</PlanChatBubble></>}
        {view.messages.map(message => <div key={message.id}>
          <PlanChatBubble from={message.from}>{message.proposedChange ? decisionPresentationFor(message.proposedChange.verdict?.path, memberCount).summary : message.text}</PlanChatBubble>
          {message.proposedChange && <ChangeConfirmCard
            message={message}
            proposedChange={message.proposedChange}
            currentItem={view.itemById[message.proposedChange.item_id] || (item.id === message.proposedChange.item_id ? item : null)}
            showRecognizedItem={mode === 'global'}
            onApply={() => actions.applyProposal(message, message.proposedChange)}
            onDismiss={() => actions.dismissProposal(message.id)}
          />}
          <CandidateOptionList message={message} onSelect={actions.selectCandidateOption} />
        </div>)}
        {view.durable.nextBefore !== null && <button type="button" className="changeCancelAction" onClick={actions.loadOlderMessages}>Load earlier messages</button>}
        {view.sending && <PlanChatBubble from="tripSync">Request saved. Waiting for Cadensy…</PlanChatBubble>}
        {view.durable.error && <div className="assistantError"><p>{view.durable.error.text}</p>{view.durable.error.retryable && <button type="button" className="changeCancelAction" onClick={actions.retrySavedRequest}>Retry saved request</button>}{view.durable.pending?.submissionId && <button type="button" className="changeCancelAction" onClick={actions.recoverSavedRequest}>Recover expired request</button>}</div>}
        {!view.durable.loading && !view.durable.runtimeAvailable && <p className="assistantOptionNote">Assistant delivery is unavailable here. You can review saved history, but new messages cannot be sent.</p>}
        {mode === 'details' && <div className="detailSheet">{detailSections.map(group => <section key={group.section}><h3>{group.section}</h3><dl>{group.rows.map(row => <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl></section>)}</div>}
        {mode === 'replacePlace' && <ReplacePlacePanel item={item} onCommand={onCommand} onResolvedOutcome={onResolvedOutcome}/>}
        {view.pendingRedirect && <p className="redirectHint">{view.pendingRedirect}</p>}
      </div>
      {mode !== 'details' && mode !== 'replacePlace' && <div className="drawerComposer"><input ref={view.inputRef} value={view.draft} disabled={!view.durable.runtimeAvailable} onChange={event => actions.updateDraft(event.target.value)} onKeyDown={event => event.key === 'Enter' && actions.sendMessage()} placeholder={view.placeholder}/><button type="button" className="changeCancelAction" aria-label="Delete this Plan Assistant conversation" disabled={!view.durable.conversation || view.sending} onClick={() => { if (window.confirm('Delete this private Plan Assistant conversation? It will no longer be available.')) actions.deleteConversation() }}>Delete chat</button><button aria-label="Send message" disabled={view.sending || !view.durable.runtimeAvailable || !view.draft.trim()} onClick={actions.sendMessage}>{view.sending ? '...' : '↑'}</button></div>}
    </aside>
  if (inline) return drawer
  return <div className="drawerOverlay" onClick={onClose}>{drawer}</div>
}

export default function PlanFeature({ onCommand }) {
  const currentTrip = usePlanCurrentTrip()
  if (!currentTrip) {
    return <div className="emptyState quietEmptyState">
      <span></span>
      <h2>No trip loaded</h2>
      <p>This plan needs a real trip from your account or an invite session.</p>
    </div>
  }
  return <LoadedPlanFeature currentTrip={currentTrip} onCommand={onCommand}/>
}

function LoadedPlanFeature({ currentTrip, onCommand }) {
  const {
    view,
    actions,
  } = usePlanInteractionRuntime({ currentTrip })
  const app = view.app
  const isOrganizer = app.currentUser?.role === 'organizer'

  if (!app.loading.initial && view.days.length === 0) return <NewTripPlan currentTrip={currentTrip}/>

  return <>
  <PlanCoverMasthead currentTrip={currentTrip}/>
  <div className={cx('planSplit', !view.drawerItem && 'withMap', view.drawerItem && 'withAssistant')}>
    <section className="planMainPane">
      <div className="pageHeading planHeading"><div className="planSummaryIntro"><span className="eyebrow">{tripCityLabel(currentTrip)}</span><h1>{currentTrip.name || 'Untitled trip'}</h1><p>{currentTrip.dates || 'Dates not set'}</p><div className="planSummaryStats"><span><b>{view.days.length}</b> days</span><span><b>{view.days.reduce((total, day) => total + day.items.length, 0)}</b> stops</span><span><b>{view.days.reduce((total, day) => total + day.items.filter(item => !item.isMeal).length, 0)}</b> activities</span><span><b>{view.days.reduce((total, day) => total + day.items.filter(item => item.isMeal).length, 0)}</b> meals</span></div></div><div className="planCollaboratorStrip"><span>Collaborators</span><div className="collaboratorMeta"><div className="collaboratorAvatars"><i>{app.currentUser?.initials || 'You'}</i>{(currentTrip.people || 1) > 1 && <i className="collaboratorCount">+{(currentTrip.people || 1) - 1}</i>}</div><span className="collaboratorMemberCount">{currentTrip.people || 1} {(currentTrip.people || 1) === 1 ? 'member' : 'members'}</span></div>{isOrganizer && <Link to={tripHref(currentTrip.id, 'members')}>Manage members →</Link>}</div></div>
      {app.loading.initial && <div className="planNotice"><span>…</span><div><strong>Loading trip data</strong><p>Fetching the current plan from the backend.</p></div></div>}
      {app.error && <div className="planNotice"><span>!</span><div><strong>Backend request failed</strong><p>{app.error}</p></div><button type="button" onClick={app.refreshAll}>Retry</button></div>}
      {app.planNeedsRefresh && <div className="planNotice planRefreshNotice"><span>↻</span><div><strong>Preferences updated</strong><p>Your current plan was generated using earlier preferences and has not been changed. Future replans and change proposals will use the latest planning inputs.</p></div><Link to={tripHref(currentTrip.id, 'preferences')}>Review →</Link></div>}
      {app.conflictCreated && !app.decisionResolved && <Link className="planNotice" to={tripHref(currentTrip.id, 'updates')}><span>!</span><div><strong>Proposed change waiting for confirmation</strong><p>A hard constraint is involved. The current plan remains active until the affected members accept.</p></div><b>Review →</b></Link>}
      {app.decisionResolved && <div className="successNotice"><span>✓</span><div><strong>The plan was updated</strong><p>Every affected member confirmed. Bookings elsewhere in the plan are unchanged.</p></div></div>}
      <div className="accordionPlan">
        {view.days.map(day => {
          const open = view.openDays.includes(day.id)
          const dayMenuOpen = day.items.some(item => item.id === view.menuOpen)
          const sightseeingItems = day.items.filter(item => !item.isMeal)
          const mealItems = day.items.filter(item => item.isMeal)
          return <section className={cx('accordionDay', open && 'open', dayMenuOpen && 'menuOpen')} key={day.id}>
            <button className="accordionHead" onClick={() => actions.toggleDay(day.id)} aria-expanded={open}>
              <span className="dayHeaderIndex"><small>Day</small><b>{day.label.replace(/[^0-9]/g, '') || '—'}</b><em>{day.date}</em></span><span className="dayHeaderMain"><h2>{dayDisplayTitle(day, currentTrip.destination)}</h2></span><span className="dayHeaderStats"><b>{sightseeingItems.length} activities</b><b>{mealItems.length} meals</b><b>{day.items.length} stops</b></span><i>{open ? '−' : '+'}</i>
            </button>
            <div className="accordionBody"><div className="accordionInner">
              <div className="activityBlocks">{day.items.map((item, index) => <div className={cx('activityBlockGroup', view.menuOpen === item.id && 'menuOpen', index === day.items.length - 1 && 'lastStop')} key={item.id}>
                <article id={`trip-item-${item.id}`} className={cx('activityBlock', item.isMeal && 'mealStopBlock', item.settledness === 'booked' && 'booked', view.selectedTripItemId === item.id && 'selected', view.highlightedItemId === item.id && 'updatedFlash')} onClick={() => actions.selectPlanItem(item.id)}>
                  <button type="button" className={cx('activityIndex', item.isMeal && 'mealStopIndex', view.historyOpen === item.id && 'open')} aria-label="Show change history" onClick={event => { event.stopPropagation(); actions.toggleHistory(item.id) }}><b>{item.isMeal ? 'M' : day.items.slice(0, index).filter(previous => !previous.isMeal).length + 1}</b></button>
                  <div className="activityMain"><div className="activityPlaceLine"><StopCategoryIcon item={item}/><div className="activityTitle"><div><h3>{placeDisplayName(item.title)}</h3>{usefulLocalName(item) && <span className="activityLocalName">{usefulLocalName(item)}</span>}<small>{categoryPresentation(item).label} · {compactAddress(item.place, item.title, currentTrip.destination, item.localTitle)}</small></div>{visibleStatus(item.status) && <Badge tone={statusTone(item.status)}>{visibleStatus(item.status)}</Badge>}</div></div>{item.note && <p>{item.note}</p>}{item.locked && <small className="lockedNote">Existing reservation</small>}</div>
                  <time className="activityStartTime" dateTime={String(item.startHour ?? '')}>{item.time}</time>
                  <div className="activityActions"><button className="itemIconAction" title="Discuss" onClick={() => actions.toggleCommentComposer(item.id)}>💬{(view.comments[item.id] || []).length > 0 && <i>{view.comments[item.id].length}</i>}</button><button className="itemIconAction" title="Ask Cadensy" onClick={() => actions.openDrawer(item, 'ask', day)}>✦</button><div className="moreWrap"><button className="moreBtn" onClick={() => actions.toggleMenu(item.id)}>•••</button>{view.menuOpen === item.id && <div className="actionMenu"><button onClick={() => actions.openDrawer(item, 'editTime', day)}>Edit time</button><button onClick={() => actions.openDrawer(item, 'replacePlace', day)}>Replace place</button><button disabled={app.loading.action} onClick={() => actions.toggleBooked(item)}>{item.settledness === 'booked' ? 'Remove booked status' : 'Mark as booked'}</button><button onClick={() => actions.openDrawer(item, 'removePlan', day)}>Remove from plan</button></div>}</div></div>
                  {(view.comments[item.id] || []).length > 0 && <div className="publicThread">{view.comments[item.id].map((comment, i) => <div key={comment.id || `${item.id}-${i}`}><span>{comment.initials || comment.name.slice(0,2).toUpperCase()}</span><p><strong>{comment.name}</strong>{comment.text}</p></div>)}</div>}
                  {view.historyOpen === item.id && (view.changeHistory[item.id] || []).length > 0 && <div className="itemHistoryPanel">
                    <div className="itemHistoryHead"><strong>Change history</strong><span>{(view.changeHistory[item.id] || []).length} records</span></div>
                    <ol>{view.changeHistory[item.id].map(change => <li key={change.id}>
                      <span>{originLabel(change.origin)}</span>
                      <p>{formatChangeSummary(change)}</p>
                      <small>{formatChangeTime(change.applied_at)}{change.reason ? ` · ${change.reason}` : ''}</small>
                    </li>)}</ol>
                  </div>}
                  {view.commenting === item.id && <div className="publicComposer"><label>Group note</label><textarea rows="2" value={view.commentDraft} onChange={e => actions.updateCommentDraft(e.target.value)} placeholder="Whole group can see this note." />{view.commentError && <p className="formError">{view.commentError}</p>}<div><button onClick={actions.cancelCommentComposer}>Cancel</button><Button disabled={app.loading.action || !view.commentDraft.trim()} onClick={() => actions.submitComment(item.id)}>{app.loading.action ? 'Posting...' : 'Post note'}</Button></div></div>}
                </article>
                {app.activeRounds?.filter(round => round.itemId === item.id || round.itemTitle === item.title).map(round => <PlanDecisionRoundCard key={round.id} round={round} compact onCommand={onCommand}/>)}
                {index < day.items.length - 1 && <div className="routeSegment">
                  <span>Next stop</span>
                  <strong>{formatStraightLineDistance(item, day.items[index + 1])}</strong>
                  <button type="button" onClick={() => actions.openAddStop(day, item, day.items[index + 1])}>+ Add stop</button>
                </div>}
              </div>)}</div>
            </div></div>
          </section>
        })}
      </div>
    </section>
    {!view.drawerItem && <aside className="tripMapRail" aria-label="Trip route overview">
      <div className="tripMapCard">
        <div className="mapDayTabs">
          <button type="button" className={view.railDay === 'all' ? 'active' : ''} onClick={actions.showAllOnMap}>All</button>
          {view.days.map(day => <button type="button" key={day.id} className={view.railDay === day.id ? 'active' : ''} onClick={() => actions.showDayOnMap(day.id)}>{day.label.split(' · ')[0]}</button>)}
        </div>
        <TripMap days={view.railDays} destination={currentTrip.destination} selectedItemId={view.selectedTripItemId} onSelectItem={actions.handleSelectTripItem} variant="real" markerMode={view.railDay === 'all' ? 'day' : 'stop'}/>
        <div className="tripMapSummary">
          <strong>{view.railDay === 'all' ? `${view.days.reduce((n, d) => n + d.items.length, 0)} stops across ${view.days.length} days` : `${view.railDays[0]?.items.length || 0} stops · ${view.railDays[0]?.date || ''}`}</strong>
          <p>Tap a pin to jump to that stop.</p>
        </div>
        <div className="dayContextCard">
          <span className="eyebrow">Day at a glance</span>
          <strong>{view.railDay === 'all' ? 'Full trip overview' : `${view.railDays[0]?.label || 'Selected day'}`}</strong>
          <div className="dayContextStats"><span><b>{view.railDay === 'all' ? view.days.reduce((n, d) => n + d.items.length, 0) : (view.railDays[0]?.items.length || 0)}</b> stops</span><span><b>{view.railDay === 'all' ? view.days.length : (view.railDays[0]?.items.length ? totalRouteMiles(view.railDays[0].items).toFixed(1) : '0')}</b> {view.railDay === 'all' ? 'days' : 'mi route'}</span></div>
          <p>Select a day above to focus the route and scan its stops.</p>
        </div>
      </div>
    </aside>}
    {view.drawerItem && <AssistantDrawer item={view.drawerItem} mode={view.drawerMode} onClose={actions.closeDrawer} onCommand={onCommand} onResolvedOutcome={resolution => {
      if (resolution.kind === 'focus-item') {
        actions.closeDrawer()
        actions.focusPlanItem(resolution.itemId)
      } else if (resolution.kind === 'focus-round') {
        actions.closeDrawer()
        actions.focusPlanItem(resolution.itemId, 'round')
      }
    }} inline/>}
  </div>
  <AddStopDialog context={view.addStopContext} app={app} onClose={actions.closeAddStop}/>
  </>
}
