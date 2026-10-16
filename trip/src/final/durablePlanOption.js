// A persisted option is an untrusted historical projection. This adapter is
// display-only: it intentionally exports no mutation or Apply operation.
export const advisoryCandidatesFromPersisted = options => (options || []).map(option => ({
  id: option.id,
  ordinal: option.ordinal,
  label: option.title || '',
  title: option.title || '',
  body: option.summary || '',
  tradeoff: '',
  item_id: option.target_plan_item_id,
  patch: option.safe_patch || {},
  capturedPlanRevision: option.plan_revision,
  capturedPlanningFactsRevision: option.planning_facts_revision,
  advisory: true,
}))
