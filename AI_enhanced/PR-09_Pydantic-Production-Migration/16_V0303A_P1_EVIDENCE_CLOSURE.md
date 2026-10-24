# v0.3.0-03A P1 Acceptance Evidence Closure

Date: 2026-10-23

## Scope

This correction closes only the two P1 acceptance-evidence gaps reported in
`15_V0303A_INDEPENDENT_CODE_REVIEW.md`. It changes test code and Stage 03A
evidence documents only. No production business code, Legacy Runtime, Pydantic
adapter behavior, Domain authorization, Preview, Durable fencing, schema,
configuration, CI/CD, provider, or AWS resource was changed.

## P1-01: physical PostgreSQL connection attribution

The prior A4/A6 tests used separate SQLAlchemy Sessions and committed
transactions, but did not prove those Sessions used distinct PostgreSQL
backends. The revised tests create a test-only engine using `NullPool` and bind
the real Coordinator, Preview, Durable flow, and mutator to its sessionmaker.
`NullPool` closes a DBAPI connection when each Session closes, preventing a pool
checkout from reusing a physical backend.

The test-only `_TracingSession` records psycopg's `Connection.info.backend_pid`
at actual transaction start. This does not issue SQL and therefore does not
interfere with Preview's subsequent `SET TRANSACTION READ ONLY`. The trace keeps
physical backend PID separate from SQLAlchemy's in-process Session identity.

For each A4/A6 race it requires, in order:

1. The real Coordinator/Preview reaches the FunctionModel barrier after worker
   transactions have begun.
2. A separately traced backend commits the membership or revision mutation.
3. The worker resumes and starts a post-commit Preview/Durable transaction.
4. A further independent observer transaction reads the committed state.
5. The expected `membership_revoked` or `stale_context` boundary rejects output;
   no Assistant or Option is durable.

The model barrier uses bounded `threading.Event` waits, releases in `finally`,
joins the worker, and propagates worker exceptions. No sleep synchronization is
used. This retains production's intentional behavior of closing bounded Preview
read transactions before model latency.

## P1-02: stale historical-option rejection

The A2 test now runs separately for a Plan revision and a planning-facts
revision. It persists one real Pydantic-produced advisory Option, projects it
through authorized durable history, resolves that same stored option as
`historical_option_ready`, commits the designated revision change, then calls
`conversation_service.resolve_historical_option(...)` again with the same
assistant and option IDs. The second result is asserted to be
`ready=False` with `code="historical_option_stale"`.

The test also asserts that the stored option retains its original revision
metadata and that the AI workflow did not mutate the Plan item. This is distinct
from the A6 in-flight Preview rejection path.

## Newly executed results

All commands used a newly initialized task-owned PostgreSQL 18 cluster on
loopback port `55440`, database `cadensy_v0303a_fix_test`, with test gates
`APP_ENV=test`, `MOCK_AI=1`, `DISABLE_SCHEDULER=1`, and
`DEV_ALLOW_MEMBERSHIP_HEADER=0`. Provider and AWS credentials were removed from
the pytest child environment. The Pydantic model was a local FunctionModel with
model requests disabled.

| Validation | Result | Exit |
| --- | --- | --- |
| A2/A4/A6 focused nodes | 5 passed in 3.04s | 0 |
| Complete Stage 03A direct module | 8 passed in 3.16s | 0 |
| Relevant Pydantic/Preview/Durable/authorization/concurrency/telemetry/Legacy regression set | 192 passed in 7.66s | 0 |
| Python compilation and JSON validation | passed | 0 |

Safe backend-PID observations from the focused execution are recorded in the
paired JSON evidence. They identify server processes only; no connection string,
credential, prompt, private user content, or database rows are recorded.

## Remaining limitations

This closes the specific A2/A4/A6 P1 evidence gaps. It does not claim full Stage
03 acceptance, lease-recovery races, timeout/cancellation durability,
end-to-end telemetry sequence evaluation, a Pydantic-versus-Legacy formal
comparison, Stage 03B, or any production authorization.
