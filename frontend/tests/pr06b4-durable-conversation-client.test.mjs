import assert from 'node:assert/strict'
import test from 'node:test'
import { readFileSync } from 'node:fs'

import {
  conversationIdentityFor,
  claimPendingRequest,
  createDurableConversationClient,
  durableConversationError,
  failedSubmissionError,
  latestSubmissionFromMessages,
  normalizeConversationMessages,
  pendingSubmissionFromMessages,
  submissionDisposition,
} from '../../trip/src/final/durableConversationClient.js'

test('durable conversation client selects only approved server-owned context keys', () => {
  assert.deepEqual(conversationIdentityFor({ scope: 'personal' }), { scope: 'personal', contextKey: 'personal' })
  assert.deepEqual(conversationIdentityFor({ scope: 'plan_assistant' }), { scope: 'plan_assistant', contextKey: 'global' })
  assert.deepEqual(conversationIdentityFor({ scope: 'plan_assistant', itemId: 'a'.repeat(32) }), {
    scope: 'plan_assistant', contextKey: `item:${'a'.repeat(32)}`,
  })
  assert.throws(() => conversationIdentityFor({ scope: 'plan_assistant', itemId: 'untrusted' }))
})

test('durable conversation client submits one message with an idempotency key and never client history', async () => {
  const calls = []
  const client = createDurableConversationClient({
    tripId: 'trip id',
    requestJson: async (path, options = {}) => { calls.push({ path, options }); return { ok: true } },
  })
  await client.resolve({ scope: 'personal', contextKey: 'personal' })
  await client.list({ scope: 'personal', contextKey: 'personal' })
  await client.get('conversation id')
  await client.messages('conversation id', 9)
  await client.resolveOption('conversation id', 'assistant message id', 'option id')
  await client.submit('conversation id', { text: 'hello', selectedPlanItemId: 'f'.repeat(32), idempotencyKey: 'submission-key' })
  await client.recover('conversation id', 'submission id', 'recovery-key')
  await client.delete('conversation id')

  assert.equal(calls[0].path, '/api/trips/trip%20id/conversations')
  assert.deepEqual(JSON.parse(calls[0].options.body), { scope: 'personal', context_key: 'personal' })
  assert.equal(calls[1].path, '/api/trips/trip%20id/conversations?scope=personal&context_key=personal')
  assert.equal(calls[2].path, '/api/trips/trip%20id/conversations/conversation%20id')
  assert.equal(calls[3].path, '/api/trips/trip%20id/conversations/conversation%20id/messages?limit=50&before=9')
  assert.equal(calls[4].path, '/api/trips/trip%20id/conversations/conversation%20id/messages/assistant%20message%20id/options/option%20id/resolve')
  assert.equal(calls[4].options.method, 'POST')
  assert.equal(calls[5].options.headers['Idempotency-Key'], 'submission-key')
  assert.deepEqual(JSON.parse(calls[5].options.body), { text: 'hello', selected_plan_item_id: 'f'.repeat(32) })
  assert.equal(calls[5].options.body.includes('history'), false)
  assert.equal(calls[6].options.headers['Idempotency-Key'], 'recovery-key')
  assert.equal(calls[7].options.method, 'DELETE')
})

test('durable history is presented in ascending server sequence order', () => {
  assert.deepEqual(normalizeConversationMessages({ messages: [
    { id: 'b', sequence: 2, role: 'assistant', content: 'second', options: [] },
    { id: 'a', sequence: 1, role: 'user', content: 'first', options: [] },
  ] }), [
    { id: 'a', sequence: 1, role: 'user', text: 'first', options: [], selectedPlanItemId: null, submission: null },
    { id: 'b', sequence: 2, role: 'assistant', text: 'second', options: [], selectedPlanItemId: null, submission: null },
  ])
})

test('refreshed history restores only the server-issued pending submission identity', () => {
  const messages = normalizeConversationMessages({ messages: [{
    id: 'user-1', sequence: 4, role: 'user', content: 'saved once', options: [],
    submission: {
      submission_id: 'submission-1', state: 'running', attempt_number: 1,
      lease_expires_at: '2030-01-01T00:00:00Z', assistant_message_id: null,
    },
  }] })
  const pending = pendingSubmissionFromMessages(messages, 'conversation-1')
  assert.equal(pending.conversationId, 'conversation-1')
  assert.equal(pending.submissionId, 'submission-1')
  assert.equal(pending.restored, true)
  assert.equal('text' in pending, false)
  assert.equal('idempotencyKey' in pending, false)
})

test('rapid duplicate sends synchronously claim only one pending request before network work', () => {
  const pendingRef = { current: null }
  const first = { idempotencyKey: 'first' }
  const second = { idempotencyKey: 'second' }

  assert.equal(claimPendingRequest(pendingRef, first), true)
  assert.equal(claimPendingRequest(pendingRef, second), false)
  assert.equal(pendingRef.current, first)

  const hookSource = readFileSync(new URL('../../trip/src/final/useDurableConversation.js', import.meta.url), 'utf8')
  assert.ok(hookSource.indexOf('claimPendingRequest(pendingRef, request)') < hookSource.indexOf('return await submitRequest(request)'))
})

test('submission state distinguishes running, recoverable, failed, completed, and network uncertainty', () => {
  const future = '2030-01-01T00:00:00Z'
  const past = '2020-01-01T00:00:00Z'
  assert.equal(submissionDisposition({ submissionId: 's', state: 'running', leaseExpiresAt: future }, Date.parse('2025-01-01T00:00:00Z')), 'running')
  assert.equal(submissionDisposition({ submissionId: 's', state: 'running', leaseExpiresAt: past }, Date.parse('2025-01-01T00:00:00Z')), 'recoverable')
  assert.equal(submissionDisposition({ submissionId: 's', state: 'failed' }), 'failed')
  assert.equal(submissionDisposition({ submissionId: 's', state: 'completed' }), 'completed')
  assert.equal(submissionDisposition({ state: 'running' }), 'network_unknown')
})

test('network uncertainty is retryable while terminal validation is not', () => {
  assert.deepEqual(durableConversationError(new Error('offline')).kind, 'network_unknown')
  const validation = durableConversationError({ status: 422 })
  assert.equal(validation.kind, 'terminal')
  assert.equal(validation.retryable, undefined)
})

test('refresh restores only the latest terminal submission failure', () => {
  const history = normalizeConversationMessages({ messages: [
    { id: 'old', sequence: 1, role: 'user', content: 'old', submission: { submission_id: 's1', state: 'failed', attempt_number: 1 } },
    { id: 'new', sequence: 2, role: 'user', content: 'new', submission: { submission_id: 's2', state: 'completed', attempt_number: 1 } },
  ] })
  assert.equal(latestSubmissionFromMessages(history).state, 'completed')

  history[1].submission.state = 'failed'
  assert.equal(latestSubmissionFromMessages(history).state, 'failed')
  assert.equal(failedSubmissionError().terminal, true)
  assert.equal(failedSubmissionError().retryable, undefined)
})
