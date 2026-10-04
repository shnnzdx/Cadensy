import { createSessionRuntime } from '../../../shared/session-runtime/index.js'

const defaultSessionRuntime = createSessionRuntime()

export const restoreTripAppBootstrapState = ({
  sessionRuntime = defaultSessionRuntime,
} = {}) => {
  const { facts, restorationHint } = sessionRuntime.restoreTechnicalSession()

  return {
    hasAccountSession: facts.kind === 'account',
    membershipId: facts.kind === 'none' ? '' : facts.membershipId || '',
    restoredTripId: restorationHint?.tripId || '',
    activeTripId: facts.kind === 'none' ? '' : facts.activeTripId || '',
  }
}
