# Cadensy AI Agent Implementation Map

Status: current implementation guide
Last reviewed: 2026-10-28

This file maps the current backend AI seams. Production and migration evidence belongs in [`../../../AI_enhanced/README.md`](../../../AI_enhanced/README.md); local operation belongs in [`../../LOCAL_DEV.md`](../../LOCAL_DEV.md).

## Recorded runtime state

Cadensy v0.3.0 records `CHAT_AGENT_RUNTIME=pydantic` on backend task definition `tripsync-backend:31`, with the Durable Conversation production latch enabled. CD-03 run `40382252075` is protected activation evidence, but it did not execute real Pydantic provider acceptance.

The local configuration default is `legacy`. That default provides a safe developer/test starting point and is not a production-state claim.

## Runtime boundary

| File | Responsibility |
| --- | --- |
| `runtime_contract.py` | Framework-neutral Chat runtime contract |
| `runtime_factory.py` | Validates `CHAT_AGENT_RUNTIME` and builds Legacy or Pydantic runtime |
| `legacy_runtime.py` | Established Legacy adapter |
| `pydantic_runtime.py` | Pydantic runtime adapter and result normalization |
| `pydantic_composition.py` | Production Pydantic/DeepSeek composition |
| `pydantic_poc.py` | Provider/model compatibility components retained by the composition path |
| `execution.py` | Agent execution lifecycle and safe outcome handling |
| `tools.py` | Trusted tool definitions and execution support |
| `trace.py` | Privacy-safe tracing/telemetry support |
| `e2e_runtime.py` | Test-only browser harness runtime; never a production fallback |

`runtime_factory.build_chat_runtime()` is the selector seam. Selecting `pydantic` is fail-closed: composition failure must not silently fall back to Legacy.

## Request and durable-conversation flow

- `backend/app/api/main.py` owns the HTTP endpoints and maps domain outcomes to API responses.
- `backend/app/domain/chat/service.py` builds the selected runtime, prepares trip-scoped context, and coordinates Chat behavior.
- `backend/app/domain/conversations/runtime.py` owns durable runtime coordination.
- `backend/app/domain/conversations/service.py` owns durable lifecycle, idempotency, conflict, busy, recovery, and fencing outcomes.
- `backend/app/domain/conversations/context.py` constructs server-owned conversation context.
- `backend/app/db/models.py` contains conversation, message, option, submission, and recovery persistence models.

Membership authorization and persisted server state are authoritative. A browser-provided membership ID or local conversation cache is not proof of identity or durable truth.

## Safety invariants

- Provider keys come from runtime secret configuration and never from source, logs, or evidence files.
- Tool execution must use trusted server-side arguments and retain authorization/trip scope.
- Idempotency and concurrency fences must survive runtime refactors.
- Telemetry must remain privacy-safe; do not record raw private constraints or provider payloads.
- `CADENSY_E2E_FAKE_RUNTIME` is valid only for the explicit test environment.
- Production execution requires both Durable Conversation gates and production signing material.

## Validation map

Core tests include:

- `tests/test_runtime_contract.py`
- `tests/test_runtime_composition.py`
- `tests/test_pydantic_runtime.py`
- `tests/test_pydantic_composition.py`
- `tests/test_pydantic_durable_runtime_acceptance.py`
- `tests/test_pydantic_concurrency_fencing_acceptance.py`
- `tests/test_agent_execution_lifecycle.py`
- `tests/test_pr06b2_conversation_api.py`
- `tests/test_pr06b2_conversation_concurrency_postgres.py`
- `tests/test_cd03_pydantic_production_release.py`

Run the smallest relevant group first, then the broader backend suite when the change crosses runtime, conversation, transaction, or persistence boundaries. Real-provider calls and production changes require separate authorization and must not be inferred from unit/integration success.
