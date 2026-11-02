# Place Service Geoapify POI Handoff

Last updated: 2026-08-15

## 1. Purpose

This document records the Place Service and Geoapify data-flow improvements completed during this thread.

The goal was to improve candidate quality and make all formal Plan generation use the same place sourcing path.

## 2. Current Formal Candidate Flow

All destinations now share the formal initial Plan candidate path:

```text
Planner
  -> Place Service
  -> PostgreSQL Place cache
  -> Geoapify if cache is missing or insufficient
  -> normalize / upsert
  -> balanced Planner candidates
```

This includes Chicago.

## 3. Chicago Path Unification

The old Chicago curated source remains in the repository:

```text
backend/data/poi_chicago.py
```

It is retained for:

- legacy reference
- tests
- demo fixtures
- compatibility
- historical comparison

It is no longer the formal candidate source for initial Plan generation.

Removed from the formal Place Service path:

- import of `data.poi_chicago.POIS`
- `_is_chicago(...)`
- `_chicago_places(...)`
- early return to `curated_chicago:*` candidates

Chicago now returns `geoapify:*` candidates like other provider-backed cities.

## 4. POI Quality Ranking

Place Service ranking was improved without collapsing diversity.

Higher priority:

- clear names
- category-specific tourism relevance
- explicit category group
- reliable address information
- user-interest relevance where available downstream
- useful geographic spread

Lower priority:

- vague names
- category `other`
- unclear or generic places
- weak travel relevance
- missing or abnormal address facts

The category round-robin remains important: ranking improvements should not concentrate all candidates into one category or one neighborhood.

## 5. Name Handling

Name responsibility remains strict:

```text
Geoapify / Place cache owns names.
Planner only selects candidate ids.
Backend resolves candidate ids into PlanItem names.
```

If a provider record has no reliable English name and the original name is not Latin-script, the Place Service can filter it before Planner rather than asking AI to translate.

## 6. Unknown Metadata

Unknown values remain unknown:

- missing price is not free
- missing duration is not 90 minutes
- missing opening hours are not all-day
- missing walking metadata is not low walking

Downstream code must explicitly tolerate `None`.

## 7. Files To Inspect

```text
backend/app/domain/places/service.py
backend/app/domain/places/geoapify.py
backend/app/db/models.py
backend/app/db/init_schema.py
backend/tests/test_places.py
backend/app/domain/plans/generator.py
```

## 8. Validation Notes

Important validation done during the thread:

- Geoapify and Place Service unit coverage was updated.
- Chicago cache/provider behavior is now tested in `test_places.py`.
- Backend syntax checks passed for the changed Place Service files.

In the final local environment, full DB-backed Place Service pytest was blocked by missing `psycopg` in Anaconda, not by assertion failure.

