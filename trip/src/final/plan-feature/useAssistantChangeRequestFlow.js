import { useEffect, useMemo, useRef, useState } from 'react'
import { useTripApp } from '../TripAppState.jsx'
import { useDurableConversation } from '../useDurableConversation.js'
import { trip } from '../tripContent.js'
import { serializeWorkspaceRoute } from '../../../../shared/trip-navigation-route/index.js'

const tripHref = (tripId, section) => serializeWorkspaceRoute({ kind: 'trip', tripId, section })

// Only these three fields can move a block, and a round settles exactly one
// item, so anything aimed elsewhere is dropped here as well as on the server.
const roundAlternativesFrom = (candidateOptions, targetItemId) =>
  (candidateOptions || [])
    .filter(option => option.item_id === targetItemId && option.patch)
    .map(option => ({
      item_id: option.item_id,
      label: option.label || '',
      title: option.title || '',
      body: option.body || '',
      tradeoff: option.tradeoff || '',
      start_hour: option.patch.start_hour ?? null,
      day_date: option.patch.day_date ?? null,
      duration_min: option.patch.duration_min ?? null,
    }))
    .filter(option =>
      option.start_hour !== null || option.day_date !== null || option.duration_min !== null,
    )

const ASSISTANT_LOADING_MESSAGES = [
  'Reviewing the itinerary...',
  'Checking the proposed change...',
  'Looking for member impact...',
  'Organizing the options...',
]

const assistantErrorText = err => (
  err?.status === 409
    ? 'A vote is already open for this time block.'
    : err?.status === 422
      ? 'Reopening this block needs a written reason.'
      : err?.status >= 500
        ? 'The backend hit an error while applying this change. Try again or check the backend logs.'
        : 'I could not reach the backend. Try again in a moment.'
)

const hasExecutablePatch = option => Boolean(option?.patch && Object.keys(option.patch).length)

const removeRequestPatch = () => ({ remove: true })

const isRemoveRequest = text => /\b(remove|delete|clear)\b/i.test(text || '')

const candidateOptionsFromPersisted = options => (options || []).map(option => ({
  id: option.id,
  label: option.title || '',
  title: option.title || '',
  body: option.summary || '',
  tradeoff: '',
  item_id: option.target_plan_item_id,
  patch: option.safe_patch || {},
  // Persisted options are advisory display data. Selecting one always invokes
  // the existing fresh Classify/Preview seam below before Apply is available.
  capturedPlanRevision: option.plan_revision,
  capturedPlanningFactsRevision: option.planning_facts_revision,
}))

export function useAssistantChangeRequestFlow({ item, mode, onCommand, onResolvedOutcome }) {
  const app = useTripApp()
  const [pendingRedirect, setPendingRedirect] = useState('')
  const [draft, setDraft] = useState('')
  const [messageDetails, setMessageDetails] = useState({})
  const threadRef = useRef(null)
  const inputRef = useRef(null)

  const promptExamples = {
    global: '',
    ask: '',
    editTime: '',
    moveDay: '',
    replacePlace: '',
    removePlan: '',
    details: '',
  }

  const placeholder = mode === 'global'
    ? 'Ask Cadensy or request a change...'
    : 'Ask about this item or request a change...'

  const itemId = mode === 'global' ? null : item.id
  const durable = useDurableConversation({ app, scope: 'plan_assistant', itemId })
  const itemById = useMemo(() => Object.fromEntries((app.days || []).flatMap(day => day.items.map(planItem => [planItem.id, planItem]))), [app.days])
  const messages = useMemo(() => {
    let mostRecentUserText = ''
    return durable.messages.map(message => {
      if (message.role === 'user') mostRecentUserText = message.text
      return {
        id: message.id,
        from: message.role === 'user' ? 'you' : 'tripSync',
        text: message.text,
        candidateOptions: candidateOptionsFromPersisted(message.options),
        request: message.role === 'assistant' ? mostRecentUserText : '',
        ...messageDetails[message.id],
      }
    })
  }, [durable.messages, messageDetails])
  const sending = Boolean(durable.pending)

  useEffect(() => {
    setDraft(promptExamples[mode] || '')
    setMessageDetails({})
    setPendingRedirect('')
  }, [item.id, mode])

  useEffect(() => {
    const thread = threadRef.current
    if (thread) thread.scrollTo({ top: thread.scrollHeight, behavior: 'smooth' })
  }, [messages, pendingRedirect])

  useEffect(() => {
    window.setTimeout(() => inputRef.current?.focus({ preventScroll: true }), 80)
  }, [item.id, mode])

  const updateMessage = (id, patch) => {
    setMessageDetails(current => ({ ...current, [id]: { ...current[id], ...patch } }))
  }

  const sendMessage = async () => {
    const text = draft.trim()
    if (!text || sending) return
    setDraft('')
    try {
      await durable.send({ text, selectedPlanItemId: itemId })
    } catch (err) {
      // The hook preserves the original idempotency key and presents a
      // retryable status; it never fabricates an Assistant completion.
    }
  }

  const dismissProposal = id => updateMessage(id, { proposedChange: null, applyError: '', selectionNote: '' })

  const selectCandidateOption = async (message, option) => {
    const targetItem = itemById[option.item_id] || (item.id === option.item_id ? item : null)
    if (!targetItem) {
      updateMessage(message.id, {
        selectedCandidateId: option.id,
        proposedChange: null,
        classifyingCandidate: false,
        selectionNote: '',
        applyError: 'That option is no longer available from the current plan.',
      })
      return
    }

    updateMessage(message.id, {
      selectedCandidateId: option.id,
      proposedChange: null,
      applied: false,
      applyError: '',
      selectionNote: '',
      classifyingCandidate: hasExecutablePatch(option),
    })

    if (!hasExecutablePatch(option)) {
      updateMessage(message.id, {
        classifyingCandidate: false,
        selectionNote: 'That option keeps the Current Plan as-is.',
      })
      return
    }

    try {
      const verdict = await app.classify({
        item: targetItem,
        actionType: mode,
        request: message.request,
        patch: option.patch || {},
      })
      updateMessage(message.id, {
        classifyingCandidate: false,
        proposedChange: {
          item_id: option.item_id,
          item_title: targetItem.title || option.title,
          patch: option.patch || {},
          verdict,
        },
      })
    } catch (err) {
      updateMessage(message.id, {
        classifyingCandidate: false,
        proposedChange: null,
        applyError: assistantErrorText(err),
      })
    }
  }

  const applyProposal = async (message, proposedChange) => {
    const targetItem = itemById[proposedChange.item_id] || (item.id === proposedChange.item_id ? item : { id: proposedChange.item_id, title: proposedChange.item_title })
    updateMessage(message.id, { applying: true, applyError: '' })
    try {
      const outcome = await app.submitChange({
        item: targetItem,
        actionType: mode,
        request: message.request,
        verdict: proposedChange.verdict,
        patch: proposedChange.patch,
        options: roundAlternativesFrom(message.candidateOptions, targetItem.id),
      })
      if (!outcome) {
        updateMessage(message.id, { applying: false })
        return
      }
      updateMessage(message.id, { applying: false, applied: true })
      if (outcome.applied) {
        app.notify('Updated')
        onResolvedOutcome?.({ kind: 'focus-item', itemId: targetItem.id, outcome, targetItem })
      } else if (outcome.path === 'round' || outcome.path === 'reopen_round') {
        app.notify('Vote opened')
        onResolvedOutcome?.({ kind: 'focus-round', itemId: targetItem.id, outcome, targetItem })
      } else {
        app.setUpdateFilter?.('actions')
        setPendingRedirect('Affected members need to confirm. Opening trip notes...')
        const tripId = app.activeTripId || app.trip?.id || trip?.id
        if (tripId) onCommand?.({ type: 'navigate', to: tripHref(tripId, 'updates'), delayMs: 850 })
      }
    } catch (err) {
      const applyError = assistantErrorText(err)
      updateMessage(message.id, { applying: false, applyError })
    }
  }

  return {
    view: {
      pendingRedirect,
      draft,
      messages,
      sending,
      durable,
      placeholder,
      itemById,
      threadRef,
      inputRef,
    },
    actions: {
      updateDraft: setDraft,
      sendMessage,
      dismissProposal,
      selectCandidateOption,
      applyProposal,
      retrySavedRequest: durable.retryPending,
      recoverSavedRequest: durable.recoverPending,
      loadOlderMessages: durable.loadOlder,
      deleteConversation: durable.deleteConversation,
    },
  }
}
