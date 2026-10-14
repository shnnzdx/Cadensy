# PR-06A.3 — Request and Domain Correlation Report

The `/api/trips/{trip_id}/chat` wrapper is the sole terminal owner. It creates one
typed context, emits `chat.request_started`, invokes the existing implementation,
and emits exactly one `chat.request_completed` from `finally`, including HTTP access,
not-found, conflict, and runtime-failure exits. It uses a typed internal terminal
fact, never reply text inference: success, deterministic clarification, degraded
fallback, 403, 404, 409, 503, and unhandled errors remain distinct. The selected
context is passed through the chat service and B.3 Preview runtime input.

Service degradation emits `agent.fallback` only when the existing runtime failure or
empty-reply fallback is actually applied. `domain.classification_completed` is emitted
only around actual `orch.classify_change` calls: legacy classification, Pydantic
suggested-change classification, deterministic history selection, B.3 Preview
candidate classification, and safe-option validation. Failures retain the original
business behavior and emit the closed domain error category best-effort.

The final request wrapper is an observation boundary: event construction, correlation
creation, enum mapping, replacement, emitter, and sink failures cannot alter the
existing response, authorization, transaction, or fallback behavior.

No domain authorization, transaction, revision, idempotency, API response shape, or
Preview authority policy was changed.
