# Cadensy Application Documentation Index

Status: current application documentation map
Last reviewed: 2026-11-11

This directory is the application documentation domain: product, frontend,
backend, shared workspace, and application-level handoffs. AWS deployment
records remain in [`../AWS/README.md`](../AWS/README.md), while AI-agent
engineering evidence remains in [`../AI_enhanced/README.md`](../AI_enhanced/README.md).
It contains both current contracts and historical evidence; those categories
are not interchangeable.

## Start here

| Need | Current source |
| --- | --- |
| Repository setup and architecture | [`../README.md`](../README.md) |
| Current operational handoff | [`../HANDOFF.md`](../HANDOFF.md) |
| Integration status and next work | [`../INTEGRATION-ROADMAP.md`](../INTEGRATION-ROADMAP.md) |
| Product contract | [`PRODUCT.md`](PRODUCT.md) |
| Project description | [`PROJECT_DESCRIPTION.md`](PROJECT_DESCRIPTION.md) |
| Known navigation limitations | [`navigation-known-wrong-behavior.md`](navigation-known-wrong-behavior.md) |
| General continuation prompt | [`HANDOFF_PROMPT_CURRENT.md`](HANDOFF_PROMPT_CURRENT.md) |
| Backend-refactor continuation prompt | [`HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md`](HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md) |
| Repository agent rules | [`../AGENTS.md`](../AGENTS.md) and [`AGENTS.md`](AGENTS.md) |
| Backend operation | [`../backend/README.md`](../backend/README.md) and [`../backend/LOCAL_DEV.md`](../backend/LOCAL_DEV.md) |
| AWS deployment operation | [`../AWS/README.md`](../AWS/README.md) |
| AI-agent engineering evidence | [`../AI_enhanced/README.md`](../AI_enhanced/README.md) |
| Trip-independent routes and unused `X-Trip-Id` | [`trip-independent-x-trip-id.md`](trip-independent-x-trip-id.md) |

## Evidence lifecycle

- **Current contract:** files listed above describe the intended or recorded current state. Update them in the same change that makes their statements stale.
- **Point-in-time evidence:** `AI_enhanced/PR-*`, `AI_enhanced/00_*`, [`backend/`](backend/), [`frontend/`](frontend/), and the dated task records below preserve reviews and handoffs. Their conclusions apply at the time written.
- **Archived material:** [`_archive/`](_archive/) contains superseded designs, deployment records, temporary links, and old handoffs. It is retained for traceability, not implementation guidance.

Historical evidence is normally immutable. If a conclusion is superseded, update the current index and add newer evidence instead of rewriting the old report.

## Point-in-time task records

Index entry added: 2026-11-06. These files record a completed task. They are not current contracts and they are not production-state evidence.

| Record | What it preserves |
| --- | --- |
| [`MERGE-PHASE1_2026-11-05.md`](MERGE-PHASE1_2026-11-05.md) | 2026-11-05 squash of PR #49, then PR #48, onto `main`. Merged to `main`. Not a production deployment. Not verified in production. |
| [`design-04-phase1-task0-coverage-note.md`](design-04-phase1-task0-coverage-note.md) | 2026-11-06 read-only re-run of the DESIGN-04 Phase 1 Task 0 caller searches. No success-path header fix. Not runtime proof and not exhaustive. |

## Maintenance rules

1. Label status claims with a date and distinguish design, implementation, test evidence, and production observation.
2. Do not call a local default a production state. The backend local default may remain `legacy` while recorded production runs `pydantic`.
3. Do not infer live AWS state from an old deployment report. Use current protected-run evidence or explicitly perform a new read-only verification.
4. Archive a document when its purpose has been replaced and no active workflow links to it. Preserve stable paths for heavily cross-referenced evidence by marking the containing directory as historical instead of moving every file.
5. Never store credentials, provider keys, access tokens, or unredacted user content in documentation or evidence.
