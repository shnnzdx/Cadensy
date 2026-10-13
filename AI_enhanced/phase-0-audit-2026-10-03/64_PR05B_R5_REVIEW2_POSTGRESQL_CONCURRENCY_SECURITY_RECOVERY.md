# Cadensy R5 — Review 2 of 3: PostgreSQL Concurrency, Security & Failure Recovery

**Review date:** 2026-10-13<br>
**Worktree:** `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`<br>
**Branch:** `codex/pr05b1-schema-foundation`<br>
**Scope:** independent PostgreSQL/security/failure-recovery review. No production code, schema, feature gate, PR-04 artifact, commit, or deployment was changed.

## 1. Executive Summary and Verdict

### REVIEW 2 — PASS

The R5 protected paths have adequate current source and real disposable-PostgreSQL evidence for the scoped concurrency, authorization, durable replay, privacy, and rollback invariants. No demonstrated P0/P1 safety defect remains. The review executed **505 focused tests** in safe local PostgreSQL/isolated-runtime environments: 181 concurrency/security/kernel tests, 246 protected-writer/generator/auth/tool tests, and 78 isolated Pydantic/runtime tests.

The result does **not** activate production. It authorizes only Review 3, whose job is the complete final regression/acceptance run.

## 2. Review 1 PASS Evidence and Starting Baseline

Report 63 records `REVIEW 1 — PASS` at lines 10 and 245-251. It records no Review 1 code correction (lines 218-220), and retains only two nonblocking P3 observations:

- process/fleet gate homogeneity and operational retention/key-rotation/runbook evidence before production (`63:215,295`);
- Preview remains intentionally advisory/no persisted token; Apply remains independently authoritative (`63:216,296`).

The current worktree was already dirty from accepted B.1-B.3 work. Review 2 made no source modification. Current source used here is the latest accumulated implementation, including the accepted B.3 closure paths in `preview_apply.py`, `chat/service.py`, `api/main.py`, runtime adapters, transaction modules, and their test suites.

## 3. Frozen Protocol and Source Inventory

| Source | Relevant frozen/current rule |
| --- | --- |
| Report 43:499-517 | Final design baseline; later phase evidence is required to move from initial conditional acceptance. |
| Report 49:26,35,39-49,69-71,147,159-174,220-238,270,324 | Trip-first locking; ledger identity; revision ownership; no-op; HMAC; scope; no provider work under root lock; default-off admission. |
| Reports 50-53 | Trip schema/kernel/durable/revision writer acceptance. |
| Reports 54-59 | Generator identity, detached work, validate/commit and protected HTTP boundaries. |
| Report 61:543-588 | Final B.2E cross-writer/profile closure. |
| Report 62:421-446 | Final B.3 closure evidence: 10 B.3, 3 cleanup, 77 relevant, 989 Legacy, and 119 Pydantic tests; B.3 PASS. |
| Report 63 | Review 1 PASS and explicit Review 2 threat focus. |
| `transactions/kernel.py:172-418`; `trip_kernel.py:97-315` | Kernel Session ownership, Trip-first root locks, fresh credential/membership authorization and typed DB failures. |
| `transactions/durable.py:249-606`; `trip_durable.py:79-193` | Locked ledger/replay, HMAC/outcome verification, revision/outcome atomicity. |
| `preview_apply.py:188-492`; `api/main.py:369-410,1153-1242,1883-2016` | B.3 read brackets, HTTP lifecycle, complete-profile gate and protected Apply adapter. |

## 4. Threat Model and Review Scope

The review evaluated repository-reachable behavior for Account, Guest, organizer, removed/revoked actors, cross-Trip credential/resource attempts, concurrent human writers, Scheduler, malformed AI/runtime output, stale Preview/Apply, replay/lost-response behavior, and transaction failure.

It does not claim to simulate every operating-system crash or a distributed deployment. It tests the durable outcome model and local PostgreSQL behavior that the accepted R5 scope actually exposes.

## 5. PostgreSQL Locking and Isolation Audit

### Lock order and ownership

Interactive Plan commands enter `PlanTransactionKernel`, which creates and owns a fresh Session/transaction (`transactions/kernel.py:172-247`). It locks, in order, `Trip FOR UPDATE` (`260-265`), current credential and membership (`269-274`, `312-397`), optional all memberships before Plan (`276-287`), all candidate Plans (`289-301`), then the locked durable ledger and scoped children in the writer adapters. The Trip kernel follows the equivalent root-first order (`transactions/trip_kernel.py:97-133,182-297`). Neither receives a request-owned Session.

Scheduler has server-owned authority but still enters a Trip-root locked path; its secondary row ordering is safe for the reviewed writers because each competing writer first serializes on the same Trip root. Evidence below includes actual Scheduler-vs-Booking root contention, not merely a source inference.

### Preview isolation

`RevisionBracketedReadTripCapability._read_session()` and `RevisionBracketedPreview._bounded_read()` each begin a short transaction and execute `SET TRANSACTION READ ONLY` on PostgreSQL (`preview_apply.py:230-242,459-468`). PostgreSQL's normal `READ COMMITTED` isolation is deliberately **not** represented as `REPEATABLE READ`.

This is correct for the frozen protocol: it does not promise one long snapshot. It captures monotonically advancing Plan/Trip revisions, verifies those revisions before every bounded capability/classification/options read, and independently verifies them after all runtime/classification work (`preview_apply.py:244-270,329-357,378-400,402-457,471-492`). A protected relevant writer commits a revision advance; a subsequent statement/final bracket rejects rather than emits a current-looking stale verdict. The review found no required invariant that needs a long `REPEATABLE READ` transaction, which would instead conflict with the bounded/no-provider-latency design.

## 6. Plan/Trip Revision Correctness

| Mutation family | Source revision behavior | Dynamic evidence | Result |
| --- | --- | --- | --- |
| Plan/decision writer | Plan durable command validates expected Plan revision then advances exactly on `state_changed`. | `test_durable_idempotency_kernel.py:284-371,730-984`; protected B.2C suite. | Confirmed. |
| Preferences/constraints, membership | Trip durable validates expected facts; one Plan receives atomic companion revision where required. | `test_trip_durable_command_kernel.py:177-447`; `test_b2e_unified_writers.py:227-241`. | Confirmed. |
| Generator | Detached draft is revalidated against Trip/Plan facts at commit. | `test_generator_commit.py:587-930,1510-1661`. | Confirmed. |
| Scheduler/organizer | Actual terminal transition advances Plan once; later stale/illegal command is rejected or valid no-op. | `test_b2e_unified_writers.py:256-268,678-915`. | Confirmed. |
| No-op/replay | No second revision; safe completed outcome can replay. | `test_durable_idempotency_kernel.py:305-324,694-730`; `test_trip_durable_command_kernel.py:221-306`. | Confirmed. |
| zero/multiple Plan / overflow | Controlled failure; no arbitrary Plan or partial state. | `test_trip_transaction_kernel.py:183-274`; `test_durable_idempotency_kernel.py:371-395`; `test_trip_durable_command_kernel.py:324-344`. | Confirmed. |

The Review 2 execution included these tests in the 181-test kernel/concurrency run. Separate Trips use separate Trip root rows; no global lock or mutation route was found.

## 7. B.3 Preview Read Consistency

Preview captures exactly one Plan and both revisions (`preview_apply.py:329-357`). The capability's descriptor construction Session is closed before Runtime/provider execution, and every invoked tool creates a separate bounded fresh Session (`188-228,230-242`). Selected candidate and options are Plan-scoped and remove unsafe/removed candidates before output (`359-453`); `_verify_final()` independently re-checks current revisions (`455-492`).

Executed evidence:

- `test_preview_uses_detached_fresh_reads_and_returns_a_non_authoritative_candidate` and `test_preview_closes_template_and_per_tool_sessions_before_runtime_returns`, lines 231-283;
- `test_preview_discards_candidate_when_revision_changes_during_runtime`, lines 284-298;
- `test_preview_holds_no_trip_root_lock_during_runtime_and_discards_after_real_writer`, lines 300-349;
- foreign/removed option tests at lines 519-579.

The blocking-runtime test invokes a real tool read, pauses outside a database transaction, proves another connection obtains `Trip FOR UPDATE NOWAIT`, commits a real protected Change Proposal writer, then observes `StalePreview`. It is direct evidence for no root lock during runtime latency and stale rejection after real writer progress.

## 8. B.3 Preview/Apply TOCTOU

| Scenario | Source / test evidence | Result |
| --- | --- | --- |
| Writer between Preview bounded reads / during runtime | `preview_apply.py:244-270,319,455-492`; `test_b3_revision_bracketed_preview.py:300-349`. | Real protected writer commits while Preview is paused; Preview rejects. |
| Writer during classification | `_assert_captured_current` precedes classification (`378-400`) and final verification follows; test lines 284-298 advances revision after tool work. | Rejects stale input before a candidate is returned. |
| Writer after successful Preview | HTTP Preview then real writer then old-revision Apply at `test_b3_revision_bracketed_preview.py:581-644`. | Apply is HTTP 409; no stale mutation. |
| Guest revoke/removal before Apply | Fresh locked reauthorization at `kernel.py:312-397`; HTTP Guest replay/revocation at `test_protected_change_proposal_writer.py:522-549`; kernel revocation races at `test_transaction_kernel.py:449-558`. | Denied before callback/effect. |
| Booking/scheduler/organizer lifecycle after Preview | Apply uses the same locked Change Proposal writer (`main.py:1942-2016`; `change_proposal.py:92-239`) and thus rechecks current Plan state. Real scheduler/booking/organizer races are in `test_b2e_unified_writers.py:678-915`. | Older Preview cannot bypass current-domain checks. |
| Concurrent Apply, same/different key | Plan durable concurrency tests `test_durable_idempotency_kernel.py:462-567`; Change Proposal root contention `test_protected_change_proposal_writer.py:446-521`. | One effect/outcome; replay or safe stale/key conflict. |
| Forged Preview patch/revision/foreign item/options | Apply ignores Preview authority and rebuilds command; item scope/child lock at `change_proposal.py:92-239`; foreign test `test_protected_change_proposal_writer.py:355-416`; B.3 foreign option test lines 519-579. | Safe scope/error; no second state machine. |

No persisted Preview token is required by the frozen design. The relevant security property is fresh protected Apply authentication, revision validation, Plan-scoped row lookup, and deterministic reclassification—not trust in a client-held preview artifact.

## 9. Durable Ledger and HMAC Security

Plan durable execution locks the record inside the owned transaction (`durable.py:291-415`), rejects mismatched/stale new commands, stages revision/domain/outcome together, and only emits replay via `_replay()` after current authorization and HMAC/outcome checks (`415-606`). Trip durable has the same locked replay discipline (`trip_durable.py:85-193`).

The executed suite verifies: canonical equivalent forms vs semantic changes, operation/resource/body/revision key reuse conflicts, actor authorization before replay, HMAC rotation, unknown key/version failure, outcome contract validation, expiry, and unsafe result rejection (`test_durable_idempotency_kernel.py:330-1075`; `test_trip_durable_command_kernel.py:241-530`). Safe output validators reject transaction/credential state, binary/cyclic/unsupported values and disallowed fields (`durable.py:520-606`). No raw bearer, Guest credential, constraint text, SQL traceback, or agent trace becomes a durable public outcome through these paths.

## 10. Same-Key Concurrent Command Execution

`test_concurrent_identical_commands_execute_once_then_replay` and `test_concurrent_same_key_different_payload_waits_then_conflicts` (`test_durable_idempotency_kernel.py:462-567`) use independent connections and real row locking. The observed outcomes are one mutation plus one replay for the identical command, and one mutation plus a controlled conflict for changed payload. `test_concurrent_trip_commands_have_real_root_lock_contention_and_one_stale_result` (`test_trip_durable_command_kernel.py:536-595`) records distinct backend PIDs and checks `pg_blocking_pids()` before release.

This establishes both ledger uniqueness and the required serialized re-evaluation rather than assuming Python mutex behavior.

## 11. Historical Replay and Key Rotation

Historical replay first requires current actor authorization; revocation/removal does not reveal a stored outcome (`test_durable_idempotency_kernel.py:568-607`; `test_trip_durable_command_kernel.py:383-421`). It then verifies stored HMAC using a retained known signing key and rejects an unknown version (`durable.py:415-431`; `trip_durable.py:158-193`; test lines 643-665 and 345-421). Historical and current safe response policy are both enforced (`test_durable_idempotency_kernel.py:806-905`; `test_trip_durable_command_kernel.py:345-383`). Expired retained identity fails closed without re-execution (`durable test:984-1010`).

An ambiguous client response is consequently recoverable by retrying the same authorized command identity: a committed command is replayable; a pre-commit injected failure leaves neither business effect nor completed ledger (next section). The review does not claim a network partition magically rolls back a server commit.

## 12. Account/Guest Authorization

`PlanTransactionKernel._reauthorize_actor()` consumes a preliminarily verified credential kind rather than reclassifying bearer strings (`kernel.py:312-328`). Account and Guest rows are locked and matched to current scope, membership and revocation/expiry state (`330-397`). Account-first token-prefix compatibility is tested at `test_transaction_kernel.py:233-271` and Trip-kernel equivalent at `test_trip_transaction_kernel.py:322-328`.

Current authorization is tested before replay, after preliminary identity and under existing Logout-style credential-only locks (`test_transaction_kernel.py:379-465,465-558,592-645`; `test_trip_transaction_kernel.py:573-688`). Ordinary member/organizer role and cross-Trip resource behavior are also exercised by the protected writer suites and `test_auth.py` (part of the 246-test run). Forged request trip/membership headers never substitute for the locked token/membership binding in the protected kernel.

## 13. Cross-Trip Resource Isolation

All protected child lookup must be constrained to the locked authoritative Plan. Change Proposal does so before child locking (`transactions/change_proposal.py:92-183,205-239`). `test_change_foreign_item_is_not_locked_or_recorded` (`test_protected_change_proposal_writer.py:355-416`) asserts a foreign PlanItem is not locked, no foreign Plan revision changes, and no ledger outcome is created. B.3 options also omit foreign and removed items (`test_b3_revision_bracketed_preview.py:479-579`). Trip kernel guest mismatch tests at `test_trip_transaction_kernel.py:416-443` reject before Plan locking.

No cross-Trip mutation or replay disclosure was reproduced.

## 14. AI Agent Tool Authority and Privacy

PR-04 composition remains read-only: B.3's capability supplies only `build_read_only_trip_tools` with fresh read Sessions (`preview_apply.py:188-228`), while `chat_with_trip` explicitly documents and implements no `propose_change()` call (`main.py:1153-1164`). The candidate must be reclassified by deterministic orchestration inside the protected writer; model output never supplies an authoritative identity/revision token.

Runtime/tool/privacy tests (`test_agents_tools.py`, `test_chat_safety_and_time.py`, `test_runtime_contract.py`) passed in the 246-test regression. Isolated Pydantic/runtime tests also passed (78 tests); Pydantic's unsupported replacement path remains fail-closed, not silently promoted. Candidate options are current-Plan-scoped, ordinary safe partial edits remain allowed, replacements require trusted tool provenance, and private classification inputs are not serialized as Preview/ledger/error data.

## 15. Candidate Provenance and Preview Scope

`_replacement_provenance_allows()` is applied to selected candidates and nonempty options before output (`preview_apply.py:367-377,427-451`). The final accepted full replacement rule is exercised at `test_b3_revision_bracketed_preview.py:540-556`. `_safe_options()` loads only non-removed PlanItems from the captured Plan and drops malformed/foreign/unclassifiable alternatives (`402-453`), while `test_b3_http_omits_foreign_and_removed_candidate_options` provides HTTP-level evidence (`557-579`). Apply independently scopes the selected item and ignores any fabricated client classification.

## 16. Fault Injection and Atomic Rollback

| Fault point | Dynamic evidence | Observed authoritative state |
| --- | --- | --- |
| callback / child mutation / false no-op | `test_durable_idempotency_kernel.py:622-730`; `test_trip_durable_command_kernel.py:291-344` | staged effect/revision/ledger rolled back; unsafe no-op cannot commit mutation. |
| after revision or ledger staging | `test_durable_idempotency_kernel.py:956-1010`; `test_trip_durable_command_kernel.py:422-447` | no completed ledger or phantom revision after injected failure. |
| Change Proposal checkpoint | `test_protected_change_proposal_writer.py:335-354` | effects, revision and ledger all roll back. |
| membership/scheduler/organizer final commit | `test_b2e_unified_writers.py:377-502` | no half-join/revocation/terminal action or ledger. |
| generator child/final flush | `test_generator_commit.py:1313-1509` | original Plan/children/ledger restored. |

The durable kernel classifies lock/deadlock/serialization errors into safe retryable typed failures (`kernel.py:59-67,412-418`), and protected routes return non-diagnostic 503 responses (`main.py:2008-2015` and parallel adapters).

## 17. Ambiguous Commit and Recovery

The tested guarantee is precise: injected failures before final commit leave no completed ledger/effect; a committed command is discoverable by the locked historical ledger identity and replayed without a second mutation. The implementation does not claim that a client network failure proves rollback. This is the correct durable-command recovery model, confirmed by the same-key/replay and final-commit-fault tests above.

No synthetic network disconnect after PostgreSQL has acknowledged `COMMIT` was introduced: it would add transport-specific test machinery without changing the durable source invariant. This is a bounded evidence limitation, not a demonstrated recovery defect.

## 18. Scheduler and Organizer Concurrency

The executed B.2E suite uses actual writers, distinct backend PIDs, Events/Queues, bounded joins, and `pg_blocking_pids()` (`test_b2e_unified_writers.py:103-223,292-376`). It further covers:

- Scheduler settlement vs Booking: `678-759` — Booking blocks, then becomes stale; one terminal state/revision.
- Expiry vs Proposal Decision: `761-791` — no mixed transition.
- Organizer extension vs settlement: `793-815` — rechecks deadline/open state.
- Escalation vs expiry: `817-842` and real overlap `894-903` — terminal state remains coherent.
- Deadlock resolution vs Booking: `844-865` and real overlap `906-915` — no overwrite.
- Same due entity retry and organizer ledger rollback: `256-268,427-523`.

The Scheduler does not fabricate a human actor and no duplicate `PlanChange`, notice, terminal transition, or false revision was observed in these writer-to-writer tests.

## 19. Protected-only Runtime Gate Security

The B.3 gate is default-off, rejects malformed values/missing/unknown environments, rejects production, and constructs an effective complete B.2C/B.2D/B.2E profile (`api/main.py:369-410`). `protected_profile.py:47-65` defines the required writer gate set. Once B.3 chooses protected Preview/Apply, errors are returned as controlled unavailable/conflict/auth responses—there is no protected-to-Legacy fallback (`main.py:1166-1211,1894-2016`).

`test_b3_protected_profile.py` (14 tests, included in the 181-test run) covers mixed profile rejection, every missing writer family, non-production acceptance, production rejection, missing/unknown environment, and invalid values. Scheduler's protected gate is part of the required profile. A heterogeneous future fleet remains a deployment prerequisite, not a locally reproducible bypass.

## 20. Exact PostgreSQL Test Evidence

Database preconditions were verified immediately before execution:

- PostgreSQL: `localhost:5432 - accepting connections`.
- `TEST_DATABASE_URL`: explicit disposable `postgresql+psycopg://…@localhost:5432/tripsync_review2_test`.
- `DATABASE_URL`: distinct local runtime sentinel `…/tripsync_review2_runtime_sentinel`.
- `DISABLE_SCHEDULER=1`, `MOCK_AI=1`; no AWS/RDS/Supabase/provider access.

The first attempt intentionally used a passwordless URL and failed during PostgreSQL schema setup with `fe_sendauth: no password supplied`; it executed no application assertion and is not presented as evidence. The rerun used the local test-only credential specified in `backend/LOCAL_DEV.md` and produced the following complete outcomes:

```powershell
# local PostgreSQL, explicit test-only URL, regression interpreter
python -m pytest -q `
  tests/test_b3_revision_bracketed_preview.py `
  tests/test_b2e_unified_writers.py `
  tests/test_durable_idempotency_kernel.py `
  tests/test_trip_durable_command_kernel.py `
  tests/test_transaction_kernel.py `
  tests/test_trip_transaction_kernel.py `
  tests/test_protected_change_proposal_writer.py `
  --junitxml=../r5-review2-postgres-security.xml
# 181 passed in 4.71s
```

This set includes independent connections/PIDs and `pg_blocking_pids()` assertions; see the cited tests in §§5-18.

## 21. Focused Regression Results

```powershell
# same verified disposable local PostgreSQL setup, legacy regression interpreter
python -m pytest -q `
  tests/test_protected_manual_plan_item.py tests/test_protected_booking_writer.py `
  tests/test_protected_vote_writer.py tests/test_protected_proposal_decision_writer.py `
  tests/test_protected_notice_objection_writer.py tests/test_protected_round_settlement_writer.py `
  tests/test_generator_commit.py tests/test_generator_durable_coordinator.py `
  tests/test_generator_protected_http.py tests/test_auth.py tests/test_agents_tools.py `
  tests/test_chat_safety_and_time.py tests/test_runtime_contract.py `
  --junitxml=../r5-review2-focused-regression.xml
# 246 passed in 7.29s

# isolated Pydantic interpreter, same disposable setup
python -m pytest -q tests/test_pydantic_runtime.py tests/test_runtime_composition.py `
  tests/test_runtime_contract.py tests/test_agents_tools.py `
  --junitxml=../r5-review2-pydantic-runtime.xml
# 78 passed in 2.50s
```

`py_compile` ran successfully for the reviewed Preview, API, transaction, auth and runtime modules. `git diff --check` passed. Review 3, not this review, owns the next full Legacy and isolated-Pydantic CI-equivalent runs.

## 22. Confirmed Findings P0–P3

| ID | Severity | Source evidence | Reproduction | Minimum fix | Review 3 blocker |
| --- | --- | --- | --- | --- | --- |
| None | P0 | No authorization bypass, cross-Trip write/disclosure, privacy leak or irrecoverable integrity corruption reproduced. | N/A | N/A | No |
| None | P1 | No stale Apply commit, root-lock bypass, partial durable completion or unsafe gate fallback reproduced. | N/A | N/A | No |
| R5-R2-P3-01 | P3 | `preview_apply.py:230-242,459-468`; §17 evidence. | No transport-level post-COMMIT disconnect was synthetically created; pre-commit injection and ledger retry behavior are tested. | Retain as operational chaos-test candidate; do not change protocol. | No |
| R5-R2-P3-02 | P3 | `api/main.py:369-410`; Report 63:215. | Fleet-wide environment homogeneity cannot be proven by a one-process test worktree. | Require deployment/runbook proof before production. | No |

## 23. Corrections and Regression Tests, If Any

**None.** The initial missing-password test setup was corrected only by supplying the documented local test-only connection credentials in the executing shell; it was not an application or repository change. No production/test source was edited.

## 24. Reused Existing Evidence and Its Limits

Reports 61-62 remain valid accepted evidence where their current source/tests are unchanged: full writer-profile closure, final B.3 closure, full Legacy `989 passed`, and isolated Pydantic `119 passed`. Review 2 did not relabel those historical results as newly run.

Review 2 newly executed targeted tests and JUnit artifacts listed in §§20-21. It reused existing exact test assertions for all previously covered writer-to-writer combinations rather than manufacturing redundant scheduling variants. The limitation is bounded: Review 3 must still run its final full suites after this Review 2 PASS.

## 25. Remaining Known Risks

1. Production activation remains denied pending Review 3 and operational deployment evidence.
2. Multi-process gate homogeneity, retention/key-rotation operations, monitoring and rollback/runbook readiness are external activation prerequisites.
3. A transport-level ambiguous commit chaos test is not part of the focused suite; durable ledger recovery behavior itself is tested.
4. Provider cancellation is not assumed to forcibly stop arbitrary remote work; R5's safety boundary is that no root write transaction spans it.

None is a demonstrated current R5 P0/P1 defect.

## 26. Review 3 Prerequisites

**Satisfied:**

- Review 1 PASS has been verified from Report 63.
- Review 2 real PostgreSQL concurrency/security/failure suites pass.
- No Review 2 implementation correction requires a special additional regression run.
- JUnit evidence is available at `r5-review2-postgres-security.xml`, `r5-review2-focused-regression.xml`, and `r5-review2-pydantic-runtime.xml` in this worktree.

**Required for Review 3:** perform—not merely cite—the full Legacy and isolated Pydantic CI-equivalent suites, inspect their current results, perform final compilation/diff checks, and issue the final R5 acceptance decision. Production remains out of scope even if Review 3 passes.

## 27. Final Review 2 Verdict

### REVIEW 2 — PASS

Real PostgreSQL evidence supports Trip-root serialization, fresh locked authorization, scoped resources, revision invalidation, same-key durability, rollback integrity, B.3 stale rejection, Scheduler/Organizer interleavings, protected-profile gating, and non-authoritative Agent/Preview behavior. No unresolved P0/P1 safety defect was demonstrated.

- **Review 3 readiness:** **YES** — it may begin as the final full-regression and acceptance review.
- **Production activation readiness:** **NO** — production is fail-closed and awaits Review 3 plus the stated operational activation requirements.
- **Full CI consequence of Review 2 corrections:** none; no production correction was made. Review 3 still owns the required final full CI exercise.

## A. Concurrency Matrix

| Scenario | Writer/reader pair | PostgreSQL evidence | Expected outcome | Actual outcome | Verdict |
| --- | --- | --- | --- | --- | --- |
| Root serialization | Trip kernel vs Trip kernel | independent PIDs + `pg_blocking_pids`, `test_trip_transaction_kernel.py:444-512` | second waits, sees committed state | observed | Pass |
| Plan/Trip shared root | Plan kernel vs Trip kernel | real backend blocker check, `test_trip_transaction_kernel.py:513-572` | no bypass/deadlock | observed | Pass |
| Same-key Plan command | two durable Plan commands | independent connections, `test_durable_idempotency_kernel.py:462-527` | one effect + replay | observed | Pass |
| Same-key changed payload | two durable Plan commands | real wait + conflict, test lines 528-567 | one effect + conflict | observed | Pass |
| Trip facts race | two Trip durable commands | `pg_blocking_pids`, `test_trip_durable_command_kernel.py:536-595` | one commits, one stale | observed | Pass |
| B.3 Runtime latency | Preview vs real Change Proposal | `FOR UPDATE NOWAIT` plus writer, B3 test 300-349 | no held root; Preview stale | observed | Pass |
| Scheduler vs Booking | server scheduler vs protected booking | PIDs + `pg_blocking_pids`, B2E 678-759 | one terminal outcome; booking stale | observed | Pass |
| Scheduler/organizer combinations | expiry/decision, extend/settle, escalation/expiry, deadlock/booking | real writer overlap B2E 761-915 | coherent terminal state | observed | Pass |

## B. Security Matrix

| Threat | Entrypoint | Authorization/validation boundary | Test evidence | Result |
| --- | --- | --- | --- | --- |
| Account token with Guest-looking prefix | Plan/Trip protected kernels | preliminary account-first scope + locked Account session | `test_transaction_kernel.py:233-271`; Trip test 322-328 | Accepted only as correct Account |
| Revoked/removed actor replay | durable Plan/Trip/Change route | locked credential + membership before ledger replay | durable tests 568-607/383-421; Change 522-549 | Denied, no callback/effect |
| Foreign PlanItem / option | Apply / Preview | Plan-scoped row lookup/options filtering | Change 355-416; B3 519-579 | Rejected/omitted; no ledger |
| Modified patch/revision | Apply | HMAC semantics + expected revision + deterministic writer | durable 330-371; B3 581-644 | Conflict/stale; no duplicate write |
| Unknown HMAC/outcome version | replay | trusted key/outcome registry | durable 643-905 | Fail closed |
| Ordinary user organizer action | organizer protected route | locked role gate | B2E protected route/action tests | Safe role rejection |
| Malformed/mixed feature config | Preview/Apply routes | effective profile admission | `test_b3_protected_profile.py` | Fail closed |
| AI-provided identifiers | Preview / Apply | agent result advisory; server row scope/reclassification | B3 + tools/runtime tests | No authority |

## C. Durable Recovery Matrix

| Scenario | Expected Ledger/domain state | Observed state | Replay/retry result | Verdict |
| --- | --- | --- | --- | --- |
| First success then same key | one effect/revision + completed ledger | durable tests 284-324 | historical safe replay | Pass |
| Same key changed command | no second effect | tests 330-371, 528-567 | controlled conflict | Pass |
| Authorization revoked before replay | no disclosure/effect | tests 568-607, 383-421 | denied before replay | Pass |
| Tampered/unknown stored integrity | no replay | tests 643-665, 345-421 | fail closed | Pass |
| Unsafe/obsolete outcome | no unsafe replay | tests 806-905 | fail closed | Pass |
| Expired retained ledger | no re-execution | tests 984-1010 | controlled failure | Pass |
| Failure after child/revision/ledger staging | no partial domain/revision/completed ledger | tests in §16 | retry may execute once later | Pass |

## D. Invariant Traceability Matrix

| Invariant | Frozen contract | Source location | Actual test | Evidence grade | Verdict |
| --- | --- | --- | --- | --- | --- |
| Trip-first serialization | Report 49:26,69-71 | `kernel.py:260-301`; `trip_kernel.py:182-297` | kernel/Trip/B2E races | Source- and test-confirmed | Pass |
| Fresh locked auth | Report 43; Report 49 | `kernel.py:312-397` | revocation/removal tests | Source- and test-confirmed | Pass |
| Revision ownership | Report 49:39-49,147 | `durable.py:291-415`; `trip_durable.py:85-149` | durable/Trip/B2E tests | Source- and test-confirmed | Pass |
| Atomic durable result | Report 49:220-238 | durable/trip durable source | fault injection tests | Source- and test-confirmed | Pass |
| B.3 bracketed reads | Report 49:49 | `preview_apply.py:188-492` | B3 tests 231-349,581-644 | Source- and test-confirmed | Pass |
| No provider under root lock | Report 49:270 | generator durable coordinator | B3 latency + generator tests | Source- and test-confirmed | Pass |
| Gate fail-closed | Report 49:324 | `main.py:369-410` | profile suite (14 tests) | Source- and test-confirmed | Pass |
| Agent no write/privacy | PR-04/Report 43 | chat/preview/runtime source | tools/chat/runtime/Pydantic tests | Source- and test-confirmed | Pass |

## E. Findings Matrix

| ID | Severity | Source evidence | Reproduction | Minimum fix | Review 3 blocker |
| --- | --- | --- | --- | --- | --- |
| R5-R2-P3-01 | P3 | durable ledger/replay source and §17 | No post-COMMIT transport chaos injection; no defect observed | Optional later chaos test | No |
| R5-R2-P3-02 | P3 | `main.py:369-410`; Report 63 | Fleet homogeneity is deployment-owned | Activation runbook/rollout control | No |
