# Cadensy PR-04A — Runtime Contract and Legacy Adapter Characterization

Status: implemented and locally verified on a disposable PostgreSQL database
with the Legacy dependency environment and fake AI only. This is an additive
seam. It is not a Pydantic AI integration, Chat-route migration, selector,
feature flag, provider change, deployment, AWS/RDS operation, or production
verification.

## 1. Verified base and workspace

| Item | Evidence |
| --- | --- |
| Verified base | `origin/main` at `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` after `git fetch origin --prune` |
| Feature branch | `codex/pr04a-runtime-contract` |
| Worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04a-runtime-contract` |
| Starting state | Clean new worktree created from `origin/main`; the original worktree was dirty with unrelated user-owned AWS/document changes and was not modified. |
| Legacy test environment | Existing isolated `New\backend\.venv-pr03r1a-legacy`, Python 3.13.5; `pydantic-ai-slim` absent; `pip check` passed. |
| Test database | `postgresql+psycopg://…@localhost:5432/cadensy_pr04a_test`, a named disposable local database. |

## 2. Changed files

| File | Purpose |
| --- | --- |
| `backend/app/agents/runtime_contract.py` | Framework-neutral DTOs, `ReadTripCapability`, and `ChatAgentRuntime` Protocol. |
| `backend/app/agents/legacy_runtime.py` | Thin, non-wired Legacy adapter and worker-owned scoped read capability. |
| `backend/tests/test_runtime_contract.py` | Contract/characterization tests. |
| `backend/tests/runtime_contract_fake_runtime.py` | Test-only alternative Runtime using only the neutral capability/tool API. |
| `AI_enhanced/phase-0-audit-2026-10-03/28_PR04A_RUNTIME_CONTRACT_IMPLEMENTATION.md` | This implementation record. |

## 3. Final framework-neutral contract

### RuntimeRequest

`RuntimeRequest` is frozen and contains only:

* `message`;
* bounded `RuntimeHistoryTurn` values;
* optional `selected_item_ref`, which is semantic context only;
* server-generated `request_id`;
* explicit `RuntimeLimits` (round, aggregate-token, per-response-token, and
  guard-rejection limits).

It contains no trip ID, actor/membership ID, request Session, ORM entity,
FastAPI Request, credential, or provider configuration.
`selected_item_ref` remains intentionally non-authoritative. The existing
current Chat Service continues to turn selected-item context into its Legacy
user message before calling `base.call_agent`; this non-wired adapter does not
invent a second reference-resolution path.

### ReadTripCapability

`ReadTripCapability` has one narrow operation:
`run_with_read_only_tools(operation)`. Its `RuntimeReadTool` descriptors expose
only a name, description, neutral parameter schema, and scoped `invoke(...)`
operation. They do not expose SQL, a Session, commit/flush, write commands,
scope IDs, Legacy guard state, or cache/orchestration policy.

The Legacy implementation, `LegacyReadTripCapability`, privately holds the
immutable trip ID, actor membership ID, and a controlled Session factory. It
opens a worker-owned Session, calls the existing
`build_read_only_trip_tools(...)`, runs the supplied operation, and closes that
Session at operation completion. It is the sole Runtime-side scope authority;
`selected_item_ref` cannot override it.

### RuntimeResult

`RuntimeResult` contains:

* a required non-empty user-visible `reply` for normal success;
* one primary `outcome`: `AgentClarification`, `AgentSuggestedChange`, or
  `AgentReplyOnly`;
* an orthogonal tuple of `AgentCandidateOption` values;
* a separate, redacted `RuntimeObservation`.

`AgentSuggestedChange` contains only `item_ref` and a filtered safe patch.
`safe_patch` means allowed-field filtering and Runtime/model suggestion syntax
only: it is not Domain-authorized, fully value-validated, fresh-state/revision
validated, replacement-authorized, transactional, or an apply instruction. It
has no Domain verdict, notice/round/confirm information, revision, proposal,
vote, or apply authority. Therefore a reply, suggested change, and candidate
options can coexist exactly as the Legacy `AgentRunResult` tool trajectory can.
Failure observations may carry an empty reply so the existing Application
Service can retain its deterministic safe-degradation behavior.

The common contract imports no Pydantic AI, OpenAI, DeepSeek, FastAPI, or
SQLAlchemy type.

## 4. Legacy adapter mapping

`LegacyChatAgentRuntime` is an independently callable thin wrapper around the
existing `base.call_agent(...)` loop. It uses existing tool schemas, guards,
cache behavior, provider catalog, limits, and PR-01C
`run_agent_with_deadline(...)`; it does not implement another provider/tool
loop. `LegacyRuntimeReadTool` privately translates the neutral descriptor back
to `base.AgentTool` only inside this Legacy adapter. A future Runtime consumes
the neutral descriptor and `invoke(...)` directly, without importing or
casting to `base.AgentTool`.

| Legacy `AgentRunResult` evidence | Neutral mapping |
| --- | --- |
| non-empty `content`, no valid `classify_change` result | `reply` + `AgentReplyOnly` |
| valid `classify_change` result | `reply` + `AgentSuggestedChange(item_ref, safe_patch)` |
| valid `propose_options` result | orthogonal `candidate_options` collection |
| both tool results | reply, suggested change, and candidate options together |
| stopped reason | empty reply plus redacted normalized `RuntimeFailure` observation |
| empty non-stopped content | `malformed_runtime_output` observation |

The adapter filters patch fields to the existing safe change-field set and
does not carry raw provider responses, headers, prompts, raw tool results, or
private wording in `RuntimeObservation`.

## 5. Classification-boundary preservation

No Domain rule or Chat Service behavior changed. The adapter only maps the
read-only `classify_change` tool result to non-authoritative suggested-action
syntax; it neither creates a `ProposedChatChange` nor invokes a Domain decision
as a final verdict.

The existing current/default path remains:

```text
respond_to_trip_chat
  -> _run_chat_agent_with_timeout
  -> base.call_agent
```

The existing Chat Service still validates returned item identity and normalized
patch against its request-side scoped item set, then recomputes deterministic
Domain classification in its request Session for a preview. PR-04A does not
claim or add a fresh item reread, snapshot reload, revision validation, or
optimistic concurrency guarantee.

## 6. Execution and Session ownership preservation

The adapter calls the pre-existing PR-01C `run_agent_with_deadline` and accepts
the existing `AgentExecutionConfig`; it does not add a second cancellation or
timeout system. Request timeout, provider budget, tool budget, bounded worker
admission, late-result discard, and lifecycle behavior remain owned by
`app.agents.execution`.

Because the capability keeps its worker Session open around the complete tool
loop, the adapter cannot hand a Session-bound tool factory directly to
`base.call_agent`. Its actual deadline ordering is:

```text
pre-construction active check
→ capability opens the independent worker Session and constructs scoped tools
→ post-construction active check
→ Legacy Agent loop
```

The post-construction check occurs in the capability callback before Legacy
tool translation; an additional defensive check remains after that translation.
Focused failure-injection tests prove both that an already inactive request
never enters capability/session construction and that a request which expires
during construction cannot enter the provider loop. This does not claim a
running synchronous worker or remotely accepted request can be force-stopped.

The new capability characterization test uses a controlled Session factory and
proves the tool builder receives that worker Session with fixed capability scope
and closes it afterward. The established PR-01C lifecycle suite also passed in
the combined range. This is evidence of independent worker ownership, not a
claim that local cancellation kills a running synchronous worker or remotely
accepted provider request.

## 7. Minimal failure and observation mapping

| Legacy condition | Normalized failure |
| --- | --- |
| `token_limit_exceeded` / `round_limit_exceeded` | `usage_limit` |
| guard rejection limit | `malformed_runtime_output` |
| provider deadline | `provider_timeout` |
| tool deadline | `tool_timeout` |
| request deadline | `request_timeout` |
| worker capacity exhaustion | `runtime_capacity` |
| other unexpected exception | `unexpected_exception` |

The mapping records only the normalized kind and non-secret technical type or
stopped reason. It intentionally does not attempt PR-04D's full evaluation
taxonomy work.

## 8. Tests and verification

All test commands used only the disposable local target with:

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/cadensy_pr04a_test'
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
$env:GEOAPIFY_API_KEY=''
$env:DEEPSEEK_API_KEY=''
```

| Command | Result | Evidence |
| --- | --- | --- |
| `python -m pytest -q tests/test_runtime_contract.py` | **12 passed** | Reply/outcome mapping, neutral alternative-Runtime invocation, deadline construction/discard (including inactive-before-construction), scope/session ownership, AST isolation, taxonomy, and current Chat path not wired to the adapter. |
| `python -m pytest -q tests/test_runtime_contract.py tests/test_agents_call_agent.py tests/test_agent_execution_lifecycle.py tests/test_pr01c_agent_lifecycle.py tests/test_chat_agent_branch.py tests/test_evaluation_foundation.py` | **90 passed** | Corrected seam plus Legacy loop, lifecycle/session, Chat branch, and evaluation regression contracts. |
| `python -m pytest -q --ignore=tests/test_pydantic_ai_poc.py --ignore=tests/test_provider_smoke_harness.py --ignore=tests/test_live_provider_smoke_adapter.py --junitxml=<temporary file>` | **487 passed; 0 collected skips** | Full Legacy regression with the approved CI partition; no Pydantic dependency was added to the Legacy environment. |
| `python -m pip check` | **No broken requirements** | Legacy environment consistency. |
| UTF-8-normalized SHA-256 of `evals/datasets/chat_change_preview_v1.json` | **`e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`** | Frozen Golden Dataset unchanged. |

The bare Legacy-environment `pytest -q` is not the valid partition command:
it attempts to collect the three intentionally isolated Pydantic/smoke modules
without their dependency. The approved CI command above explicitly excludes
them and proves the zero-collected-skip contract. No skip, xfail, or insecure
authentication fallback was introduced.

## 9. Review Corrections Applied

1. **Framework-neutral capability usability:** `RuntimeReadTool` now provides
   neutral descriptor fields and `invoke(...)`; a test-only alternative Runtime
   imports only the common contract and successfully invokes a scoped Legacy
   read tool without importing or casting to `base.AgentTool`.
2. **Deferred-tool deadline parity:** the Legacy adapter uses PR-01C's existing
   execution context and explicitly checks request activity before it enters
   capability/tool construction and again after construction, before the Legacy
   Agent loop. A deterministic inactive-deadline test proves capability/session
   construction does not begin when the request is already inactive; a blocking
   construction test proves no provider loop starts after construction outlives
   the request.
3. **AST isolation detector:** import detection now examines `ast.Import` alias
   modules and `ast.ImportFrom.module`; unit examples verify that both
   `from pydantic_ai import Agent` and `from sqlalchemy.orm import Session` are
   detected.
4. **Capacity taxonomy:** `RuntimeFailure.normalized_kind` is now a minimal
   `Literal` set, and `AgentExecutionCapacityExceeded` maps to
   `runtime_capacity`, not `usage_limit`.
5. **safe_patch semantics:** contract and report now state that filtering known
   fields is not Domain/value/fresh-state/revision/replacement/transactional
   authorization.

## 10. Explicit anti-goals verified

| Anti-goal | Result |
| --- | --- |
| Current Chat Route/Service execution wiring changed | No |
| Runtime selector or `CHAT_AGENT_RUNTIME` added | No |
| Feature flag added | No |
| Pydantic Runtime implemented or imported into common contract | No |
| DeepSeek configuration or Provider call changed/made | No |
| Domain/Organizer policy or write tools changed | No |
| Fresh reread, revision, or optimistic concurrency behavior added | No |
| Frozen Golden Dataset or evaluation oracle labels changed | No |
| AWS/RDS, migration, deployment, push, PR, or merge action | No |

## 11. Remaining risks and recommended next step

* Candidate-option mapping characterizes direct Legacy tool output only.
  Deterministic history selection, fuzzy/exact resolution, stale-option replies,
  and authoritative `ProposedChatChange` construction remain intentionally in
  Chat Service; future adapters need separate parity evidence.
* Legacy tool-loop output has no explicit semantic clarification tag. The
  common contract supports `AgentClarification` with explicit reply wording,
  but PR-04A does not invent a heuristic that relabels current free-text Legacy
  replies. Deterministic preflight clarification remains Chat Service behavior.
* The adapter maps suggested syntax only. Current request-side reclassification
  is preserved outside it and remains neither a fresh reread nor a concurrency
  guarantee.
* The full Legacy run proves the approved CI partition, not the isolated
  Pydantic/smoke suite. Those remain governed by their own lock and CI job.

Recommended next step: independent review of this PR-04A diff. Only after
separate authorization may PR-04B introduce an isolated Pydantic adapter behind
this contract; it must not change the frozen dataset, current Chat path, or
Domain authority.
