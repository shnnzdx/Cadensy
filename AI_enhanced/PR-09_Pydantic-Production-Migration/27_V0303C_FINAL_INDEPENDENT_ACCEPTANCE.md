# v0.3.0-03C — Final Independent Acceptance Review

Date: 2026-10-23

## Verdict

**BLOCKED — do not create the Stage 03C local commit.**

The frozen A/B evaluation, the recorded durable acceptance evidence, telemetry
and privacy evidence, and the two new Stage 03C artifacts are source-identical
and internally valid.  However, a fresh reproduction of the repository's
single-process full backend command deterministically fails three tests.  The
failure is a confirmed test-isolation defect introduced by the Stage 03A/03B
acceptance fixtures, and the repository's current CI still executes the
monolithic command.  Thus the validated file partition is not the
repository-supported CI execution strategy.  The requirement for a passing
complete backend gate is not met.

This is a test-infrastructure/reliability blocker, not evidence of a
production Pydantic Runtime, Legacy Runtime, authorization, fencing, timeout,
privacy, schema, or durable-persistence defect.  It nonetheless prevents a
truthful local Stage 03C acceptance and commit.

## 1. Reviewed Git source identity and changed-file inventory

| Item | Verified value |
| --- | --- |
| Worktree | `C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303c-pydantic-formal-evaluation` |
| Branch | `codex/v0303c-pydantic-formal-evaluation` |
| HEAD reviewed | `ae030a9b1f0c2e765a5c925b128fcfaafc40f7ae` |
| Parent / Stage 03A | `b1981d849e24e9c88a4ccf997c8831addbd4a48b` |
| Stage 02 ancestor | `8012dce80d5d8ecd30906192311d685ed1d9e13f` |
| Initial worktree state | two untracked Stage 03C artifacts only: `25_` and `26_` |
| Stage 03A/03B delta from Stage 02 | two acceptance-test files plus documents/evidence `13`–`24`; no `backend/app`, schema, dependency, configuration, CI/CD, or release-workflow change |
| Stage 03C source delta before this review | no tracked source change; reports/evidence only |

The final-review artifact `27_` is added by this review.  No production source,
schema, dependency, provider configuration, CI/CD file, credential, database
data, cache, generated test log, push, PR, merge, or deployment was created.

## 2. Frozen evaluation assessment and source integrity

`25_V0303C_FORMAL_EVALUATION_REPORT.md` and
`26_V0303C_FORMAL_EVALUATION_EVIDENCE.json` were read in full.  The JSON parses.
All eight canonical-LF SHA-256 values recorded in `26_` match the reviewed
source, including the frozen dataset, grader, formal runner, symmetric harness,
and listed Pydantic/Durable/telemetry tests.  Canonicalization is UTF-8 bytes
with CRLF/CR normalized to LF; it avoids a Windows checkout line-ending false
mismatch.

The independent local command below used a new disposable loopback-only
PostgreSQL database, test mode, `MOCK_AI=1`, scheduler disabled, membership
header override disabled, local deterministic FunctionModel, and no DeepSeek or
AWS credentials:

```text
python -m evals.formal_ab_runner --output <task-local temporary JSON>
exit 0
```

It recorded the reviewed HEAD, dataset SHA
`e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`,
grader `chat-ab-grader-v1`, and exactly the frozen eight case IDs for each
Runtime.  `formal_ab_runner` verifies the dataset version, canonical hash and
case order before execution.  The symmetric harness compiles execution inputs
only and creates observations before calling the grader; expected labels are not
used to synthesize observations.

| Runtime | Observations | Runtime executions | Safety | Business | Historical business gaps |
| --- | ---: | ---: | ---: | ---: | --- |
| Legacy | 8 | 5 | 8/8 PASS | 6/8 PASS | `ambiguous-time`, `privacy-injection` |
| Pydantic | 8 | 5 | 8/8 PASS | 6/8 PASS | `ambiguous-time`, `privacy-injection` |

The scores match, but this review does **not** infer a business-parity
improvement.  `ambiguous-time` remains a safe `unexpected_exception` degraded
outcome rather than the expected clarification; `privacy-injection` remains a
safe deterministic clarification rather than the frozen `reply_only` class.
Both were visible in the structured per-case outcomes.  Pre-runtime
`ambiguous-item` and `cross-trip-denied` execute neither Runtime as intended.
Valid change trajectories differ (`superset` Legacy versus
`alternate_authoritative` Pydantic) while retaining grounded authoritative
domain behavior and no durable side effect.

## 3. Durable, security, telemetry, and privacy assessment

The recorded 50-test composed evaluation is source-identical and supports the
following non-duplicated claims:

| Guarantee | Executed source/evidence | Assessment |
| --- | --- | --- |
| Conversation continuity and historical Option staleness | Stage 03A A1/A2 | PASS |
| Membership revocation and cross-trip denial | Stage 03A A3/A4/A5 | PASS |
| Plan/Facts revision fencing | Stage 03A A6 and revision-bracketed Preview tests | PASS |
| Same-key idempotency and different-key concurrency | Stage 03B B1/B2 | PASS |
| Lease recovery ownership and stale completion/failure fencing | Stage 03B B3/B4/B5 | PASS |
| Timeout, late-result rejection, `result_consumed=false` | Stage 03B B6 and telemetry tests | PASS |
| Trusted Tool Evidence and safe Preview classification | Pydantic Runtime/Preview evidence | PASS |

This evidence is direct Pydantic Coordinator/FunctionModel execution where
claimed.  Direct Legacy durable execution was not run and remains correctly
classified as a shared application contract, not fabricated Legacy coverage.

The 115-test Pydantic/Preview/Telemetry result in `26_` remains source-matched.
Its scope includes actual Runtime selection, model/tool classification,
authorized scoped reads, cache/guard outcomes, Preview behavior, timeout and
late-completion facts, and privacy validation.  Evidence review found no raw
prompts, private constraints, authentication material, database URLs, provider
payloads, token-usage claims, or provider-cost claims.  A pattern scan of
`25_`/`26_` found no credential or connection-string value.

## 4. Full backend regression reconciliation

The recorded partitioned manifest is internally consistent:

| Command set | Files owned | Collected / result | Exit |
| --- | --- | ---: | ---: |
| Partition A | every backend test except the three upsert modules | 1351 passed, 10 warnings | 0 |
| Partition B | exactly `test_upsert_demo_login.py`, `test_upsert_demo_seed.py`, and `test_upsert_fixed_accounts.py` | 5 passed | 0 |
| File-disjoint total | all 1356 collected tests, no duplicate ownership | 1356 passed; 0 skipped, xfailed, or deselected | 0 |

The recorded commands and test counts reconcile to `pytest --collect-only -q`
at the reviewed source (1356 collected).  Pydantic dependencies were available
in the tested interpreter (`pydantic-ai 2.54.0`).  The evidence hashes for the
in-scope tests match current source.

This makes the following statement true but deliberately limited:

```text
PARTITIONED BACKEND REGRESSION: PASS
```

It does not make the repository's normal one-process test gate pass.  Both
`.github/workflows/build-validation.yml` and
`.github/workflows/database-readiness.yml` contain monolithic backend pytest
steps; the latter explicitly runs `python -m pytest -q`.  No CI partition
implements the validated A/B file split.  Therefore the repository-supported
CI strategy is inconsistent with the partition-only result.

## 5. Reproduced single-process failures and root-cause dispositions

Independent reproduction used a second new task-owned disposable database and
the exact command below; no source changed between this run and the reviewed
HEAD:

```text
python -m pytest -q
exit 1: 1353 passed, 3 failed, 10 warnings in 72.87s
```

All three failures have the same **CONFIRMED TEST ISOLATION ISSUE** disposition:

| Node ID | Exact final assertion | Fresh-process / partition behavior | Disposition |
| --- | --- | --- | --- |
| `tests/test_upsert_demo_login.py::test_upsert_demo_login_keeps_legacy_account_without_recreating_demo_trip` | expected no `Trip`; found a persisted trip ID | target module participates in the 5-pass fresh Partition B; the bounded fresh `purge_demo_data` + three-upsert-module order passed 7/7 | CONFIRMED TEST ISOLATION ISSUE |
| `tests/test_upsert_demo_seed.py::test_upsert_demo_seed_is_idempotent` | expected one trip; observed 17 | same evidence | CONFIRMED TEST ISOLATION ISSUE |
| `tests/test_upsert_fixed_accounts.py::test_upsert_fixed_accounts_is_idempotent` | expected two users; observed 34 | same evidence | CONFIRMED TEST ISOLATION ISSUE |

The failure is deterministic in the exact full-suite order, rather than an
unexplained timing symptom.  After the failures' own fixture rollback, direct
database inspection contained 16 trips and 32 users, all with `Stage 03 trip`,
`stage03-owner-*`, and `stage03-other-*` identities.  Those are generated by
`_seed_committed_trip` in
`backend/tests/test_pydantic_durable_runtime_acceptance.py` (line 129) and
reused by the Stage 03B concurrency module.  It creates a normal session
factory from the session-scoped `test_engine` and commits setup rows via
`with factory.begin()` for real independent-worker testing.  These rows bypass
the ordinary `db` fixture's outer rollback.  The source has no autouse cleanup
for those committed rows.

This finding is supported by, not merely inferred from, the following checks:

- Fresh bounded sequences `test_purge_demo_data.py` followed by all three
  upsert modules: 7 passed, exit 0.
- Fresh bounded sequence `test_trips.py` followed by all three upsert modules:
  55 passed, exit 0.
- A fresh exact one-process complete suite: the same three assertions fail and
  the persisted Stage 03 fixture signature/count is observed.
- The Stage 03A/03B tests and their committed fixture rows predate Stage 03C;
  Stage 03C itself changes no executable source.

The affected test helpers assert global-table cardinalities, so retained
unrelated committed rows make them fail.  This does not alter production
runtime behavior and no authorization, fencing, timeout, telemetry, privacy,
or durable persistence security assertion failed.  It is nevertheless a
migration-test-induced test-infrastructure reliability defect: the existing
monolithic CI command is expected to reproduce it.  Its remedy must be a
separate scoped test-isolation correction (fixture cleanup or compatible test
ownership/CI design), followed by a successful single-process CI-equivalent
run.  No such change is authorized or made by this final review.

## 6. Remaining risks, commit status, and final decision

- The two frozen business gaps remain historical and explicitly unhealed.
- Real-provider quality, latency, and cost are outside this offline evaluation.
- Direct Legacy durable execution remains untested; only shared application
  contract coverage is claimed.
- The reproducible monolithic test-isolation failure is a release-quality
  blocker until repaired and revalidated under the actual CI command.

No production application code changed.  No P0/P1 production migration
regression was observed, but the P1-level CI/test-isolation reliability defect
means Stage 03C cannot be accepted or committed under this gate.

`STAGE 03C FINAL INDEPENDENT ACCEPTANCE: BLOCKED`

`LOCAL COMMIT: NOT PERFORMED`
