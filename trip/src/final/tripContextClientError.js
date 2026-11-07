/**
 * Presentation for the decided trip-context detail strings.
 *
 * The HTTP body field is a string `detail`. These helpers key off that
 * string after it has been copied onto `error.message`. They do not read a
 * nested code field, and they do not invalidate a technical session.
 */

export const TRIP_CONTEXT_REQUIRED_DETAIL = 'trip_context_required'
export const TRIP_CONTEXT_MISMATCH_DETAIL = 'trip_context_mismatch'

const TRIP_CONTEXT_REQUIRED_TEXT = 'This action needs a selected trip.'
const TRIP_CONTEXT_MISMATCH_TEXT = 'The selected trip does not match this page.'

export const messageFromResponseBody = (body, status) => (
  typeof body?.detail === 'string' ? body.detail : `Request failed (${status})`
)

export const tripContextClientText = error => {
  if (error?.status === 400 && error?.message === TRIP_CONTEXT_REQUIRED_DETAIL) {
    return TRIP_CONTEXT_REQUIRED_TEXT
  }
  if (error?.status === 409 && error?.message === TRIP_CONTEXT_MISMATCH_DETAIL) {
    return TRIP_CONTEXT_MISMATCH_TEXT
  }
  return null
}

export const friendlyError = err => {
  const tripContext = tripContextClientText(err)
  if (tripContext) return tripContext
  if (err?.status === 409) return 'A vote is already open for this time block.'
  if (err?.status === 422) return err.message || 'Reopening this block needs a written reason.'
  if (err?.status >= 500) return err.message || 'The backend hit an error while processing this request.'
  if (!err?.status) return 'Could not reach the backend.'
  return err.message || 'Something went wrong.'
}

export const extendRoundFailureNotice = err => (
  tripContextClientText(err)
  || (err?.status === 409
    ? 'This round has already been extended once.'
    : 'Could not extend this round.')
)

export const assistantChangeErrorText = err => {
  const tripContext = tripContextClientText(err)
  if (tripContext) return tripContext
  if (err?.status === 409) return 'A vote is already open for this time block.'
  if (err?.status === 422) return 'Reopening this block needs a written reason.'
  if (err?.status >= 500) return 'The backend hit an error while applying this change. Try again or check the backend logs.'
  return 'I could not reach the backend. Try again in a moment.'
}

export const addStopWarningText = error => {
  const tripContext = tripContextClientText(error)
  if (tripContext) return tripContext
  if (error?.status === 409) return error.message || "This stop may affect the next activity's time."
  return null
}

export const generateItineraryErrorText = err => {
  const tripContext = tripContextClientText(err)
  if (tripContext) return tripContext
  if (err?.code === 'organizer_preference_missing' || err?.status === 422) {
    return 'Fill your preferences before generating the first itinerary.'
  }
  if (err?.status === 409) return 'An itinerary already exists.'
  return 'Could not generate the itinerary. Try again in a moment.'
}
