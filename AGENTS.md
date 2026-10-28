# Repo Working Rules For AI Agents

Start here before changing architecture, session logic, navigation logic, or Trip workspace behavior.

## Read First

Read these files before substantial work:

- `README.md`
- `INTEGRATION-ROADMAP.md`
- `HANDOFF.md`
- `docs/README.md`
- `docs/navigation-known-wrong-behavior.md`

If the task is backend cleanup or repo consolidation, also read:

- `docs/HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md`
- `backend/README.md`
- `backend/LOCAL_DEV.md`

If the task changes AI-agent runtime, durable conversation behavior, provider
handling, or AI evidence, also read:

- `AI_enhanced/README.md`
- `backend/app/agents/AI_AGENT_SUMMARY.md`

## Documentation Lifecycle

- Update current contracts in the same change that makes them stale.
- Treat `AI_enhanced/PR-*`, `AI_enhanced/00_*`, `docs/backend/`, and
  `docs/frontend/` as point-in-time evidence, not current status.
- Move superseded planning, temporary deployment links/assets, and replaced
  handoffs to `docs/_archive/`.
- Do not rewrite historical evidence to match the present; add a newer record
  and update the current index.

## Current Source Of Truth

Treat these as the active source-of-truth:

- `frontend/`
- `trip/`
- `backend/`
- `shared/`
- `docs/`

Do not treat these as primary source files:

- `frontend/dist/`
- `trip/dist/`
- `frontend/public/trip-app/assets/`

If you change `trip/` and the embedded `/trip` experience must reflect it, use:

- `cd frontend && npm run build:trip-preview`

## Frozen Architecture Boundaries

These boundaries are already established. Do not casually redesign them.

### Candidate 1: Navigation

- `shared/trip-navigation-policy/` owns workspace destination policy, route reachability, restoration fallback, and invite/join destination decisions.
- UI code must not reintroduce role-based or destination policy inline.

### Candidate 2: Technical Session

- `shared/session-runtime/` owns raw `tripsync:*` storage keys, bearer-token mechanics, request identity headers, invite adoption cache persistence, invalid-session clearing, and logout sequencing.
- Runtime callers must not directly own `localStorage` session persistence policy, `Authorization`, `X-Trip-Id`, or `X-Membership-Id` assembly.

### Candidate 3: Plan Workspace

- `trip/src/final/plan-feature/PlanFeature.jsx` is the single public Plan feature boundary.
- `usePlanInteractionRuntime` owns Plan selection, focus, comments, map/list coordination, menu state, booking interactions, and drawer open/close state.
- `useAssistantChangeRequestFlow` owns drawer-local Cadensy conversation, proposal apply flow, change-request outcome handling, and command emission for confirm redirects.
- `trip/src/final/FinalApp.jsx` should only own Plan route mount, workspace composition, PlanFeature inputs, and command execution.

## UI Changes Are Allowed

You can freely change product and trip UI design when the change is mostly visual:

- typography, color, spacing, radius, shadows
- layout, section order, responsive structure
- drawer/modal appearance
- navbar/sidebar styling
- Plan page visual structure
- Product page visual structure
- animation and transitions
- loading, empty, hover, and active states

## Use This Decision Rule

When deciding where a change belongs:

- If the change is about how it looks, change the UI.
- If the change is about where the user should go, respect Candidate 1.
- If the change is about who the user is, what trip/session is active, or which identity headers/storage are used, respect Candidate 2.
- If the change is about how Plan selection/comments/map/booking/drawer/Cadensy behavior works, respect Candidate 3.

## Do Not Reintroduce These Old Problems

- Do not hard-code role-based destination logic inside UI components.
- Do not read or write raw `tripsync:*` session keys outside `shared/session-runtime/`, except in tests and explicit compatibility fixtures.
- Do not manually assemble `Authorization`, `X-Trip-Id`, or `X-Membership-Id` outside the session runtime seam.
- Do not move Plan interaction logic back into `FinalApp`.
- Do not duplicate Plan selection, comments, drawer, map, booking, or assistant state in a second place just to support a visual redesign.
- Do not use `shared/` as a dumping ground for unrelated helpers.

## For Refactors

Before major refactors:

- assess first
- grill the strongest candidate
- freeze the boundary
- make a migration plan
- implement in phases

Do not refactor based only on file size.
Use locality, leverage, testability, migration risk, and the deletion test.

## Backend Default

If no newer instruction overrides this, the safest next architecture step is:

- backend architecture assessment first
- implementation second

Do not start by rewriting backend modules before identifying the real seam.
