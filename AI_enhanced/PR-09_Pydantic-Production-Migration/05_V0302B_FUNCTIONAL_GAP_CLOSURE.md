# v0.3.0-02B Functional Parity Gap Closure and Final Review

## A. Original implementation state

v0.3.0-02 started from `5ce3782df8222cc87fd468bf1cf649a6d4aefe69` on
`codex/v0302-pydantic-functional-parity`. The production selector remained
Legacy. The prior review correctly recorded `INCOMPLETE` because it had not
demonstrated the new replacement and targeted-options workflows against the
separate historical frozen business mismatches.

This closure keeps the existing architecture: Pydantic uses only the
capability-bound read handlers; Preview owns scope/revision/classification; and
Durable Conversation owns persistence. No schema, API, provider, dependency,
selector, production configuration, or Legacy runtime behavior changed.

## B. Confirmed replacement candidate-order defect and correction

`_replacement_patch_is_evidenced` formerly stopped at the first core-field
match. Thus a priced candidate encountered before an otherwise identical
unpriced candidate rejected an omitted-price patch, while the reversed order
accepted it. The matcher now continues examining eligible candidates. It accepts
an omitted price only when *some* exact unpriced candidate exists, and accepts a
supplied price only on an exact price match.

`test_replacement_evidence_price_matching_is_order_independent` proves both
orders, mismatched price rejection, split `succeeded`/`cache_hit` evidence, a
guard-rejected entry that carries no authority, and a no-match case. Preview's
independent provenance gate was not changed.

## C. Targeted Options parity correction

The Pydantic `propose_options` wrapper now safely exposes the existing handler's
`conflict_item_ids` and `suggestions` parameters. Before dispatch it requires a
Current Plan read and rejects any supplied conflict id not observed in that
run. The typed suggestion DTO permits only the existing validator's executable
single-item fields (`start_hour`, `day_date`, `duration_min`) plus bounded
presentation text. It does not implement a second generator or validator:
`app.agents.tools._validated_suggestions` remains the authority.

Budgets: at most four conflict ids, four suggestions, 1,000 conflict-description
characters, 64 day-query characters, 4,000 body/tradeoff characters, and fixed
small bounds for ids/titles/labels/dates. Schema validation rejects an oversized
payload before handler dispatch. A real PostgreSQL handler test adds an unrelated
600-minute item, demonstrates the named Art/Dinner conflict still targets Art,
and proves an invalid model suggestion is omitted by the existing Domain path.
When a specific conflict also narrows to a day, the named ids must have been
returned by a Current Plan read for that same normalized day query. This blocks a
full-plan id from silently redirecting to another day's generic-longest fallback.

Keep/no-change is preserved as the existing `{}` option. Generic fallback is
still available when no conflict ids/suggestions are supplied. No option is a
Plan mutation or authorization to apply one.

## D. Trusted evidence and policy analysis

- Only a successful or same-run cache-hit replacement handler result becomes
  detached `ReplacementCandidateEvidence`; rejected, failed, timed-out, cancelled,
  forged, or malformed paths cannot authorize a venue patch.
- Replacement fields exact-match evidence in the runtime and again in unchanged
  Preview. Options have no independent venue authority.
- Cache keys are per-run and values are saved only after a public projection
  passes the adapter type boundary.
- Conflict ids are checked against the current execution's scoped read ids;
  model text cannot choose a foreign Plan item.
- Existing Domain classification validates model-written suggestion changes;
  Preview independently enforces membership, scope, revision brackets, and
  deterministic classification.
- Durable persistence remains fenced, validates safe projections, deduplicates
  options, and does not write the Plan.

## E. New functional evaluation

`07_V0302B_FUNCTIONAL_EVALUATION.json` defines 11 deterministic scenarios with
inputs, expected outcomes, measurable checks, named test evidence, and results.
All 11 passed. They cover facts, grounded direct change, two-item conflict,
multiple compromises, replacement evidence/refusal, foreign id rejection,
membership/revision defenses, clarification, and durable no-write persistence.
It also records the candidate-order, suggestion-validation, bounded-payload,
and Durable-deduplication checks.

## F-G. Historical frozen evaluation and exact root-cause findings

The frozen PR-04D3 dataset, historical reports, harness, and grader were not
modified. Both the PR-04D3 baseline and the v0.3.0-02 artifact show Legacy and
Pydantic at 6/8 business and 8/8 safety: `ambiguous-time` and
`privacy-injection` are symmetric shared Application paths. The exact trace,
baseline comparison, classification, and separate-change recommendation are in
`06_V0302B_FROZEN_EVALUATION_DIAGNOSTIC.md`.

Accordingly, these two historical product-contract mismatches do not block the
v0.3.0-02 migration implementation. They do prevent claiming that every
historical Golden business expectation currently matches Application behavior.

## H. Code changes and validation

Changed implementation/tests:

- `backend/app/agents/pydantic_runtime.py`
- `backend/tests/test_pydantic_runtime.py`

New/update documentation:

- `02_V0302_FUNCTIONAL_PARITY_DESIGN.md`
- `03_V0302_FROZEN_AB_EVALUATION.json`
- `04_V0302_FINAL_LOCAL_REVIEW.md`
- this report, `06_V0302B_FROZEN_EVALUATION_DIAGNOSTIC.md`, and
  `07_V0302B_FUNCTIONAL_EVALUATION.json`

All validation used `APP_ENV=test`, `MOCK_AI=1`, scheduler disabled, provider
credentials removed, and only disposable `test_cadensy_v0302` on local port
`55434`.

| Command | Result |
| --- | --- |
| `python -m pytest backend/tests/test_pydantic_runtime.py -q` | `43 passed in 2.36s` |
| Relevant Pydantic/Legacy tool policy/Preview/Durable/concurrency/telemetry/evaluation suite | `242 passed in 9.74s` |
| Complete backend suite, split only to retain terminal evidence | `1337 passed, 10 warnings` (`534 + 184 + 119 + 252 + 248`) |
| `python -m compileall -q backend/app/agents/pydantic_runtime.py` | pass, exit `0` |
| `git diff --check` | pass, exit `0` |

The 10 warnings are the pre-existing Alembic `path_separator` deprecation from
`test_trip_bound_phase1_migration.py`; no test failed.

Exact invocation shape (with the environment declared immediately above):

```powershell
& .\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_pydantic_runtime.py -q
& .\backend\.venv\Scripts\python.exe -m pytest backend/tests/test_pydantic_runtime.py backend/tests/test_pydantic_composition.py backend/tests/test_agents_tools.py backend/tests/test_runtime_contract.py backend/tests/test_b3_revision_bracketed_preview.py backend/tests/test_pr06b3_runtime.py backend/tests/test_pr06b3_context.py backend/tests/test_pr06b2_conversation_concurrency_postgres.py backend/tests/test_durable_idempotency_kernel.py backend/tests/test_telemetry_privacy.py backend/tests/test_telemetry_execution_wiring.py backend/tests/test_telemetry_contract.py backend/tests/test_evaluation_foundation.py backend/tests/test_symmetric_evaluation_harness.py backend/tests/test_formal_ab_runner.py backend/tests/test_ab_grade_partition.py -q
& .\backend\.venv\Scripts\python.exe -m pytest backend/tests -q
& .\backend\.venv\Scripts\python.exe -m compileall -q backend/app/agents/pydantic_runtime.py
git diff --check
```

Because the interactive terminal limits a single captured command to 30 seconds,
the complete suite was additionally captured in five non-overlapping sorted-file
partitions. Their sum is the reported `1337`; the ordinary full-suite command
was also run against the same disposable database.

## I. Remaining product-contract gaps

- The two frozen shared Application mismatches remain for separately approved
  product work; no Golden labels were weakened.
- Real provider behavior/cost/latency, production selector activation, browser
  acceptance, and deployment remain outside this local FunctionModel scope.

## J. PR readiness recommendation

The demonstrated v0.3.0-02 migration functionality, trusted evidence boundary,
targeted Options interface, Preview integration, Durable persistence, Legacy
compatibility, and regression tests are ready for code review. A reviewer should
independently check evidence is sourced only by successful handler execution,
the immutable DTO boundary is retained, cache/guard state cannot authorize an
operation, and Preview still validates the revision-bracketed candidate.

`v0.3.0-02 IMPLEMENTATION: PASS`

`NEW FEATURE FUNCTIONAL EVALUATION: PASS`

`HISTORICAL FROZEN BUSINESS: KNOWN BASELINE GAP`

`HISTORICAL FROZEN SAFETY: PASS`

`REPLACEMENT EVIDENCE: PASS`

`TARGETED OPTIONS PARITY: PASS`

`PYDANTIC PREVIEW INTEGRATION: PASS`

`DURABLE CONVERSATION INTEGRATION: PASS`

`LEGACY REGRESSION: PASS`

`FULL BACKEND REGRESSION: PASS`

`READY FOR CODE REVIEW: YES`

`PRODUCTION CHANGE AUTHORIZATION: NOT GRANTED`
