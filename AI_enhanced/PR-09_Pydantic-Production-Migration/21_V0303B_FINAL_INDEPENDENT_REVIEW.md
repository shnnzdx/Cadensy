# v0.3.0-03B — Final Independent Review

## Verdict

**BLOCKED.** The reviewed scope is test/evidence-only and no production defect was found, but two P1 evidence gaps prevent the required gated local commit. No source assertions or production code were changed during this review.

## Reviewed identity and scope

- Worktree: `C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing`
- Branch / HEAD: `codex/v0303b-pydantic-concurrency-fencing` / `b1981d849e24e9c88a4ccf997c8831addbd4a48b`
- Accepted parent: `8012dce80d5d8ecd30906192311d685ed1d9e13f`
- The Stage 03A worktree was independently clean.
- Current pre-commit scope is limited to the allowed Stage 03B test plus reports `19`, `20`, and this review report. No tracked production, schema, dependency, configuration, selector, workflow, Legacy, or Stage 03A source changed.
- `20_V0303B_CONCURRENCY_FENCING_EVIDENCE.json` parsed successfully. Its listed SHA-256 values match the reviewed files, including `backend/tests/test_pydantic_concurrency_fencing_acceptance.py` at `3b7918eebc17a6c53c1d40eafad5488068a62d9738e81a8783b00bbef18e5f8d`.

## Independent execution

A new task-owned PostgreSQL 18 cluster was initialized on loopback only, with a distinct externally configured disposable runtime database and test database. Pytest's existing `tests/conftest.py` deliberately redirects imported application runtime to the validated disposable test database; no production database was reachable.

| Command | Result | Exit |
| --- | --- | ---: |
| `python -m pytest tests/test_pydantic_concurrency_fencing_acceptance.py -q -s` | 7 passed | 0 |
| `python -m pytest tests/test_pydantic_concurrency_fencing_acceptance.py::test_stale_pydantic_completion_cannot_overwrite_recovery tests/test_pydantic_concurrency_fencing_acceptance.py::test_stale_pydantic_failure_cannot_overwrite_recovery tests/test_pydantic_concurrency_fencing_acceptance.py::test_pydantic_request_timeout_discards_late_function_model_result -q -s` | 3 passed | 0 |

The review database had actual distinct PostgreSQL backend PID attribution for B2, B3 claimants, B4, and B5. All barriers had bounded waits; B1/B2/B4/B5 captured worker exceptions and released/joined their explicit threads in `finally` blocks.

## Scenario coverage matrix

| Test | FunctionModel | Preview | Coordinator | PostgreSQL | Independent connections | Durable outcome | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 same-key replay | Yes | Yes | Yes | Yes | Not claimed | One authoritative completion; no HTTP dispatch coverage | Pass, scoped |
| B2 different-key busy | Yes | Yes | Yes | Yes | Yes, traced PID | `ConversationBusy`; one completion | Pass |
| B3 expired same-key / recovery-key | No, service-only | No | No | Yes | Not required for serial path | `(1,1) -> (2,2) -> (3,3)`; replay path covered | Pass, service-only |
| B3 concurrent recovery claims | No, service-only | No | No | Yes | Two claimant PIDs | One winner object / one `ConversationBusy` | **P1 gap** |
| B4 stale completion | Yes | Yes | Yes | Yes | Yes, traced before/mutator/after/observer | Old completion discarded; current `(2,2)` completes | Pass |
| B5 stale failure | Yes | Yes | Yes | Yes | Yes, traced before/mutator/after/observer | Old failure discarded; current `(2,2)` completes | Pass |
| B6 timeout / late result | Yes | Yes | Yes | Yes | Not a connection race | No durable assistant/options observed | **P1 gap** |

B4 is a real durable-completion fence: after its blocked FunctionModel returns, `ConversationRuntimeCoordinator.execute()` reaches its completion phase and calls `service.complete_submission()` ([runtime.py](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\backend\app\domain\conversations\runtime.py:220)); the newer recovery causes existing service fencing to reject the old attempt. B5 separately drives the old FunctionModel exception to `_fail_or_discard()` / `fail_submission()` ([runtime.py](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\backend\app\domain\conversations\runtime.py:240)). These are not merely fabricated-fence service calls.

## Findings

### P1 — B3 concurrent recovery does not independently observe committed durable state

The acceptance requirement requires each claimed race to prove the committed recovery is visible in a fresh observer transaction, including stored Attempt/Fence, recovery ownership, and final rows. The claimant test asserts only in-memory result objects and two claimant PID values ([test](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\backend\tests\test_pydantic_concurrency_fencing_acceptance.py:234)); after the race it checks message counts and plan revision, but never independently reads `ConversationSubmission` or `ConversationSubmissionRecovery` ([test](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\backend\tests\test_pydantic_concurrency_fencing_acceptance.py:271)).

The JSON consequently overstates persisted recovery evidence ([evidence](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\AI_enhanced\PR-09_Pydantic-Production-Migration\20_V0303B_CONCURRENCY_FENCING_EVIDENCE.json:90)). Existing PR-06B.2 coverage validates the service contract generally, but does not replace a direct persisted-observer assertion for this Stage 03B race.

Minimal correction: add a new `trace.phase("observer")` transaction after both claimants join, assert the stored Submission is `(attempt_number=2, fence=2, state="running")`, assert exactly one recovery row with the winning accepted attempt/fence and running state, assert no duplicate messages/options, and record/assert a distinct observer PID.

### P1 — B6 does not prove the claimed late-result discard and cleanup in its Coordinator scenario

The test releases the FunctionModel and waits only for an event set immediately before `ModelResponse` is returned ([test](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\backend\tests\test_pydantic_concurrency_fencing_acceptance.py:57), [test](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\backend\tests\test_pydantic_concurrency_fencing_acceptance.py:381)). It allows either `failed` or `discarded`, does not assert `request_timeout`, does not wait for/inspect the public lifecycle event that establishes `result_consumed=false`, and does not establish complete late worker cleanup before its durable assertions.

Therefore report line 36 and JSON fields `deadline_scope`, `runtime_failure_category`, and `late_result_consumed` are broader than this test proves ([report](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\AI_enhanced\PR-09_Pydantic-Production-Migration\19_V0303B_CONCURRENCY_FENCING_ACCEPTANCE.md:36), [evidence](C:\Users\zdxzh\Desktop\capstone\Cadensy-v0303b-pydantic-concurrency-fencing\AI_enhanced\PR-09_Pydantic-Production-Migration\20_V0303B_CONCURRENCY_FENCING_EVIDENCE.json:139)). The review independently observed telemetry consistent with the claim, but that observation is not asserted by the acceptance test and is insufficient to make the source evidence self-proving.

Minimal correction: inject a test-only lifecycle recorder through the existing public execution seam; assert the Coordinator result is `failed` with `failure_category == "request_timeout"`; after release, wait for the terminal `late_completion` event carrying `result_consumed=False`; and wait for the worker-complete synchronization point before the final independent database read. Keep the existing 50 ms deadline isolated and bounded; do not claim physical thread termination.

## Replay, lease, fencing, and isolation observations

- B1 correctly distinguishes service admission plus direct Coordinator dispatch from HTTP orchestration. Same-key active/completed replay is non-claiming, canonical mismatch is `IdempotencyConflict`, and exactly one user/assistant with zero options is asserted.
- B2 uses a real blocked Pydantic FunctionModel and an independent traced competing PostgreSQL transaction. The first running slot remains authoritative and the second key gets `ConversationBusy`.
- B3 correctly exercises active same-key replay, expiry-based same-key reclaim, recovery-key grant, and same recovery-key replay. It does not separately call `recover_submission()` before expiry; pre-expiry refusal is verified by existing service regression rather than this module. This is a P2 coverage attribution limitation, not a product finding.
- B4/B5 use a committed independent recovery before the old FunctionModel resumes. The observer verifies final `(2,2,"completed")`, one user, one assistant, zero options, and unchanged Plan revision.

## Standards review

No documented repository-standard breach or P0/P1 standards finding was identified. Two non-blocking P2 observations remain:

- The new module imports private underscored helpers from the Stage 03A test module. The Stage 03B scope explicitly asks for fixture reuse, so this is acceptable now, but it couples future fixture maintenance.
- Report line 55 could explain that pytest intentionally maps both environment names to the validated disposable test database in `tests/conftest.py`; it should not be read as an application/uvicorn configuration change.

## Evidence, security, and scope conclusion

No credentials, database URLs, private prompts, provider payloads, or production data were added to reviewed artifacts. No provider, AWS, production, schema, protected Apply, Legacy, or selector operation occurred. The temporary review PostgreSQL cluster must be stopped after this review; its task-owned directory is retained.

Because the two P1 findings leave the final evidence internally incomplete, Stage 03B cannot receive the required PASS or local commit. The minimal corrections above are test/evidence work only; they require a new review and updated hash-backed evidence before a commit can be considered.
