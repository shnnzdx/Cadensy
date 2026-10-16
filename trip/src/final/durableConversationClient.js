/** Durable Conversation API adapter. It accepts no client history. */

export const conversationIdentityFor = ({ scope, itemId = null }) => {
  if (scope === 'personal') return { scope: 'personal', contextKey: 'personal' }
  if (scope === 'plan_assistant') {
    if (!itemId) return { scope: 'plan_assistant', contextKey: 'global' }
    if (!/^[0-9a-f]{32}$/.test(itemId)) throw new Error('Invalid Plan Item context')
    return { scope: 'plan_assistant', contextKey: `item:${itemId}` }
  }
  throw new Error('Invalid Conversation context')
}

export const createIdempotencyKey = () => (
  globalThis.crypto?.randomUUID?.() || `conversation-${Date.now()}-${Math.random().toString(36).slice(2)}`
)

export const normalizeConversationMessages = payload => (
  [...(payload?.messages || [])]
    .sort((left, right) => left.sequence - right.sequence)
    .map(message => ({
      id: message.id,
      sequence: message.sequence,
      role: message.role,
      text: message.content,
      options: message.options || [],
      selectedPlanItemId: message.selected_plan_item_id || null,
      submission: message.submission ? normalizeSubmission(message.submission) : null,
    }))
)

export const normalizeSubmission = payload => ({
  submissionId: payload.submission_id,
  state: payload.state,
  attemptNumber: payload.attempt_number,
  leaseExpiresAt: payload.lease_expires_at || null,
  assistantMessageId: payload.assistant_message_id || null,
  replayed: Boolean(payload.replayed),
  recoveryAcceptedAttemptNumber: payload.recovery_accepted_attempt_number ?? null,
  recoveryAcceptedFence: payload.recovery_accepted_fence ?? null,
  recoveryState: payload.recovery_state ?? null,
})

export const pendingSubmissionFromMessages = (messages, conversationId) => {
  const pending = [...messages].reverse().find(message => message.submission?.state === 'running')
  return pending ? {
    conversationId,
    submissionId: pending.submission.submissionId,
    leaseExpiresAt: pending.submission.leaseExpiresAt,
    recoveryKey: createIdempotencyKey(),
    restored: true,
  } : null
}

export const createDurableConversationClient = ({ requestJson, tripId }) => {
  const root = `/api/trips/${encodeURIComponent(tripId)}/conversations`
  const resolve = async identity => requestJson(root, {
    method: 'POST', body: JSON.stringify({ scope: identity.scope, context_key: identity.contextKey }),
  })
  const messages = async (conversationId, before = null) => requestJson(
    `${root}/${encodeURIComponent(conversationId)}/messages?limit=50${before === null ? '' : `&before=${encodeURIComponent(before)}`}`,
  )
  return {
    resolve,
    list: ({ scope, contextKey } = {}) => requestJson(`${root}?${new URLSearchParams({
      ...(scope ? { scope } : {}),
      ...(contextKey ? { context_key: contextKey } : {}),
    }).toString()}`),
    get: conversationId => requestJson(`${root}/${encodeURIComponent(conversationId)}`),
    messages,
    status: async (conversationId, submissionId) => normalizeSubmission(await requestJson(`${root}/${encodeURIComponent(conversationId)}/submissions/${encodeURIComponent(submissionId)}`)),
    submit: async (conversationId, { text, selectedPlanItemId = null, idempotencyKey }) => normalizeSubmission(await requestJson(
      `${root}/${encodeURIComponent(conversationId)}/messages`, {
        method: 'POST', headers: { 'Idempotency-Key': idempotencyKey },
        body: JSON.stringify({ text, ...(selectedPlanItemId ? { selected_plan_item_id: selectedPlanItemId } : {}) }),
      },
    )),
    recover: async (conversationId, submissionId, recoveryKey) => normalizeSubmission(await requestJson(
      `${root}/${encodeURIComponent(conversationId)}/submissions/${encodeURIComponent(submissionId)}/recover`, {
        method: 'POST', headers: { 'Idempotency-Key': recoveryKey },
      },
    )),
    delete: conversationId => requestJson(`${root}/${encodeURIComponent(conversationId)}`, { method: 'DELETE' }),
  }
}

export const durableConversationError = error => {
  if (error?.status === 401) return { terminal: true, text: 'Your session has expired. Please sign in again.' }
  if (error?.status === 404) return { terminal: true, text: 'This private conversation is no longer available.' }
  if (error?.status === 409 && error?.message === 'conversation_busy') return { retryable: true, text: 'Cadensy is still working on this conversation.' }
  if (error?.status === 409) return { retryable: true, text: 'This request is already being processed. Reconnecting…' }
  if (error?.status === 422) return { terminal: true, text: 'This message cannot be processed safely.' }
  if (!error?.status) return { retryable: true, text: 'Connection interrupted. We will check the saved request before retrying.' }
  return { retryable: true, text: 'Conversation service is temporarily unavailable.' }
}
