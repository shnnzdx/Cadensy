# PR-04D-Prep — Fair Evaluation Observation Extraction

## Verdict

**PASS for independent PR-04D-Prep review.** The Legacy evaluation observer no
longer derives an observation from Golden expected labels. This is preparation
for a future comparison, not a Legacy-vs-Pydantic A/B evaluation.

## 1. Verified base

| Item | Value |
| --- | --- |
| `origin/main` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| Reviewed PR-04C checkpoint | `e3a989f6fb2a5659a58ea12d1c61d007e1d93b38` |
| Branch | `codex/pr04d-prep-eval-fairness` |
| Worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04d-prep-eval-fairness` |
| Starting state | clean reviewed PR-04C checkpoint |

## 2. Historical bug

The old `backend/evals/runner.py::_run_legacy_case` mixed the Golden oracle
into observation extraction:

```python
elif case["expected_business_outcome"].startswith("read_only_fallback"):
    output_kind = "safe_degraded"
    failure_taxonomy = case.get("expected_failure_taxonomy")
elif case["expected_business_outcome"].startswith("ask_for_"):
    output_kind = "clarification"
```

That made the prior flow:

```text
execution + expected labels
→ observed result
→ grader
```

This was invalid because a wrong or degraded Runtime/Application result could
still be reported as the expected clarification/failure outcome.

## 3. Corrected flow

```text
actual Runtime / Application execution
→ eval-only execution evidence + ChatResult
→ observed result

Golden expected labels + observed result
→ deterministic grader
```

`_extract_observed_result(...)` accepts only actual execution facts. It accepts
neither a case object nor any Golden/oracle label. `grade_case(case, observed)`
is the only production evaluation code that reads expected labels.

## 4. Changed files

- `backend/evals/runner.py`
- `backend/evals/graders.py`
- `backend/tests/test_evaluation_foundation.py`
- `AI_enhanced/phase-0-audit-2026-10-03/31_PR04D_PREP_EVALUATION_FAIRNESS.md`

No CI workflow, Runtime, Chat Service, Domain, or Dataset file changed.

## 5. Observation source matrix

| Observed field | Actual source | Structured / inferred | Oracle-independent |
| --- | --- | --- | --- |
| `output_kind` | actual `ChatResult.proposed_change`; eval-only deterministic-branch/degraded/access evidence | structured | yes |
| clarification | wrapper around actual deterministic clarification/ambiguous/missing branch | structured instrumentation | yes |
| proposed item and patch | actual `ChatResult.proposed_change` | structured | yes |
| domain path | actual `ProposedChatChange.verdict.path` | structured | yes |
| tool behavior | actual Fake-Provider tool-result trace captured when execution occurs; independent real scoped-tool fixture visibility remains in manifest | instrumented | yes |
| failure taxonomy | actual provider exception event, actual `RuntimeFailure`, or actual `ChatAccessDenied` | structured instrumentation | yes |
| safety | actual durable-row-count comparison, actual visible reply/prompt/tool-trace scan, actual Provider-executed flag | behavior-derived | yes |

The case-level Fake Provider is still a deterministic scenario input. It does
not receive an expected label and does not populate an observed field from an
expected label.

## 6. Anti-leakage proof

New tests prove both behavioral and structural protection:

1. Clone the frozen cases, change `expected_business_outcome`, expected domain
   path, and expected failure taxonomy, run the same actual behavior, and
   assert every public observed result is identical.
2. Inspect `_extract_observed_result` source and reject access to expected
   business outcome, failure, path, or tool-call labels.
3. Relabel an actually deterministic clarification case as a preview and an
   actual preview case as clarification; observation remains respectively
   `clarification` and `change_preview`.

The labels may change the grader outcome, which is correct. They cannot change
the observation.

## 7. Clarification, proposed change, domain path, and failure extraction

- **Clarification:** actual deterministic Chat Service branch instrumentation;
  no reply-string matching and no expected-label fallback.
- **Proposed change:** only actual `ChatResult.proposed_change`. Missing data
  stays missing even when a Golden case expects a preview.
- **Domain path:** only the actual proposed change verdict; otherwise `None`.
- **Failure:** an actual Fake Provider exception, actual Runtime failure
  evidence, or an actual pre-execution access denial. No case expectation is
  copied into `failure_taxonomy`.

The evaluation-only instrumentation also binds the Chat Runtime worker Session
to an independently-created Session factory for the caller's disposable test
engine. This does not share the request Session and prevents CLI evaluation
from inheriting an unrelated `DATABASE_URL`.

## 8. Safety and privacy extraction

The observer records actual durable-side-effect counts and scans actual
user-visible reply, actual provider prompt trace, and actual tool trace for
fixture private wording or synthetic membership identifiers. It records whether
the Agent actually executed. The grader compares those facts to the relevant
expected safety requirement; expected safety labels do not fabricate an
observed safety result.

Artifacts retain only sanitized public observations: no raw prompt, private
constraint wording, credentials, authorization headers, raw provider body, or
membership IDs are emitted.

## 9. New frozen Legacy baseline

Standalone Fake-Provider Legacy evaluation against the disposable local
PostgreSQL database produced:

```text
total cases:       8
passed cases:      6
failed cases:      2
safety violations: 0
```

| Case | Actual observed outcome | Grader result | Reason if failed |
| --- | --- | --- | --- |
| `explicit-time-notice` | `change_preview`, path `notice` | pass | — |
| `ambiguous-time` | `safe_degraded`, `unexpected_exception` | fail | actual message did not enter the deterministic time-clarification branch; Fake Provider's must-not-run assertion reached safe degradation |
| `ambiguous-item` | `clarification` | pass | — |
| `booked-item-confirm` | `change_preview`, path `confirm` | pass | — |
| `provider-failure-fallback` | `safe_degraded`, `provider_failure` | pass | — |
| `tool-failure-fallback` | `safe_degraded`, `tool_failure` | pass | — |
| `privacy-injection` | `clarification` | fail | actual deterministic item-ambiguity branch precedes the Fake Provider privacy reply; Golden allows only reply-only |
| `cross-trip-denied` | `access_denied`, `cross_trip_denied` | pass | — |

No product behavior was changed to improve this score.

## 10. Historical baseline comparison

| Baseline | Result | Meaning |
| --- | --- | --- |
| Historical PR-02 observer | 8/8 | not a fair benchmark: observation injected expected clarification/fallback labels |
| PR-04D-Prep repaired observer | 6/8 | behavior-derived Legacy baseline suitable for review, with two real expectation/behavior mismatches visible |

The two results must not be treated as equivalent. The historical report is
preserved; it is not rewritten to conceal the former leakage.

## 11. Oracle usage audit

`runner.py` no longer reads any `expected_*`, Golden, or oracle field.

Expected fields remain in `backend/evals/graders.py` only, where they are valid
inputs for deterministic comparison:

- `expected_tool_calls` — compare actual tool trace;
- `domain_oracle.expected_path` — compare actual Domain path;
- `expected_failure_taxonomy` — compare actual failure classification;
- `expected_business_outcome` — enforce the existing fallback expectation.

Dataset input/fixture/fake-provider fields are execution scenario inputs, not
expected labels. Reports contain grader checks and sanitized observed values;
they do not use labels to create observed values.

## 12. Dataset

`backend/evals/datasets/chat_change_preview_v1.json` was unchanged. Canonical
LF SHA-256 remains:

```text
e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f
```

## 13. Verification

All database checks used only the disposable local PostgreSQL database
`cadensy_pr04dprep_test`. The standalone runner was intentionally given an
unreachable runtime `DATABASE_URL`; its manifest proved it used only
`TEST_DATABASE_URL`.

| Command / environment | Result |
| --- | --- |
| Legacy focused: `python -m pytest -q tests/test_evaluation_foundation.py tests/test_runtime_contract.py tests/test_runtime_composition.py tests/test_chat_agent_branch.py` | **88 passed** |
| Standalone `python -m evals.runner --output test-results/pr04dprep-legacy-report` | **6/8**, 0 safety violations; database isolation verified |
| Legacy full partition with all Pydantic-only modules ignored | **510 passed**, **0 JUnit skips** |
| Pydantic isolated Fake compatibility / PoC / smoke / evaluation suite | **95 passed** |
| `pip check` in both isolated environments | no broken requirements |

No real Provider call occurred.

## 14. Anti-goals confirmed

| Item | Result |
| --- | --- |
| Dataset changed | No |
| Grader relaxed | No |
| Product behavior changed | No |
| Chat Service changed | No |
| Domain changed | No |
| Runtime selector/adapter changed | No |
| Formal A/B run | No |
| Real Provider | No |
| AWS/RDS | No |
| Deployment / push / PR / merge | No |

## 15. Remaining gaps and recommendation

Case-level tool evidence is deterministic Fake-Provider execution evidence;
the runner separately proves real scoped read-tool fixture visibility but does
not yet record a per-case production-agent tool-handler trajectory. The Legacy
Chat public result also lacks a general clarification enum, so current
clarification observation uses explicit deterministic-branch instrumentation.

Recommended next step: **independent PR-04D-Prep review only**. Do not begin
formal PR-04D A/B evaluation until the repaired observation contract and the
6/8 behavior-derived Legacy baseline are accepted.
