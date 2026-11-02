# Plan UI Category Icon Handoff

Last updated: 2026-08-15

## 1. Purpose

This document records the Plan UI card change that removed activity photo placeholders and replaced them with compact category icons.

## 2. Behavior Now True

Plan cards no longer show:

```text
PHOTO
```

They no longer reserve a large rectangle for activity images.

The new card hierarchy is:

```text
[icon]  Place name                         9:00 AM
        Category
        Short address
```

For meals:

```text
[utensils icon]  Restaurant name           12:15 PM
                 LUNCH
                 Short area/address
```

## 3. Icon Mapping

The mapping is intentionally simple:

```text
Museum        -> column / museum
Park Garden   -> leaf
Historic      -> landmark
Art Gallery   -> gallery
Aquarium      -> wave
Attraction    -> landmark or wave when water-related
Unknown       -> map pin
Meal          -> utensils
```

No large Geoapify icon taxonomy was introduced.

## 4. Address And Time

Stored backend address remains unchanged.

Only display is compacted using existing frontend helper logic.

Start time is rendered separately as a right-side `<time>` element, so it is easier to scan than the address.

## 5. Files To Inspect

```text
trip/src/final/plan-feature/PlanFeature.jsx
trip/src/final/final.css
frontend/tests/trip-preview-integration.test.mjs
frontend/public/trip-app/*
```

## 6. Validation

Validated commands:

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

The integration test now asserts Plan cards use compact category icons and do not reintroduce activity photo placeholders.

## 7. Boundary

This is a Plan page display change.

It must not be confused with Trip Cover work:

```text
My Trips / Invite -> Unsplash city cover
Plan items        -> category or meal icons
```

