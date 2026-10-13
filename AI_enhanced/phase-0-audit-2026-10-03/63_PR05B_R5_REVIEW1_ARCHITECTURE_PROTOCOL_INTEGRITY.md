# Cadensy R5 — Review 1 of 3: Architecture & Protocol Integrity

**Review date:** 2026-10-13<br>
**Worktree:** `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`<br>
**Branch:** `codex/pr05b1-schema-foundation`<br>
**Scope:** independent source and accepted-evidence review only. No production code, schema, configuration, or frozen PR-04 artifact was changed in this review.

## 1. Executive Summary

**Verdict: REVIEW 1 — PASS.** The current R5 implementation composes the accepted Plan-bound and Trip-bound durable command protocols with B.3 revision-bracketed preview/apply without introducing a second domain decision engine, transaction kernel, ledger protocol, or agent write path. No demonstrated P0, P1, or P2 architecture/protocol defect was found.

The review verified the current source rather than relying on earlier phase labels. The material conclusions are:

- The Plan kernel and Trip kernel own independent, short-lived write Sessions and establish the canonical Trip-first lock boundary; durable wrappers own replay, HMAC verification, revision movement, safe outcome persistence, and atomic completion.
- B.3 Preview has only read authority. It captures Plan and Trip facts revisions, constructs session-free tool descriptors, invokes every tool through a fresh bounded read Session, deterministically classifies against current scoped facts, and independently verifies both revisions before serialization.
- B.3 Apply is an adapter to the accepted `plan.change.submit.v1` protected Change Proposal writer. It re-authenticates/re-authorizes under locks and recomputes the authoritative result; Preview metadata has no authority to mutate a plan.
- Admission requires every listed B.2C/B.2D/B.2E protected writer gate in an explicitly non-production process. An incomplete/mixed profile rejects B.3 instead of falling back to Legacy.
- Final evidence in Reports 61 and 62 was produced after the relevant closure changes. In particular, the former Legacy CI cleanup failure was corrected and the final Legacy run was `989 passed`; the B.3-focused suite was `10 passed`.

This is code readiness for Review 2, not production authorization. Production remains fail-closed and operational rollout prerequisites remain open.

## 2. Final B.2E/B.3 Acceptance Evidence Verified

| Area | Final report and evidence inspected | Current disposition |
| --- | --- | --- |
| B.2E writer coverage | Report 61, §§ final closure, lines 543-588: full writer-profile closure, actual-overlap evidence, and final `PASS`. | Supersedes Report 60's historical `BLOCKED` audit result. |
| B.3 closure | Report 62, lines 421-425: B.3 focused `10 passed`; cleanup regression `3 passed`; relevant regression `77 passed`; Legacy `989 passed`; isolated Pydantic `119 passed`. Line 446 records final `PASS`. | Current evidence is post-closure evidence. |
| Earlier conditional results | Reports 43 and 60 record design/audit-stage conditional or blocked status; Reports 49-59 and 61-62 record later implementation/closure results. | Historical records, not current blockers. |
| Final Legacy failure | Report 62 identifies the fixture-owned User cleanup gap and its correction in `backend/tests/test_generator_commit.py:218-253`, then records the later clean Legacy suite. | Resolved; no lingering source contradiction observed. |
| Retained limits | Reports 49:259-265, 49:324; Report 62 final limitations. | Production activation, fleet-wide gate homogeneity, retention/runbook and later operational work remain outside this PASS. |

No test was re-run merely to duplicate final acceptance. The evidence was inspected for a test-to-current-source relationship; narrow static inspection was used for this review.

## 3. Source and Frozen Contract Inventory

| Contract / source | Review use |
| --- | --- |
| `43_PR05B_FINAL_PROTOCOL_DECISIONS.md:499-517` | Original architecture decision: initially conditional; later implementation evidence required. |
| `49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md:9,26,35,39-49,69-71,147,159-174,220-238,270,324,332` | Frozen Trip/Plan revision, lock, ledger, HMAC, no-op, provider separation, gate and safety contracts. |
| Reports 50-53 | Schema, Trip kernel, Trip durable ledger, and real writer implementation acceptance. |
| Reports 54-59 | Generator UUID identity, detached snapshot, provider/cache separation, durable coordinator, validate/commit, HTTP gate closure. |
| Reports 60-62 | Writer inventory/history, final B.2E closure, final B.3 closure. |
| `backend/app/db/models.py:112-141,276-336` | Trip facts revision, ledger scope/result checks and persisted contract fields. |
| `backend/app/domain/transactions/kernel.py:172-247,264-390` | Plan kernel ownership and lock acquisition. |
| `backend/app/domain/transactions/trip_kernel.py:97-133,182-297` | Trip kernel ownership and Trip-first locking. |
| `backend/app/domain/transactions/durable.py:249-425` | Plan durable command, locked ledger/replay and Plan revision behavior. |
| `backend/app/domain/transactions/trip_durable.py:79-193` | Trip durable command, dual revision/replay and outcome policy behavior. |
| `backend/app/domain/preview_apply.py:188-268,273-490` | B.3 bounded tool reads, capture, deterministic classification, options and independent final check. |
| `backend/app/api/main.py:369-410,1153-1240,1883-2016` | B.3 admission, Preview route, Apply routing and protected adapter. |
| PR-04 runtime code: `backend/app/agents/runtime_contract.py`, `legacy_runtime.py:64-230`, `pydantic_runtime.py:161-288`, `backend/app/domain/chat/service.py:53-82,119-222` | Runtime compatibility and non-authoritative agent boundary. |

## 4. System Architecture Diagram

```text
HTTP / job entrypoint
    |
    +-- Legacy gate selected --> Legacy domain service --> request Session --> legacy response
    |
    +-- Protected gate selected --> preliminary authentication (primitive scope only)
                                  |
                                  +--> PlanTransactionKernel / TripTransactionKernel
                                         Trip FOR UPDATE
                                         -> credential -> membership(s) -> Plan(s) -> ledger -> children
                                  |
                                  +--> DurableCommandKernel / TripDurableCommandKernel
                                         HMAC + expected revision + locked replay
                                         -> deterministic domain mutation
                                         -> revision + safe ledger outcome, one transaction

B.3 Preview (no writer path)
    preliminary scope -> capture Plan+Trip revisions -> detached tool descriptors
    -> runtime -> bounded read-only tools -> deterministic classification
    -> independent final revision check -> safe Preview DTO

B.3 Apply
    headers + fresh credential -> `plan.change.submit.v1` Change Proposal durable writer
    -> same protected Plan protocol; no Preview token/state-machine authority
```

## 5. HTTP/Agent/Domain/Transaction Call Graphs

| Operation | Source-backed flow | Boundary result |
| --- | --- | --- |
| Legacy Chat | `main.py` chat route -> `chat/service.py:119-222` -> `build_chat_runtime` -> `LegacyReadTripCapability` (`legacy_runtime.py:64-143`) -> Legacy response. | Default path remains Legacy when B.3 is off; capability is read-only. |
| B.3 Chat Preview | `main.py:1153-1240` -> `preview_trip_chat_revision_bracketed` (`chat/service.py:53-82`) -> `RevisionBracketedPreview` (`preview_apply.py:273-490`). | Route closes request DB before runtime; preview owns no write Session. |
| Legacy change submission | `main.py:1883-2016`, gate off -> existing validation/orchestrator path. | Legacy compatibility preserved; not admitted in a B.3 protected profile. |
| B.3 Apply | `main.py:1942-2016` `_protected_submit_change` -> `execute_protected_change_proposal` (`transactions/change_proposal.py:92-183`) -> Plan durable kernel. | No protected failure falls back to Legacy. |
| Protected generator | protected generator route in `main.py` -> `generator_durable.py:334-606` -> Trip kernel/locked commit. | Detached preparation/provider phase is outside root lock; commit is durable and atomic. |
| Membership join/remove | protected routes -> `transactions/membership_join.py` / `membership_remove.py` -> Trip durable kernel. | Trip facts and exactly-one Plan revision move together for real planning changes. |
| Scheduler settlement/expiry | `jobs/scheduler.py` protected gate -> `transactions/scheduler.py:23-56` -> locked Plan/domain settlement. | Scheduler is an explicit protected-gate participant; no implicit upgrade. |
| Organizer actions | `main.py` protected organizer endpoints -> `transactions/organizer_actions.py:47-75` -> Plan durable kernel. | Scoped child locks and deterministic legacy business functions are reused. |
| B.2C writers | manual, booking, notice objection, change proposal, vote, proposal decision, standalone settlement -> their `transactions/*` adapters -> Plan durable kernel. | One Plan revision / ledger protocol, not per-writer alternatives. |
| Preferences/constraints | protected requirement routes -> `TripDurableCommandKernel` (`trip_durable.py:79-193`). | Pre-Plan facts use Trip revision; an existing single Plan receives the corresponding atomic Plan revision. |

API code performs adaptation, header parsing, safe error mapping, and gate selection; transaction and deterministic decisions remain in domain transaction adapters/kernels. No inspected path gives a chat runtime ledger, revision, ORM write, or Domain verdict authority.

## 6. B.1 Schema and Aggregate Integrity

`Trip.planning_facts_revision` has a non-negative check and mapped server-defaulted state in `db/models.py:112-141`. The ledger mapping and scope/result mutual-exclusion checks reside at `db/models.py:276-336`. This matches the frozen split:

- Planning requirements mutations: Trip facts revision; when exactly one authoritative Plan exists, atomically also that Plan's `revision` (Report 49:39-49).
- Plan/decision mutations: Plan revision only.
- True no-op and replay: neither revision changes (Report 49:147, `trip_durable.py:129-146`, Plan durable behavior at `durable.py:311-415`).
- Zero/multiple Plans fail closed in the applicable aggregate protocol rather than selecting an arbitrary root (Report 49:26; `trip_kernel.py:182-297`).

The source contains the expected scoped result fields rather than representing a Trip command as an ambiguous Plan result. No schema-contract contradiction was found.

## 7. B.2 Transaction Kernel Integrity

`PlanTransactionKernel.execute()` owns creation, `session.begin()`, result isolation/context invalidation, rollback, and closure at `kernel.py:172-247`. Its private `_run_locked_command()` is the reviewed internal durable seam, not a public commit escape hatch (`kernel.py:209-247`). `TripTransactionKernel` provides the analogous isolated transaction at `trip_kernel.py:97-133`.

Both kernels acquire the root Trip first and then credential, membership, aggregate/ledger and scoped children under row locks (`kernel.py:264-390`; `trip_kernel.py:182-297`). This matches Report 49:69-71. Fresh locked authorization prevents preliminary request authentication or old ORM identity state from authorizing the mutation. No request-owned Session is passed into these units of work.

## 8. Durable Idempotency and HMAC Integrity

Plan commands are handled by `DurableCommandKernel` (`durable.py:249-425`); Trip commands by `TripDurableCommandKernel` (`trip_durable.py:79-193`). In both paths, the flow is: lock lookup under the aggregate transaction, verify command/fingerprint/HMAC/key-version semantics, reject inappropriate revision/key reuse, return only safe replay payloads, otherwise invoke deterministic mutation and persist its safe outcome atomically.

The frozen identity is `(trip_id, actor_membership_id, idempotency_key)` (Report 49:35), with operation and canonical semantic fields in the HMAC envelope rather than overloading key uniqueness. Current replay explicitly validates aggregate result shape and outcome contract (`trip_durable.py:158-193`); Plan replay is rooted in the same locked ledger mechanics (`durable.py:320,415-425`). Report 49:159-174 preserves Plan HMAC format and distinguishes signing-key rotation from fingerprint-version support.

This review found no route that writes a completed protected result outside its durable transaction, and no replay before locked, fresh authorization.

## 9. B.2D Generator Integration

The generator durable coordinator uses a prepared detached snapshot and defers root-lock acquisition until validate/commit (`generator_durable.py:334-358,380-522`). It locks/rechecks Plan and Trip facts at commit and replays from the locked ledger (`generator_durable.py:513-606`). Report 49:270 requires no provider/cache work while a root lock is held; Reports 54-59 and the source separation meet that boundary.

The generator path is a B.3 admission dependency through the protected profile; it is not silently activated by Preview/Apply.

## 10. B.2E Writer Coverage

Report 60's original inventory was an audit baseline. Report 61's final closure, including actual cross-writer overlap evidence, is the controlling result. Current source has adapters for:

| Writer group | Protected implementation exists | Gate-selected when enabled | Default selection | Evidence status |
| --- | --- | --- | --- | --- |
| Manual item / booking / proposal / vote / decision / objection / settlement | Yes, `backend/app/domain/transactions/` Plan adapters | Yes | Legacy while gate off | Report 61 + writer tests |
| Preferences / constraints | Yes, Trip durable adapters | Yes | Legacy while gate off | Reports 53/61 + Trip tests |
| Membership join/remove | Yes, Trip durable adapters | Yes | Legacy while gate off | Report 61 + B.2E tests |
| Scheduler settlement/expiry | Yes, `transactions/scheduler.py` | Explicit scheduler gate | Legacy scheduler behavior while off | Report 61 + B.2E tests |
| Organizer extension/escalation/deadlock | Yes, `organizer_actions.py` | Yes | Legacy while off | Report 61 + B.2E tests |
| Generator | Yes, `generator_durable.py` | Yes | Legacy while off | Reports 54-59 + generator tests |

`protected_profile.py:1-65` lists the complete B.3-required protected gate profile. `main.py:369-410` refuses a B.3 request unless every required gate is effectively enabled in non-production. Therefore a Legacy writer is not concurrently reachable as an admitted B.3-authoritative writer merely because the application supports a default-off Legacy mode. No mixed-profile bypass was demonstrated.

## 11. B.3 Preview Revision-Bracketing

`RevisionBracketedPreview` captures the exactly-one authoritative Plan plus Plan/Trip facts revisions in `_capture()` (`preview_apply.py:329-356`). It then builds only session-free `_ReadToolDescriptor` metadata and invokes tools through `_fresh_handler_tool()` (`preview_apply.py:188-268`), which opens a bounded fresh read Session per invocation and applies PostgreSQL read-only transaction mode where supported.

After runtime output, selected-item classification and safe options are read/scoped against current facts (`preview_apply.py:402-490`), followed by an independent final Plan/Trip revision comparison. Any changed revision, missing aggregate, or duplicate aggregate rejects rather than serializing a stale candidate. No preview method calls an authoritative writer.

Earlier closure concerns were checked directly:

- `_safe_options()` scopes source and alternative items to the captured current Plan and rejects removed/foreign/malformed candidates: `preview_apply.py:402-454`; covered by `test_b3_revision_bracketed_preview.py:519-579`.
- Replacement provenance applies full accepted source matching rather than accepting an unsafe partial replacement: the provenance helper below the options path; covered by `test_b3_revision_bracketed_preview.py:540-556`.
- `_fresh_handler_tool()` captures descriptors, not the builder Session or original bound handler: `preview_apply.py:188-268`; session closure/isolation is exercised at `test_b3_revision_bracketed_preview.py:231-283`.
- Real HTTP Preview is exercised with a deterministic runtime seam but the actual capture/tools/classification/final verification/serialization service, not a mocked Preview response: `test_b3_revision_bracketed_preview.py:392-518,557-644`.

## 12. B.3 Apply Authorization and Reclassification

The B.3 Apply route checks admission before selecting the protected path and uses `_protected_submit_change()` (`main.py:1883-2016`). The adapter requires idempotency and expected revision inputs, constructs the established command, then invokes `execute_protected_change_proposal()` rather than duplicating proposal rules.

The protected writer locks and scopes its Plan item and relevant decision rows (`change_proposal.py:92-183,205-239`) and calls the deterministic orchestrator under the locked Plan context. This establishes fresh credential/membership authorization, Plan/Trip ownership, expected revision validation, ledger lookup, current fact loading, classification, effect/revision/ledger atomicity and detached safe output. Its regression evidence includes replay/stale/rollback/privacy/foreign-child/root-contention/credential cases in `test_protected_change_proposal_writer.py:229-657`.

## 13. Preview-to-Apply Data Contract

Preview is advisory and read-only. The client may submit a chosen candidate patch, reason, alternatives, and the captured expected Plan revision; the Apply request is not trusted because it arose from Preview. It is re-authorized and reclassified in the locked writer.

There is intentionally no persisted Preview token. A modified preview artifact cannot grant authority: headers and body are merely command input subject to fresh authorization, current Plan scope, revision validation, canonical fingerprinting, and deterministic domain classification. A planning-fact or Plan change after Preview causes a revision conflict rather than application under stale facts. `test_b3_revision_bracketed_preview.py:581-644` covers the Preview-then-real-writer stale and stale Apply scenario. No second proposal state machine, ledger, Plan revision owner, kernel, or AI write path was introduced.

## 14. PR-04 Runtime Compatibility

The runtime contract remains framework-neutral. Legacy execution uses the existing `LegacyReadTripCapability` / `LegacyRuntimeReadTool` shape (`legacy_runtime.py:64-173`) and records tool results through a generic hook (`legacy_runtime.py:182-230`), preserving guard and cache semantics without granting writes. `chat/service.py:53-82` composes B.3 as a dedicated preview boundary rather than replacing regular runtime composition.

Pydantic remains an isolated implementation (`pydantic_runtime.py:161-288`). It has intentional, safe differences: candidate replacement options are empty where replacement provenance cannot be proven; that path fails closed instead of inventing parity. The agent receives neither database mutation APIs nor authoritative identity/revision ownership. Existing PR-04 isolated compatibility evidence is included in Report 62's final `119 passed` result.

## 15. Candidate Provenance, Item Scope and Privacy

Candidate item/alternative selection is Plan-scoped and excludes removed state before it reaches the safe preview response (`preview_apply.py:402-454`). Classification remains the deterministic authority and runs against fresh current facts; chat-generated text/classification is not an authority to write.

The Preview DTO does not serialize private member constraints, credentials, raw provider payloads, membership identifiers, or server HMAC/ledger internals. Protected application stores only approved safe outcomes. Evidence: Preview scoping/provenance tests `test_b3_revision_bracketed_preview.py:519-644`; Change Proposal privacy/ledger test `test_protected_change_proposal_writer.py:322-334`; durable policy tests `test_durable_idempotency_kernel.py:834-983`.

## 16. Session Lifetime and Ownership

The request route reduces preliminary actor information to primitive scope and closes the dependency database before B.3 runtime execution (`main.py:1153-1240`). Preview tool creation does not retain a Session, and every tool call owns and closes a bounded read Session (`preview_apply.py:188-268`). Protected writes use kernel-created short-lived Sessions and invalidate the locked context before return (`kernel.py:172-247`; `trip_kernel.py:97-133`).

This directly addresses stale identity-map/session escape and remote/runtime latency under transaction concerns. It does not promise cancellation of an arbitrary remote provider call; no root write lock spans such a call.

## 17. Protected Feature Gate and Mixed-Runtime Risks

`_b3_preview_apply_gate_state()` is explicit about all conditions: raw B.3 gate, recognized non-production environment, full writer profile admission, and fail-closed states (`main.py:369-410`). `b3_profile_admission()` enumerates required B.2C/B.2D/B.2E gates (`protected_profile.py:47-65`). The Preview and Apply routes return controlled unavailable/error results; they do not silently redirect an attempted protected call to Legacy.

The local code cannot itself prove a future multiprocess deployment has homogeneous environment values. That is a production activation prerequisite, not a present architecture defect: process-level code correctly fails closed on its own incomplete profile.

## 18. Evidence-to-Invariant Traceability Matrix

| Major invariant | Source status | Test / accepted evidence | Grade |
| --- | --- | --- | --- |
| Trip-first root lock and fresh auth | `kernel.py:264-390`; `trip_kernel.py:182-297` | `test_trip_transaction_kernel.py:444-600`; Report 61 | Source- and test-confirmed |
| Plan/Trip revision ownership | `trip_durable.py:105-146`; `durable.py:311-415` | Trip durable, B.2E and B.3 test suites recorded in Reports 61-62 | Source- and test-confirmed |
| Locked replay/HMAC/outcome safety | `durable.py:320-425`; `trip_durable.py:158-193` | `test_durable_idempotency_kernel.py:284-1031` | Source- and test-confirmed |
| Generator no provider under lock | `generator_durable.py:334-606` | Reports 54-59; `test_generator_commit.py` lifecycle/rollback/replay tests | Source- and test-confirmed |
| B.3 stale Preview rejection | `preview_apply.py:329-490` | `test_b3_revision_bracketed_preview.py:284-375,581-644` | Source- and test-confirmed |
| Fresh, bounded B.3 tools | `preview_apply.py:188-268` | `test_b3_revision_bracketed_preview.py:231-283` | Source- and test-confirmed |
| Real HTTP Preview path | `main.py:1153-1240`; `chat/service.py:53-82` | `test_b3_revision_bracketed_preview.py:392-518,557-644` | Source- and test-confirmed |
| Apply auth/revision/replay | `main.py:1942-2016`; `change_proposal.py:92-239` | `test_protected_change_proposal_writer.py:229-657` | Source- and test-confirmed |
| Complete profile admission | `main.py:369-410`; `protected_profile.py:47-65` | `test_b3_protected_profile.py:10-71`; Report 62 | Source- and test-confirmed |
| Legacy behavior and Pydantic isolation | `chat/service.py:119-222`; `legacy_runtime.py:64-230`; `pydantic_runtime.py:161-288` | Report 62 Legacy `989 passed`, Pydantic `119 passed` | Source- and test-confirmed |
| No preview writes / privacy-safe artifact | `preview_apply.py:273-490` | B.3/Change Proposal privacy evidence above | Source- and test-confirmed |

## 19. Confirmed Findings P0–P3

| ID | Severity | Evidence | Risk | Required action | Review 2 blocker? |
| --- | --- | --- | --- | --- |
| None | P0 | No demonstrated authorization bypass, direct agent write path, or fundamental frozen-protocol violation found. | N/A | N/A | No |
| None | P1 | No demonstrated stale Apply bypass, mixed-profile admission bypass, or incompatible revision/ledger ownership found. | N/A | N/A | No |
| None | P2 | Evidence is code-linked and post-closure; no material test gap was demonstrated in this architectural review. | N/A | N/A | No |
| R5-R1-P3-01 | P3 | Report 49:259-265,324 and Report 62 retained limits; source gate is process-local (`main.py:369-410`). | Future production fleet must prove homogeneous gates, operational ledger retention/key rotation process, and monitoring/runbook readiness. This is not a local non-production protocol failure. | Address as production-activation evidence; do not relax gates. | No |
| R5-R1-P3-02 | P3 | Preview has no persisted token by accepted design; source makes Apply independently authoritative (`main.py:1942-2016`; `change_proposal.py:92-239`). | Consumers must treat preview as advisory, not an authorization grant. | Preserve documentation/client contract and regression coverage. | No |

## 20. Corrections Made During Review 1, If Any

**None.** No self-contained, demonstrated P0/P1 discrepancy required a code or test correction. This review created only this report.

## 21. Remaining Known Limitations

1. Production remains intentionally unavailable for this profile. Local non-production success is not deployment authorization.
2. Multi-process/multi-worker gate homogeneity must be established operationally before activation; the source correctly rejects an incomplete local profile but cannot inspect another process.
3. Ledger retention, signing-key rotation operations, backup/recovery, operational observability, and deployment rollback require runbook evidence before production activation.
4. B.3 intentionally does not persist Preview tokens. Security rests on fresh protected Apply authentication, revision checking and deterministic reclassification; this is accepted, not a missing feature.
5. Pydantic replacement candidates remain conservatively unsupported where provenance cannot be proven. That fail-closed difference is intentional.

## 22. Explicit Review 2 Prerequisites

**Satisfied to begin Review 2:**

- Review 1 found no P0/P1 architecture blocker.
- Final Report 61 B.2E evidence and Report 62 B.3 post-closure evidence were verified.
- Disposable localhost PostgreSQL configuration and independent-connection tests are documented by the accepted phases; Review 2 should independently re-check test database safety before execution.
- Review 2 should focus on live PostgreSQL locking, cancellation/rollback, actual root-lock waiting, replay races, authorization races, and error mapping rather than redoing this architecture review.

**Not yet satisfied for Review 3:** Review 2 itself must finish with its own verdict and any required fixes/evidence. Review 3 is not started or authorized by this report.

**Not satisfied for production activation:** the P3 operational/deployment prerequisites above, plus the later Review 2 and Review 3 final acceptance, remain required.

## 23. Final Review 1 Verdict

### REVIEW 1 — PASS

The reviewed R5 architecture conforms to the frozen Reports 43/49 contracts and the accepted PR-04 runtime boundaries. B.2E protected coverage, B.3 revision-bracketed Preview, and B.3 protected Apply are coherently integrated. No unresolved P0/P1 architectural defect was demonstrated.

- **Review 2 readiness:** **YES** — may begin as the next independent PostgreSQL concurrency/security review.
- **Review 3 readiness:** **NOT YET** — assess only after Review 2 completes; this review did not begin it.
- **Production activation readiness:** **NO** — intentionally disabled and pending later security/concurrency/final-regression acceptance plus operational activation prerequisites.

## A. Protocol Conformance Matrix

| Invariant | Frozen source | Code location | Test evidence | Verdict |
| --- | --- | --- | --- | --- |
| Trip-first lock / fresh auth | Report 49:26,69-71 | `trip_kernel.py:182-297`; `kernel.py:264-390` | `test_trip_transaction_kernel.py:444-600`; Report 61 | Conforms |
| Trip facts + exactly-one Plan dual revision | Report 49:39-49 | `trip_durable.py:105-146` | Phase 4/B.2E evidence in Reports 53/61 | Conforms |
| Plan mutation revision once, replay/no-op zero | Report 49:147 | `durable.py:311-415` | `test_durable_idempotency_kernel.py:284-730` | Conforms |
| Canonical HMAC / key version / safe replay | Report 49:159-174,220-238 | `durable.py:320-425`; `trip_durable.py:158-193` | `test_durable_idempotency_kernel.py:643-1031` | Conforms |
| Provider outside root lock | Report 49:270 | `generator_durable.py:334-522` | Reports 54-59; generator tests | Conforms |
| B.3 captures and verifies both revisions | Report 49:49 | `preview_apply.py:329-490` | `test_b3_revision_bracketed_preview.py:284-375,581-644` | Conforms |
| Default-off complete-profile non-production gate | Report 49:324 | `main.py:369-410`; `protected_profile.py:47-65` | `test_b3_protected_profile.py:10-71` | Conforms |
| Agent read-only / deterministic decisions | PR-04 contracts; Report 43 | `chat/service.py:53-82`; `legacy_runtime.py:64-230`; `preview_apply.py:273-490` | PR-04 / B.3 evidence in Report 62 | Conforms |

## B. Execution Boundary Matrix

| Operation | Entrypoint | Auth | Transaction owner | Revision owner | Gate | Verdict |
| --- | --- | --- | --- | --- | --- | --- |
| Legacy Chat | chat API | Legacy scope | request/read capability | none | B.3 off | Preserved |
| B.3 Preview | chat API | preliminary scope then read membership | bounded read Sessions | none | B.3 complete profile | Read-only |
| B.3 Apply | change API | fresh protected credential/membership | Plan kernel + durable | Plan durable | B.3 complete profile | Protected |
| Generator | generator API | Trip kernel revalidation | Trip kernel/coordinator | durable generator | B.2D gate | Protected |
| Membership | join/remove API | Trip kernel revalidation | Trip kernel + Trip durable | Trip + exact Plan | B.2E gate | Protected |
| Scheduler | job | protected job path | locked Plan path | Plan writer | scheduler gate | Protected |
| Organizer | organizer API | Plan kernel revalidation | Plan kernel + durable | Plan durable | B.2E gate | Protected |
| Plan/decision writers | respective APIs | Plan kernel revalidation | Plan kernel + durable | Plan durable | B.2C gate | Protected |
| Preferences/constraints | requirements APIs | Trip kernel revalidation | Trip kernel + Trip durable | Trip + exact Plan | requirements gates | Protected |

## C. B.3 Read/Write Trust Matrix

| Input or artifact | Trust level | Validation owner | May mutate state? | Protection |
| --- | --- | --- | --- | --- |
| Bearer / preliminary actor scope | Untrusted until locked revalidation | transaction kernel | No | credential + membership locks |
| Captured Plan/Trip revision | Observational | Preview capture/final check; Apply durable expected revision | No | independent final read / locked stale check |
| Runtime candidate text/options | Untrusted advisory | deterministic classifier + scoped options | No | Plan scope, provenance, omission on invalid |
| Preview DTO / client-selected patch | Untrusted command input | protected Change Proposal writer | Yes, only after revalidation | HMAC, expected revision, fresh auth, locks, deterministic classification |
| Ledger outcome | Trusted only after locked contract verification | durable kernels | No direct mutation on replay | HMAC, scope, outcome policy, expiry, authorization |
| Agent tools | Read-only operational input | bounded preview capability | No | fresh read-only Session per invocation |

## D. Findings Matrix

| ID | Severity | Evidence | Risk | Required action | Review 2 blocker? |
| --- | --- | --- | --- | --- | --- |
| R5-R1-P3-01 | P3 | Report 49:259-265,324; `main.py:369-410` | Deployment configuration/operational readiness | Later activation proof and runbook | No |
| R5-R1-P3-02 | P3 | `main.py:1942-2016`; `change_proposal.py:92-239` | Client could misunderstand advisory Preview | Keep contract documentation/tests | No |
