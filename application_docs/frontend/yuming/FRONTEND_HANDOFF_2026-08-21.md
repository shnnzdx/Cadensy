# Frontend Handoff - 2026-08-21

## Scope

This handoff covers the frontend changes made in the trip app and the synced preview bundle.

Primary frontend files touched:

- `trip/src/final/FinalApp.jsx`
- `trip/src/final/TripAppState.jsx`
- `trip/src/final/final.css`
- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js`
- `frontend/public/trip-app/*`

The user runs the app through:

- `frontend/public/trip-app`

So after changing `trip/`, the preview bundle must be rebuilt and synced.

## What Changed

### 1. Navbar Trip Name Removed

The trip name was removed from the itinerary navbar/header area.

File:

- `trip/src/final/FinalApp.jsx`

Reason:

- User wanted the itinerary nav label/name removed.

### 2. Add Stop Dialog Has Selectable Time

The Add Stop modal now lets the user choose a start time.

Behavior:

- Time choices are generated from the gap between the previous and next stop.
- Options are quarter-hour increments.
- A new stop is 30 minutes.
- If the previous stop has no duration, frontend assumes a 90-minute block, matching backend.
- Submit is disabled if no valid time fits.

Files:

- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/final.css`
- `trip/src/final/TripAppState.jsx`

Frontend sends:

```js
{
  title,
  afterItemId,
  beforeItemId,
  startHour
}
```

### 3. Remove Flow Means Remove From Itinerary

Frontend remove requests now submit a remove patch instead of changing the item into free time.

Behavior:

- `remove it` / remove action means the activity disappears from Current Plan after the backend applies it.
- UI change cards show removal as a removal, not as a time/title edit.

Files:

- `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js`
- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/TripAppState.jsx`

### 4. Confirm Page Alternatives Removed

The Confirm page now only supports:

- `Accept`
- `Decline change`

Removed from Confirm UI:

- Need alternative
- Alternative option list
- Alternative selection/apply controls

Important distinction:

- AI chat/group-round candidate options are still allowed.
- Confirm-page alternatives are removed.

Files:

- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/TripAppState.jsx`
- `trip/src/final/final.css`

### 5. Accept UI State Fixed

Accept now updates the visible proposal state using the backend's returned proposal payload.

Behavior:

- If current user has accepted, UI shows waiting state instead of leaving the button stale.
- If current user is not an affected member, UI does not show an accept action for them.
- Member statuses update immediately after decision.

Files:

- `trip/src/final/TripAppState.jsx`
- `trip/src/final/plan-feature/PlanFeature.jsx`

### 6. Organizer Deadlock UI Simplified

Organizer exits now match backend product logic:

- Keep current
- Split group
- Remove activity

Files:

- `trip/src/final/plan-feature/PlanFeature.jsx`

### 7. Replacement Place Panel Added

The Replace Place action no longer drops directly into ordinary chat.

New UI:

- Search input
- Provider-backed candidate list
- Candidate selection
- Replace place button

Data source:

- `GET /api/plans/items/{item_id}/replacement-places?q=...`

Files:

- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/TripAppState.jsx`
- `trip/src/final/final.css`

Replacement patch includes:

```js
{
  title,
  local_title,
  place,
  lat,
  lng,
  photo_url,
  price_per_person,
  tags
}
```

Important bug fix:

- If the replacement candidate has no `local_title`, frontend sends `local_title: null`.
- This lets backend clear stale local subtitles like `德发长饺子馆`.

### 8. Replacement Mode Composer Hidden

In `replacePlace` mode, the drawer now shows the candidate panel instead of the normal chat composer.

Reason:

- User said clicking Replace entered chat and felt confusing.
- The product now supports direct candidate selection first.

Files:

- `trip/src/final/plan-feature/PlanFeature.jsx`

### 9. Synced Embedded Preview

After `trip` build, the output was synced into:

- `frontend/public/trip-app`

This changed:

- `frontend/public/trip-app/index.html`
- `frontend/public/trip-app/embed-manifest.json`
- bundled asset files under `frontend/public/trip-app/assets`

## Verification Run

Commands run:

```bash
cd trip
npm run build

cd ../frontend
npm run sync:trip-preview
npm run build
```

Results:

```text
trip build passed
sync:trip-preview passed
frontend build passed
```

Frontend build warnings:

- `punycode` deprecation warning from dependency/runtime.
- large chunk warning from Vite/Vinext.

These warnings existed at build-time but did not fail the build.

## Manual QA Checklist

After restarting backend and frontend:

1. Open a trip itinerary.
2. Click `+ Add stop` between two activities.
3. Confirm the modal shows a constrained start-time dropdown.
4. Try replacing a place from the Replace action.
5. Confirm candidate list appears without entering ordinary chat first.
6. Replace a place that has no local title.
7. Confirm the old subtitle does not remain under the new title.
8. Open a Confirm proposal as an affected member.
9. Confirm only `Accept` and `Decline change` appear.
10. Accept and verify the status chip updates immediately.

## Notes For Next Developer

- The frontend the user sees is the synced copy in `frontend/public/trip-app`, not only `trip/dist`.
- Do not run frontend/backend servers for the user unless they explicitly ask; they prefer running terminals themselves.
- After frontend source changes, always run:

```bash
cd trip && npm run build
cd ../frontend && npm run sync:trip-preview
```

- If only backend files change, frontend sync is not needed.
