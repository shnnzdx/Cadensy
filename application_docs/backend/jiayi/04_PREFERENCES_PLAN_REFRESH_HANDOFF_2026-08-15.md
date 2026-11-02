# Preferences Plan Refresh Handoff

Last updated: 2026-08-15

## 1. Purpose

This document records the behavior around saving Preferences after a Current Plan already exists.

The product goal was to make new Preferences matter for future planning without silently rewriting an accepted Current Plan.

## 2. Product Rule

Saving Preferences must not silently overwrite Current Plan.

Current Plan can continue to exist.

But the system should know the plan was generated from earlier preferences and expose a lightweight refresh state.

## 3. Current Behavior

When relevant preference or constraint data changes after a plan exists:

```text
save preference / constraint
  -> do not mutate existing PlanItem rows
  -> mark existing plan as needing refresh
  -> Current Plan API exposes needs_refresh
  -> frontend shows a lightweight notice
```

The Plan page notice communicates:

```text
Preferences updated.
Your current plan was generated using earlier preferences.
Future replans and change proposals use the latest planning inputs.
```

## 4. Future Planning Uses Latest Preferences

Latest Preferences must influence:

- regenerate / future plan generation paths
- replan paths when introduced
- AI change proposals and classification inputs where supported
- meal selection constraints such as dietary requirements or budget where available

## 5. What Was Not Done

Not added:

- automatic silent Current Plan rewrite
- new plan-versioning architecture
- targeted repair system
- full two-pass Planner
- broad UI navigation changes

## 6. Files To Inspect

```text
backend/app/domain/preferences/service.py
backend/app/domain/plans/generator.py
backend/app/domain/trips/service.py
backend/app/db/models.py
trip/src/final/TripAppState.jsx
trip/src/final/plan-feature/PlanFeature.jsx
backend/tests/test_preferences.py
backend/tests/test_trips.py
```

## 7. Boundary To Preserve

Current Plan mutation remains explicit.

Do not create behavior where saving a preference quietly rewrites accepted plan items.

