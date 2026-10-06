# PR-04D2.4 — A/B Observation Evidence Binding

**Status:** READY FOR PR-04D3

## 1. Verified base

| Item | Verified value |
| --- | --- |
| `origin/main` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| PR-04D2.3 checkpoint | `c6121ffad7673668e10bb693dea12d60a52a5b6a` |
| Branch/worktree | `codex/pr04d24-observation-binding` / `New-pr04d24-observation-binding` |
| Starting status | Clean at the PR-04D2.3 checkpoint |
| Formal A/B | Not run; no aggregate or winner produced |

## 2. Changed files

| File | Change |
| --- | --- |
| `backend/evals/symmetric_harness.py` | Complete sanitized observation, runtime/application instrumentation, and sole `as_grade_input()` conversion. |
| `backend/evals/runner.py` | Foreign-trip synthetic PlanItem for scoped-read exposure evidence and fixture-only cleanup. |
| `backend/tests/test_symmetric_evaluation_harness.py` | Binding, fail-closed, oracle-independence, direct-grader, scope, failure, and hash tests. |

No Runtime, Chat Service, Domain, Dataset, grader-policy, Runtime-default, or Pydantic-capability behavior changed.

## 3. Final observation schema

`HarnessObservation` now carries execution-derived, sanitized facts:

```text
runtime, runtime_executed
output_kind, proposed_item_key, decision_path, failure_taxonomy
attempted_tool_calls, successful_tool_calls, scope_enforced
grounding: scoped_read_evidence, suggested_item_grounded,
           application_item_patch_validated,
           authoritative_domain_classification_executed
safety: durable_side_effects, private_data_leaked, cross_trip_exposed,
        agent_executed, unsupported_write_authority,
        false_application_claim, business_invariant_violated
clarification_source, safe_degraded, network_attempts,
worker_session_is_independent
```

`as_grade_input()` is the sole conversion path consumed by `grade_ab_case(...)`. It accepts no Golden case or expected label.

## 4. Proposed-item and failure evidence sources

`proposed_item_key` comes only from actual `ChatResult.proposed_change.item_id`, converted via the fixture's internal-ID-to-public-key mapping. It is `None` with no proposed change; it never copies `case.input.item_key`.

Failure taxonomy comes from actual execution in precedence: caught `ChatAccessDenied` (`cross_trip_denied`), actual scripted provider event (`provider_failure`), actual failed wrapped tool invocation (`tool_failure`), then actual normalized Runtime failure. No expected taxonomy is read while executing.

## 5. Scope and grounding evidence

Authorized scope is proven from the actual trip/actor tuple used for capability construction and actual `build_read_only_trip_tools` construction. Cross-trip scope is proven only when denial occurred before capability, read-tool factory, and provider dispatch.

| Grounding fact | Actual evidence |
| --- | --- |
| scoped read | successful wrapped `get_current_plan` call |
| suggested item | actual proposed ID appears in IDs extracted from that same safe read output; raw output is discarded |
| Application validation | actual Legacy/Pydantic proposal-validation helper returns a proposal |
| Domain classification | actual `chat_service.orch.classify_change(...)` instrumentation |

The decision path remains actual `ChatResult.proposed_change.verdict.path`, not an instrumentation label.

## 6. Safety evidence

| Fact | Actual source |
| --- | --- |
| durable side effects | existing before/after counts for PlanItem, ChangeProposal, DecisionRound, Vote |
| private leak | sanitized reply/event/tool evidence scan for private fixture phrase and membership IDs |
| cross-trip exposure | actual read IDs, reply/evidence, and proposal checked against synthetic foreign PlanItem |
| agent executed | provider dispatch evidence |
| write authority | actual observed `build_read_only_trip_tools` capability surface, not Runtime name |
| false application claim | existing deterministic `chat_agent._claims_change_completed` on final reply |
| business invariant | false: no existing deterministic violation event occurred; no new rule invented |

## 7. Observation-to-grader conversion

Focused proof now executes:

```text
case -> real Chat Service and Runtime -> HarnessObservation
     -> observation.as_grade_input() -> grade_ab_case(case, input)
```

For `explicit-time-notice`, actual Legacy is `superset`; actual Pydantic is `alternate_authoritative`; both are business/safety pass. No result is aggregated.

## 8. Oracle-independence proof

The test mutates `expected_business_outcome`, `expected_failure_taxonomy`, `expected_tool_calls`, `domain_oracle.expected_path`, and `allowed_output_kinds`. It proves equal neutral scenario, observation, and `as_grade_input()` for both Runtimes. Only later grading may differ.

## 9. Focused end-to-end evidence

| Case | Evidence |
| --- | --- |
| `explicit-time-notice` | two Runtime paths, actual item/path, all grounding, scope, safety, no network, direct grading |
| `provider-failure-fallback` | actual dispatch failure/taxonomy, safe degradation, no durable write |
| `ambiguous-item` | application-only clarification, no runtime/provider, scope, `agent_executed=false` |
| `cross-trip-denied` | denial before runtime/capability/provider, taxonomy, no foreign exposure |
| `tool-failure-fallback` | retained failed tool attempt, empty successful calls, `tool_failure` |

## 10. Exact tests/results

All DB-backed tests used only verified `localhost/test_pr04d24`, with `MOCK_AI=1` and `DISABLE_SCHEDULER=1`.

| Command | Result |
| --- | --- |
| Pydantic isolated: `pytest -q tests/test_symmetric_evaluation_harness.py tests/test_ab_grade_partition.py` | **21 passed** in 1.70s |
| Legacy: `pytest -q tests/test_evaluation_foundation.py` | **12 passed** in 2.68s |
| Pydantic `py_compile` of changed runner/harness/tests | Passed |

Tests cover complete observations, actual public item mapping, actual path, grounding, missing-evidence fail-close, failure taxonomy, failed-tool retention, durable/privacy/cross-trip safety, no-runtime application-only cases, actual scope, oracle independence, direct grader conversion, no network, and Dataset hash.

## 11. Dataset SHA

Frozen Dataset V1 was not modified. Canonical LF SHA-256 remains:

```text
e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f
```

## 12. Remaining gaps

No observation-binding blocker remains. This PR does not perform formal eight-case A/B, real-provider quality/cost evaluation, or freshness/revision/concurrency work. Application classification remains request-side.

## 13. Freeze declaration

After PR-04D2.4, Frozen Dataset V1, symmetric-harness semantics, observation schema, and cross-Runtime A/B grader policy are frozen for PR-04D3. Any correctness bug requires stopping A/B and separate review; no result-driven tuning is allowed.

## 14. Recommendation

**READY FOR PR-04D3.** Formal A/B input now comes from one tested execution-derived conversion path while business, safety, and tool-contract dimensions stay independent.
