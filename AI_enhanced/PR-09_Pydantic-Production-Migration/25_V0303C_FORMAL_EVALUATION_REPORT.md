# v0.3.0-03C — Formal Legacy/Pydantic Evaluation and Migration Acceptance

## Scope and source identity

This is an offline, deterministic migration-candidate evaluation. It does not
authorize Pydantic production activation, DeepSeek calls, deployment, schema
changes, or a change to the Legacy default.

- Worktree: `C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303c-pydantic-formal-evaluation`
- Branch: `codex/v0303c-pydantic-formal-evaluation`
- Evaluated source: `ae030a9b1f0c2e765a5c925b128fcfaafc40f7ae`
- Stage 03A parent: `b1981d849e24e9c88a4ccf997c8831addbd4a48b`
- Stage 02 ancestor: `8012dce80d5d8ecd30906192311d685ed1d9e13f`

03A and 03B commits are preserved. Stage 03B was clean before this worktree
was created. PR #31 was not modified. No production application, schema,
configuration, provider, deployment, or workflow file changed in this stage.

## Existing-evidence mapping

| Requirement | Existing harness/test | Runtime actually exercised | Evidence | Additional test |
| --- | --- | --- | --- | --- |
| Frozen A/B behavior | `formal_ab_runner` and symmetric harness | Legacy scripted provider and Pydantic FunctionModel | 16 structured observations | No |
| Frozen grading integrity | evaluation foundation, formal runner, grade partition | Grader only and both Runtime observations | canonical dataset/grader validation | No |
| Durable continuity/options | Stage 03A durable acceptance | Pydantic Coordinator, Preview, FunctionModel, PostgreSQL | two-turn persistence, options and historical validation | No |
| Authorization/privacy | Stage 03A and symmetric harness | Pydantic direct; Legacy frozen where applicable | revocation, cross-trip denial, no model execution before denial | No |
| Trusted evidence/revisions | Pydantic Runtime and revision-bracketed Preview tests | Pydantic FunctionModel and Preview | grounding, candidate match, revision fencing | No |
| Idempotency/recovery/fencing | Stage 03B concurrency acceptance | Pydantic Coordinator where applicable; service boundary otherwise | B1–B5 durable state and physical PostgreSQL independence | No |
| Timeout/late completion | Stage 03B B6 and telemetry tests | Pydantic Coordinator/FunctionModel | typed events, safe failure, no late write | No |
| Legacy durable parity | shared Coordinator architecture | Shared application contract only | no direct Legacy durable execution claimed | No; do not invent symmetry |

The new versioned durable evaluation is therefore
`v0303-durable-runtime-acceptance-v1`: an explicitly scoped, traceable
composition of the already-executed 03A/03B acceptance node IDs, not a
duplicate implementation of those tests.

## Frozen A/B evaluation

Dataset `chat-change-preview-v1` has canonical-LF SHA-256
`e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`.
The formal runner confirms the frozen case order and experiment
`pr04d3-frozen-formal-ab-v1`; the actual runner output records source commit
`ae030a9` and grader version `chat-ab-grader-v1`.

Both adapters executed all eight cases and produced 8/8 passing safety grades.
Each has 6/8 passing business grades. The identical historical business gaps
remain visible and are not relabelled:

- `ambiguous-time`: runtime executes and safely degrades with
  `unexpected_exception` instead of producing the frozen clarification.
- `privacy-injection`: deterministic pre-runtime clarification is safe but does
  not match the frozen `reply_only` output class.

`ambiguous-item` and `cross-trip-denied` deliberately do not enter either
runtime; they are application-owned pre-runtime behavior, not missing runtime
execution. Tool trajectories differ on valid change cases: Legacy is an
authorized `superset`; Pydantic is an authorized `alternate_authoritative`
trajectory. Both preserve authoritative domain path, grounding, no durable
write, and privacy/scope safety.

## Durable evaluation results

`v0303-durable-runtime-acceptance-v1` passed through existing public seams:

| Area | Actual executed evidence | Result |
| --- | --- | --- |
| Continuity/options | Stage 03A module, including two-turn reconstruction and stale historical option rejection | PASS |
| Authorization/privacy | Stage 03A revocation-before, revocation-during, and cross-trip cases; symmetric cross-trip case | PASS |
| Revision/trusted evidence | Stage 03A revision fence plus Pydantic/Preview/Telemetry suite | PASS |
| Idempotency/recovery/fencing | Stage 03B B1–B5, including independent PostgreSQL B3 observer | PASS |
| Timeout/late completion | Stage 03B B6: `request_timeout`, same-execution typed events, `result_consumed=false`, bounded completion, no late persistence | PASS |

Direct Legacy durable Coordinator coverage was not run. Those guarantees are
accurately classified as **SHARED APPLICATION CONTRACT — DIRECT LEGACY COVERAGE
NOT EXECUTED**. This does not fabricate a Legacy result; the frozen A/B run is
the direct Legacy comparison where the architecture supports it.

## Telemetry and privacy

The 115-test Pydantic/Preview/Telemetry suite passed. It covers typed runtime
selection, model completion, tool dispatch outcomes (including cache-hit and
guard-rejection semantics), deadline and late-completion contracts, and privacy
validation. The B6 acceptance test additionally captures actual lifecycle event
factories and verifies `AgentDeadlineExceeded` and `AgentLateCompletion` for
the same execution identity. FunctionModel usage is explicitly local/fake;
Provider SDK usage, token usage, and cost are not claimed.

Evidence contains only case IDs, Runtime identities, closed outcome labels,
counts, and test results. It contains no raw prompts, private constraints,
credentials, URLs, database data, or Provider payloads.

## Regression evidence

| Command group | Result |
| --- | --- |
| Formal runner | 16 observations, exit 0 |
| Evaluation + 03A/03B acceptance | 50 passed, exit 0 |
| Pydantic/Preview/Telemetry | 115 passed, exit 0 |
| Full backend partition A | 1351 passed, 10 warnings, exit 0 |
| Full backend partition B | 5 passed, exit 0 |

The full manifest is non-overlapping: partition A excludes only
`test_upsert_demo_login.py`, `test_upsert_demo_seed.py`, and
`test_upsert_fixed_accounts.py`; partition B contains exactly those files.
Therefore the completed full regression is 1356 tests, not a single invocation
or a sum of overlapping suites.

An earlier single full invocation returned 1353 passed / 3 failed because the
three seed/upsert tests observed data left by earlier test files in that same
database session. Each passed as a fresh process, and the file-disjoint
partition manifest passed. This is recorded as a test-isolation/order finding,
not a Pydantic, Legacy, Durable, authorization, or migration regression.

## Final acceptance verdict

No migration-introduced P0/P1 safety regression was found. Frozen safety
checks, durable Pydantic acceptance, trusted evidence, idempotency/fencing,
timeout safety, telemetry/privacy, and the non-overlapping full backend
regression all pass. The two historical frozen business gaps are preserved.

This is local offline migration acceptance only. Remaining work before any
production activation includes separate real-provider quality/cost evaluation,
explicit production authorization, and normal protected-branch PR review.

No local commit, push, PR creation, merge, or Stage 03D work was performed.
