# PR-06B.0 — Current Chat Architecture Audit

Status: contract-freeze audit only. No application, schema, Alembic, AWS, or PR-06C changes are included.

## Repository and branch facts

- Audited branch: `codex/pr06b-independent-development`.
- Audit baseline: `b589fc7786c097d4bfe0eafe430afd90b00ca2b7`.
- `git fetch origin --prune` completed during this audit. `HEAD`, `origin/main`, and `origin/codex/pr06b-independent-development` resolve to the same baseline; `origin/main...HEAD` is `0 0`.
- `codex/pr06c-production-telemetry-validation` remains a separate descendant branch. It is out of scope.

## Evidence-based current request path

### Personal Chat

`PersonalThread` owns `messages` with React local state, constructs a history list from that state, and calls `app.chatWithTrip`. It does not load or save a server-owned conversation. [trip/src/final/FinalApp.jsx:739](../../trip/src/final/FinalApp.jsx#L739), [trip/src/final/FinalApp.jsx:752](../../trip/src/final/FinalApp.jsx#L752)

The UI labels this thread “Just for you · not shared with the group.” That is a privacy promise which persistence must preserve. [trip/src/final/FinalApp.jsx:781](../../trip/src/final/FinalApp.jsx#L781)

### Plan Assistant

`useAssistantChangeRequestFlow` owns an independent local `messages` state. It resets that state when `item.id` or interaction mode changes, then serializes user-visible assistant `candidateOptions` with its history. [trip/src/final/plan-feature/useAssistantChangeRequestFlow.js:50](../../trip/src/final/plan-feature/useAssistantChangeRequestFlow.js#L50), [trip/src/final/plan-feature/useAssistantChangeRequestFlow.js:73](../../trip/src/final/plan-feature/useAssistantChangeRequestFlow.js#L73), [trip/src/final/plan-feature/useAssistantChangeRequestFlow.js:96](../../trip/src/final/plan-feature/useAssistantChangeRequestFlow.js#L96), [trip/src/final/plan-feature/useAssistantChangeRequestFlow.js:172](../../trip/src/final/plan-feature/useAssistantChangeRequestFlow.js#L172)

This is product-distinct from Personal Chat despite sharing an endpoint. The current reset behavior supports a V1 scope model of `global` and `item:<item_id>`; UI interaction mode must not become a conversation identity.

### Client API seam

`chatWithTrip` sends `message`, optional `item_id`, and only the final ten client-supplied history turns to `POST /api/trips/{tripId}/chat`. [trip/src/final/TripAppState.jsx:1027](../../trip/src/final/TripAppState.jsx#L1027)

The HTTP DTO accepts up to ten history turns and up to ten candidate options per turn. Candidate option display fields and `patch` are currently client-supplied. [backend/app/api/main.py:524](../../backend/app/api/main.py#L524), [backend/app/api/main.py:534](../../backend/app/api/main.py#L534), [backend/app/api/main.py:540](../../backend/app/api/main.py#L540)

### Standard service path

The normal endpoint enforces the trip scope, then converts client history into `HistoryTurn` / `HistoryCandidateOption` and calls `respond_to_trip_chat`. [backend/app/api/main.py:1268](../../backend/app/api/main.py#L1268)

`respond_to_trip_chat` checks that the Membership belongs to the Trip, loads the current Trip and Plan data, and passes history into the Agent branch. [backend/app/domain/chat/service.py:133](../../backend/app/domain/chat/service.py#L133)

That service uses history for item-reference resolution and a deterministic follow-up option-selection resolver before calling the Runtime. [backend/app/domain/chat/service.py:176](../../backend/app/domain/chat/service.py#L176), [backend/app/domain/chat/service.py:397](../../backend/app/domain/chat/service.py#L397), [backend/app/domain/chat/service.py:742](../../backend/app/domain/chat/service.py#L742)

The Runtime receives only textual `RuntimeHistoryTurn`s. [backend/app/domain/chat/service.py:222](../../backend/app/domain/chat/service.py#L222), [backend/app/agents/runtime_contract.py:17](../../backend/app/agents/runtime_contract.py#L17)

### Protected Preview path

When the protected Preview gate is enabled, the endpoint extracts primitive identity data, closes the request Session before Runtime work, then calls `preview_trip_chat_revision_bracketed`. [backend/app/api/main.py:1189](../../backend/app/api/main.py#L1189)

The revision-bracketed capability opens short read-only Sessions for tool construction and each invocation, rechecks active Membership and captured revisions, and never exposes a generic ORM session to a Runtime. [backend/app/domain/preview_apply.py:165](../../backend/app/domain/preview_apply.py#L165), [backend/app/domain/preview_apply.py:198](../../backend/app/domain/preview_apply.py#L198), [backend/app/domain/preview_apply.py:254](../../backend/app/domain/preview_apply.py#L254)

Important gap to resolve in PR-06B: this Preview request converts history to `RuntimeHistoryTurn` text only. The standard service’s deterministic historical candidate-option resolver is therefore not a common, server-owned abstraction today. [backend/app/domain/chat/service.py:64](../../backend/app/domain/chat/service.py#L64)

### Runtime and authority boundaries

Legacy maps the neutral history contract into the provider call while operating only through capability-provided read tools. [backend/app/agents/legacy_runtime.py:177](../../backend/app/agents/legacy_runtime.py#L177), [backend/app/agents/legacy_runtime.py:217](../../backend/app/agents/legacy_runtime.py#L217)

Pydantic runtime uses the same `RuntimeRequest`/capability contract, requires a scoped Current Plan read before a suggested change, and returns typed non-authoritative output. [backend/app/agents/pydantic_runtime.py:197](../../backend/app/agents/pydantic_runtime.py#L197), [backend/app/agents/pydantic_runtime.py:253](../../backend/app/agents/pydantic_runtime.py#L253), [backend/app/agents/pydantic_runtime.py:331](../../backend/app/agents/pydantic_runtime.py#L331)

Both paths remain read-only. Candidate suggestions are revalidated against authoritative Plan data and domain classification in the Preview layer. [backend/app/domain/preview_apply.py:380](../../backend/app/domain/preview_apply.py#L380), [backend/app/domain/preview_apply.py:425](../../backend/app/domain/preview_apply.py#L425)

## Identity and authorization facts

`current_membership` resolves an Account bearer plus `X-Trip-Id`, or a valid Guest bearer; a Membership ID is explicitly not an authentication credential. [backend/app/api/main.py:195](../../backend/app/api/main.py#L195)

Account sessions are revocable and expire. Guest sessions are bounded, revocable, Trip-bound, and additionally require an active Guest Membership state. [backend/app/domain/auth.py:223](../../backend/app/domain/auth.py#L223), [backend/app/domain/auth.py:249](../../backend/app/domain/auth.py#L249)

Account Membership lookup excludes `removed` rows. [backend/app/domain/auth.py:294](../../backend/app/domain/auth.py#L294) The Preview capability separately rechecks that the Membership remains active while Runtime work is underway. [backend/app/domain/preview_apply.py:534](../../backend/app/domain/preview_apply.py#L534)

## Schema and rejoin finding

`TripMembership` has a partial unique index on `(trip_id, user_id)` whenever `user_id IS NOT NULL`; it does **not** exclude `status = 'removed'`. [backend/app/db/models.py:148](../../backend/app/db/models.py#L148)

Therefore a later account rejoin cannot safely create a fresh Membership row without first changing that index. Reusing the removed row would violate D5 by reconnecting the new membership incarnation to old private conversations. PR-06B.1 must replace this with an active-only account membership uniqueness rule and create a new Membership row for rejoin. The Membership primary key is then the required ownership incarnation; no extra “incarnation number” is needed for V1.

## Current tests relevant to preservation

- `backend/tests/test_chat.py`: endpoint/auth/chat baseline coverage.
- `backend/tests/test_chat_agent_branch.py`: history reference resolution, ordinal candidate selection, stale options, and deterministic clarification behavior; examples begin at [backend/tests/test_chat_agent_branch.py:1079](../../backend/tests/test_chat_agent_branch.py#L1079).
- `backend/tests/test_b3_revision_bracketed_preview.py`: protected Preview revision and authorization behavior.
- `backend/tests/test_agents_tools.py` and `backend/tests/test_pr01c_agent_lifecycle.py`: scoped tools and bounded execution behavior.

## Audit conclusion

PR-06B must replace the client-provided history trust boundary with server-owned, Membership-owned durable state while preserving the existing deterministic resolver and read-only Runtime boundary. No current code proves persistent recovery, durable idempotency, retention/deletion, or cross-device account recovery; those are planned features, not completed capabilities.
