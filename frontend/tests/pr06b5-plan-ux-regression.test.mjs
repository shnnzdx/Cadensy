import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const planFeatureUrl = new URL('../../trip/src/final/plan-feature/PlanFeature.jsx', import.meta.url)
const interactionRuntimeUrl = new URL('../../trip/src/final/plan-feature/usePlanInteractionRuntime.js', import.meta.url)
const assistantFlowUrl = new URL('../../trip/src/final/plan-feature/useAssistantChangeRequestFlow.js', import.meta.url)
const tripMapUrl = new URL('../../trip/src/final/TripMap.jsx', import.meta.url)

const sourceOf = url => readFile(url, 'utf8')

test('Current Plan exposes the existing global Plan Assistant through the plan interaction boundary', async () => {
  const [feature, runtime, flow] = await Promise.all([
    sourceOf(planFeatureUrl),
    sourceOf(interactionRuntimeUrl),
    sourceOf(assistantFlowUrl),
  ])

  assert.match(feature, /onClick=\{actions\.openGlobalAssistant\}>Ask Cadensy about this trip<\/button>/)
  assert.match(runtime, /const openGlobalAssistant = useCallback\(\(\) => \{/)
  assert.match(runtime, /id: null,\s*title: currentTrip\.name \|\| 'Current plan'/s)
  assert.match(runtime, /setDrawerMode\('global'\)/)
  assert.match(runtime, /openGlobalAssistant,/)
  assert.match(flow, /const itemId = mode === 'global'\s*\? null\s*: item\.id/s)
  assert.match(flow, /useDurableConversation\(\{ app, scope: 'plan_assistant', itemId \}\)/)
})

test('historical option resolution renders an accessible explanation and never applies a change by selection alone', async () => {
  const [feature, flow] = await Promise.all([sourceOf(planFeatureUrl), sourceOf(assistantFlowUrl)])

  assert.match(feature, /message\.selectionNote && <p className="assistantOptionNote" role="status">\{message\.selectionNote\}<\/p>/)
  assert.match(flow, /const resolved = await durable\.resolveOption\(/)
  assert.match(flow, /if \(!resolved\.ready\) \{\s*updateMessage\(message\.id, \{\s*classifyingCandidate: false,\s*selectionNote: resolved\.note,/s)
  const selectionStart = flow.indexOf('const selectCandidateOption = async (message, option) => {')
  const applyStart = flow.indexOf('const applyProposal = async')
  assert.ok(selectionStart >= 0 && applyStart > selectionStart, 'candidate selection seam must remain separately inspectable from Apply')
  assert.doesNotMatch(flow.slice(selectionStart, applyStart), /submitChange\(/)
})

test('TripMap cancels and guards delayed invalidation across updates and unmount', async () => {
  const source = await sourceOf(tripMapUrl)

  assert.match(source, /const invalidateTimerRef = useRef\(null\)/)
  assert.match(source, /if \(invalidateTimerRef\.current !== null\) window\.clearTimeout\(invalidateTimerRef\.current\)/)
  assert.match(source, /invalidateTimerRef\.current = window\.setTimeout\(\(\) => \{\s*if \(mapRef\.current !== map\) return\s*invalidateTimerRef\.current = null\s*map\.invalidateSize\(\)/s)
  assert.match(source, /return \(\) => \{\s*window\.clearTimeout\(invalidateTimer\)/s)
  assert.match(source, /return \(\) => \{\s*if \(invalidateTimerRef\.current !== null\) window\.clearTimeout\(invalidateTimerRef\.current\)/s)
})
