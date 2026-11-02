# Meals And Daily Route Handoff

Last updated: 2026-08-15

## 1. Purpose

This document records the meal-planning and daily route-quality work completed during the thread.

The core product goal was to make each full travel day feel like a real itinerary rather than a list of random activity slots.

## 2. Meals Are Separate From Sightseeing

Meals are no longer treated as just random activity slots.

Current behavior:

- meals are `PlanItem` rows with `is_meal = true`
- meal type is derived and exposed as `lunch` or `dinner`
- meals do not count toward the sightseeing activity count
- day summary can show `3 activities · 2 meals`
- Plan UI shows `LUNCH` and `DINNER`, not a generic `Meal`

## 3. Lunch And Dinner Windows

The intended windows are:

```text
Lunch:  11:30-13:30 / 14:00 range depending on route
Dinner: 17:30-20:00 range depending on route
```

Times can vary naturally. The goal is stable structure, not mechanical sameness.

## 4. Meal Candidate Rules

Lunch can accept:

- restaurant
- casual dining
- cafe with substantial food
- bakery
- food hall / food court

Dinner is stricter:

- restaurant
- dining
- regional cuisine

Dinner should not treat cafe-only venues as a good default.

## 5. Route-Aware Meal Selection

Meal selection considers the day's sightseeing route.

Intended pattern:

```text
morning sightseeing
  -> nearby lunch
  -> afternoon sightseeing
  -> nearby dinner around final sightseeing area
  -> optional evening activity if high quality and appropriate
```

Lunch should be near the morning-to-afternoon transition.
Dinner should be near the final sightseeing cluster.

The code uses coordinate distance where routing data is not available. It should not claim real walking times unless real routing is introduced later.

## 6. Meal Fallback Ladder

Flexible meals are final fallback only.

Expected fallback order:

```text
nearby high-quality restaurant
  -> expanded reasonable radius
  -> relaxed meal-specific categories
  -> honest flexible meal break
```

For food-rich cities like New York, San Francisco, Paris, Tokyo, and Washington DC, the system should normally find real restaurant candidates when provider data is available.

## 7. Daily Sightseeing Variation

Sightseeing count remains a soft preference around 2-4 activities per day.

The day pattern can vary, for example:

```text
Day 1: 3 sightseeing activities
Day 2: 2 sightseeing activities
Day 3: 4 sightseeing activities
Day 4: 3 sightseeing activities
```

Meals do not count toward this number.

## 8. Optional Evening Activity

Dinner is not always required to be the final stop.

An evening activity may appear after dinner when there is a good candidate such as:

- night market
- viewpoint
- show
- river walk
- night attraction

This is optional and should not force low-quality POIs into the day.

## 9. Files To Inspect

```text
backend/app/domain/plans/generator.py
backend/app/agents/planner.py
backend/app/domain/places/service.py
backend/tests/test_plan_generation.py
backend/tests/test_planner.py
trip/src/final/TripAppState.jsx
trip/src/final/plan-feature/PlanFeature.jsx
```

