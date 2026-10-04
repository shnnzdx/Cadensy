import { SESSION_RUNTIME_CODES } from '../../../shared/session-runtime/index.js'

export const classifyTechnicalSessionInvalidation = ({ scope, facts, status }) => {
  if (status !== 401) return null

  if (facts?.kind === 'guest') {
    return SESSION_RUNTIME_CODES.invalidation.MEMBERSHIP_CREDENTIALS_INVALID
  }

  if (facts?.kind === 'account') {
    return SESSION_RUNTIME_CODES.invalidation.ACCOUNT_CREDENTIALS_INVALID
  }

  return null
}
