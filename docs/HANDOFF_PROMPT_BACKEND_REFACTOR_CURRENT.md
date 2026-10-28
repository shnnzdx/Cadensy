# Backend Refactor Continuation Prompt

Status: current
Last reviewed: 2026-10-28

Use this only for backend cleanup or repository consolidation:

> Read `AGENTS.md`, `HANDOFF.md`, `docs/README.md`, `backend/README.md`, `backend/LOCAL_DEV.md`, `AI_enhanced/README.md`, and this prompt. Treat older files in `docs/backend/` as historical handoffs.
>
> Start with an architecture assessment. Map API composition, service dependencies, transaction ownership, durable conversation persistence, idempotency/concurrency boundaries, runtime selection, telemetry, and test seams. Rank candidates by locality, leverage, testability, migration risk, and the deletion test. Grill the strongest candidate, freeze the boundary, write a phased migration plan, then implement.
>
> Do not refactor solely because a module is large. Do not weaken membership authorization, server-owned conversation context, transaction ownership, idempotency, concurrency fencing, redaction, or fail-closed runtime selection. Keep `shared/session-runtime/` responsible for browser session mechanics and identity-header assembly; backend code remains responsible for authorization and persisted truth.
>
> Recorded production is Pydantic on `tripsync-backend:31`, while local configuration can default to Legacy. Preserve that distinction. CD-03 is activation evidence but not real-provider Pydantic end-to-end acceptance. Historical `AI_enhanced/PR-*` reports must not override newer status evidence.
>
> Prefer compatibility-preserving migrations with tests at each seam. Do not perform live AWS changes, database migrations, or provider calls unless the task explicitly authorizes them. Never expose secret values.

The first deliverable for a broad refactor should be an evidence-backed assessment and migration boundary, not an immediate rewrite.
