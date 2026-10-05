# Cadensy PR-04B — Isolated Pydantic AI Runtime Adapter

Status: locally implemented and verified with the isolated Pydantic fake-model
environment and a disposable local PostgreSQL database. This is an additive,
unwired adapter. It is not Chat-route integration, a runtime selector, a
feature flag, a Provider rollout, an A/B result, or deployment evidence.

## 1. Verified base

| Item | Value |
| --- | --- |
| `origin/main` after `git fetch origin --prune` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| Reviewed PR-04A local checkpoint | `fee8332221c583a35b88600ac0dbcd2f8dc23ead` |
| PR-04B branch | `codex/pr04b-pydantic-runtime` |
| PR-04B worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04b-pydantic-runtime` |
| Start state | A PR-04A reviewed-but-uncommitted worktree with exactly the five reviewed PR-04A files. It was checkpointed locally; no push occurred. |
| Pydantic environment | Existing isolated `New\backend\.venv-pydantic-poc`, Python `3.13.5`, from `requirements-pydantic-ai-poc.lock.txt` |
| Framework versions | Pydantic `2.13.4`; `pydantic-ai-slim[openai]` `2.54.0`; OpenAI SDK `3.24.0` |
| Test database | Named disposable local PostgreSQL `cadensy_pr04a_test`; no AWS/RDS connection was used. |

The original `New` worktree, which contains user-owned unrelated changes, was
not modified.

## 2. Changed files

| File | Purpose |
| --- | --- |
| `backend/app/agents/pydantic_runtime.py` | Isolated `PydanticChatAgentRuntime` adapter, typed output/tool boundary, same-run evidence, limit/deadline mapping, and redacted failure mapping. |
| `backend/tests/test_pydantic_runtime.py` | Fake-model and failure-injection tests at the public common Runtime seam. |
| `.github/workflows/build-validation.yml` | Explicitly keeps the new Pydantic module out of the Legacy zero-skip partition and runs it in the existing Pydantic isolated job. |
| `backend/tests/test_ci_workflow_contract.py` | Pins that CI partition contract. |
| `AI_enhanced/phase-0-audit-2026-10-03/29_PR04B_PYDANTIC_RUNTIME_ADAPTER.md` | This implementation record. |

No dependency lockfile changed. PR-04A's common contract, Legacy adapter,
Chat Service, Domain code, dataset, and Provider smoke Harness were not
modified.

## 3. Adapter architecture

```text
RuntimeRequest
      ↓
PydanticChatAgentRuntime.run(...)
      ↓
PR-01C run_agent_with_deadline
      ↓
ReadTripCapability
      ↓
scoped neutral RuntimeReadTool descriptors
      ↓
Pydantic AI typed Agent / output validation
      ↓
RuntimeResult
```

`PydanticChatAgentRuntime` implements the existing `ChatAgentRuntime` Protocol:

```text
run(RuntimeRequest, ReadTripCapability, execution=AgentExecutionConfig)
    -> RuntimeResult
```

It imports neither `app.agents.base` nor `base.AgentTool`, and it does not use
`LegacyRuntimeReadTool._legacy_tool` or
`LegacyRuntimeReadTool._for_legacy_orchestration`. The common
`runtime_contract.py` remains Pydantic-free.

## 4. Pydantic-specific internals

The adapter-local implementation uses:

* Pydantic AI `Agent` with typed `ToolOutput` result tools;
* a local `_PydanticRunDeps` object holding only neutral tool descriptors, the
  PR-01C execution context, and ephemeral read/trajectory state;
* one typed function tool and strict Pydantic argument parsing;
* typed reply-only, clarification, and suggested-change models;
* `ModelRetry` for bounded output-validation retry; the minimum read tool has
  its own zero-retry policy;
* explicit `UsageLimits`;
* a `_DeadlineBoundModel` wrapper that applies remaining PR-01C provider budget
  before and after every model request.

These framework types do not appear in the common Runtime contract, Legacy
adapter, Chat Service, or Domain layer. There is no production-like Provider
factory in this PR: tests inject `FunctionModel` only.

## 5. Minimum tool surface

PR-04B intentionally exposes only `get_current_plan`.

| Tool | Why exposed now | Model-visible parameters | Authorization / scope | Output use |
| --- | --- | --- | --- | --- |
| `get_current_plan` | Minimal useful change-preview grounding: read scoped plan facts before a typed suggestion. | Strict non-empty `day` only. | The injected `ReadTripCapability` owns immutable trip/actor scope and its independent Session. The model receives neither. | Its safe Current Plan projection contributes same-run item references used to validate a suggested change. |

The adapter exposes no classification, replacement, option, generic-query, or
write tool. Therefore candidate options are deliberately returned as `()` in
this PR; this is a known parity gap, not fabricated parity.

PR-04B Pydantic change suggestions support only:

* `start_hour`;
* `day_date`;
* `duration_min`.

They do not support replacement venue/title/location/price changes. A typed
output containing `title`, `place`, `price_per_person`, `lat`, or `lng` fails
closed through bounded structured-output validation; unsupported fields are
never silently dropped to form a partial suggestion. Replacement support
requires separately reviewed provenance tooling and is not implemented here.

The function-tool schema and test evidence establish that the model cannot pass
trip ID, membership ID, Session, SQL/query, or commit flag. The adapter validates
the `day` argument before it reaches the scoped tool; an invalid argument is a
bounded malformed-output failure and the scoped tool is never invoked.

## 6. Same-run scoped-read evidence

`get_current_plan` extracts only item IDs from the safe scoped tool result into
ephemeral `_PydanticRunDeps.read_item_refs`. The output validator rejects a
typed suggested change unless its `item_ref` appears in that set during the
same invocation. A rejected output receives at most the configured one retry
(`RuntimeLimits.guard_reject_limit` in the covered request); exhaustion maps to
a failure rather than a partial `AgentSuggestedChange`.

This prevents a suggestion founded solely on a prompt, history entry, selected
item hint, or guessed ID. `selected_item_ref` remains an unauthoritative
semantic hint. A forged/cross-trip ID is rejected because it was not returned
by the scoped read tool; scope itself remains entirely inside the capability.

## 7. Output mapping and Domain boundary

| Typed adapter output | Common Runtime output |
| --- | --- |
| Reply model | non-empty `reply` + `AgentReplyOnly()` |
| Clarification model | non-empty question/wording in `reply` + `AgentClarification()` |
| Suggested-change model | non-empty `reply` + `AgentSuggestedChange(item_ref, safe_patch)` |
| Candidate options | empty tuple in PR-04B |
| Failed run | empty reply + `AgentReplyOnly()` + normalized `RuntimeFailure` |

`safe_patch` is restricted to the provenance-free schedule/date/duration fields
with basic scalar and calendar validation. It remains model syntax only: it is not a
Domain-authorized patch, deterministic verdict, authoritative preview,
fresh-state/revision-safe record, write command, proposal, vote, or apply.

The Runtime may observe scoped plan facts but makes no authoritative
classification and never invokes a mutation. Current Chat Service behavior is
unchanged and still owns request-side validation plus deterministic Domain
classification when it builds an authoritative preview. This PR does not add a
fresh reread, revision check, or optimistic concurrency behavior.

## 8. Execution, deadline, and Session ownership

The outer boundary remains exactly PR-01C:

```text
run_agent_with_deadline
    ↓
pre-capability request-active check
    ↓
capability opens independent worker Session / builds scoped tools
    ↓
post-construction request-active check
    ↓
Pydantic typed Agent run
```

`_DeadlineBoundModel` calls `before_provider()` before every Pydantic model
request, supplies the remaining bounded timeout setting, and calls
`after_provider()` before a response can be consumed. This is an inner adapter
control; PR-01C remains the hard outer request deadline, worker-capacity, local
cancellation, and late-result-discard authority.

Function-tool timing intentionally uses PR-01C only:

```text
before_tool → neutral RuntimeReadTool.invoke → after_tool
```

Pydantic's separate `tool_timeout` is disabled rather than double-counting a
second timeout. The exposed Pydantic function tool has no framework retry
budget. A PR-01C tool deadline therefore causes
`AgentToolDeadlineExceeded` to end the run and normalize to `tool_timeout`; a
late tool result is not incorporated into a subsequent model turn.

The adapter stores no request-scoped SQLAlchemy Session, ORM entity, trip ID,
or membership ID in Pydantic dependencies. `LegacyReadTripCapability` proves
the adapter can consume the neutral surface while an independent worker Session
is created and closed by the capability. Cancellation signals local work and
discards late results; it does not claim to force-kill a synchronous worker or
an already accepted remote request.

## 9. Limits, retries, usage, and failures

`RuntimeLimits` maps conservatively:

| Common input | Pydantic control | Limitation |
| --- | --- | --- |
| `max_rounds` | request limit and tool-call limit | Pydantic request/tool accounting is not identical to Legacy rounds. |
| `max_total_tokens` | total-token limit | Framework-reported usage only. |
| `max_tokens` | output-token limit | It is a bounded Pydantic usage control, not a claim of exact Legacy per-response semantics. |
| `guard_reject_limit` | typed output-validation retry count | The exposed read tool has zero framework retries so a PR-01C tool timeout is terminal; this is not Legacy guard/cache parity. |

`RuntimeObservation.total_tokens` is `None` unless the framework reports a
positive token total. Fake-model usage is compatibility evidence only, not
Provider-billed usage; no unknown figure is represented as zero.

| Error family | Common `RuntimeFailure.normalized_kind` |
| --- | --- |
| PR-01C provider deadline / generic provider timeout | `provider_timeout` |
| PR-01C tool deadline | `tool_timeout` |
| PR-01C request deadline or local cancellation | `request_timeout` |
| PR-01C worker capacity | `runtime_capacity` |
| Pydantic `UsageLimitExceeded` | `usage_limit` |
| Pydantic malformed structured result or typed/output retry exhaustion | `malformed_runtime_output` |
| Capability composition failure, adapter `ValueError` / `TypeError`, or other unexpected exception | `unexpected_exception` with non-secret exception-class technical kind |

No raw prompt, tool payload, HTTP body, credential, Authorization header, or
private constraint wording is persisted in the Runtime observation.

## 10. Tests and verification

All tests set `MOCK_AI=1`, clear Provider key variables, and use the disposable
local PostgreSQL test URL. Pydantic tests additionally set
`models.ALLOW_MODEL_REQUESTS = False`, so a live model cannot be substituted
silently.

| Environment / command | Result |
| --- | --- |
| Pydantic `python -m pytest -q tests/test_pydantic_runtime.py tests/test_pydantic_ai_poc.py tests/test_provider_smoke_harness.py tests/test_live_provider_smoke_adapter.py tests/test_evaluation_foundation.py` | **71 passed** |
| Legacy `python -m pytest -q tests/test_runtime_contract.py` | **12 passed** |
| Legacy `python -m pytest -q tests/test_runtime_contract.py tests/test_chat_agent_branch.py tests/test_ci_workflow_contract.py` | **66 passed** |
| Approved Legacy partition `python -m pytest -q --ignore=tests/test_pydantic_ai_poc.py --ignore=tests/test_pydantic_runtime.py --ignore=tests/test_provider_smoke_harness.py --ignore=tests/test_live_provider_smoke_adapter.py --junitxml=<temporary file>` | **487 passed; 0 collected skips** |
| Pydantic and Legacy `python -m pip check` | **No broken requirements** in both isolated environments |

New adapter coverage includes reply-only, clarification, valid same-run
scoped-read suggestion, unread and forged-reference failure, replacement-shaped
and price-only rejection, strict tool arguments/schema, independent Session
cleanup, no durable plan/proposal/vote write, tool timeout, request timeout/late
discard, usage limit, malformed output retry exhaustion, capability-composition
and adapter-TypeError taxonomy, common-DTO output, no Legacy internals, and no
current Chat Service wiring.

## 11. Frozen dataset

`backend/evals/datasets/chat_change_preview_v1.json` remains byte-for-byte
unchanged in Git. Its project-canonical UTF-8/LF SHA-256 remains:

```text
e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f
```

No PR-04D observation-oracle work was attempted.

## 12. Review Corrections Applied

1. **Replacement-shaped patches fail closed:** PR-04B now accepts only
   `start_hour`, `day_date`, and `duration_min`. Title, venue, location, and
   price fields produce bounded typed-output validation failure rather than a
   partial `AgentSuggestedChange`.
2. **Replacement support is deferred:** the adapter still does not expose
   `find_replacement_place`, carry candidate provenance, or expand the common
   Runtime contract. Replacement suggestions require a separately reviewed
   provenance design.
3. **PR-01C tool deadline is terminal:** Pydantic's independent tool timeout
   remains disabled and the function tool has zero framework retries. A custom
   `AgentToolDeadlineExceeded` ends the run and maps to `tool_timeout`; this is
   not described as a normal Pydantic retry path.
4. **Failure taxonomy is narrower:** only Pydantic typed/output retry exhaustion
   maps to `malformed_runtime_output`. Missing minimum capability composition
   and unexpected adapter `ValueError`/`TypeError` map to
   `unexpected_exception`.
5. **Selected-item parity gap recorded:** PR-04B does not currently consume
   `RuntimeRequest.selected_item_ref`. Selected-item interpretation remains a
   Chat Application Service responsibility and must be handled explicitly if
   PR-04C composition is separately authorized. It remains semantic only, never
   authorization.

## 13. Explicit anti-goals

| Anti-goal | Result |
| --- | --- |
| Current Chat Route/Service wired to Pydantic | No |
| Runtime selector / `CHAT_AGENT_RUNTIME` / feature flag | No |
| Silent Legacy fallback | No |
| Legacy vs Pydantic A/B claim | No |
| Domain or Organizer policy change | No |
| Write tool, proposal, vote, Plan mutation, migration | No |
| Fresh reread / optimistic concurrency redesign | No |
| Frozen Golden Dataset change | No |
| Real DeepSeek/Provider call or API-key use | No |
| AWS/RDS action, deployment, push, PR, or merge | No |

## 14. Remaining parity gaps and recommended next step

* Candidate-option, fuzzy/history follow-up, Legacy guard, and cache behavior
  are intentionally not Pydantic-parity claims.
* Clarification is typed inside this adapter, while current Legacy clarification
  also includes deterministic Chat Service preflight; PR-04B does not merge or
  replace those responsibilities.
* The adapter does not consume `RuntimeRequest.selected_item_ref`; it neither
  grants scope nor supplies selected-item interpretation. That responsibility
  remains in Chat Application Service pending separately authorized PR-04C
  composition work.
* Pydantic request/tool/retry and usage semantics are bounded but not proven
  identical to Legacy. Real-provider behavior, quality, cost, reliability, and
  production deployment remain unproven.
* Pydantic's local cancellation and Pydantic model settings do not prove remote
  request termination.

Recommended next step: independent PR-04B review only. PR-04C composition,
runtime selection, any Provider use, and PR-04D evaluation require separate
authorization.
