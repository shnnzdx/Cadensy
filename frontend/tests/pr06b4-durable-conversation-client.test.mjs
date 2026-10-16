import assert from 'node:assert/strict'
import test from 'node:test'

import {
  conversationIdentityFor,
  createDurableConversationClient,
  normalizeConversationMessages,
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
  await client.messages('conversation id', 9)
  await client.submit('conversation id', { text: 'hello', selectedPlanItemId: 'f'.repeat(32), idempotencyKey: 'submission-key' })
  await client.recover('conversation id', 'submission id', 'recovery-key')
  await client.delete('conversation id')

  assert.equal(calls[0].path, '/api/trips/trip%20id/conversations')
  assert.deepEqual(JSON.parse(calls[0].options.body), { scope: 'personal', context_key: 'personal' })
  assert.equal(calls[1].path, '/api/trips/trip%20id/conversations/conversation%20id/messages?limit=50&before=9')
  assert.equal(calls[2].options.headers['Idempotency-Key'], 'submission-key')
  assert.deepEqual(JSON.parse(calls[2].options.body), { text: 'hello', selected_plan_item_id: 'f'.repeat(32) })
  assert.equal(calls[2].options.body.includes('history'), false)
  assert.equal(calls[3].options.headers['Idempotency-Key'], 'recovery-key')
  assert.equal(calls[4].options.method, 'DELETE')
})

test('durable history is presented in ascending server sequence order', () => {
  assert.deepEqual(normalizeConversationMessages({ messages: [
    { id: 'b', sequence: 2, role: 'assistant', content: 'second', options: [] },
    { id: 'a', sequence: 1, role: 'user', content: 'first', options: [] },
  ] }), [
    { id: 'a', sequence: 1, role: 'user', text: 'first', options: [], selectedPlanItemId: null },
    { id: 'b', sequence: 2, role: 'assistant', text: 'second', options: [], selectedPlanItemId: null },
  ])
})
