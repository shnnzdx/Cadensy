# PR-04C — Controlled Runtime Composition and Default-Legacy Selector

## Verdict

**PASS for independent PR-04C review.** The Application Service now uses the
reviewed framework-neutral runtime seam, while the default remains the reviewed
Legacy runtime. No provider, deployment, AWS/RDS, or real DeepSeek action was
performed.

## 1. Verified base

| Item | Verified value |
| --- | --- |
| `origin/main` | `c4ff19c8d0d4efd62d79f9aba2ac3005c7ed0143` |
| Reviewed PR-04B local checkpoint | `c5f4098f17474361ad9fb17b460fa51a3891b687` |
| PR-04C branch | `codex/pr04c-runtime-selector` |
| PR-04C worktree | `C:\Users\zdxzh\Desktop\capstone\New-pr04c-runtime-selector` |
| Starting status | clean reviewed checkpoint; PR-04C changes were created only in this worktree |

## 2. Changed files

- `.github/workflows/build-validation.yml`
- `backend/app/agents/runtime_factory.py` (new)
- `backend/app/agents/legacy_runtime.py`
- `backend/app/domain/chat/service.py`
- `backend/tests/test_chat.py`
- `backend/tests/test_chat_agent_branch.py`
- `backend/tests/test_ci_workflow_contract.py`
- `backend/tests/test_pr01c_agent_lifecycle.py`
- `backend/tests/test_pydantic_runtime.py`
- `backend/tests/test_runtime_composition.py` (new)
- `backend/tests/test_runtime_contract.py`

The Golden Dataset was not changed.

## 3. Before and after call path

Before PR-04C:

```text
Chat Service
  -> _run_chat_agent_with_timeout
  -> base.call_agent
```

After PR-04C:

```text
Chat Service deterministic preflight
  -> RuntimeRequest + LegacyReadTripCapability + PR-01C execution config
  -> build_chat_runtime
  -> selected ChatAgentRuntime
       -> LegacyChatAgentRuntime -> base.call_agent
       -> PydanticChatAgentRuntime only through explicit injected composition
  -> application validation/reclassification
```

The deterministic preflight remains in `respond_to_trip_chat` before runtime
composition: history option selection, item/reference resolution, ambiguity,
known-fact clarification, and explanation responses return without a Runtime
call.

## 4. Selector contract

`backend/app/agents/runtime_factory.py` owns `CHAT_AGENT_RUNTIME` selection.

| Environment value | Result |
| --- | --- |
| missing | direct `LegacyChatAgentRuntime` |
| empty | direct `LegacyChatAgentRuntime` |
| `legacy` | `LegacyChatAgentRuntime` |
| `pydantic` | only an explicitly injected Pydantic composition factory |
| any other value | `ChatRuntimeConfigurationError`, before runtime execution |

No aliases are accepted. There is no `try Pydantic then Legacy` or `try Legacy
then Pydantic` path.

## 5. Default behavior and Legacy parity

With no selector environment variable, the factory directly creates
`LegacyChatAgentRuntime`; it does not import or attempt Pydantic first. The
Service test verifies the default adapter receives one common request with the
selected item reference and existing deadline configuration.

Preserved Legacy behavior includes:

- the normalized user message and history sent to Legacy;
- selected-item interpretation in the Application Service;
- read-only scoped tools, guard/cache behavior, and PR-01C execution boundary;
- candidate options as the independent `RuntimeResult.candidate_options` field;
- existing safe reply sanitation and completed-change wording protection;
- deterministic Domain reclassification; and
- Legacy replacement preview provenance.

Legacy replacement data needs the previous raw `find_replacement_place` tool
evidence. `LegacyReadTripCapability` now holds that evidence privately for the
single Application revalidation step. It is not placed in the common Runtime
DTO and Pydantic cannot use it to acquire replacement support.

## 6. Pydantic composition

The isolated Pydantic environment proves that an explicit `pydantic` selector
can return an injected `PydanticChatAgentRuntime` backed by a local
`FunctionModel`. Chat Service composition tests use an injected test Runtime to
prove common reply, clarification/failure, supported suggestion, and
fail-closed handling behavior without a live Provider.

Current Pydantic support is unchanged from PR-04B:

- supported suggested fields: `start_hour`, `day_date`, `duration_min`;
- no replacement fields or replacement provenance;
- no candidate-options parity;
- no production Provider factory in this PR.

Consequently `CHAT_AGENT_RUNTIME=pydantic` without explicit, reviewed provider
composition fails closed into the existing safe degraded Chat response. It does
not fall back to Legacy. This is controlled composition wiring, not a production
Pydantic rollout.

## 7. Application and Domain boundary

Runtime output is syntax and observation, not authority. The Service:

1. validates the returned `item_ref` against its request-side scoped items;
2. normalizes the suggested patch;
3. enforces the expected selected/reference item where relevant;
4. rejects replacement-shaped common results without Legacy provenance; and
5. recomputes `orch.classify_change` in the request Session before creating a
   `ProposedChatChange` preview.

This is not a fresh authoritative reread, revision validation, or optimistic
concurrency guarantee. Those remain later hardening work.

`selected_item_ref` is semantic context only. It is constructed by the
Application Service and never grants trip or membership authority. Authority is
held by `LegacyReadTripCapability`, which contains trip/actor scope and opens
its own worker Session.

## 8. Session ownership and failure handling

The request `Session` remains limited to deterministic preflight and Domain
reclassification. The capability receives a Session factory, not a request
Session; it opens and closes its own short-lived worker Session around scoped
read tools. Test fixtures that intentionally keep rows in an uncommitted test
transaction now supply an empty fake read-tool surface at the adapter seam;
they do not pass the request Session into the worker. Dedicated PR-01C tests
continue to exercise independent Session creation, cleanup, cancellation, and
late-result discard.

`RuntimeResult.observation.failure` is now the common Service failure signal.
Selected Runtime failure (or configuration error) takes the existing redacted,
deterministic degraded reply path. Exception messages, provider bodies,
credentials, prompts, and raw tool payloads are not exposed. Silent fallback:
**NO**.

## 9. Dependency isolation and CI

`runtime_factory.py` imports only the Runtime contract and Legacy adapter at
module load. It does not import `pydantic_ai`, `pydantic_runtime`, a Provider
SDK, or a smoke harness. The Legacy-only Python 3.13.5 environment has no
`pydantic_ai` package and successfully imports/runs the Chat Service.

The Pydantic isolated CI job now explicitly executes
`tests/test_runtime_composition.py` as well as the Pydantic adapter/PoC/smoke
tests. The Legacy job remains independent, has no Pydantic installation, and
its required names are unchanged.

## 10. Test evidence

All commands were run with only local disposable PostgreSQL URLs,
`DISABLE_SCHEDULER=1`, `MOCK_AI=1`, empty DeepSeek/Geoapify values, and no
`CHAT_AGENT_RUNTIME` for the default-Legacy partition.

```text
Legacy Python 3.13.5:
python -m pytest -q \
  --ignore=tests/test_pydantic_ai_poc.py \
  --ignore=tests/test_pydantic_runtime.py \
  --ignore=tests/test_provider_smoke_harness.py \
  --ignore=tests/test_live_provider_smoke_adapter.py \
  --junitxml=test-results/pr04c-legacy-junit.xml
=> 504 passed in 27.32s
=> JUnit collected skips: 0

Legacy Python 3.13.5:
python -m pip check
=> No broken requirements found.

Pydantic PoC Python 3.13.5:
python -m pytest -q \
  tests/test_pydantic_ai_poc.py \
  tests/test_pydantic_runtime.py \
  tests/test_runtime_composition.py \
  tests/test_provider_smoke_harness.py \
  tests/test_live_provider_smoke_adapter.py \
  tests/test_evaluation_foundation.py
=> 89 passed in 4.50s

Pydantic PoC Python 3.13.5:
python -m pip check
=> No broken requirements found.
```

Focused composition/runtime/lifecycle/HTTP/CI-contract evidence also passed:
`101 passed in 3.42s`.

Frozen normalized Dataset SHA-256:

```text
e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f
```

## 11. Anti-goals confirmed

| Item | Status |
| --- | --- |
| Pydantic made default | No |
| silent Runtime fallback | No |
| A/B comparison or rollout | No |
| Domain-policy change | No |
| Golden Dataset change | No |
| real Provider call | No |
| AWS/RDS operation | No |
| deployment | No |
| Git push / PR / merge | No |

## 12. Remaining risks and recommended next step

The following remain explicitly unproven or unsupported:

- Pydantic replacement proposals and candidate-option parity;
- full selected-item behavioral parity beyond the reviewed Application context
  construction (the Pydantic adapter still does not independently consume
  `selected_item_ref`);
- real-provider Pydantic behavior in this composed path;
- comparative quality, latency, cost, and reliability; and
- deployment/provider configuration correctness.

Recommended next step: **independent PR-04C review only**. Do not automatically
start PR-04D.
