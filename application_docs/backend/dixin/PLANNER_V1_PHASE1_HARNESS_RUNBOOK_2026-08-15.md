# Planner V1 Phase 1 Harness Runbook

Last updated: 2026-08-15

## Purpose

This is the local operator note for the real-AI Planner evaluation harness.

Primary script:

```text
backend/app/agents/agent-server/run_planner_eval.py
```

Superseded script:

```text
backend/scripts/diagnose_planner.py
```

That older entry point now only redirects to the new harness.

## Preconditions

Before running the harness:

1. local backend virtual environment exists
2. `backend/.env` is populated
3. `MOCK_AI=0`
4. PostgreSQL is reachable for `TEST_DATABASE_URL` or `DATABASE_URL`
5. the configured DeepSeek credentials are valid

This harness evaluates the real Planner path only.

## Command

From `backend/`:

```powershell
$env:MOCK_AI='0'
$env:DISABLE_SCHEDULER='1'
.\.venv\Scripts\python.exe -u app/agents/agent-server/run_planner_eval.py
```

## What It Does

The harness runs two scenarios:

1. Chicago rich-data baseline
2. fixed sparse-data non-Chicago baseline

Each scenario is executed twice:

- `run_1`
- `run_2`

Each run is isolated and rolled back after capture.

## Output

The script prints:

1. human-readable scenario/run summaries
2. a final machine-readable JSON report

The six core metric groups are:

1. generation outcome
2. final legality
3. AI survival / fallback
4. day completeness
5. cross-day variety
6. geographic coherence

Diagnostic context is also included, but it is not part of the six core metric groups.

## Important Limits

- This is a baseline harness, not a Planner tuning step.
- It does not compare `MOCK_AI=1` against `MOCK_AI=0`.
- It does not change Planner prompt or generator behavior.
- Fixed place supply is enforced inside the harness so the two runs of each scenario see equivalent candidate input.

## Latest Status

As of 2026-08-15, this harness has already been used to establish the current
real-AI baseline and to validate the Phase 2C minimal structured-output fix.

Practical status:

- DeepSeek real Planner path is working
- structured output is no longer failing mainly at the JSON/schema layer
- deterministic validation remains authoritative
- invalid planner-day output still falls back safely to rules
- some runs can still degrade later during meal-anchor assembly, which is a
  Planner quality limitation rather than a structured-output parser failure

This means the harness should now be treated as:

```text
baseline / regression / diagnosis tooling
```

not as an experimental one-off script.
