import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { preferenceBudgetError } from "../../trip/src/final/preferenceBudget.js";

test("budget validation accepts a missing budget and rejects ideal above maximum", () => {
  assert.equal(preferenceBudgetError("", ""), null);
  assert.equal(preferenceBudgetError("100", ""), null);
  assert.equal(preferenceBudgetError("100", "100"), null);
  assert.equal(preferenceBudgetError("80", "120"), null);
  assert.equal(preferenceBudgetError("$800", "$500"), "ideal_budget_exceeds_maximum");
});

test("the preference form keeps dates and budget visible and stays editable after a plan", async () => {
  const source = await readFile(new URL("../../trip/src/final/FinalApp.jsx", import.meta.url), "utf8");
  assert.match(source, /稍后补充/);
  assert.match(source, /preferenceBudgetError\(form\.idealBudget, form\.maxBudget\)/);
  assert.match(source, /The current plan stays in place and is marked to refresh/);
  assert.doesNotMatch(source, /preferenceReadOnly/);
  assert.doesNotMatch(source, /Read-only: these preferences were used to generate the current plan/);
});
