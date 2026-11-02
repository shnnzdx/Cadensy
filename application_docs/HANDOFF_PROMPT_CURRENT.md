# Current Continuation Prompt

Status: current
Last reviewed: 2026-10-28

Use this prompt when handing the repository to another engineering agent:

> Work in the Cadensy repository. Read `AGENTS.md`, `README.md`, `HANDOFF.md`, `INTEGRATION-ROADMAP.md`, `docs/README.md`, and `docs/navigation-known-wrong-behavior.md` before substantial work. For backend cleanup also read `docs/HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md`, `backend/README.md`, and `backend/LOCAL_DEV.md`.
>
> Treat `frontend/`, `trip/`, `backend/`, `shared/`, and `docs/` as source. Do not treat generated `dist` files or `frontend/public/trip-app/assets/` as primary source. Preserve the frozen navigation-policy, session-runtime, and PlanFeature boundaries described in `AGENTS.md`.
>
> Recorded v0.3.0 production evidence shows backend task definition `tripsync-backend:31`, `CHAT_AGENT_RUNTIME=pydantic`, the Durable Conversation production latch enabled, and frontend task definition `tripsync-frontend:19`. CD-03 run `40382252075` is protected activation evidence, but its summary says real Pydantic provider acceptance was not executed. The local default may still be `legacy`; do not confuse local default with production state.
>
> `AI_enhanced/` is the latest AI engineering evidence collection. Its PR folders are immutable point-in-time reports; old statements such as “Legacy active”, “not merged”, or “not authorized” describe their report date, not current production. Start at `AI_enhanced/README.md`.
>
> Assess before refactoring, keep changes scoped, test in proportion to risk, and update current documentation when its claims become stale. Move replaced planning or deployment documents to `docs/_archive/`; keep heavily cross-referenced evidence in place and mark it historical. Never commit secrets or unredacted user/provider data.

Before acting, state which contract owns the requested behavior and whether the evidence being used is current, historical, or a fresh verification.
