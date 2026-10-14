# PR-06A.3 — Request and Domain Correlation Report

The `/api/trips/{trip_id}/chat` wrapper is the sole terminal owner. It creates one
typed context, emits `chat.request_started`, invokes the existing implementation,
and emits exactly one `chat.request_completed` from `finally`, including HTTP access,
not-found, and conflict exits. The selected context is passed through the chat service
and B.3 Preview runtime input.

Service degradation emits `agent.fallback` only when the existing runtime failure or
empty-reply fallback is actually applied. `domain.classification_completed` is emitted
only around actual `orch.classify_change` calls, with a closed domain error category.

No domain authorization, transaction, revision, idempotency, API response shape, or
Preview authority policy was changed.
