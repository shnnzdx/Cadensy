# v0.3.0-03A Final Independent Acceptance Review

Date: 2026-10-23

## Identity and reviewed scope

| Item | Value |
| --- | --- |
| Branch | `codex/v0303a-pydantic-durable-acceptance` |
| Candidate baseline / HEAD before this commit | `8012dce80d5d8ecd30906192311d685ed1d9e13f` |
| Merge-base with reviewed baseline | `8012dce80d5d8ecd30906192311d685ed1d9e13f` |
| Scope | One acceptance-test module and Stage 03A documents/evidence `13` through `18` |
| Production source changes | None |

The original blocked assessment in `15_V0303A_INDEPENDENT_CODE_REVIEW.md`
remains historical evidence. It correctly recorded that the original seven
parametrized cases did not establish physical connection independence or direct
historical-option staleness. The follow-up closure artifacts `16` and `17`
identify those deficiencies and do not rewrite that chronology.

## P1 closure verification

### P1-01 — physical PostgreSQL connection evidence: PASS

`independent_postgres_sessions` creates a test-only Engine with `NullPool` and
binds the real Coordinator, Preview, Durable, and test mutator Session factory
to it. `_TracingSession` receives the actual SQLAlchemy transaction connection
in `after_begin` and reads psycopg's server-assigned `backend_pid`; it does not
open an unrelated diagnostic connection or issue a query before Preview makes
its short transaction read-only.

For A4 and both A6 parameters, phase attribution covers the worker before the
FunctionModel barrier, the mutator, worker work after the commit marker, and a
final observer transaction. `_assert_independent_postgres_race` requires each
phase to have a PID and asserts the mutator PID is disjoint from before, after,
and observer PIDs. The mutator's `factory.begin()` exits before the commit marker
and FunctionModel release. Bounded Events, `finally` release/join, and surfaced
worker exceptions prevent a sleep race or stranded worker.

Fresh independent execution on a new loopback-only PostgreSQL 18 cluster
recorded these safe backend PID groups:

| Scenario | Worker before | Mutator | Worker after | Observer |
| --- | --- | --- | --- | --- |
| A4 revocation | `9716, 28540` | `15004` | `22972, 36508` | `50440` |
| A6 Plan revision | `47512, 52836` | `31580` | `25964, 49676` | `37464` |
| A6 facts revision | `50604, 53700` | `28508` | `4964, 23172` | `50836` |

Each run rejected the delayed result at the actual Preview/Durable authority
boundary, persisted zero unauthorized Assistant/Option rows, and left the AI
Plan item unchanged.

### P1-02 — historical Option stale rejection: PASS

A2 is parameterized for Plan revision and planning-facts revision. It drives a
real local Pydantic `FunctionModel` through `ConversationRuntimeCoordinator`,
`RevisionBracketedPreview`, scoped reads, and Durable persistence. Authorized
history returns the persisted option; the actual
`conversation_service.resolve_historical_option(...)` initially yields
`ready=True`, `code="historical_option_ready"`.

After the test commits the selected revision change, it resolves the same
assistant and option IDs again. Both parameters assert `ready=False` and
`code="historical_option_stale"`, preserve original stored option revision
metadata, and prove the Plan item was not mutated by the AI workflow. The DTO
field is named `code`; it is the service's equivalent of the acceptance
requirement's stated denial reason.

## Final scenario matrix

| Scenario | Pytest cases | Result |
| --- | --- | --- |
| A1 two-turn durable context | 1 | Ordered server-owned history and no Plan mutation |
| A2 persisted advisory Option and stale rejection | 2: Plan/Facts revisions | Ready before change; `historical_option_stale` after change |
| A3 pre-runtime revocation | 1 | No model call or durable output |
| A4 mid-runtime revocation | 1 | `membership_revoked`, no durable output, physical PID evidence |
| A5 cross-trip isolation | 1 | Rejected before model execution |
| A6 in-flight revision fencing | 2: Plan/Facts revisions | `stale_context`, no durable output, physical PID evidence |

This is eight pytest cases: A2 and A6 are each two parametrized cases. The P1
focused command selects two A2, one A4, and two A6 cases, hence five cases.

## Test and evidence verification

The independent review used a newly initialized task-owned PostgreSQL cluster on
loopback port `55441`, database `cadensy_v0303a_final_test`, with `APP_ENV=test`,
`MOCK_AI=1`, `DISABLE_SCHEDULER=1`, and `DEV_ALLOW_MEMBERSHIP_HEADER=0`.
Provider and AWS credentials were removed from the child process.

| Command | Result | Exit |
| --- | --- | --- |
| Focused A2/A4/A6 nodes with backend-PID output | 5 passed in 2.98s | 0 |
| `pytest -q tests/test_pydantic_durable_runtime_acceptance.py` | 8 passed in 2.88s | 0 |
| `pytest --collect-only -q tests/test_pydantic_durable_runtime_acceptance.py` | 8 tests collected | 0 |
| Previously execution-backed relevant regression set | 192 passed in 7.66s | 0 |

The four source hashes recorded in `17_V0303A_P1_EVIDENCE_CLOSURE.json` match
the current test and documents `13`, `15`, and `16`. Both JSON files parse. The
recorded P1-closure command names, node IDs, outcomes, safe PID attribution, and
exit codes match the tested source. Its compile/JSON command was corrected to a
real executed parser command during this final review.

## Remaining limitations

- This is not a claim of full Stage 03: lease recovery, timeout/cancellation,
  end-to-end telemetry sequence evaluation, and Pydantic-versus-Legacy formal
  comparison remain outside this slice.
- The acceptance fixtures intentionally commit setup rows to support genuine
  independent transactions. They are run only against a newly created,
  task-owned disposable database in this validation; UUID fixture identities and
  conversation-scoped assertions avoid cross-scenario claims. The data is
  retained only in that disposable database until its lifecycle cleanup.

## Verdict

No P0/P1 production or acceptance-evidence blocker remains. Production business
code is unchanged. This review authorizes the gated local Stage 03A commit; the
resulting commit SHA is reported by Git and in the final handoff.

`STAGE 03A FINAL REVIEW: PASS`
