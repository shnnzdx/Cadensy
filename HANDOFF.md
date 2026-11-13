# Cadensy Current Handoff

Status: current operational handoff
Last reviewed: 2026-11-07
Release evidence baseline: Cadensy v0.3.0

This file summarizes the state recorded in the repository. It is not a fresh live-AWS inspection. For documentation ownership and evidence rules, start at [`application_docs/README.md`](application_docs/README.md).

## Contribution log

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
