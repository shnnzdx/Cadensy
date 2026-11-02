# Validation And Boundaries Handoff

Last updated: 2026-08-15

## 1. Purpose

This document records the validation state and boundaries after Jiayi's full thread of changes.

## 2. Validation That Passed

Frontend and build validation passed:

```bash
cd trip
npm run build
```

```bash
cd frontend
npm run build:trip-preview
```

```bash
node --test frontend/tests/trip-preview-integration.test.mjs
```

The frontend integration suite reported:

```text
9 passed
```

Python syntax validation passed for the changed Place Service files using AST parsing.

Git whitespace validation passed for the focused changed files:

```bash
git diff --check
```

## 3. Backend Test Limitation

Targeted backend pytest was attempted with the available Anaconda pytest:

```bash
cd backend
/opt/anaconda3/bin/pytest tests/test_places.py
```

Observed result:

```text
12 passed
6 errors
```

The 6 errors were environment errors from a missing PostgreSQL driver:

```text
ModuleNotFoundError: No module named 'psycopg'
```

The repository's backend requirements include:

```text
psycopg[binary]>=3.2
```

Use a backend virtual environment with requirements installed before treating DB-backed tests as complete.

## 4. Boundaries Preserved

Do not casually change these without a new explicit scope:

- `shared/session-runtime` owns technical session state
- `shared/trip-navigation-policy` owns workspace navigation decisions
- `trip/src/final/plan-feature/PlanFeature.jsx` remains the public Plan feature boundary
- `usePlanInteractionRuntime` owns Plan selection/map/comment interaction
- `useAssistantChangeRequestFlow` owns drawer assistant flow
- backend deterministic rules own decision paths
- AI provider routing stays as configured by env

## 5. Not In Scope During This Thread

Not implemented:

- new AI provider fallback
- new Planner agent
- new validator service
- targeted repair system
- two-pass Planner
- schedule skeleton architecture
- replacing Geoapify with another provider
- using PlanItem photos as Trip covers
- silently rewriting Current Plan after Preferences save

## 6. Next Recommended Checks

Before building on this work:

1. Create or restore a backend venv in `backend/`.
2. Install `backend/requirements.txt`.
3. Run DB-backed backend tests with PostgreSQL available.
4. Generate one real non-Chicago trip and one Chicago trip to verify both use Place Service candidates.
5. Inspect Plan UI in browser for one full travel day with sightseeing plus lunch and dinner.
6. Verify Invite for Washington DC or another real trip shows Trip cover or neutral placeholder, never a wrong city fallback.

