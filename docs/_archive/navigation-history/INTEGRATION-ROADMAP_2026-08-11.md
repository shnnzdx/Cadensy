# TripSync Frontend Integration Roadmap

## Purpose

This document records:

- what was changed to make `frontend/` and `trip/` more compatible
- why those changes were made this way
- what should happen next if the team wants deeper unification

## Starting Problem

The repository had two separate frontend implementations for the same product:

- `frontend/` was the main product/landing shell
- `trip/` was a separate workspace app built independently

That created a few concrete problems:

- duplicated design decisions
- duplicated route assumptions
- manual, fragile copy steps for embedding `trip` into `frontend`
- business paths and embed paths hardcoded in multiple places
- no stable contract describing how `frontend` should host `trip`

## What Was Changed

### 0. Repository cleanup and local tool separation

Updated:

- local skill installation artifacts were moved outside the repository to `C:\Users\ROG\Desktop\capstone\TripSync-Skills-Summary`
- frontend design reference documents were kept under `docs/frontend/`
- the removed `monad-pipeline-animation/` folder is no longer part of the active root-level project structure

This keeps application commits focused on product code, integration contracts, tests, and project docs.

### 1. Shared preview theme

Added:

- `shared/tripsync-preview-theme.css`

This gives both apps one shared source for the embedded Trip preview's base visual tokens.

### 2. Shared embed contract

Added:

- `shared/tripsync-preview-contract.js`
- `frontend/app/trip/preview-config.ts`

This centralizes:

- `/trip-app` base path
- default preview route
- iframe source construction
- absolute invite/preview URL helpers

### 3. Shared domain path helpers

Added:

- `shared/tripsync-domain.js`

This centralizes the most repeated business paths:

- workspace home
- create-trip flow
- account sections
- trip workspace sections
- invite join links
- legacy organizer/participant helper names that now map to the active workspace routes

### 4. Automated Trip asset sync

Added:

- `frontend/scripts/sync-trip-preview.mjs`

Updated:

- `frontend/package.json`

This replaces a fragile manual copy workflow with a repeatable sync command.

Later updated:

- `frontend/scripts/sync-trip-preview.mjs`

The script now runs `npm run build` correctly on Windows when invoked through:

```bash
cd frontend
npm run build:trip-preview
```

### 5. Embed manifest

Generated:

- `frontend/public/trip-app/embed-manifest.json`

This lets the shell know which Trip build is currently embedded.

### 6. Compatibility tests

Added:

- `frontend/tests/trip-preview-integration.test.mjs`

These tests verify:

- shared preview routing contract
- shared preview theme contract
- sync script behavior
- shared product content and demo Trip fallback data

### 7. Shared product and demo data

Added:

- `shared/tripsync-product-content.js`
- `shared/tripsync-demo-data.js`

Updated:

- `frontend/app/how-it-works/page.tsx`
- `frontend/app/FeatureStory.tsx`
- `frontend/app/ProductPrinciples.tsx`
- `trip/src/final/tripContent.js`

This moves stable product workflow copy, product principles, and Trip demo fallback data into shared modules. `trip/src/final/tripContent.js` remains as a compatibility layer so existing Trip workspace imports keep working.

### 8. Shared workspace navigation and session seams

Added:

- `shared/trip-navigation-route/`
- `shared/trip-navigation-policy/`
- `shared/session-runtime/`

Updated:

- `trip/src/final/workspace-navigation-model.js`
- `trip/src/final/navigation-normalizers.js`
- `trip/src/final/TripAppState.jsx`
- `trip/src/final/FinalApp.jsx`
- `frontend/app/login/page.tsx`
- `trip/src/final/plan-feature/PlanFeature.jsx`
- `trip/src/final/plan-feature/usePlanInteractionRuntime.js`
- `trip/src/final/plan-feature/useAssistantChangeRequestFlow.js`

This later architecture pass established two deep shared seams without merging the apps:

- `trip-navigation-policy` owns workspace destination policy, route reachability, restoration fallback, and invite/join destination decisions
- `session-runtime` owns raw `tripsync:*` key policy, bearer-token mechanics, request identity derivation, invite adoption cache persistence, invalid-session clearing, and logout technical-session sequencing

The remaining runtime split is intentional:

- `frontend` still owns the host `/trip` shell
- `trip` still owns workspace rendering and domain hydration
- `FinalApp` now mounts the Plan route and executes Plan feature navigation commands, but no longer owns Plan workspace orchestration directly
- `PlanFeature` is now the single public Plan workspace boundary, with deep internal seams for interaction state/effects and assistant/change-request flow
- browser navigation execution still lives outside the shared policy/runtime modules

## Why This Approach

We did not hard-merge the two apps immediately.

Instead, the work followed this order:

1. stabilize integration seams
2. remove repeated constants and path rules
3. automate syncing
4. add tests around those seams
5. only then consider deeper codebase consolidation

This reduces regression risk while the team still has two active code shapes.

## Current State

As of Tuesday, August 11, 2026:

- `frontend` builds successfully
- `trip` builds successfully
- full `frontend npm test` passes
- `/trip` embeds the synced static Trip workspace using shared contracts
- shared route helpers match the active `trip/src/final/FinalApp.jsx` routes:
  `#/`, `#/create`, `#/account/:section`, `#/trip/:tripId/:section`, and `#/join/:token`
- `frontend` now reads product workflow/principle content from `shared`
- `trip` now reads demo fallback data from `shared`
- workspace route guards, restoration fallback, and invite/join destination ownership now flow through `shared/trip-navigation-policy/`
- technical session restore/adopt/invalidate/logout and request identity ownership now flow through `shared/session-runtime/`
- raw `tripsync:*` session key knowledge is now isolated to `shared/session-runtime/`
- Plan workspace orchestration now flows through `trip/src/final/plan-feature/PlanFeature.jsx` instead of living directly inside `trip/src/final/FinalApp.jsx`
- `frontend/public/trip-app/` has been regenerated from the current `trip` build
- AWS identity validation has succeeded through GitHub Actions using repository secrets and `aws sts get-caller-identity`

Still true:

- `frontend` and `trip` are separate apps
- product data and page configuration are not fully unified
- Phase 3 deeper runtime merge is intentionally paused
- no AWS application resources have been created or deployed from this repository yet

## Recommended Next Changes

### Near-term

1. Commit and push the current Phase 1/Phase 2 integration changes.
   Keep the commit scoped to route contracts, shared content/data, tests, docs, and regenerated `frontend/public/trip-app` assets.

2. Run GitHub Actions after push.
   Confirm AWS identity validation still succeeds and add a separate build/test validation workflow before any deployment workflow.

3. Continue moving Trip workspace copy into shared modules.
   Candidates: invite copy, account labels, route metadata, member role labels.

4. Add lightweight schemas or TypeScript types for shared Trip data.
   Goal: one checked shape for trip IDs, sample entities, plan days, updates, and page-level content.

5. Decide the first AWS deployment target only after local and GitHub build validation are green.
   Current likely order: static frontend hosting first, backend/API second, database last.

### Mid-term

1. Create a shared UI primitive layer for both apps.
   Candidates: badges, buttons, status chips, shell headers, plan cards.

2. Move shared Trip route definitions into a reusable router config or path map.

3. Reduce static embed coupling by letting `frontend` consume more shared runtime code directly.

### Long-term

1. Revisit Phase 3 when the team is ready.
   Decide whether `trip` remains a standalone prototype surface or gets absorbed into `frontend`.

2. If absorbed:
   Move core Trip workspace pages into `frontend/app`
   Replace static embed deployment with a single deployment surface
   Retire duplicated assets and transitional sync scripts

## Phase Status

| Phase | Status | Notes |
| --- | --- | --- |
| Phase 1: route/embed contract alignment | Complete | `shared/tripsync-preview-contract.js`, `shared/tripsync-domain.js`, manifest, and tests now match the active Trip routes. |
| Phase 2: shared product/demo data | Complete for first pass | Stable product content and Trip fallback data now live in `shared/`; existing UI imports remain compatible. |
| Candidate 1: workspace navigation seam | Complete | `shared/trip-navigation-policy/` now owns workspace destination policy while `frontend` and `trip` keep execution/rendering local. |
| Candidate 2: technical session seam | Complete | `shared/session-runtime/` now owns raw session persistence, bearer token mechanics, request identity, invite-cache persistence, invalidation, and logout sequencing. |
| Candidate 3: FinalApp Plan workspace seam | Complete | `PlanFeature` is now the single public Plan boundary; `usePlanInteractionRuntime` and `useAssistantChangeRequestFlow` own the deeper Plan interaction and assistant/change-request behavior. |
| Phase 3: deeper runtime merge | Paused | Do not move Trip workspace pages into `frontend/app` yet. |
| AWS deployment | Not started | Identity validation succeeded; no billable AWS resources should be created until deployment architecture is approved. |

## Decision Rule For Future Work

When choosing the next refactor, prefer this order:

1. shared contract
2. shared data
3. shared primitives
4. shared page composition
5. runtime merge

That order keeps the system usable while steadily reducing duplication.
