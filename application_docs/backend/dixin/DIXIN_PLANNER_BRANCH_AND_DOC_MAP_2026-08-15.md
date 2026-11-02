# Dixin Planner Branch And Doc Map

Last updated: 2026-08-15

## Purpose

This file explains how the current Planner V1 documents in `docs/backend/dixin/`
map to the repository states that mattered during the August 15, 2026 backend
AI work.

Use this when a future engineer or AI needs to understand:

- which documents describe the approved Planner V1 baseline
- which branch snapshot preserved the local pre-cleanup working state
- which branch should be treated as the active source of truth now

## Current Source Of Truth

For ongoing project work, the active source of truth is:

```text
origin/main
```

At the time of writing, the relevant `main` commits are:

- `9d40bc2` `Finalize AI V1 planner baseline and docs`
- `0f5a5b4` `Add chat agent observed issues note`

Practical meaning:

- the active Planner V1 harness and documentation are on `origin/main`
- the active Chat Agent V1 completion documentation is on `origin/main`
- future follow-up work should start from `main`, not from an older local-only
  snapshot

## Local Backup Snapshot

Before cleaning the local worktree, a safety backup branch was created:

```text
codex/backup-before-clean-20260815
```

This branch is a local preservation snapshot of the pre-cleanup working tree.

Important clarification:

- it is a local backup branch, not the product source of truth
- it exists in case someone later needs to inspect the exact local state that
  existed before the workspace was reset to `origin/main`
- it should be used as a recovery/reference point, not as the default base for
  new work

## Which Dixin Documents Matter Most

### 1. Planner Phase 0 baseline

Primary file:

- `docs/backend/dixin/PLANNER_V1_PHASE0_AUDIT_AND_EVALUATION_DESIGN_2026-08-15.md`

What it means:

- this is the architecture and evaluation-design baseline
- it explains why Planner V1 should be evaluated before being tuned
- it captures the approved reasoning that led to the harness work

Branch relationship:

- present on `origin/main`
- also present in `codex/backup-before-clean-20260815`

### 2. Planner Phase 1 operator note

Primary file:

- `docs/backend/dixin/PLANNER_V1_PHASE1_HARNESS_RUNBOOK_2026-08-15.md`

What it means:

- this is the local execution/runbook note for the real-AI Planner harness
- it explains how to run the harness and what it is for
- it records the latest Phase 2C interpretation at the operator level

Branch relationship:

- present on `origin/main`
- also present in `codex/backup-before-clean-20260815`

### 3. AI V1 final capstone status

Primary file:

- `docs/backend/dixin/CADENSY_AI_V1_CAPSTONE_COMPLETE_2026-08-15.md`

What it means:

- this is the capstone-level completion verdict
- it records that Chat Agent V1 is complete and Planner V1 is complete for
  capstone scope
- it moves remaining issues into known limitations / future improvements instead
  of treating them as unfinished V1 blockers

Branch relationship:

- present on `origin/main`
- also present in `codex/backup-before-clean-20260815`

### 4. Chat Agent historical baseline

Primary file:

- `docs/backend/dixin/CHAT_AGENT_V1_HISTORY_AND_FUTURE_2026-08-15.md`

What it means:

- this is the historical record of what was restored and validated for Chat
  Agent V1
- use it when someone asks why specific chat-agent behavior must be preserved

Branch relationship:

- present on `origin/main`
- also present in `codex/backup-before-clean-20260815`

## Simple Rule

If someone asks:

```text
Which branch should I build from?
```

Answer:

```text
origin/main
```

If someone asks:

```text
Which branch preserved the exact local pre-cleanup snapshot?
```

Answer:

```text
codex/backup-before-clean-20260815
```

If someone asks:

```text
Which documents explain the Planner V1 audit and harness work?
```

Answer:

- `PLANNER_V1_PHASE0_AUDIT_AND_EVALUATION_DESIGN_2026-08-15.md`
- `PLANNER_V1_PHASE1_HARNESS_RUNBOOK_2026-08-15.md`

## Final Reminder

Do not treat the backup branch as a second competing source of truth.

The intended relationship is:

```text
origin/main = active project baseline
codex/backup-before-clean-20260815 = local recovery snapshot
docs/backend/dixin/*.md = explanation of what the August 15 AI work meant
```
