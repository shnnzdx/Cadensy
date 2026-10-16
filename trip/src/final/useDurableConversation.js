import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  conversationIdentityFor,
  createIdempotencyKey,
  durableConversationError,
  normalizeConversationMessages,
} from './durableConversationClient.js'

const POLL_INTERVAL_MS = 2500
const MAX_STATUS_POLLS = 12

// This hook deliberately has no model or chat-history input. The server is the
// sole reconstruction source; the browser holds only a pending request's
// idempotency/recovery keys long enough to recover an interrupted request.
export function useDurableConversation({ app, scope, itemId = null }) {
  const identity = useMemo(() => conversationIdentityFor({ scope, itemId }), [scope, itemId])
  const [conversation, setConversation] = useState(null)
  const [messages, setMessages] = useState([])
  const [nextBefore, setNextBefore] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [pending, setPending] = useState(null)
  const [resetVersion, setResetVersion] = useState(0)
  const generation = useRef(0)
  const pendingRef = useRef(null)

  const client = useMemo(() => app.durableConversationClient(), [app.durableConversationClient])
  const identityKey = `${app.activeTripId || ''}:${app.membershipId || app.currentUser?.membershipId || ''}:${identity.scope}:${identity.contextKey}`

  const readMessages = useCallback(async (conversationId, before = null, replace = true, expectedGeneration = generation.current) => {
    const page = await client.messages(conversationId, before)
    if (expectedGeneration !== generation.current) return []
    const normalized = normalizeConversationMessages(page)
    setMessages(current => replace ? normalized : [...normalized, ...current])
    setNextBefore(page.next_before ?? null)
    return normalized
  }, [client])

  useEffect(() => {
    let cancelled = false
    const thisGeneration = ++generation.current
    setLoading(true)
    setError(null)
    setConversation(null)
    setMessages([])
    setNextBefore(null)
    setPending(null)
    pendingRef.current = null
    ;(async () => {
      try {
        const resolved = await client.resolve(identity)
        if (cancelled || generation.current !== thisGeneration) return
        setConversation(resolved)
        await readMessages(resolved.id, null, true, thisGeneration)
      } catch (cause) {
        if (!cancelled && generation.current === thisGeneration) setError(durableConversationError(cause))
      } finally {
        if (!cancelled && generation.current === thisGeneration) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [client, identity, identityKey, readMessages, resetVersion])

  const checkStatus = useCallback(async request => {
    const currentGeneration = generation.current
    let latest = request
    for (let attempt = 0; attempt < MAX_STATUS_POLLS; attempt += 1) {
      if (generation.current !== currentGeneration) return null
      const status = await client.status(request.conversationId, request.submissionId)
      if (generation.current !== currentGeneration) return null
      latest = { ...latest, ...status }
      pendingRef.current = latest
      setPending(latest)
      if (status.state !== 'running') {
        await readMessages(request.conversationId, null, true, currentGeneration)
        pendingRef.current = null
        setPending(null)
        return status
      }
      await new Promise(resolve => window.setTimeout(resolve, POLL_INTERVAL_MS))
    }
    if (generation.current === currentGeneration) {
      setError({
        retryable: true,
        text: 'Your message is saved, but assistant delivery is still pending. This environment may have durable Runtime delivery disabled.',
      })
    }
    return latest
  }, [client, readMessages])

  const submitRequest = useCallback(async request => {
    setError(null)
    const admitted = await client.submit(request.conversationId, {
      text: request.text,
      selectedPlanItemId: request.selectedPlanItemId,
      idempotencyKey: request.idempotencyKey,
    })
    const next = { ...request, ...admitted }
    pendingRef.current = next
    setPending(next)
    await readMessages(request.conversationId)
    return checkStatus(next)
  }, [checkStatus, client, readMessages])

  const send = useCallback(async ({ text, selectedPlanItemId = null }) => {
    if (!conversation || pendingRef.current || !text?.trim()) return null
    const request = {
      conversationId: conversation.id,
      text: text.trim(),
      selectedPlanItemId,
      idempotencyKey: createIdempotencyKey(),
      recoveryKey: createIdempotencyKey(),
    }
    try {
      return await submitRequest(request)
    } catch (cause) {
      pendingRef.current = request
      setPending(request)
      setError(durableConversationError(cause))
      return null
    }
  }, [conversation, submitRequest])

  const retryPending = useCallback(async () => {
    const request = pendingRef.current
    if (!request) return null
    try {
      return await submitRequest(request)
    } catch (cause) {
      setError(durableConversationError(cause))
      return null
    }
  }, [submitRequest])

  const recoverPending = useCallback(async () => {
    const request = pendingRef.current
    if (!request?.submissionId) return retryPending()
    try {
      const result = await client.recover(request.conversationId, request.submissionId, request.recoveryKey)
      const next = { ...request, ...result }
      pendingRef.current = next
      setPending(next)
      return checkStatus(next)
    } catch (cause) {
      setError(durableConversationError(cause))
      return null
    }
  }, [checkStatus, client, retryPending])

  const loadOlder = useCallback(async () => {
    if (!conversation || nextBefore === null) return
    try {
      await readMessages(conversation.id, nextBefore, false)
    } catch (cause) {
      setError(durableConversationError(cause))
    }
  }, [conversation, nextBefore, readMessages])

  const deleteConversation = useCallback(async () => {
    if (!conversation) return false
    try {
      await client.delete(conversation.id)
      pendingRef.current = null
      setResetVersion(version => version + 1)
      return true
    } catch (cause) {
      setError(durableConversationError(cause))
      return false
    }
  }, [client, conversation])

  return {
    conversation,
    messages,
    nextBefore,
    loading,
    pending,
    error,
    send,
    retryPending,
    recoverPending,
    loadOlder,
    deleteConversation,
  }
}
