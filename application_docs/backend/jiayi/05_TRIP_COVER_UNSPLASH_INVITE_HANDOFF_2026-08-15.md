# Trip Cover Unsplash Invite Handoff

Last updated: 2026-08-15

## 1. Purpose

This document records the Trip Cover and Invite image work completed during the thread.

The goal was to use Unsplash city covers for Trips while keeping Trip covers separate from Place and PlanItem images.

## 2. Source Of Truth

Trip cover source of truth is the Trip record:

```text
trip.cover_image_url
trip.cover_image_source
trip.cover_attribution_name
trip.cover_attribution_url
trip.cover_source_url
```

My Trips and Invite should read the same Trip cover fields.

## 3. Unsplash Service Behavior

Backend owns Unsplash access.

Expected flow:

```text
Trip destination
  -> backend Unsplash cover service
  -> city/travel image search
  -> select landscape cover
  -> persist cover fields on Trip
  -> frontend reads persisted fields
```

The browser never receives `UNSPLASH_ACCESS_KEY`.

## 4. Caching Behavior

Trip covers are saved on the Trip.

My Trips should not call Unsplash every time it opens.

When a cover already exists, it is reused.
Provider failures fall back to the neutral Travel cover rather than a wrong city image.

## 5. Invite Page Fix

The Invite page previously showed a hardcoded/demo city image in at least one path, which caused a Washington DC trip to display Chicago.

Fixed behavior:

```text
trip.cover_image_url exists
  -> show the persisted Trip city cover

missing cover
  -> show neutral Travel cover placeholder
```

Invite page must not:

- hardcode Chicago
- use array position or demo image classes as fallback
- use Place.image_url or PlanItem.photo_url as Trip cover fallback
- call a second image API just for Invite

The text overlay such as `Share this trip with the group.` remains.

## 6. Frontend Loading Behavior

My Trips behavior:

- current workspace large cover uses eager/high-priority loading
- other trip covers use lazy loading
- frontend requests CDN-sized variants, not original huge image files

Invite behavior:

- reuses `TripCoverHero`
- reads existing persisted cover fields
- renders Unsplash attribution when available

## 7. Files To Inspect

```text
backend/app/domain/trips/cover_service.py
backend/app/domain/trips/service.py
backend/scripts/validate_unsplash_covers.py
backend/tests/test_trip_covers.py
backend/tests/test_invites.py
trip/src/final/trip-cover.js
trip/src/final/FinalApp.jsx
trip/src/final/final.css
frontend/tests/trip-preview-integration.test.mjs
```

## 8. Validation Notes

Real Unsplash smoke validation was performed during the thread for cities including:

- Washington DC
- Saint Louis
- Paris
- Tokyo

Browser validation confirmed the Washington DC Invite page used a Washington DC cover from the persisted Trip cover flow rather than the old Chicago image.

