/** Client-side budget check. Empty values are optional. */

const numericBudget = value => {
  if (value == null || value === "") return null
  const numeric = Number(String(value).replace(/[^0-9.]/g, ""))
  if (!Number.isFinite(numeric)) return null
  return numeric || null
}

export function preferenceBudgetError(ideal, maximum) {
  const idealBudget = numericBudget(ideal)
  const maximumBudget = numericBudget(maximum)
  if (idealBudget == null || maximumBudget == null) return null
  if (idealBudget > maximumBudget) return "ideal_budget_exceeds_maximum"
  return null
}
