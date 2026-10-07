# TripSync Backend

Python + FastAPI + PostgreSQL. This backend owns the decision paths, itinerary data,
and decision ledger for the Trip workspace.

Product behavior is defined by:

- [`../README.md`](../README.md)
- [`../trip/BACKEND.md`](../trip/BACKEND.md)

Local environment and PostgreSQL setup are documented in:

- [`LOCAL_DEV.md`](LOCAL_DEV.md)

Recent backend handoff notes live in:

- [`../trip/交接.md`](../trip/%E4%BA%A4%E6%8E%A5.md)

---

## Quick Start

Requirements:

- Python 3.13
- PostgreSQL

Run everything from the `backend/` directory.

```powershell
cd backend

# If `python` is not available on your machine, replace it with your installed
# interpreter path, for example:
# D:\ANACONDA\python.exe -m venv .venv
python -m venv .venv

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
createdb tripsync
createdb tripsync_test
.\.venv\Scripts\python.exe -m app.db.seed
.\.venv\Scripts\python.exe -m uvicorn app.api.main:app --port 8000 --reload
```

If you see `Python was not found` after creating `.venv`, that usually means the
Windows `python` command is not on `PATH`. That is fine. After `.venv` exists, stop
using bare `python` commands and use:

```powershell
.\.venv\Scripts\python.exe
```

for every backend step instead.

Open:

- [http://localhost:8000/docs](http://localhost:8000/docs)
- [http://localhost:8000/api/health](http://localhost:8000/api/health)

Run tests:

```powershell
cd backend
$env:DISABLE_SCHEDULER='1'
.\.venv\Scripts\python.exe -m pytest -q
```

`.\.venv\Scripts\python.exe -m app.db.seed` will reset and rebuild demo data, so only
use it for local development or disposable demo databases.

If PostgreSQL is not running yet, start it before `seed`, `uvicorn`, or `pytest`.
The exact command depends on your local installation. A quick readiness check is:

```powershell
pg_isready -h localhost -p 5432
```

If `pg_isready` is not on `PATH`, run the same check from your PostgreSQL install folder.

If a focused pytest run collides with an older local `tripsync_test` database that you do
not want to touch, use a different disposable test database name for the current shell
instead of deleting that database blindly, for example:

```powershell
cd backend
$env:TEST_DATABASE_URL='postgresql+psycopg://postgres:postgres@localhost:5432/tripsync_test_codex'
.\.venv\Scripts\python.exe -m pytest tests/test_paths.py -q
```

## Login Works Only When These Three Things Are True

For the `frontend` login page to enter the Trip workspace successfully, all three must
be true at the same time:

1. The backend is running on `http://127.0.0.1:8000`
2. `backend/.env` points at a reachable PostgreSQL database
3. That database already contains the demo organizer login and its trip membership

The default demo login is:

```text
email: organizer@cadensy.local
password: 12345678
```

Important:

- opening `http://127.0.0.1:8000/docs` only proves the API process started
- it does not prove the database contains the login account
- it does not prove the account is attached to a trip

If login fails with `Invalid email or password`, the usual cause is that the database
was never seeded, or an older local database exists without the password hash.

If login fails after the backend starts cleanly, run one of these:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.db.seed
```

or, if you want to keep the existing local data and only restore the demo organizer
password login:

```powershell
cd backend
.\.venv\Scripts\python.exe -m app.db.enable_auth
```

After that, restart uvicorn and try the login again.

---

## Environment Variables

Put runtime variables in `backend/.env`. Do not commit real secrets.

| Name | Purpose | Default |
| --- | --- | --- |
| `DATABASE_URL` | runtime database | `postgresql+psycopg://localhost/tripsync` |
| `TEST_DATABASE_URL` | pytest database | `.../tripsync_test` |
| `UNSPLASH_ACCESS_KEY` | backend-only Trip city cover search | none |
| `DEEPSEEK_API_KEY` | DeepSeek cloud API key | none |
| `DEEPSEEK_BASE_URL` | DeepSeek base URL | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | DeepSeek model name | `deepseek-v4-flash` |
| `CHAT_AGENT_RUNTIME` | Chat Runtime selector | `legacy` |
| `MOCK_AI` | use local mock AI when `1` | `1` |
| `SETTLE_TICK_SECONDS` | settlement polling interval | `60` |
| `DISABLE_SCHEDULER` | disable scheduler when `1` | none |
| `DEV_ALLOW_MEMBERSHIP_HEADER` | retired; production fails fast if truthy | `0` |
| `FRONTEND_BASE_URL` | frontend base URL for redirects | `http://localhost:5173` |
| `CORS_ORIGINS` | allowed frontend origins | `http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000` |

Current AI runtime:

```text
chat      -> deepseek
planner   -> deepseek
explainer -> deepseek
```

`CHAT_AGENT_RUNTIME=legacy` is the default and current safe Chat behavior.
`CHAT_AGENT_RUNTIME=pydantic` is an explicit opt-in composition path. It
requires `MOCK_AI=0`, a present `DEEPSEEK_API_KEY`, the official
`https://api.deepseek.com` endpoint, and a configured or defaulted
`DEEPSEEK_MODEL`. Selecting it never falls back to Legacy: a composition
failure returns the existing safe degraded Chat response. The Pydantic path
pins model thinking off and does not inherit `DEEPSEEK_THINKING`.

Never hard-code real credentials into source files.

---

## Directory Layout

```text
app/
|-- domain/
|   |-- constraints/
|   |   |-- types.py
|   |   `-- engine.py
|   `-- decisions/
|       `-- orchestrator.py
|-- db/
|   |-- models.py
|   |-- session.py
|   `-- seed.py
|-- api/
|   `-- main.py
`-- jobs/
    `-- scheduler.py
tests/
```

Dependency direction is intentionally one-way:

`api -> domain -> db`

Do not pull API concerns back into `domain`.

---

## Core Architecture

### Constraint Engine

`domain/constraints/engine.py` exposes a deterministic classifier:

```python
classify(change, constraints) -> Classification
```

It does not call AI, touch the database, or make network requests.

### Decision Orchestrator

`domain/decisions/orchestrator.py` executes the decision path after classification:

- apply directly
- open a round
- reopen a round
- create a confirmation proposal

### API Layer

`api/main.py` should stay thin:

- parse request
- call domain
- return JSON

Business rules belong in domain code, not in route handlers.

---

## Privacy Model

Privacy is enforced structurally, not by hoping a filter catches everything:

1. Private raw user wording is stored separately from decision-safe constraint data.
2. Decision outputs do not carry identity fields or original wording.
3. Notice tables do not store actor identity in a way that leaks through normal reads.

The privacy regression tests protect this behavior.

---

## Database Guardrails

The database enforces key invariants directly:

- only one open round per item
- only one pending proposal per item
- one vote per member per round
- one confirmation response per member per proposal

Application code should still pre-check where possible so the user receives a clean
409 response instead of a raw 500.

---

## Change Ledger

`plan_change` is append-only. Current plan state is derived from the original plan plus
the accumulated ledger.

Each entry records its origin, for example:

- `notice`
- `round`
- `reopen_round`
- `confirm`
- `ai_generate`
- `rule_generate`

This is the main audit trail for explaining how the itinerary changed.

---

## Tests

Run:

```powershell
cd backend
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
.\.venv\Scripts\python.exe -m pytest -q
```

Important safety rules:

- `TEST_DATABASE_URL` must point to a clearly test-only database
- it must not match `DATABASE_URL`
- pytest may recreate the test database as a clean UTF-8 database

Representative test areas:

- `test_engine.py`: decision rules, privacy, determinism
- `test_schema.py`: database invariants
- `test_paths.py`: end-to-end decision flows
- `test_jobs.py`: scheduler behavior
- `test_auth.py`: registration, login, logout, bearer session, first-trip account flow
- `test_agent*.py`: chat and agent behavior
- `test_plan_generation.py`: planner fallback behavior
- `test_comments.py` / `test_booking.py`: comments, booking, organizer actions

---

## Global Place Library

Planner gets candidate locations through `app/domain/places/service.py`; it does not
call a third-party provider directly.

- All destinations, including Chicago, first query the PostgreSQL `place` table through the shared Place Service.
- `data/poi_chicago.py` remains in the repository for legacy reference, tests, demo fixtures, and compatibility, but it is no longer the formal Planner candidate source.
- Fewer than 12 cached city places triggers a Geoapify city geocode followed by a
  Geoapify Places request.
- Results are normalized and upserted using the unique provider identity
  `(provider, provider_place_id)`.
- Enabling real AI with `MOCK_AI=0` does not bypass this place pipeline: Planner still
  needs real place candidates from PostgreSQL cache and Geoapify before DeepSeek can
  choose among them.
- Missing image, opening hours, price, duration, or walking metadata remains null.
- A missing key, timeout, rate limit, provider 5xx, or invalid response falls back to
  the existing cache. With no candidates, normal blocked-plan behavior remains active.

Geoapify configuration belongs only in `backend/.env`:

```env
GEOAPIFY_API_KEY=
```

Trip city covers use a separate backend-only Unsplash key:

```env
UNSPLASH_ACCESS_KEY=
```

Unsplash is only for Trip city covers. It does not provide Planner candidate places.

The dashboard persists the selected hotlinked image URL and attribution on `Trip`.
Place and PlanItem photos remain separate. Provider failure keeps the neutral Travel cover.

For a new, empty local database, apply the versioned schema baseline:

```powershell
cd backend
.\.venv\Scripts\python.exe -m alembic -c alembic.ini upgrade head
```

Do not run `app.db.init_schema` as a substitute for migration history. For an
existing database created before Alembic, use the reviewed preflight and
stamping procedure in
`AI_enhanced/phase-0-audit-2026-10-03/11_PR05A_ALEMBIC_BASELINE.md`; stamping
is allowed only after a zero-drift comparison and a backup plan.
