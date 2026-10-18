import assert from 'node:assert/strict'
import test from 'node:test'

import { advisoryCandidatesFromPersisted } from '../../trip/src/final/durablePlanOption.js'

test('persisted conversation options remain advisory display projections with captured revision evidence', () => {
  const [candidate] = advisoryCandidatesFromPersisted([{
    id: 'option-1', ordinal: 2, title: 'Move museum later', summary: 'Avoids the rain window.',
    target_plan_item_id: 'a'.repeat(32), safe_patch: { start_hour: 15 },
    plan_revision: 7, planning_facts_revision: 9,
  }])

  assert.deepEqual(candidate, {
    id: 'option-1', ordinal: 2, label: 'Move museum later', title: 'Move museum later',
    body: 'Avoids the rain window.', tradeoff: '', item_id: 'a'.repeat(32),
    patch: { start_hour: 15 }, capturedPlanRevision: 7, capturedPlanningFactsRevision: 9, advisory: true,
  })
  assert.equal(typeof candidate.apply, 'undefined')
})
