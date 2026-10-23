# Handoff Prompt: Current Repo Takeover

Last refreshed: August 16, 2026

Use this when handing the current repository to another engineer or AI agent.

This document supersedes the older dated general prompts:

- `docs/HANDOFF_PROMPT_2026-08-14.md`
- `docs/HANDOFF_PROMPT_2026-08-11.md`

For deeper product/runtime specifics, read:

- `HANDOFF.md`

```text
You are taking over `C:\Users\zdxzh\Desktop\capstone\New`.

Treat the following as the current baseline unless you re-check the code and find stronger
new evidence.

1. Read these first:

- `AGENTS.md`
- `README.md`
- `INTEGRATION-ROADMAP.md`
- `HANDOFF.md`
- `backend/README.md`
- `backend/LOCAL_DEV.md`
- `docs/HANDOFF_PROMPT_CURRENT.md`
- `docs/navigation-known-wrong-behavior.md`

2. Before trusting any old branch summary:

- run `git status --short --branch`
- run `git log --oneline --decorate -n 10`
- treat the checked code as the truth, not an old handoff hash

3. Source-of-truth directories:

- `frontend/`
- `trip/`
- `backend/`
- `shared/`
- `docs/`
- `AWS/`

Do not treat these as primary source files:

- `frontend/public/trip-app/assets/`
- `trip/dist/`
- `frontend/dist/`

4. Current repo shape:

- `frontend/` is the main site, login/signup flow, and `/trip` host shell
- `/trip` is still an embedded workspace, not a direct source page
- `trip/` is still the source of the embedded Trip workspace
- `backend/` owns auth, membership, trip facts, planner, places, and decision paths
- `shared/` owns the frozen navigation/session seams

5. Important frozen boundaries:

- `shared/trip-navigation-policy/` owns workspace destination policy
- `shared/session-runtime/` owns technical session persistence and request identity
- `trip/src/final/plan-feature/PlanFeature.jsx` is the public Plan feature boundary
- `usePlanInteractionRuntime` owns Plan interaction state
- `useAssistantChangeRequestFlow` owns drawer-local assistant and change-request flow

Do not casually move these responsibilities back into UI components or `FinalApp`.

6. Embedded Trip reminder:

- changing `trip/` does not automatically update visible `/trip`
- after changing `trip/`, run:
  `cd frontend && npm run build:trip-preview`
- if `/trip` still looks old, check:
  - `frontend/public/trip-app/index.html`
  - `frontend/public/trip-app/embed-manifest.json`
- do not hand-edit generated bundle files to “fix” UI behavior

7. Windows local runtime gotchas:

- on this machine, bare `python` may fail with:
  `Python was not found`
- if that happens, create the venv once with a real interpreter path, for example:
  `D:\ANACONDA\python.exe -m venv .venv`
- after the venv exists, use:
  `.\.venv\Scripts\python.exe`
  for install, seed, uvicorn, and pytest

8. Backend startup reality:

- opening `http://127.0.0.1:8000/docs` is not enough
- real login requires:
  - backend running
  - `DATABASE_URL` reachable
  - database seeded with valid account/trip state
- if login fails, check backend traceback before assuming the browser could not connect

9. AI provider realities:

- the current real-provider route is DeepSeek-only; `DEEPSEEK_*` controls it
- Chat Runtime defaults to `CHAT_AGENT_RUNTIME=legacy`; `pydantic` remains explicit opt-in
- `MOCK_AI=1` is still the safest non-production local default
- production activation uses the protected CD-02 workflow, never legacy provider selectors
- do not assume “AI is broken” before checking runtime gates, provider configuration, and backend traceback

10. Current repo truths worth preserving unless intentionally changing them:

- `/trip` loads static output built from `trip/`
- backend decision paths remain backend-owned
- `shared/` still owns navigation/session seams
- generated frontend assets are not source-of-truth
- current handoff/runtime/product specifics live in `HANDOFF.md`

11. Do not do these:

- do not edit generated frontend or trip assets by hand
- do not rename `tripsync:*` storage keys casually
- do not reintroduce navigation policy inline in UI components
- do not assume `/docs` means login is correctly wired
- do not let AI choose decision paths

12. Before changing anything, answer:

- Is this a `frontend`, `trip`, `shared`, `backend`, `docs`, or `AWS` task?
- Does it touch a frozen boundary?
- If it touches `trip/`, have you rebuilt and resynced the embedded preview?
- If login fails, did you verify backend traceback, database contents, and membership state instead of only checking `/docs`?
```

## Why This Prompt Exists

This is the current general-purpose takeover prompt.

Use `HANDOFF.md` for the deeper operational and product baseline.

Use `docs/HANDOFF_PROMPT_BACKEND_REFACTOR_CURRENT.md` when the task is specifically backend
cleanup, architecture assessment, or repo consolidation.
