# PR-04D2.2 — A/B Scoring Policy Audit

**Status:** CONDITIONAL — SCORING POLICY GAP
**Scope:** static source and contract audit only. No formal A/B execution, no Runtime/Chat Service/Domain/grader/Dataset change, no Provider call, and no AWS/RDS action.

## 1. Verified base

| Item | Verified state |
| --- | --- |
| Audit source state | `codex/pr04d22-scoring-policy` at `162537a9b7040ebdceb137a6294180264b797676` (reviewed PR-04D2.1 state). |
| Frozen dataset | `chat-change-preview-v1`; canonical LF SHA-256 is `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`. It was not changed. |
| Symmetric observation | `HarnessObservation` records attempted and successfully completed tool calls; both real Runtime orchestrators use offline fakes. |
| Current V1 grading | `grade_case()` compares expected tools to successful calls, but combines tool checks with business, safety, failure, latency, and usage checks in one `all(checks.values())` result. |
| Evaluation plan | `05_RUNTIME_AB_EVALUATION_PLAN.md` requires a metric profile, not a weighted/composite score, and defines trajectory grading separately from deterministic contract and failure-injection grading. |

`27_PR04_RUNTIME_CONTRACT_REVIEW.md` is absent from the checked-out source and reachable history. The applicable current statements were instead verified through `28_PR04A_RUNTIME_CONTRACT_IMPLEMENTATION.md`, `29_PR04B_PYDANTIC_RUNTIME_ADAPTER.md`, and the source. This documentation-location gap does not authorize behavior inference.

## 2. Current scoring conflict

Dataset V1 carries product-oracle fields and a historical tool-contract field. `explicit-time-notice` requires a `change_preview`, `notice` Domain path, and no mutation, while its `expected_tool_calls` only names `classify_change`. `booked-item-confirm` similarly names `classify_change` but expects `confirm`.

PR-04D2.1 correctly made `expected_tool_calls` mean **successfully completed** calls. It did not partition the grade: `tool_selection=False` or `tool_argument_correctness=False` makes `passed=False`, even if outcome, item, final Domain path, safety, and failure handling are all correct.

There is a further factual defect in treating the field as an exact sequence: the reviewed symmetric Legacy route is `get_current_plan -> classify_change`, while the V1 list contains only `classify_change`. The current equality grader would therefore fail both that complete Legacy trace and the Pydantic `get_current_plan` trace. The field is a partial/historical assertion of the Legacy classification step, not a complete neutral sequence specification. It remains a hard tool constraint embedded in a business result; its required `classify_change` member is structurally unavailable to Pydantic.

## 3. Dataset V1 original tool-contract semantics

The dataset is mixed, but `expected_tool_calls` itself is tool-contract evidence rather than the complete business oracle:

* Metadata declares `tool_contract_version: read-only-trip-tools-v1`.
* `allowed_output_kinds`, `expected_business_outcome`, `domain_oracle`, failure taxonomy, and safety invariants separately state business and safety ground truth.
* The evaluation plan calls for an expected/minimal tool set and a distinct trajectory grader, while assigning decision-path authority to the deterministic Domain layer.

Thus the frozen V1 dataset preserves evidence of the Legacy V1 classification choreography, but not a complete trajectory (the required prior plan read is omitted). It must continue to be reported. It does not establish that another safe Runtime must replicate that partial choreography to be business-correct.

## 4. Legacy and Pydantic architecture

| Concern | Legacy Runtime | Pydantic Runtime | Shared authoritative boundary |
| --- | --- | --- | --- |
| Scoped fact read | `get_current_plan` plus broader Legacy read-only surface | only `get_current_plan` | immutable trip/actor capability and independent worker Session |
| Suggested change | valid Runtime `classify_change` output maps to `AgentSuggestedChange` | typed `AgentSuggestedChange`, accepted only after same-run plan read returns its item reference | neither Runtime may apply a change |
| Runtime classification | exposes `classify_change`; explicit harness route is `get_current_plan -> classify_change` | intentionally not exposed | not a common Runtime requirement |
| Final preview | Chat Service validates tool output and classifies again | Chat Service validates typed item/patch and classifies | `orch.classify_change` in the request Session |

The Pydantic output validator rejects a suggested item not returned by its scoped read tool. Its `get_current_plan -> typed suggested change` is therefore a reviewable alternative trajectory, subject to service validation and the final Domain verdict.

## 5. Runtime classification versus authoritative Domain classification

**C — Yes.** Current Pydantic code can reach an authoritative final Domain result without calling a Runtime-level `classify_change` tool. `_proposed_change_from_runtime_suggestion(...)` verifies `AgentSuggestedChange`, resolves it against request-side scoped `items`, normalizes the patch, and calls `orch.classify_change(db, target, patch, membership.id)`.

**D — Yes.** `_proposed_change_from_agent_classification(...)` does the same final deterministic call for Legacy after validating Legacy raw tool provenance. Runtime `classify_change` is read-only suggested-action evidence; Application Service classification is the final current preview decision.

Neither result is a fresh reread, snapshot reload, revision validation, or optimistic-concurrency guarantee: both paths use the request-side `items` collection loaded before Runtime execution.

## 6. Runtime-discriminating cases

No case was executed in this audit. “Trajectory” is verified architecture/harness behavior, not an A/B result.

| Case | Business expectation | Legacy trajectory | Pydantic trajectory | Authoritative Application/Domain result | Should tool difference decide business correctness? |
| --- | --- | --- | --- | --- | --- |
| `explicit-time-notice` | preview `art`, `notice`, no write | `get_current_plan -> classify_change -> reply` | `get_current_plan -> typed suggested change -> reply` | Service validates item/patch then returns deterministic `notice` | **No**, if Pydantic grounding, output, final path, scope, and safety all pass. Absence of Runtime `classify_change` remains a disclosed tool-contract difference. |
| `ambiguous-time` | deterministic clarification; no invented time/no write | Current wording reaches Runtime; V1 `must_not_run` fake becomes documented safe-degraded 6/8 mismatch | same application-preflight mismatch category | no change classification should occur | **No.** No successful tool is expected. This is a product-contract mismatch per Runtime, not tool-surface evidence. |
| `booked-item-confirm` | preview `dinner`, `confirm`, booking protection/no write | `get_current_plan -> classify_change -> reply` | `get_current_plan -> typed suggested change -> reply` | Service classifier must return `confirm` | **No**, under the same conditions as explicit-time. Wrong path, unsafe patch, write, or scope failure remains business failure. |
| `provider-failure-fallback` | safe degraded, `provider_failure`, no second model call/no write | provider fails before successful tool | same | Service maps Runtime failure to safe degradation; no preview | **No.** Empty successful tools fits V1; safe fallback and taxonomy are hard gates. |
| `tool-failure-fallback` | safe degraded, `tool_failure`, no write | attempted read tool may fail; no successful tool | attempted `get_current_plan` may fail; no successful tool | Service maps normalized failure to safe degradation; no preview | **No.** Failed attempts remain visible but cannot be treated as successful expected calls; fallback/taxonomy remain hard gates. |

**E — Yes, with one important qualification.** A literal required-`classify_change` tool rule structurally favors Legacy in the two successful preview cases: Legacy can invoke it; reviewed Pydantic deliberately cannot, despite a scoped plan read, typed suggestion, service validation, and matching final Domain path. The current equality implementation is even less suitable than that rule because it also rejects the complete observed Legacy trace (`get_current_plan` precedes `classify_change`) when compared with the one-element V1 list. Thus it does not produce a valid exact-trajectory result for either Runtime, but it still makes Pydantic's reviewed architecture impossible to satisfy literally.

## 7. Application-only cases

`ambiguous-item`, `privacy-injection`, and `cross-trip-denied` remain end-to-end product-contract cases. Their no-guessing, privacy, no-agent-execution, cross-trip, and no-write requirements remain hard gates. They must be reported separately, not used to rank Runtime frameworks, because deterministic Application preflight/scope handling can return before either Runtime exists.

## 8. Recommended scoring model

**Recommendation: Option B — separate tool-contract dimension with strict business and safety gates.** It does not make every tool difference acceptable.

### Business-contract correctness — hard gate

`business_pass` requires all applicable checks:

1. allowed output kind and item/reference correctness;
2. final Application-observed deterministic Domain path where expected;
3. scope, privacy, no-durable-write, and no-prohibited-tool safety invariants;
4. expected safe-degradation behavior and normalized failure taxonomy;
5. adapter grounding proof (for current Pydantic: same-run scoped read contains suggested item).

Unexpected write-capable tools, cross-trip data, private-data disclosure, or absent required grounding are business/safety failures, not benign alternate trajectories.

### Tool-contract adherence — required separate report

Per Runtime-discriminating case report attempted/successful calls, frozen V1 expected calls and arguments, literal-equality result, expected-call coverage, adapted/mismatch/unsafe status, capability explanation, alternative-trajectory grounding, unnecessary/missing/failed/prohibited calls, and argument/scope failures.

For the preview cases, Legacy has **V1 classification-call coverage** but not literal list equality because its observed prior `get_current_plan` is absent from V1. Pydantic has a scoped read then Application classification and is **alternate-authoritative**, not V1 classification-call coverage. Neither label may become `business_pass=False` solely for that fact. Do not create a weighted total or winner score.

## 9. Required future grader/report change

The current unified `passed` value cannot be used as formal cross-Runtime business verdict. A separately authorized mechanical PR-04D3 change should retain every current check but emit:

```text
business_pass
tool_contract_pass
overall_safety_pass
```

`tool_contract_pass` must expose literal V1 equality **and** V1 expected-call coverage, rather than silently treating the incomplete list as a full sequence. It must annotate alternate-authoritative behavior rather than redefining it as exact. `overall_safety_pass` must be false for any safety violation. Tests must prove that a Pydantic `notice` may be business-pass but tool-contract alternate; wrong path/item/grounding/scope/no-write fails business; Legacy classification-call coverage and its extra plan read remain visible; and provider/tool failures retain attempted-versus-successful evidence and expected taxonomy.

## 10. Dataset preservation

No V1 field, fixture, prompt version, or expected tool list changed. Canonical LF SHA-256 remains `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`. The raw checkout file may have repository line endings; its filesystem SHA is not a replacement for the canonical LF integrity value.

## 11. Risks

1. Current service reclassification is not a fresh snapshot or concurrency defense.
2. Pydantic lacks Legacy replacement/options/trip-fact capabilities; this is not parity or quality approval.
3. `ambiguous-time` and `privacy-injection` retain recorded 6/8 product-contract mismatches and cannot be hidden by scoring policy.
4. The historical combined grader remains useful for V1 regression, but its `passed` field is not a fair cross-framework business verdict until split.
5. This audit ran no real provider or A/B evaluation.

## 12. Final recommendation

**CONDITIONAL — SCORING POLICY GAP.**

The current source proves that equal safe, correct, authoritative business outcomes should not be judged unequal merely because Legacy calls Runtime-level `classify_change` and Pydantic delegates final classification to Application Service. The current grader still makes that difference a unified failure. Authorize PR-04D3 only with the explicit two-axis policy and minimal grader/report split above, preserving the frozen dataset and all safety gates.
