# Trip Workspace

This folder is the standalone Trip workspace frontend that the main `frontend/` app embeds
through `/trip`.

It is still a separate app, but it is no longer an isolated prototype. The active runtime
now depends on:

- `shared/trip-navigation-policy/`
- `shared/session-runtime/`
- `trip/src/final/plan-feature/`
- `backend/`

If you change the Trip UI here and want `http://localhost:3000/trip` to reflect it, run:

```powershell
cd frontend
npm run build:trip-preview
```

That rebuilds `trip/` and syncs the result into `frontend/public/trip-app/`.

## Quick Start

```powershell
cd trip
npm install
npm run dev
```

Default local URL:

```text
http://localhost:5173
```

Important:

- the standalone Trip app is useful for direct UI work
- the real product flow also depends on `backend/`
- the embedded `/trip` shell in `frontend/` uses the built preview bundle, not `trip/src`
  directly

## Routes

Hash routes live under `#/`:

- `#/`
- `#/create`
- `#/account/profile`
- `#/account/travel`
- `#/account/notifications`
- `#/account/settings`
- `#/trip/:tripId/plan`
- `#/trip/:tripId/chat`
- `#/trip/:tripId/conflict`
- `#/trip/:tripId/updates`
- `#/trip/:tripId/preferences`
- `#/trip/:tripId/members`
- `#/trip/:tripId/invite`
- `#/join/:token`

## Current Source Of Truth

Read these first before changing Trip behavior:

1. `../AGENTS.md`
2. `../README.md`
3. `../INTEGRATION-ROADMAP.md`
4. `../HANDOFF.md`
5. `../application_docs/navigation-known-wrong-behavior.md`
6. `FRONTEND.md`
7. `BACKEND.md`

## Active Architecture

The current ownership split is intentional:

- `frontend/` owns the host `/trip` shell
- `trip/src/final/FinalApp.jsx` owns route mount, workspace composition, and command
  execution
- `trip/src/final/plan-feature/PlanFeature.jsx` is the single public Plan feature boundary
- `trip/src/final/plan-feature/usePlanInteractionRuntime.js` owns Plan selection,
  comments, map/list coordination, menu state, booking interactions, and drawer state
- `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js` owns drawer-local
  assistant conversation and change-request flow
- `trip/src/final/TripAppState.jsx` owns Trip hydration, polling, and backend coordination
- `shared/trip-navigation-policy/` owns destination policy and restoration fallback
- `shared/session-runtime/` owns browser session persistence, bearer token mechanics, and
  request identity headers

Do not move session, navigation, or Plan runtime ownership back into random UI components.

## Product Rule

The product maintains one living Current Plan.

It does not use an old-style final publish or lock phase. Changes to itinerary items flow
through backend-owned decision paths:

- `notice`
- `round`
- `reopen_round`
- `confirm`

Frontend should submit intent and render the backend result. It should not reintroduce
frontend-owned path policy.

## Local Development Checklist

For the real login and post-login workspace:

1. start PostgreSQL
2. start `backend/`
3. seed or enable auth in the runtime database
4. start `frontend/`
5. optionally run `frontend/build:trip-preview` after Trip UI changes

Seeing `frontend/` alone is not enough to verify the real logged-in Trip flow.

## Known Good Current Behaviors

The current merged workspace already includes these important behaviors:

- account-backed login stays on `My Trips` when landing on `/`
- guest-backed sessions still return into their trip
- dashboard uses real trip summaries instead of fake default cards
- demo data is opt-in instead of default
- saving preferences returns the user to the Plan page
- per-activity history reads real `PlanChange` rows
- initial generation history is hidden from user-facing activity history
- vote rounds auto-settle when every member has voted
- moving an item to another date also updates matching `day_index`
- day headers use canonical trip dates from the trip window
- single-member generation no longer treats budget ceiling as a group blocker

## Related Files

- `FRONTEND.md`
- `BACKEND.md`
- `src/final/FinalApp.jsx`
- `src/final/TripAppState.jsx`
- `src/final/final.css`
- `src/final/plan-feature/PlanFeature.jsx`
- `src/final/plan-feature/usePlanInteractionRuntime.js`
- `src/final/plan-feature/useAssistantChangeRequestFlow.js`
