# Cadensy Integration Roadmap

Status: current roadmap
Last reviewed: 2026-10-28

This roadmap records what is integrated now and what remains. The original 2026-08-11 roadmap is preserved at [`docs/_archive/navigation-history/INTEGRATION-ROADMAP_2026-08-11.md`](docs/_archive/navigation-history/INTEGRATION-ROADMAP_2026-08-11.md).

## Current system shape

| Area | Current state | Owner |
| --- | --- | --- |
| Product shell and `/trip` host | Integrated; embedded Trip remains supported | `frontend/` |
| Trip workspace | Active source implementation | `trip/` |
| Navigation policy | Extracted and shared | `shared/trip-navigation-policy/` |
| Session mechanics | Extracted and shared | `shared/session-runtime/` |
| Plan workspace behavior | Frozen behind PlanFeature/runtime hooks | `trip/src/final/plan-feature/` |
| API and persistence | FastAPI/PostgreSQL backend | `backend/` |
| AI runtime | Recorded production runtime is Pydantic | `backend/app/agents/`, `AI_enhanced/` |
| AWS delivery | Implemented with protected release evidence | `.github/workflows/`, `AWS/` |

## Completed integration milestones

- Product and Trip are connected through the frontend `/trip` shell.
- Destination and restoration rules moved out of UI components into the shared navigation policy.
- Technical session storage and request identity assembly moved into the shared session runtime.
- Plan selection, comments, map/list coordination, booking, drawer state, and Cadensy change-request behavior were separated from `FinalApp.jsx` behind the frozen Plan boundary.
- Backend persistence, durable conversations, idempotency, concurrency fencing, safe telemetry, and AI-runtime selection received dedicated implementation and evidence work.
- Pydantic runtime migration was released as v0.3.0 and activated by the protected CD-03 record.

## Remaining work

### 1. Complete structured return intent

Product login and signup currently preserve a simple `next` destination. They do not carry the full workspace intent needed for precise restoration after authentication. Any fix must extend the shared navigation/session boundaries rather than insert destination policy into pages.

### 2. Decide the long-term `/trip` host model

The iframe path is still the default host behavior. A future change may make the host own more restoration and shell state, but it must preserve the current source boundary and avoid duplicating Trip state.

### 3. Close provider-level production evidence

The CD-03 activation record did not run real Pydantic provider acceptance. Add bounded, redacted evidence through an approved workflow before claiming production real-provider end-to-end acceptance.

### 4. Continue backend consolidation by seam

Before backend cleanup, use locality, leverage, testability, migration risk, and the deletion test. Identify and freeze the strongest seam, then migrate in phases. Do not begin with a broad rewrite.

### 5. Keep documentation state synchronized

Treat [`docs/README.md`](docs/README.md) as the documentation map. Update current contracts with implementation changes and archive superseded planning/deployment material. Keep point-in-time evidence immutable.

## Constraints

- Do not reintroduce role-based destination logic into UI components.
- Do not access raw `tripsync:*` session keys or assemble identity headers outside `shared/session-runtime/`, except explicit tests/fixtures.
- Do not move Plan interaction or assistant state back into `FinalApp.jsx`.
- Do not treat generated bundles as primary source.
- Do not infer current production state from historical deployment reports.

Active navigation limitations are documented in [`docs/navigation-known-wrong-behavior.md`](docs/navigation-known-wrong-behavior.md); current operational details are in [`HANDOFF.md`](HANDOFF.md).
