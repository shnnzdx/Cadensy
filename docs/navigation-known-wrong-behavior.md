# Navigation Known-Wrong Behavior

Status: active limitation record
Originally recorded: 2026-08-11
Last verified against source: 2026-10-28

These notes describe current observable behavior that should not be silently frozen as
desired product policy. Resolved characterization details remain only when they explain
the boundary around an active limitation.

## Login and Return Intent

- The login page only transports a host-level `next` string and accepts it when it starts
  with `/`. It does not carry a structured workspace return intent.
- After that minimal check, login redirects to `window.location.href = nextPath` without
  route reachability or role-based validation.
- This means a slash-prefixed path can win even when it does not encode a valid workspace
  destination.

## Restored Trip Selection

- The runtime still distinguishes:
  - technical trip context used to start backend hydration
  - restored product intent used to decide the initial workspace destination
- Those are no longer the same ownership path:
  `shared/session-runtime` restores technical session material, while
  `tripNavigationPolicy.resolveDestination()` owns the post-hydration product destination.
- The remaining known limitation is not the restored-selection policy itself; it is that
  pre-login transport still only carries a host-level `next` string rather than a
  structured workspace return intent.

## Organizer-Only Workspace Routes

- Organizer-only entries are still declared as workspace routes in `FinalApp`, because
  `FinalApp` remains the workspace route mount and composition layer.
- This is no longer a known-wrong authorization path by itself.
- Current reachability and fallback behavior is owned centrally by
  `tripNavigationPolicy.resolveDestination()`, not by tab hiding or page-local fallback
  logic.

## Phase 7 Audit Note

- Before: a participant could manually open `/trip/:tripId/members` or
  `/trip/:tripId/invite` because the workspace only hid tabs and the `members` page still
  rendered its own local fallback.
- After: current-route reachability is evaluated centrally through
  `tripNavigationPolicy.resolveDestination()`, and participants are redirected to
  `/trip/:tripId/plan` per the frozen `role-not-authorized` policy case.
- Reason: Phase 7 approved moving current-route authorization and fallback ownership out
  of `FinalApp` and into the shared workspace policy seam.
- Frozen policy cases: `current-route-organizer-only-rejected-for-participant` and
  `organizer-only-route-with-unknown-state-remains-role-restricted`.

## Phase 9 Audit Note

- Before: `TripAppState` restored `tripsync:tripId` as technical request context, but the
  runtime did not explicitly normalize that persisted value into a
  `restoredSelection` intent before deciding the initial workspace destination.
- After: once initial session facts and authoritative `/api/trips` access facts are
  hydrated, the initial workspace destination is resolved through
  `tripNavigationPolicy.resolveDestination()` using the restored selection plus the
  current route as competing intents.
- Reason: Phase 9 approved moving restoration-time destination ownership out of
  implicit `TripAppState` assumptions and into the frozen navigation-policy seam.
- Frozen policy cases: `restored-selection-used-when-no-stronger-valid-intent-exists`,
  `stale-restored-selection-absent-from-trip-facts-falls-back-to-default`, and
  `restored-selection-with-unknown-state-is-accepted-when-trip-is-reachable`.

## Candidate 2 Closeout Note

- Technical session restore, request identity derivation, invite adoption cache
  persistence, invalid-session clearing, and logout clear sequencing are now centralized
  in `shared/session-runtime`.
- Runtime callers no longer own raw `tripsync:*` keys or bearer-token mechanics directly.

## Candidate 3 Closeout Note

- `FinalApp` no longer owns Plan workspace orchestration directly.
- The `/trip/:tripId/plan` route now mounts `PlanFeature`, while browser navigation
  execution still remains in `FinalApp` through the command boundary.
- Plan-specific selection, comments, map/list coordination, menu/booking behavior,
  drawer lifecycle, and Cadensy change-request flow now live under
  `trip/src/final/plan-feature/`.
- This document should therefore continue to describe navigation limitations and
  host/workspace handoff limitations, not stale Plan-page ownership assumptions.

## Host / Workspace Handoff

- The host `/trip` page always boots the workspace through the default iframe preview
  source `/trip-app/index.html#/`.
- There is no structured host-to-workspace handoff for a workspace-level return intent.

## Invite / Join Flow

- Repeated-invite handling depends on a browser-local `tripsync:invite:<token>` record.
- Fresh join completion navigates to `/trip/:tripId/preferences`.
- Repeated invite reopen navigates to `/trip/:tripId/plan`.
- Invalid or stale invite links remain on the join route and render an unavailable UI state.

These behaviors are intentionally documented separately from the approved target product
policy so that Phase 2 matrix tests can encode the desired rules without inheriting the
current limitations.
