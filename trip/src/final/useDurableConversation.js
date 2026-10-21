import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  conversationIdentityFor,
  claimPendingRequest,
  createIdempotencyKey,
  durableConversationError,
  failedSubmissionError,
  latestSubmissionFromMessages,
  normalizeConversationMessages,
  pendingSubmissionFromMessages,
  submissionDisposition,
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
        const hydrated = await readMessages(resolved.id, null, true, thisGeneration)
        const restored = pendingSubmissionFromMessages(hydrated, resolved.id)
        if (restored && !cancelled && generation.current === thisGeneration) {
          pendingRef.current = restored
          setPending(restored)
          // A refresh has no original request key/text to replay. It first
          // reads the authorized durable status; recovery remains a separate,
          // owner-authorized action after the server lease expires.
          const status = await client.status(resolved.id, restored.submissionId)
          if (cancelled || generation.current !== thisGeneration) return
          const current = { ...restored, ...status, generation: thisGeneration }
          pendingRef.current = current
          setPending(current)
          const disposition = submissionDisposition(current)
          if (disposition === 'completed' || disposition === 'failed') {
            await readMessages(resolved.id, null, true, thisGeneration)
            pendingRef.current = null
            setPending(null)
            if (disposition === 'failed') {
              setError(failedSubmissionError())
            }
          } else if (disposition === 'recoverable') {
            setError({
              kind: 'recoverable', recoverable: true,
              text: 'This saved request did not finish before its lease expired. You can safely recover it.',
            })
          }
        } else if (latestSubmissionFromMessages(hydrated)?.state === 'failed') {
          setError(failedSubmissionError())
        }
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
      const disposition = submissionDisposition(latest)
      if (disposition === 'completed' || disposition === 'failed') {
        await readMessages(request.conversationId, null, true, currentGeneration)
        pendingRef.current = null
        setPending(null)
        setError(disposition === 'failed' ? failedSubmissionError() : null)
        return status
      }
      if (disposition === 'recoverable') {
        setError({
          kind: 'recoverable', recoverable: true,
          text: 'This saved request did not finish before its lease expired. You can safely recover it.',
        })
        return status
      }
      await new Promise(resolve => window.setTimeout(resolve, POLL_INTERVAL_MS))
    }
    if (generation.current === currentGeneration) {
      setError({
        kind: 'running',
        retryable: true,
        text: 'Your message is saved and Cadensy is still working. Retry checks the same saved request; it does not submit a new one.',
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
    if (request.generation !== generation.current) return null
    const next = { ...request, ...admitted }
    pendingRef.current = next
    setPending(next)
    await readMessages(request.conversationId)
    return checkStatus(next)
  }, [checkStatus, client, readMessages])

  const send = useCallback(async ({ text, selectedPlanItemId = null }) => {
    if (!conversation || pendingRef.current || !text?.trim()) return null
    if (conversation.runtime_available !== true) {
      setError({
        kind: 'terminal',
        terminal: true,
        text: 'Cadensy assistant delivery is unavailable in this environment. Your message was not sent.',
      })
      return { preserveDraft: true }
    }
    const request = {
      conversationId: conversation.id,
      text: text.trim(),
      selectedPlanItemId,
      idempotencyKey: createIdempotencyKey(),
      recoveryKey: createIdempotencyKey(),
      generation: generation.current,
    }
    if (!claimPendingRequest(pendingRef, request)) return null
    setPending(request)
    try {
      return await submitRequest(request)
    } catch (cause) {
      if (request.generation !== generation.current) return null
      const mapped = durableConversationError(cause)
      if (mapped.terminal) {
        pendingRef.current = null
        setPending(null)
      }
      setError(mapped)
      return mapped.terminal ? { preserveDraft: true } : null
    }
  }, [conversation, submitRequest])

  const retryPending = useCallback(async () => {
    const request = pendingRef.current
    if (!request) return null
    if (request.submissionId || request.restored || !request.text || !request.idempotencyKey) {
      try {
        return await checkStatus(request)
      } catch (cause) {
        const mapped = durableConversationError(cause)
        if (mapped.terminal) {
          pendingRef.current = null
          setPending(null)
        }
        setError(mapped)
        return null
      }
    }
    try {
      return await submitRequest(request)
    } catch (cause) {
      const mapped = durableConversationError(cause)
      if (mapped.terminal) {
        pendingRef.current = null
        setPending(null)
      }
      setError(mapped)
      return null
    }
  }, [checkStatus, submitRequest])

  const recoverPending = useCallback(async () => {
    const request = pendingRef.current
    if (!request?.submissionId) return retryPending()
    if (submissionDisposition(request) !== 'recoverable') {
      setError({
        kind: 'running', retryable: true,
        text: 'This saved request is still running. Check its status before attempting recovery.',
      })
      return null
    }
    try {
      const result = await client.recover(request.conversationId, request.submissionId, request.recoveryKey)
      const next = { ...request, ...result }
      pendingRef.current = next
      setPending(next)
      return checkStatus(next)
    } catch (cause) {
      const mapped = durableConversationError(cause)
      if (mapped.terminal) {
        pendingRef.current = null
        setPending(null)
      }
      setError(mapped)
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

  const resolveOption = useCallback(async ({ assistantMessageId, optionId }) => {
    if (!conversation) throw new Error('Conversation is unavailable')
    return client.resolveOption(conversation.id, assistantMessageId, optionId)
  }, [client, conversation])

  return {
    conversation,
    messages,
    nextBefore,
    loading,
    pending,
    error,
    runtimeAvailable: conversation?.runtime_available === true,
    send,
    retryPending,
    recoverPending,
    loadOlder,
    deleteConversation,
    resolveOption,
  }
}
