# Cadensy Current Handoff

Status: current operational handoff
Last reviewed: 2026-11-20
Release evidence baseline: Cadensy v0.3.0

This file summarizes the state recorded in the repository. It is not a fresh live-AWS inspection. For documentation ownership and evidence rules, start at [`application_docs/README.md`](application_docs/README.md).

## Contribution log

### 2026-11-24 — Option C invite repository head

- Repository Alembic head for this change is `c0b2f9e5a6d3` (Rev 1
  `c0a1e8d4b5f2`, Rev 2 `c0b2f9e5a6d3`). Production was not migrated.
  Production remains at the previously completed revision `c8e1d4a90b27`
  until an Owner-approved migration, including the destructive
  revoke-in-place cleanup in Rev 2.
- HMAC key material is not created or injected by this change. Release
  pipeline pins stay on `c8e1d4a90b27` until a separate PR.
- Final plan approval is `GET /api/trips/{trip_id}/final-plan` and
  `POST /api/trips/{trip_id}/final-plan/approve`. Readiness is a read of
  the active plan, its items, open decision rounds, and pending change
  proposals. Early close follows G-1 and stays closed after later
  itinerary edits. The decision engine and the join-during-active-decision
  409 are unchanged.

### 2026-11-20 — P0 activation-funnel handoff refresh

- The current source now records privacy-safe activation events, reusable
  organizer invite links, and the first-preference flow. The active contract
  and metric definitions are in
  [`application_docs/activation-funnel.md`](application_docs/activation-funnel.md).
- Release and migration gates are pinned to Alembic revision `c8e1d4a90b27`.
  The protected schema-migration gate completed on 2026-11-22 from
  `b7d3a1e9c642` to `c8e1d4a90b27`. It did not deploy either ECS service.
- No production migration, ECS deployment, or production-data change is
  recorded by this handoff update.

### 2026-11-07 — Return-URL documentation correction

- Updated current navigation documentation after the safe internal return-URL validation merged in PR #48.
- Login and signup now normalize and validate `next` as a safe internal path before redirecting; the remaining gap is still the absence of a structured workspace return intent.
- Documentation-only maintenance; no source code, AWS resource, deployment, migration, or production data was changed.

### 2026-11-01 — Artifact scope safety correction

- Corrected artifact input classification so documentation, tests, and workflow-only changes cannot automatically publish ECR images; workflow source changes require a manual controlled rebuild.
- A prior documentation-domain rename was misclassified by the old matcher. It published one immutable backend artifact but did not deploy ECS, update a service, run a migration, or change production data. The matching frontend run was cancelled before its ECR push.

### 2026-11-01 — Documentation-domain rename

- Renamed `docs/` to [`application_docs/`](application_docs/README.md) to make its product, frontend, backend, and application-handoff scope explicit.
- Kept [`AWS/`](AWS/README.md) and [`AI_enhanced/`](AI_enhanced/README.md) as independent documentation domains; historical evidence remains unchanged.

### 2026-11-04 — Current prompt path maintenance

- Updated the current application and backend continuation prompts to reference `application_docs/` after the documentation-domain rename.
- Preserved historical `docs/` references inside archived and point-in-time evidence; those records are not rewritten.
- Documentation-only maintenance; no source code, AWS resource, deployment, migration, or production data was changed.

### 2026-10-31 — Workflow consolidation verification

- Reviewed the workflow consolidation merged by [PR #42](https://github.com/shnnzdx/Cadensy/pull/42) at `fa7a209`.
- Confirmed the active GitHub Actions inventory is ten workflows, with completed setup and superseded release YAML preserved under [`AWS/archive/workflows/`](AWS/archive/workflows/README.md).
- Confirmed the post-merge CI Regression Gates run and both immutable backend/frontend artifact runs completed successfully.
- No ECS deployment, database migration, or production data mutation was performed as part of this contribution.

## Recorded production state

- Release `v0.3.0` targets commit `ea7c9e3c8c4da3c4a518e47ffc528df5ed199233`.
- Backend task definition: `tripsync-backend:31`.
- AI runtime: `CHAT_AGENT_RUNTIME=pydantic`.
- Durable Conversation production latch: enabled.
- Frontend task definition: `tripsync-frontend:19`.
- Protected CD-03 activation run: GitHub Actions run `40382252075`.

Important evidence boundary: CD-03 proves the protected activation workflow completed and the recorded deployment checks passed. Its workflow summary says **real Pydantic provider acceptance was not executed**, so do not represent that run as a production real-provider end-to-end test.

The local backend default can remain `legacy`. That is a development default and does not supersede the recorded production state above.

## Active architecture boundaries

The current application source of truth is `frontend/`, `trip/`, `backend/`, `shared/`, and `application_docs/`. Generated bundles under `frontend/dist/`, `trip/dist/`, and `frontend/public/trip-app/assets/` are not primary source.

Do not redesign these established seams casually:

1. `shared/trip-navigation-policy/` owns workspace destinations, reachability, restoration fallback, and invite/join destination decisions.
2. `shared/session-runtime/` owns `tripsync:*` persistence, bearer-token and identity-header assembly, invite-adoption cache persistence, invalid-session clearing, and logout sequencing.
3. `trip/src/final/plan-feature/PlanFeature.jsx` is the Plan boundary. `usePlanInteractionRuntime` owns Plan interactions; `useAssistantChangeRequestFlow` owns drawer-local Cadensy conversation and proposal/application flow. `FinalApp.jsx` composes and executes commands.

If `trip/` changes and the embedded `/trip` experience must reflect it, run `cd frontend && npm run build:trip-preview`.

## AI and durable conversation behavior

- Runtime selection is explicit and fail-closed when `pydantic` is requested but cannot initialize.
- Durable conversations, membership authorization, idempotency, and concurrency fencing are backend concerns; browser state is not the authority.
- Historical AI reports are organized in [`AI_enhanced/README.md`](AI_enhanced/README.md). Reports retain their original conclusions, so old “Legacy active” or “not authorized” statements are historical.
- The implementation map is [`backend/app/agents/AI_AGENT_SUMMARY.md`](backend/app/agents/AI_AGENT_SUMMARY.md).

## Known gaps

- Product-to-workspace login/signup handoff still carries only a safe-normalized internal `next` destination rather than a complete structured return intent.
- The host `/trip` shell still defaults to the iframe path; host-owned workspace restoration is not the sole entry model.
- CD-03 did not execute real Pydantic provider acceptance.
- Provider cost/rate governance should not be described as complete unless a newer contract and evidence prove it.
- The P0 activation schema migration is live at `c8e1d4a90b27`. Backend and
  frontend promotion remain separate owner-gated operations; source or image
  evidence alone is not deployment evidence.

The first two items are tracked in [`application_docs/navigation-known-wrong-behavior.md`](application_docs/navigation-known-wrong-behavior.md).

## Safe continuation workflow

1. Read `AGENTS.md`, `README.md`, this file, `INTEGRATION-ROADMAP.md`, and the relevant source-specific docs.
2. Inspect the implementation and tests before accepting any status statement from a historical report.
3. For backend cleanup, assess the seam first; do not rewrite modules because they are large.
4. Keep local, CI, protected-deployment, and live-production evidence separate.
5. Update the current contract in the same change that invalidates it; archive superseded planning material instead of silently leaving it current.

## Validation entrypoints

Use the commands documented in [`README.md`](README.md), [`backend/LOCAL_DEV.md`](backend/LOCAL_DEV.md), and [`AWS/README.md`](AWS/README.md). Scope verification to the change. Documentation-only work should at minimum run a relative-link check and `git diff --check`.

No secret value belongs in handoff text, logs, screenshots, or committed evidence.
