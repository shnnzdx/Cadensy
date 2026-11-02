# Planner Agent Itinerary Quality Handoff

Last updated: 2026-08-15

## 1. Purpose

This document records the Planner Agent improvements made during Jiayi's itinerary-quality iteration.

The main product goal was to improve DeepSeek Planner quality and stability without changing the existing AI Provider architecture.

## 2. Provider Scope

Provider routing was intentionally kept unchanged:

```text
Chat Agent      -> DeepSeek
Planner Agent   -> DeepSeek
Explainer Agent -> DeepSeek
```

No new provider, fallback provider, or agent was added. This document should be
read against the current main-branch backend, where the active real-provider path
is DeepSeek-only.

## 3. Candidate Id Contract

Planner now follows a stricter responsibility split:

```text
AI Planner selects candidate_id
Backend resolves candidate_id to Place cache facts
PlanItem stores canonical title/local_title/place facts
```

The model is not supposed to:

- create place names
- translate place names
- modify `english_name`
- modify `local_name`
- concatenate English and local names
- invent titles from categories

The Planner output shape is centered on:

```json
{
  "candidate_id": "geoapify:example-place-id",
  "start_hour": 10.0
}
```

Backend resolves this selected id to the immutable candidate facts already loaded from Place Service.

## 4. Prompt Improvements

Planner prompt rules were made more explicit around real travel rhythm.

The prompt now emphasizes:

- full travel days usually start around 09:00-10:30
- hard constraints such as `Nothing before 9am` must be obeyed
- full days should not normally end at 14:00-16:00 when useful legal options remain
- complete days usually continue to dinner or about 18:00+
- sightseeing count should be roughly 2-4 meaningful activities per day
- days should not have mechanically identical counts
- do not add poor-quality stops just to fill a count
- leave reasonable transition time and slack
- prefer geographically close places
- respect known opening hours
- unknown data must stay unknown, not become a fake default

## 5. Planning Priority Order

The prompt and deterministic planner path now align around this priority order:

```text
1. Hard Constraints
2. Fixed / Locked Events
3. Trip Dates / User Availability
4. Opening Hours
5. Geographic Feasibility
6. Reasonable Timing and Transition
7. Meal Timing
8. Soft Preferences / Interests
9. Daily Variety
```

Soft interests do not override closure, availability, or hard constraints.

## 6. Backend Conversion To PlanItem

Backend Plan generation uses selected candidates to write real `PlanItem` rows.

Important rule:

```text
candidate_id -> candidate map -> canonical name/address/local name -> PlanItem
```

The backend, not the model, owns the final name fields persisted into `PlanItem.title` and `PlanItem.local_title`.

## 7. Files To Inspect

Important files:

```text
backend/app/agents/planner.py
backend/app/domain/plans/generator.py
backend/app/domain/places/service.py
backend/tests/test_planner.py
backend/tests/test_plan_generation.py
trip/BACKEND.md
HANDOFF.md
```

## 8. What Was Not Done

Explicitly not added:

- new validator system
- targeted repair architecture
- two-pass Planner
- schedule skeleton agent
- new agent
- provider fallback
- unrelated UI restructure

