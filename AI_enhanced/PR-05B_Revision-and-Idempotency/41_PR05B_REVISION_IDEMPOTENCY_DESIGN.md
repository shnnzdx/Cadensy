# Cadensy PR-05B — Revision, Idempotency & Concurrency Safety Design

**Status:** Round 1 architecture audit and design only — no production-code, ORM,
Alembic, frontend, test, dependency, or deployment change was made.

**Evidence vocabulary:**

- **FACT** — directly established by the cited source or read-only command.
- **INFERENCE** — a conclusion that follows from the facts, not a production incident claim.
- **PROPOSAL** — a design for a later, separately reviewed implementation.
- **UNVERIFIED** — requires a live target, a future implementation, or a controlled
  test that this audit did not run.

## 1. Executive Summary

**FACT.** Cadensy correctly keeps the model outside the authority boundary: the
runtime contract provides a read-only capability rather than a session or generic
write API, and explicitly says an agent suggestion is not fresh-state or
transactional authority ([`backend/app/agents/runtime_contract.py:63`](../../backend/app/agents/runtime_contract.py#L63),
[`backend/app/agents/runtime_contract.py:99`](../../backend/app/agents/runtime_contract.py#L99)).
The chat HTTP route likewise never calls `propose_change()`; it returns a suggestion
that the client may later submit ([`backend/app/api/main.py:854`](../../backend/app/api/main.py#L854)).

**FACT.** The current Plan/PlanItem schema has no revision column, and the request
models have neither expected revision nor idempotency key ([`backend/app/db/models.py:233`](../../backend/app/db/models.py#L233),
[`backend/app/db/models.py:251`](../../backend/app/db/models.py#L251),
[`backend/app/api/main.py:195`](../../backend/app/api/main.py#L195)). `POST
/changes` does re-run deterministic classification, but it does so before a
transactional freshness check or aggregate lock and commits afterward
([`backend/app/api/main.py:1286`](../../backend/app/api/main.py#L1286),
[`backend/app/domain/decisions/orchestrator.py:437`](../../backend/app/domain/decisions/orchestrator.py#L437)).
That is useful business validation, not optimistic concurrency control.

**INFERENCE.** The highest risks are (1) an old preview applying under state that
changed after preview, (2) duplicate durable effects after a lost HTTP response,
and (3) concurrent settlement/acceptance emitting duplicate `PlanChange` and
`UpdateNotice` rows. Existing partial unique indexes protect only certain queue
states; they do not serialize multi-statement decision flows.

**PROPOSAL.** Use the existing `Plan` row as the single, conservative consistency
aggregate for a trip's current itinerary and decision-relevant input state. Add a
monotonic `plan.revision` plus a Postgres-backed idempotency ledger. For every
covered mutation, acquire the plan aggregate lock, compare a server-derived
`expected_plan_revision`, re-read and reclassify inside that transaction, stage all
domain effects, increment the revision once, save the safe idempotency outcome in
the same transaction, and commit. This deliberately favors correct cross-item
schedule and constraint handling over maximum parallelism in a small collaborative
trip group.

**PROPOSAL.** Do not use a model-generated revision, hash, or preview token. A
preview returns server-derived plan/item identity, plan revision, normalized patch,
and authoritative classification. Initially there is no opaque preview token:
revision + authenticated actor + fresh locked reclassification supply the guarantee;
a token would otherwise be cryptographic theater. `Idempotency-Key` is a separate
client retry key.

## 2. Repository State Verification

Verification was read-only and performed on 2026-10-08. No fetch, reset, checkout,
rebase, clean, branch deletion, database connection, or cloud operation was run.

| Check | Result | Evidence / limitation |
| --- | --- | --- |
| GitHub `main` | `17026adf4b77ced2280629f2b1584fd6892e420e` | **FACT.** Read through GitHub API ref `repos/shnnzdx/Cadensy/git/ref/heads/main`. |
| Local branch / HEAD | `main` at `17026adf4b77ced2280629f2b1584fd6892e420e` | **FACT.** `git rev-parse --abbrev-ref HEAD` and `git rev-parse HEAD`. |
| Local/remote divergence | `0 0` for `HEAD...origin/main` | **FACT.** `git rev-list --left-right --count HEAD...origin/main`; this proves local tracking ref matched at inspection time. |
| Worktree state | Canonical worktree clean: `## main...origin/main`. A separate detached Codex visualization worktree exists at `81d3d32`; it was not touched. | **FACT.** `git status --short --branch` and `git worktree list --porcelain`. |
| Local branches | `main`, archival/CI branches, `backup/pr03r-local-closeout-20261006`, and `wip/new-pre-consolidation-20261006` remain. | **FACT.** `git branch --all --no-color`; no branch was modified. |
| PR-04E | PR #7 is `MERGED` at `2026-10-07T16:35:52Z`; merge commit is `17026ad…`, and head `855c1e7…` is an ancestor of local `HEAD`. | **FACT.** `gh pr view 7` plus `git merge-base --is-ancestor`. |
| Migration graph | Root `f9ff23b7b84d`; child/head `54e8dd1ca624`. | **FACT.** Revision and `down_revision` declarations in [`backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py:17`](../../backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py#L17) and [`backend/alembic/versions/54e8dd1ca624_add_bounded_guest_sessions.py:17`](../../backend/alembic/versions/54e8dd1ca624_add_bounded_guest_sessions.py#L17). This is graph inspection, not a claim about a live database's `alembic_version`. |
| Target report | Absent before this audit; created at the path named in section 16. | **FACT.** `Test-Path` returned false before this file was created. |

**UNVERIFIED.** No live database was inspected. Therefore this report does not claim
that any local, RDS, or deployed schema is at Alembic head, has no drift, or is safe
to stamp. No hosted CI or runtime deployment state was inspected beyond the public
PR merge metadata.

## 3. Current Architecture

### 3.1 Actual preview-to-apply call chains

```text
AI chat preview (durably read-only)
TripAppState.chatWithTrip
  -> POST /api/trips/{trip_id}/chat
  -> chat.respond_to_trip_chat
  -> RuntimeRequest + LegacyReadTripCapability(SessionLocal)
  -> selected Legacy or Pydantic runtime; scoped read tools only
  -> application revalidates model syntax with orch.classify_change
  -> ChatResult.proposed_change {item_id, patch, verdict}
  -> client Apply -> POST /api/plans/items/{item_id}/changes
  -> _validated_change_options -> orch.propose_change -> endpoint db.commit()

Direct classification preview (not SQL-query-only)
POST /api/plans/items/{item_id}/classify
  -> savepoint = db.begin_nested()
  -> orch.propose_change(...)
  -> serialize Outcome
  -> finally: savepoint.rollback()

Direct submit / decision paths
POST /changes -> propose_change -> notice | round | reopen round | confirm -> commit
POST /updates/{notice}/object -> object_to_notice -> round -> commit
POST /rounds/{round}/votes -> cast_vote -> maybe settle_round -> commit
scheduler.run_once -> settle_due_rounds + expire_due_proposals -> one commit
POST /proposals/{proposal}/decisions -> decide_proposal -> maybe apply -> commit
```

**FACT.** The frontend sends chat at
[`trip/src/final/TripAppState.jsx:1027`](../../trip/src/final/TripAppState.jsx#L1027),
and submits the saved suggestion at
[`trip/src/final/plan-feature/useAssistantChangeRequestFlow.js:255`](../../trip/src/final/plan-feature/useAssistantChangeRequestFlow.js#L255).
The chat service creates a runtime request and a capability with a separate
`SessionLocal`, then calls the selected runtime
([`backend/app/domain/chat/service.py:158`](../../backend/app/domain/chat/service.py#L158)).
Both Legacy and Pydantic composition use read-only tool capability rather than a
request session ([`backend/app/agents/legacy_runtime.py:104`](../../backend/app/agents/legacy_runtime.py#L104),
[`backend/app/agents/pydantic_runtime.py:212`](../../backend/app/agents/pydantic_runtime.py#L212)).

**FACT.** A runtime suggestion is rechecked by `orch.classify_change()` before
forming `ProposedChatChange`; comments explicitly reject any fresh-state or
concurrency claim for the runtime output
([`backend/app/domain/chat/service.py:1160`](../../backend/app/domain/chat/service.py#L1160)).
The runtime default remains Legacy unless `CHAT_AGENT_RUNTIME=pydantic`
([`backend/app/agents/runtime_factory.py:34`](../../backend/app/agents/runtime_factory.py#L34)).

### 3.2 Classification and data ownership

**FACT.** The constraint engine is pure: it receives an `ItemView`,
`ProposedChange`, and constraints; it does not query a DB or invoke AI
([`backend/app/domain/constraints/engine.py:1`](../../backend/app/domain/constraints/engine.py#L1),
[`backend/app/domain/constraints/types.py:86`](../../backend/app/domain/constraints/types.py#L86)).
`orchestrator.propose_change()` loads canonical constraints and performs an explicit
same-plan schedule-conflict query before choosing the Notice, Round, Reopen, or
Confirm mutation path ([`backend/app/domain/decisions/orchestrator.py:437`](../../backend/app/domain/decisions/orchestrator.py#L437)).

**FACT.** A FastAPI request obtains a `SessionLocal` session but the dependency
does not itself issue a commit or rollback ([`backend/app/db/session.py:37`](../../backend/app/db/session.py#L37)).
Write routes own their explicit `db.commit()` calls, for example direct submit at
[`backend/app/api/main.py:1286`](../../backend/app/api/main.py#L1286); the
scheduler owns its own short session and commits once per `run_once()`
([`backend/app/jobs/scheduler.py:25`](../../backend/app/jobs/scheduler.py#L25)).

**INFERENCE.** An exception before an endpoint commit is normally rolled back when
the SQLAlchemy session context closes, but that behavior is implicit in the current
session-context lifecycle rather than a uniform application transaction boundary.
PR-05B should make the mutation boundary explicit and test it rather than relying on
this inference.

### 3.3 The `/classify` savepoint is not a normal read-only query

**FACT.** The chat route is read-only and uses `classify_change()`, but the direct
`/classify` route calls the mutating `propose_change()` inside a nested transaction
and always rolls the savepoint back
([`backend/app/api/main.py:854`](../../backend/app/api/main.py#L854),
[`backend/app/api/main.py:1263`](../../backend/app/api/main.py#L1263)).

For a Notice verdict, the savepoint can update `plan_item`, add a `PlanChange`, add
an `UpdateNotice`, and flush ([`backend/app/domain/decisions/orchestrator.py:588`](../../backend/app/domain/decisions/orchestrator.py#L588)).
For a Round it can insert `decision_round`, update item settledness, and flush
([`backend/app/domain/decisions/orchestrator.py:634`](../../backend/app/domain/decisions/orchestrator.py#L634)).
For Confirm it can insert a proposal and proposal decisions, flush both, and in the
single-affected-member case run the decision/apply code
([`backend/app/domain/decisions/orchestrator.py:792`](../../backend/app/domain/decisions/orchestrator.py#L792)).

**FACT.** The `finally` executes a savepoint rollback even after constructing the
returned outcome. There is no provider, AI call, HTTP call, commit, or explicit
external side effect in this route's source call chain. The database rows staged in
the savepoint are rolled back, so it creates no intended durable domain effect.

**INFERENCE.** It is accurate to call this a *durably non-mutating preview*, but
not accurate to call it a SQL-query-only or concurrency guarantee. It executes
`SELECT`, `INSERT`, `UPDATE`, and `flush` statements; temporary transactional/index
work and locks may occur and are released by rollback. It also remains a TOCTOU
preview because final apply occurs later in another transaction. The alternative
option validator uses a savepoint around the pure `classify_change()` instead
([`backend/app/api/main.py:994`](../../backend/app/api/main.py#L994)); it has no
corresponding intended write to roll back.

## 4. Complete Mutation Inventory

All line references below are current-main evidence. “Idempotency” means the
recommended future requirement, not a current capability. `P` means plan revision
must advance; `D` means decision-state/plan revision must advance; `—` means no
current-itinerary preview invalidation is required, although the operation may still
need its own retry contract.

| Entry point / caller | Service and tables modified | Current authorization and transaction owner | Current protection / future requirement |
| --- | --- | --- | --- |
| `POST /api/trips` | `trip`, `trip_membership`, `plan` via `create_trip` ([`main.py:1098`](../../backend/app/api/main.py#L1098), [`trips/service.py:104`](../../backend/app/domain/trips/service.py#L104)) | Account auth; HTTP route commits. | New aggregate starts at revision 0; client idempotency desirable for trip creation but outside the first itinerary contract. |
| `POST /api/plans/{plan}/items` | inserts `plan_item` ([`main.py:690`](../../backend/app/api/main.py#L690)) | Scoped plan/membership; route commits directly. | **P**, idempotency required; adjacent-items check is a race without aggregate lock. |
| `POST /api/trips/{trip}/plans/generate` | updates/creates `plan`, inserts `plan_item` and `plan_change` ([`generator.py:160`](../../backend/app/domain/plans/generator.py#L160), [`generator.py:1712`](../../backend/app/domain/plans/generator.py#L1712)) | Scoped trip + organizer check; route commit. | **P**, idempotency required. `_trip_has_items()` is a read-then-write guard, not a lock ([`generator.py:307`](../../backend/app/domain/plans/generator.py#L307)). |
| `POST /api/plans/items/{item}/classify` | In savepoint it may stage `plan_item`, `plan_change`, `update_notice`, `decision_round`, `change_proposal`, `proposal_decision`; rollback prevents durable rows. | Scoped item; endpoint owns nested rollback. | Preview returns no revision; must return server plan revision in PR-05B. No idempotency record: it is not a durable mutation. |
| `POST /api/plans/items/{item}/changes` | Notice: `plan_item`, `plan_change`, `update_notice`; Round: `decision_round`, possibly item; Confirm: `change_proposal`, `proposal_decision`, possibly immediate apply. | Scoped item; endpoint commits. | **P/D**, idempotency required; `propose_change` only pre-checks active work ([`orchestrator.py:361`](../../backend/app/domain/decisions/orchestrator.py#L361)). |
| `PATCH /api/plans/items/{item}/booking` | `plan_item`, `plan_change`, `update_notice` ([`main.py:1361`](../../backend/app/api/main.py#L1361), [`orchestrator.py:539`](../../backend/app/domain/decisions/orchestrator.py#L539)) | Scoped item; endpoint commits. | **P**, idempotency required: booking changes the first classifier rule. |
| `POST /api/updates/{notice}/object` | updates `update_notice`; inserts `decision_round`; may update `plan_item` ([`main.py:1378`](../../backend/app/api/main.py#L1378), [`orchestrator.py:614`](../../backend/app/domain/decisions/orchestrator.py#L614)) | Scoped notice; endpoint commits. | **D**, idempotency required; partial unique round index is a final, not graceful, concurrent defense. |
| `POST /api/rounds/{round}/votes` | inserts or updates `vote`; can also settle `decision_round`, `plan_item`, `plan_change`, `update_notice` ([`main.py:1396`](../../backend/app/api/main.py#L1396)) | Scoped round; endpoint commits. | Vote itself needs request idempotency; any state transition/settlement is **P/D**. `(round_id, membership_id)` only prevents two vote rows ([`models.py:387`](../../backend/app/db/models.py#L387)). |
| `POST /api/rounds/{round}/settle` and scheduler | updates `decision_round`, `plan_item`; inserts `plan_change`, `update_notice` ([`orchestrator.py:700`](../../backend/app/domain/decisions/orchestrator.py#L700), [`scheduler.py:25`](../../backend/app/jobs/scheduler.py#L25)) | Scoped route or system scheduler session. | **P/D**. Scheduler does not use a client key; it needs locked state transition / work claiming. |
| `POST /api/proposals/{proposal}/decisions` | updates `proposal_decision`, then possibly `change_proposal`, `plan_item`, `plan_change`, `update_notice` ([`main.py:1426`](../../backend/app/api/main.py#L1426), [`orchestrator.py:842`](../../backend/app/domain/decisions/orchestrator.py#L842)) | Scoped proposal; endpoint commits. | Decision write needs idempotency; terminal/apply transition is **P/D**. |
| Scheduler proposal expiry | updates `change_proposal`, adds `update_notice` ([`orchestrator.py:743`](../../backend/app/domain/decisions/orchestrator.py#L743)) | Scheduler `SessionLocal`, one commit. | **D**, use locked recheck; no client idempotency key. |
| Organizer reminder / extend / escalate / deadlock | reminder adds `update_notice`; extension changes `decision_round` + notice; escalation/deadlock update `change_proposal`, and deadlock can change `plan_item` + append log/notice ([`organizer.py:71`](../../backend/app/domain/decisions/organizer.py#L71), [`organizer.py:115`](../../backend/app/domain/decisions/organizer.py#L115), [`organizer.py:151`](../../backend/app/domain/decisions/organizer.py#L151), [`organizer.py:165`](../../backend/app/domain/decisions/organizer.py#L165)) | Scoped resource plus organizer checks where required; endpoint commits. | Reminder **—** but idempotency desirable; extend/escalate **D**; deadlock **P/D**. |
| Preferences and constraints | `preference`, `trip_membership.status`; `member_constraint`, private row; marks `plan.needs_refresh` ([`preferences/service.py:157`](../../backend/app/domain/preferences/service.py#L157), [`preferences/service.py:354`](../../backend/app/domain/preferences/service.py#L354), [`preferences/service.py:426`](../../backend/app/domain/preferences/service.py#L426)) | Scoped membership; endpoint commits. | Required constraints are **P** because classification reads them. Conservatively treat preference changes as **P** too in initial scope: they are planning authority and trigger refresh, avoiding an ambiguous second version. |
| Membership / invite changes | insert/update `trip_membership`, `invite_link`, guest session; removal marks membership removed ([`trips/service.py:243`](../../backend/app/domain/trips/service.py#L243), [`trips/service.py:309`](../../backend/app/domain/trips/service.py#L309), [`trips/service.py:386`](../../backend/app/domain/trips/service.py#L386)) | Organizer/scoped rules vary; endpoint commits. | Member electorate affects rounds/proposals. Advance **D** for active membership state change after product decision; creation/revocation retry semantics are separate from core preview apply. |
| Comments | inserts `plan_item_comment` ([`main.py:1339`](../../backend/app/api/main.py#L1339)) | Scoped item; endpoint commits. | **—** for classification; user-visible duplicate prevention is a separate idempotency extension. |
| Administrative/demo scripts | seed, reset/clear, evaluation fixtures can insert/delete plan tables; they are not HTTP product paths. | Operator-owned scripts, not audited as production mutation API. | Must remain out of normal PR-05B runtime contract and never run against unknown data. Search evidence: [`backend/app/db/clear_plan.py:37`](../../backend/app/db/clear_plan.py#L37), [`backend/app/db/reset_demo.py:38`](../../backend/app/db/reset_demo.py#L38). |

**FACT.** `PlanChange` is append-only by schema intent but has no source-operation
uniqueness key ([`backend/app/db/models.py:331`](../../backend/app/db/models.py#L331)).
`UpdateNotice` also has no uniqueness tuple for a business event
([`backend/app/db/models.py:447`](../../backend/app/db/models.py#L447)). They
therefore need transaction/idempotency control at the mutation operation, not an
ad-hoc “dedupe notices by text” rule.

## 5. Existing Safety Guarantees

| Status | Protection and evidence | Boundary of the claim |
| --- | --- | --- |
| **Implemented** | Membership/trip scoped resource helpers are used before plan/item/round/proposal mutations ([`backend/app/api/main.py:927`](../../backend/app/api/main.py#L927)). | Authorization scope does not serialize writes. |
| **Implemented** | Database uniqueness: one open round per item, one waiting proposal per item, one vote per member/round, one decision per member/proposal ([`models.py:358`](../../backend/app/db/models.py#L358), [`models.py:387`](../../backend/app/db/models.py#L387), [`models.py:402`](../../backend/app/db/models.py#L402), [`models.py:432`](../../backend/app/db/models.py#L432)). | It does not cover stale previews, `PlanChange`, notices, escalated proposals, or all multi-statement flows. |
| **Implemented** | `_guard_not_pending` gives an application-level polite refusal before the partial indexes ([`orchestrator.py:361`](../../backend/app/domain/decisions/orchestrator.py#L361)). | Two transactions can both pass a non-locking pre-check. |
| **Implemented** | Direct submit reclassifies from the session’s current reads and checks schedule peers ([`orchestrator.py:453`](../../backend/app/domain/decisions/orchestrator.py#L453)). | Deterministic reclassification is not a locked fresh snapshot or CAS. |
| **Implemented** | Runtime has a read-only capability with a worker-owned short-lived session ([`legacy_runtime.py:63`](../../backend/app/agents/legacy_runtime.py#L63)). | It protects Agent authority/lifecycle, not apply concurrency. |
| **Implemented, unrelated** | Trip cover fetching locks the `Trip` row ([`cover_service.py:37`](../../backend/app/domain/trips/cover_service.py#L37)). | No current decision/plan mutation acquires that lock, so it is not itinerary protection. |
| **Tested sequentially** | Paths tests prove repeated `settle_round` in one session leaves one ledger count unchanged ([`backend/tests/test_paths.py:140`](../../backend/tests/test_paths.py#L140)) and prove sequential busy-slot refusal ([`backend/tests/test_paths.py:370`](../../backend/tests/test_paths.py#L370)). | These tests do not use two independent database sessions/connections. |
| **Tested for access** | API tests prove a foreign item cannot be submitted and check expected single-session persisted counts ([`backend/tests/test_trips.py:709`](../../backend/tests/test_trips.py#L709)). | This is access control, not concurrent duplicate proof. |
| **Tested statically** | Alembic contract tests fix the baseline root/table/index declarations without opening a database ([`backend/tests/test_alembic_baseline_contract.py:51`](../../backend/tests/test_alembic_baseline_contract.py#L51)). | No current test proves a PR-05B upgrade or live schema. |
| **Missing** | Plan/item revisions, expected revision API field, idempotency ledger/key/fingerprint/outcome replay, lock ordering, and Postgres multi-session race tests. | Directly absent from models, request DTOs, and the search inventory. |
| **Unverified** | Actual DB isolation setting, live schema, number of scheduler instances, and production duplicate/stale incidents. | `create_engine` does not set an isolation level ([`session.py:37`](../../backend/app/db/session.py#L37)); do not infer a deployed DB setting from that. |

## 6. Concurrency Threat Model

No production race is asserted below. “Confirmed” means source establishes the
interleaving can be attempted and lacks a shown synchronizer; “potential” means it
requires a controlled two-session reproduction.

| Scenario | Current interleaving and consequence | Current protection | Classification | Proposed protection and deterministic test |
| --- | --- | --- | --- | --- |
| Same item, two Notice submits | T1/T2 read loose item; both classify Notice; both update fields/log/notice; last item value can win while two durable audit effects remain. | None beyond normal transaction atomicity. | **Code-supported potential.** | Plan lock + expected revision + operation ledger. Two independent sessions/barrier: exactly one 2xx, one `409 stale_revision`; one log/notice. |
| Different items become overlapping | T1/T2 each query peers before the other commits, then both move different items into one time block. | `_schedule_conflict_item` is a non-locking read ([`orchestrator.py:402`](../../backend/app/domain/decisions/orchestrator.py#L402)). | **Code-supported potential.** | Same aggregate plan lock; re-read all peer items after lock. Barrier test proves second request sees conflict/reclassifies or stale-rejects. |
| Preview before booking | Preview says Notice; another request changes `settledness` to `booked`; old apply can be classified on a racey read rather than guaranteed Confirm. | Booking appends a log/notice but no version ([`orchestrator.py:553`](../../backend/app/domain/decisions/orchestrator.py#L553)). | **Code-supported potential.** | Advance plan revision for booking; stale preview returns `409 PREVIEW_STALE`; test uses separate sessions. |
| Preview before hard constraint change | Preview reads constraints; constraint add/update/delete crosses the read/decision window. | Current submit reclassifies, but neither side locks a shared aggregate. | **Code-supported potential.** | All constraint and apply writes lock/increment the plan aggregate; test create/update/delete cases. |
| Preview before plan generation/regeneration | Existing item/no-item observation can become outdated while generator writes items/log entries. | `_trip_has_items()` is read-then-write; plan has no `trip_id` uniqueness ([`generator.py:307`](../../backend/app/domain/plans/generator.py#L307), [`models.py:233`](../../backend/app/db/models.py#L233)). | **Code-supported potential.** | Lock existing Plan; when absent, lock Trip before create and require a separately preflighted one-plan-per-trip invariant. Test two generators and stale pre-generation preview. |
| Competing round/proposal creation | Both pass `_guard_not_pending`; inserts race at a partial index. | Partial unique index can reject an insert. | **Confirmed gap in graceful handling; potential duplicate effect only where no matching constraint applies.** | Lock plan/item, recheck; translate residual `IntegrityError` to defined conflict. Test exactly one queue row and no 500. |
| Concurrent votes / acceptances | Read-update/insert vote can conflict; two final decisions or manual settle can both observe a nonterminal state. | Vote and decision uniqueness protect one row each; not terminal transition. | **Code-supported potential.** | Lock plan -> item -> round/proposal, re-read status and decisions. Test concurrent last votes and last approvals with exactly one terminal log/notice. |
| Two settlement workers | Both select an `open` due round; each can run `settle_round`, whose idempotence check is an in-memory status read ([`orchestrator.py:700`](../../backend/app/domain/decisions/orchestrator.py#L700)). | Sequential test only; scheduler has no lease/claim ([`scheduler.py:25`](../../backend/app/jobs/scheduler.py#L25)). | **Code-supported potential.** | Ordered plan work claim with `FOR UPDATE SKIP LOCKED`, then locked status recheck. Test two scheduler sessions/events; one visible settlement. |
| Deadline extension/deadlock vs scheduler | Organizer reads open/escalated row while scheduler closes/expires it; both can append notices or mutate an item. | Application state checks only. | **Code-supported potential.** | Same ordered locks and terminal-state recheck. Test no duplicate `UpdateNotice` / contradictory status. |
| Lost response then retry | Commit succeeds but response is lost; retry repeats `POST /changes` and can emit another change/round/proposal/notice. | No Idempotency-Key input or ledger. | **Confirmed missing contract.** | Durable ledger written in same commit; retry returns stored safe result. Test deliberately discard first HTTP response after commit. |
| Same key concurrently | Not applicable yet: no key is accepted. | None. | **Missing.** | Unique `(trip, actor, key)`, conflict-safe insert, locked replay. Barrier test: one execution, one replay, same outcome. |
| Flush/commit failure after staged effects | Domain methods flush multiple rows; endpoint commits later. | One normal session transaction is likely atomic, but no uniform explicit rollback/error outcome contract. | **Unverified without fault injection.** | Explicit `with db.begin()` boundary and ledger in same transaction; inject failing flush/commit and prove no ledger/effects orphan. |

## 7. Revision Strategy Comparison

| Alternative | Correctness against current semantics | Cost / false conflicts | Assessment |
| --- | --- | --- | --- |
| A. Plan-level monotonic revision | Covers same-item updates, cross-item schedule queries, booked state, constraint reads, generated item set, plan totals, rounds/proposals that change whether work is pending. | Serializes otherwise independent edits in one trip. Requires one row update per covered mutation. | **Recommended initial consistency model.** Current classifier reads plan-wide peers and trip-wide constraints, so its actual read set is aggregate-wide. |
| B. PlanItem-level revision only | Protects one item’s direct fields. | Misses peer schedule conflicts, group constraints, plan generation, and changes to a different item that alter the read set. | **Rejected as the only token.** It is incorrect for current `select(PlanItem).where(plan_id…)` conflict checking. |
| C. Hybrid Plan + PlanItem revisions | Can later allow fine-grained UI caching and item-local write feedback while a plan revision guards aggregate reads. | Two invariants, more API fields/tests/migrations, easy to check the wrong version. | **Deferred.** It may be justified after measurement, not before correctness proof. |
| D. Separate scoped versions (content, constraints, membership, decision state) | Could reduce false conflicts by validating exact dependency sets. | Current code has no declared dependency graph; keeping scope complete across all routes is high risk. | **Deferred.** A “small” token list that misses one route weakens safety silently. |

### Recommended ownership and increment rules

**PROPOSAL.** `Plan.revision: BIGINT NOT NULL` is owned only by the application/domain
mutation kernel, never by an LLM, frontend, ORM caller, or `PlanChange` trigger. It
starts at `0` for existing and new plans and increments exactly once per successful
transaction that changes the current plan aggregate or a decision/classification
input.

Advance it once for:

1. any `PlanItem` insert/update/remove, booking marker, generated item set, plan
   status/total/current-plan replacement, or real PlanChange-producing outcome;
2. create/open/close/expire/escalate/resolve of a round or proposal when it changes
   the item's pending/decision state or can change an apply result;
3. `MemberConstraint` add/update/delete and, conservatively in the first safe
   release, preference/membership state changes that alter planning authority or
   electorate; and
4. any path that changes the facts read by `propose_change()` or changes whether a
   preview may proceed.

Do **not** increment solely for a comment, reminder notice, or immutable audit row
that does not alter those facts. A vote/individual acceptance that leaves the
decision nonterminal can be represented without an increment, but the terminal
round/proposal transition must increment. This distinction must be encoded in a
single mutation-policy function and tested, not copied into routes.

**PROPOSAL.** A plan identity never changes; regeneration changes its revision. If
legacy data can lack a Plan, lock the Trip row while finding/creating it. The current
model does not declare `Plan.trip_id` unique, so adding that invariant requires
duplicate-data preflight and must be an explicit migration decision, not an
unexamined side effect of PR-05B.

## 8. Preview and API Contract Proposal

### 8.1 Trust boundary

**FACT.** Current chat response serializes only item ID/title, patch, and
classification ([`backend/app/api/main.py:900`](../../backend/app/api/main.py#L900));
current frontend passes that patch later without a revision or token
([`trip/src/final/TripAppState.jsx:1069`](../../trip/src/final/TripAppState.jsx#L1069)).

**PROPOSAL.** The server, after membership/trip authorization and canonical read,
creates this envelope. Model output is merely syntax that may help choose the patch;
the model never sees or returns trusted concurrency metadata.

```json
{
  "preview": {
    "plan_id": "pln_123",
    "item_id": "item_456",
    "expected_plan_revision": 42,
    "patch": {"start_hour": 15.5},
    "classification": {"path": "notice", "checks": [], "findings": []},
    "generated_at": "2026-10-08T20:15:00Z",
    "expires_at": null
  }
}
```

`plan_id`, item identity, revision, normalized/allowed patch, and classification
are server-generated. `generated_at` is informational only. The revision is the
freshness precondition; no expiry is required for correctness, though a future
product timeout may add one for user experience.

**PROPOSAL.** Do not add a signed or opaque `preview_id` in the first implementation.
It cannot replace transaction-time authorization, revision checking, and
reclassification. If later needed for UX analytics, it must be server-issued, actor
and trip bound, short lived, non-authoritative, and must still be checked alongside
the revision; it must not become an LLM-controlled capability.

### 8.2 Apply request and outcomes

```http
POST /api/plans/items/item_456/changes
Idempotency-Key: 4fe4c75e-1a82-4e4f-a6ca-6c6ee0d5e987
Content-Type: application/json

{
  "expected_plan_revision": 42,
  "start_hour": 15.5,
  "request": "Move it to 3:30 PM",
  "reason": null,
  "options": []
}
```

```json
{
  "outcome": {"path": "notice", "applied": true},
  "plan_id": "pln_123",
  "plan_revision": 43,
  "replayed": false
}
```

Stale preview response:

```json
{
  "code": "PREVIEW_STALE",
  "message": "The plan changed after this preview. Refresh and review it again.",
  "plan_id": "pln_123",
  "expected_plan_revision": 42,
  "current_plan_revision": 43,
  "refresh_required": true
}
```

Use `409 Conflict` for stale revision, same-key/different-fingerprint, and a
resource that is terminal/pending after locked recheck. Use `422` only for a
well-formed request invalid under stable business validation (for example missing
reopen reason); use `401/403/404` as current auth/scope policy dictates. A successful
same-key replay returns the original success status and semantically identical safe
outcome with `replayed: true`; it does not run the domain mutation again.

**PROPOSAL.** Direct non-AI edits use exactly the same expected-revision and
idempotency contract. The chat preview is not a special write authority; it is one
way to obtain an intent. A direct `/classify` response also receives a server plan
revision, while its savepoint semantics remain unchanged until a dedicated refactor
is separately approved.

## 9. Transaction and Concurrency Strategy

### 9.1 Alternatives

| Technique | Strength | Why insufficient alone / role in recommendation |
| --- | --- | --- |
| Conditional `UPDATE … WHERE revision = :expected` (CAS) | Detects a stale aggregate atomically. | Alone it does not protect earlier peer/constraint reads or coordinate multi-row decision effects. It is a useful defensive assertion/alternative implementation, not the whole protocol. |
| `SELECT … FOR UPDATE` | Serializes mutations that share the aggregate and lets the service re-read fresh facts before classification. | It intentionally serializes all one-trip mutations and must never wrap a provider/LLM call. **Primary mechanism.** |
| Aggregate-level locking | Matches current aggregate-wide schedule/constraint reads. | Less concurrent than item locks; appropriate until dependency scopes are explicit and measured. **Recommended scope: current Plan.** |
| Serializable isolation + retry | Can detect anomalies across broad read sets. | Higher retry/deadlock complexity; does not by itself give duplicate-response semantics. Not required for initial controlled aggregate locking. |

### 9.2 Required sequence

**PROPOSAL.** After authentication, any network/provider/LLM work completes before
the short write transaction. Then perform only local database/domain work:

```text
authorize and resolve route scope (no mutation)
-> canonicalize request and calculate private-safe fingerprint
-> BEGIN
-> insert-or-lock idempotency row for a client mutation
-> if completed: compare fingerprint, replay response or 409 key reuse
-> lock Plan row FOR UPDATE; create path locks Trip first if Plan absent
-> compare expected_plan_revision
-> lock relevant PlanItem rows in stable ID order, then round/proposal rows
-> re-read authoritative item, peers, constraints, membership and decision state
-> deterministic reclassification and domain mutation
-> increment Plan.revision once
-> write safe completed idempotency outcome
-> COMMIT
-> return saved/reconstructed result
```

The idempotency first-insert must be conflict-safe (for example Postgres `INSERT …
ON CONFLICT DO NOTHING`, followed by a `SELECT … FOR UPDATE`). For a concurrent same
key, the unique index makes the later transaction wait until the earlier transaction
commits or rolls back; it then replays the committed result or starts the operation
after rollback. The row must not be separately committed before the domain change.

**PROPOSAL.** Global lock order is:

```text
IdempotencyLedger (client mutation only)
-> Plan (or Trip first only when creating a missing Plan)
-> PlanItem(s sorted by stable ID)
-> DecisionRound(s sorted by stable ID)
-> ChangeProposal(s sorted by stable ID)
-> ProposalDecision(s sorted by membership ID)
-> UpdateNotice when the endpoint specifically changes it
```

For scheduler work, read candidate plan IDs without locking, lock Plans in sorted
order using `FOR UPDATE SKIP LOCKED`, then query/recheck due rounds/proposals only
inside those locked aggregates. This prevents two ECS processes from treating a
non-locking due-row scan as a lease. It preserves the same Plan-first order.

**PROPOSAL.** Under the plan lock, a stale expected revision is rejected — not
silently reclassified and applied — because the user approved the displayed preview,
not an unshown new path. A *new direct request* at the current revision is
reclassified within the transaction. Deadlock/serialization errors receive bounded,
server-side retry only where the operation is safe to repeat under the same ledger
record; otherwise return a retriable `409/503` with no durable partial outcome. No
retry may repeat a provider/LLM request while holding a DB lock.

## 10. Idempotency Design

### 10.1 Proposed durable ledger

```text
mutation_idempotency
  id                         primary key
  trip_id                    FK trip, not null
  actor_membership_id        FK trip_membership, not null
  idempotency_key            varchar(128), not null
  operation                  varchar(80), not null
  resource_kind/resource_id  varchar, not null
  request_fingerprint        char(64), not null
  response_status            smallint, nullable until completed
  outcome_json               JSON, nullable until completed; safe fields only
  resulting_plan_revision    bigint, nullable
  created_at/completed_at/expires_at  timestamptz
  UNIQUE(trip_id, actor_membership_id, idempotency_key)
  INDEX(expires_at)
```

**PROPOSAL.** The uniqueness scope intentionally does *not* include operation or
resource. Reusing a client key for another endpoint/resource in one actor/trip scope
is a conflict, not permission to execute two unrelated writes. `operation` and
resource columns make the conflict explainable and auditable. The same key in another
trip or from another membership is independent; authorization is rechecked before
any response replay.

The server canonicalizes semantic request data (operation, route identity, normalized
patch/options/decision and reason) and stores a keyed HMAC-SHA-256 fingerprint plus a
key version, not raw private wording. The design must include all fields that can
change the result; excluding `reason` or options would make same-key/different-request
unsafe. The replay outcome stores only route-safe response data and referenced IDs,
never raw constraints, authorization headers, model/provider payloads, or private
preference wording.

### 10.2 Semantics

| Situation | Required behavior |
| --- | --- |
| First request succeeds | Effects, new revision, and completed ledger result commit atomically. |
| Same key, same payload, sequential retry | Authorize, lock/read ledger, return stored outcome; no new `PlanChange`, notice, queue row, or vote. |
| Same key, same payload, concurrent requests | One transaction executes; the other waits on the unique/ledger lock and replays its outcome. |
| Same key, different payload/operation/resource | `409 IDEMPOTENCY_KEY_REUSED`; do not disclose another resource’s result. |
| Different key, same payload | It is a new logical attempt; expected revision / domain rules decide. It is not automatically deduplicated. |
| Same key in other trip or actor | Independent ledger scope; current authorization still required. |
| Exception or DB rollback before commit | Effects and ledger row roll back together; safe retry is first execution. |
| Commit succeeds, response lost/client timeout | Retry with same key replays committed response. This is the principal use case. |
| Retry while original is in flight | Wait/replay after commit or execute only if original rolls back; no process-local cache is relied upon. |
| Retention expiry | A key after expiry is a new attempt; client retry window must be documented. Start with a conservative policy such as 24–72 hours only after product/operations approval, not an invented SLA. |

**PROPOSAL.** This provides *durable at-most-once committed database business effects
per retained idempotency scope plus replayable result*. It does not claim distributed
exactly-once external side effects. Existing database natural uniqueness (one vote,
open round, waiting proposal) remains a separate final invariant. Scheduler
deduplication remains a locked terminal-state/work-claim problem, not a browser
idempotency-key problem.

## 11. Migration and Schema Drift

### 11.1 Current graph and drift facts

**FACT.** The current Alembic root declares `revision='f9ff23b7b84d'` and no parent;
the guest-session migration declares `revision='54e8dd1ca624'` with that root as
`down_revision` ([`f9ff23b7b84d_baseline_existing_schema.py:17`](../../backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py#L17),
[`54e8dd1ca624_add_bounded_guest_sessions.py:17`](../../backend/alembic/versions/54e8dd1ca624_add_bounded_guest_sessions.py#L17)).
Alembic uses application `DATABASE_URL` and `Base.metadata`
([`backend/alembic/env.py:19`](../../backend/alembic/env.py#L19)).

**FACT.** `DecisionRound.extended_at` exists in ORM metadata and in the baseline
([`backend/app/db/models.py:358`](../../backend/app/db/models.py#L358),
[`backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py:195`](../../backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py#L195)).
`ChangeProposal` has no `extended_at` ORM column and its baseline table does not
declare one ([`backend/app/db/models.py:402`](../../backend/app/db/models.py#L402),
[`backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py:179`](../../backend/alembic/versions/f9ff23b7b84d_baseline_existing_schema.py#L179)).

**FACT / discrepancy.** The historical PR-05A report records that an older
`ensure_cloud_schema()` added `change_proposal.extended_at`
([`11_PR05A_ALEMBIC_BASELINE.md:74`](../PR-05A_Alembic-Baseline/11_PR05A_ALEMBIC_BASELINE.md#L74)). Current
source now explicitly says it no longer applies ad-hoc DDL and leaves any legacy
column untouched ([`backend/app/db/upsert_demo_login.py:28`](../../backend/app/db/upsert_demo_login.py#L28)); current `init_schema` also does not add that column
([`backend/app/db/init_schema.py:41`](../../backend/app/db/init_schema.py#L41)).
The historical observation is therefore still relevant to a database touched by the
old helper, but its source-code attribution is stale for current main.

**UNVERIFIED.** No live database was queried. This audit cannot establish whether a
particular database has `change_proposal.extended_at`, whether it contains data, or
whether it is stamped. Do not combine its reconciliation with revision/idempotency.

### 11.2 Non-destructive migration design

**PROPOSAL.** A reviewed additive migration after `54e8dd1ca624` should:

1. add `plan.revision` with server default `0`, backfill existing rows as `0`, then
   make it non-null; new Plans also initialize at `0`;
2. add the new ledger/table/indexes without editing accepted baseline revisions;
3. avoid automatic drop/rename/reconciliation of any unknown legacy column;
4. verify fresh `upgrade head`, ORM/Alembic drift, and an upgrade from a disposable
   baseline-shaped database; and
5. keep a separate explicit drift preflight for existing databases before any stamp
   or upgrade operation.

The new ledger is empty, so its unique/index creation should be low risk. Any future
unique `plan.trip_id` constraint needs a preflight report for duplicate legacy Plan
rows; do not autogenerate it blindly. The migration must be forward-compatible with
old binaries, but the *feature* cannot be enabled until every writer that can alter a
covered aggregate increments revision. A mixed old/new fleet would otherwise create
unobserved writes; use a controlled deployment gate/drain, not a silent optional
revision field.

After clients depend on ledger/revision data, prefer forward-fix or approved restore
over dropping the columns/table in a rollback. Baseline downgrade is explicitly
destructive and only tested for fresh disposable DBs in the historical runbook
([`11_PR05A_ALEMBIC_BASELINE.md:142`](../PR-05A_Alembic-Baseline/11_PR05A_ALEMBIC_BASELINE.md#L142)).

## 12. Frontend Compatibility

**FACT.** The current plan response includes `plan_id` but no revision
([`backend/app/api/main.py:389`](../../backend/app/api/main.py#L389)). `requestJson`
delegates to technical session handling, which supplies authorization/trip identity
headers only ([`trip/src/final/TripAppState.jsx:461`](../../trip/src/final/TripAppState.jsx#L461),
[`shared/session-runtime/index.js:319`](../../shared/session-runtime/index.js#L319)).
Current calls for classify, booking, manual add, generate, submit, vote, and proposal
decision send no revision or `Idempotency-Key`
([`TripAppState.jsx:782`](../../trip/src/final/TripAppState.jsx#L782),
[`TripAppState.jsx:916`](../../trip/src/final/TripAppState.jsx#L916),
[`TripAppState.jsx:936`](../../trip/src/final/TripAppState.jsx#L936),
[`TripAppState.jsx:962`](../../trip/src/final/TripAppState.jsx#L962),
[`TripAppState.jsx:1069`](../../trip/src/final/TripAppState.jsx#L1069),
[`TripAppState.jsx:1123`](../../trip/src/final/TripAppState.jsx#L1123),
[`TripAppState.jsx:1147`](../../trip/src/final/TripAppState.jsx#L1147)).

**PROPOSAL.** Introduce versioned response fields additively first, then require
`expected_plan_revision` and `Idempotency-Key` for every covered write in a
coordinated client/server release. Do not retain an unprotected legacy path under the
same endpoint after rollout merely for convenience. If staged rollout is necessary,
use an explicit versioned mutation endpoint or capability/version negotiation that
fails old mutation clients visibly; the old endpoint must not masquerade as
concurrency-safe.

Frontend behavior:

| Server result | Client behavior |
| --- | --- |
| `409 PREVIEW_STALE` | Discard local preview card, refresh Plan/actions, show that the user must review a new preview. Never auto-resubmit. |
| Same-key replay success | Treat as successful completion; refresh using returned plan revision, avoid a second optimistic UI append. |
| `409 IDEMPOTENCY_KEY_REUSED` | Treat as client/programming error, mint a key only for a new intentional action after user review. |
| In-progress identical request | Keep UI action pending or retry same key after transport recovery; do not mint a new key. |
| Round/proposal created | Use returned IDs/outcome and refresh actions; do not synthesize another queue object client-side. |
| Auth/scope failure | Clear/refresh technical session according to existing session-runtime policy; never replay another actor’s ledger result. |

## 13. Deterministic Test Matrix

No DB-backed test was run in this audit. The repository test fixture recreates its
test database ([`backend/tests/conftest.py:57`](../../backend/tests/conftest.py#L57)),
so execution is prohibited until a distinct localhost, test-only `TEST_DATABASE_URL`
is explicitly verified.

| Class | Required cases |
| --- | --- |
| A. Pure unit | Canonical fingerprint normalization (including key reuse mismatch); safe response serialization; revision increment-policy table; no model-owned token; constraint/classifier deterministic behavior remains unchanged. |
| B. API integration | Successful preview/apply; stale after another edit; stale after booking/constraint/regeneration; old-client/version handling; 401/403/cross-trip/cross-membership denial; replay after simulated response loss. |
| C. PostgreSQL transaction/race | Independent `SessionLocal` sessions/connections and events/barriers — never `sleep` or SQLite — for same revision race, same item, different-item overlap, competing queue creation, concurrent identical keys, last vote/settlement, last proposal acceptance, expiry/extend/deadlock race, and rollback with no orphan ledger/log/notice/proposal/round. |
| D. Migration | Fresh Alembic upgrade/check; disposable baseline-to-head upgrade; revision default/backfill; uniqueness/retention index; separate drift fixture showing unknown `change_proposal.extended_at` is detected and not dropped. |
| E. CI gates | New Postgres race suite; existing domain/auth/agent tests; legacy and Pydantic isolated checks remain isolated; frozen PR-04 dataset/harness/graders unchanged and checksum-protected. |

Each race test must assert the durable result, not merely an HTTP code: one
`PlanChange`/`UpdateNotice` for one logical operation; at most one queue object where
the invariant requires it; correct revision delta; no lost schedule constraint;
replayed response equality; and no Agent write capability escalation. Use explicit
barriers after first read/after lock acquisition to force the intended interleaving.

## 14. PR-05B Implementation Breakdown

### PR-05B.1 — Aggregate revision and ledger foundation

- **Goal:** approve a single mutation-kernel interface, add additive schema/model
  support and migration verification only.
- **Dependencies:** final decision on stale-reject policy, retention policy, and
  no-drift disposable DB setup.
- **Likely files:** `backend/app/db/models.py`, a new Alembic revision,
  `backend/app/db/session.py` only if a transaction helper is justified, focused
  test/migration docs.
- **DB change:** `plan.revision`, `mutation_idempotency`, indexes.
- **Explicitly unchanged:** runtime choice/tools, classifier policy, frontend,
  frozen evaluators, deployment.
- **Acceptance:** upgrade/check on isolated Postgres; schema contract tests;
  no legacy drift auto-repair; one transaction kernel unit test.
- **Risk / stop:** stop if migration preflight reveals multiple Plans per trip or
  unresolved `change_proposal.extended_at` ownership. Forward-fix rather than a
  destructive post-use downgrade.

### PR-05B.2 — Safe preview and direct apply contract

- **Goal:** return server plan revision in direct/chat previews; protect `/changes`
  with the locked re-read/reclassify/revision/idempotency transaction.
- **Dependencies:** B.1 and an approved error/JSON contract.
- **Likely files:** API DTO/serialization and direct-submit composition,
  orchestrator mutation kernel, chat service response shaping, focused API/race
  tests.
- **DB change:** none beyond B.1.
- **Explicitly unchanged:** LLM tool authority, default runtime, decision path
  rules, evaluator artifacts.
- **Acceptance:** old preview rejects 409 after edit/booking/constraint; same-key
  sequential/concurrent replay creates one durable effect; no lock spans AI/provider.
- **Risk / stop:** do not enable if any writer can bypass the new revision policy.

### PR-05B.3 — Complete decision and plan mutation coverage

- **Goal:** route manual add, generation, booking, notice objection, vote, settle,
  proposal decision/expiry, organizer actions, constraints/preferences, and
  scheduler through the aggregate/operation policies.
- **Dependencies:** B.2 kernel and a route-by-route policy matrix approved from
  section 4.
- **Likely files:** `main.py`, orchestrator, organizer, generator, preferences,
  scheduler, trip/membership policy where electorate changes, integration tests.
- **DB change:** none expected unless one-plan-per-trip preflight justifies a
  separate constraint migration.
- **Explicitly unchanged:** business decision classifications and Agent tools.
- **Acceptance:** two-worker settlement test, no duplicate notice/log/proposal/round,
  scheduling race proof, all covered rows advance revision exactly once per logical
  state transition.
- **Risk / stop:** stop rather than invent policy for membership/electorate snapshots
  or legacy multiple Plan rows.

### PR-05B.4 — Frontend transition and complete regression evidence

- **Goal:** client keeps a plan revision, creates/reuses Idempotency-Key per user
  intent, handles stale/replay/in-flight results, and removes protected legacy
  mutation route use.
- **Dependencies:** stable B.2/B.3 API and user-experience decision for stale card.
- **Likely files:** `trip/src/final/TripAppState.jsx`, assistant flow, API/client
  contract tests, docs/CI only as required.
- **DB change:** none.
- **Explicitly unchanged:** embedded build unless a separately authorized UI release
  requires it; no new agent framework/provider/telemetry/deployment work.
- **Acceptance:** browser/API compatibility tests and the entire deterministic
  Postgres matrix; frozen PR-04 checks pass unchanged.
- **Risk / stop:** no release that claims concurrency safety while an old client
  remains silently allowed to mutate without expected revision/key.

## 15. Unresolved Decisions / Blockers

1. **Product decision required:** use the recommended stale rejection rather than
   automatic reclassification/apply. The design recommends reject because a changed
   path can require Round/Confirm the user never reviewed.
2. **Live schema required:** inspect each real target before stamp/upgrade. The old
   `change_proposal.extended_at` drift is unresolved for affected databases.
3. **Plan cardinality required:** establish whether legacy data can have more than
   one `Plan` per trip before adding a uniqueness invariant or relying on Plan locking
   when a plan is absent.
4. **Retention/operations required:** choose ledger retention and HMAC-key rotation
   policy with operational owners; no performance/retention duration is asserted here.
5. **Rollout required:** all aggregate writers need the revision policy before
   feature activation; mixed old/new writers are unsafe.
6. **Database evidence required:** run the listed two-connection PostgreSQL tests on
   a verified disposable local DB. SQLite, a single session, and sleep-driven tests
   do not discharge this evidence.

These are not reasons to implement speculative code. They are reasons for the final
verdict to remain conditional until independent design review resolves them.

## 16. Final Architecture Recommendation

**Preferred design:** one `Plan` aggregate revision + short Plan-row locking +
transactional fresh deterministic reclassification + durable, scoped idempotency
ledger. This aligns with the actual classifier, which reads same-plan peer items and
trip-wide constraints, and preserves the existing policy that humans decide through
Notice/Round/Confirm while the application/domain/database own final authority.

**Rejected now:** item-only revisions (miss aggregate reads), token-only/snapshot-hash
designs (cannot ensure transactional freshness), uniqueness-only designs (cannot
deduplicate logs/notices or response-loss retries), process-local cache/Redis-only
idempotency (not durable across ECS/process restart), and serializable isolation as
the only mechanism (does not define replay semantics).

**Confidence:** high that the present source lacks revision/idempotency/aggregate
locking and that the recommended Plan scope covers current cross-item and constraint
reads. Medium on route-complete increment policy until membership semantics and
generation/no-plan data are reviewed. No confidence claim is made about any live
database or production concurrency behavior.

### Secondary engineering references

These references informed the interview-quality framing—deterministic authority,
least-privilege tools, retries/failure injection, and explicit trade-offs—rather than
being copied as an implementation template:

- [Agent Interview 100](https://github.com/BigKunLun/Agent-Interview-100)
- [AI Agent Interview Guide](https://github.com/bcefghj/ai-agent-interview-guide)
- [LLM Agent Interview Guide](https://github.com/Lau-Jonathan/LLM-Agent-Interview-Guide)
- [AI Engineer Interview Handbook](https://github.com/bicced/ai-engineer-interview-handbook)
- [100 Agentic AI Interview Questions](https://github.com/Navedphysicist/100-Agentic-AI-Interview-Questions)
- [System Design Primer](https://github.com/donnemartin/system-design-primer)

## 17. Next Implementation Prompt Outline

**Scope of first implementation step:** PR-05B.1 only: implement the reviewed
additive schema/migration and an internal Plan mutation transaction contract. Do not
change runtime selection, agent tool permissions, domain classification rules,
frontend behavior, provider use, production configuration, or frozen evaluation
artifacts.

**Required preconditions:** clean dedicated worktree; verified local disposable
PostgreSQL URL distinct from runtime DB; a written decision on stale rejection and
ledger retention; migration drift preflight plan; route coverage table accepted;
`git status` check before edits.

**Acceptance criteria:** Alembic graph remains linear from `54e8dd1ca624`; new empty
and disposable upgrade tests pass; no unknown legacy column is dropped; model cannot
write concurrency metadata; no external call happens while a write lock is held;
rollback leaves no ledger row or domain effect; current agent read-only and frozen
evaluation contracts are unchanged.

**Explicit anti-goals:** no production migration/deploy/AWS/RDS access, no real
DeepSeek call, no agent write tool, no Runtime default change, no PR-04 experiment
rerun/rewriting, no broad domain policy redesign, and no silent compatibility escape
hatch for unversioned writes.

---

## Audit command record

Read-only commands included `git status --short --branch`, `git rev-parse`,
`git rev-list --left-right --count`, `git worktree list --porcelain`, `git branch`,
`git log`, GitHub API/PR read calls, `rg`, and source/document reads. No tests,
Alembic command, application server, database command, cloud request, or provider
request was executed.

**Verdict: CONDITIONAL PASS — Design substantially complete, but live schema/data,
Plan cardinality, rollout/retention decisions, and reproducible PostgreSQL race
evidence remain unresolved.**

**PR-05B implementation has NOT started.**
