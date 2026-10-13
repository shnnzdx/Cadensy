# Cadensy PR-05B — B.2E0 System Writer Coverage and B.3 Readiness Audit

Date: 2026-10-12<br>
Worktree: `C:\Users\zdxzh\Desktop\capstone\New-pr05b1-schema-foundation`<br>
Branch: `codex/pr05b1-schema-foundation`<br>
Scope: source inspection and planning only. No application/test/migration changes, database test, feature activation, provider call, commit, push, or deployment was performed.

## 1. Executive summary

This audit counts a **writer** as a runtime-reachable operation that can commit authoritative application state through FastAPI, the in-process Scheduler, or a runtime service called by either. Test fixtures, evaluator seed data, Alembic, and explicit administrative/demo CLIs are excluded. Child writes (for example GuestSession creation during invite join) are included in their parent operation rather than double-counted.

| Classification at ordinary default runtime | Count | Meaning |
| --- | ---: | --- |
| Fully covered | 0 | No planning/Plan writer is both protocol-complete and selected by default. |
| Partially covered | 12 | A reviewed protected implementation exists and has tests, but default routing remains Legacy while the gate is absent. |
| Uncovered | 7 | Normal runtime path has no protected counterpart or required revision/lock protocol. |
| Not applicable to Plan/Trip validity | 10 | Identity, invite, comment, cache, or operational data outside frozen Preview/Apply facts. |
| **Total authoritative runtime writers** | **29** | Complete inventory is in section 4. |

The accepted kernels are valid building blocks: the Plan kernel locks Trip first, reauthorizes credential/membership, locks the single Plan, and rejects duplicate Plans (`backend/app/domain/transactions/kernel.py:249-310`); the Trip durable collaborator atomically advances Trip facts and, for one Plan, Plan revision (`backend/app/domain/transactions/trip_durable.py:95-150`). Callers that bypass these collaborators are not covered.

All twelve B.2C/B.2D protected adapters remain default-off. In addition, invite join/removal, all Organizer actions, and both Scheduler writers bypass them. These are source-confirmed gaps, not missing-test assumptions.

**Verdict: B.3 CONDITIONALLY READY.** A narrowly isolated, default-off B.3 seam may be designed only if it refuses to run in the current Legacy-mixed writer profile. B.3 full acceptance and production activation are blocked.

## 2. Audit scope and authoritative contracts

Priority was current source, accepted frozen protocol, completed reports, then historical planning documents.

| Required source | Result |
| --- | --- |
| `43_PR05B_FINAL_PROTOCOL_DECISIONS.md` | **Missing.** It is cited by reports 49/54, but the directory has no reports 41–43. Later reports are used only for the portions they restate. This is a B.3 design/acceptance documentation blocker. |
| Reports 49–53 | Present. Report 49 freezes Trip facts ownership, dual revision with exactly one Plan, duplicate-Plan failure, and canonical lock order (`49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md:5-71`). Report 53 accepts only four Trip-facts adapters (`53_PRE_PLAN_TRIP_BOUND_PHASE4_REAL_WRITER_MIGRATION.md:231-291`). |
| Reports 54–59 | Present. Report 54 explicitly leaves membership, Scheduler, Organizer, direct tooling, and Legacy paths outside Generator scope (`54_PR05B2D_GENERATOR_LIFECYCLE_AUDIT.md:393-403,450-452`). Report 59 records a default-off non-production Generator adapter (`59_PR05B2D5_GENERATOR_DEFAULT_OFF_HTTP_INTEGRATION.md:1-19,151-300`). |
| B.3/B.4 specification search | No current accepted B.3/B.4 specification file was found. `07_IMPLEMENTATION_ROADMAP.md:140-150` and `04_PYDANTIC_AI_INTEGRATION_DESIGN.md:72-74` are historical direction, not a replacement contract. |

Frozen rules available to this audit:

1. A real facts change advances `Trip.planning_facts_revision`; with exactly one Plan it advances Plan revision in the same transaction; zero Plan is valid and duplicates fail closed (`AI_enhanced/phase-0-audit-2026-10-03/49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md:39-49`).
2. Plan and Trip commands use their separate expected tokens; neither substitutes for the other (`AI_enhanced/phase-0-audit-2026-10-03/49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md:47-49`).
3. Protected mutation order is `Trip -> Credential -> Membership -> Plan -> Ledger -> Children`, with fresh owned Session and atomic completion (`AI_enhanced/phase-0-audit-2026-10-03/49_PRE_PLAN_TRIP_BOUND_PHASE0_PROTOCOL_FREEZE.md:51-71`).
4. Protected Generator output is Plan-scope and rechecks captured facts/projection before commit (`AI_enhanced/phase-0-audit-2026-10-03/54_PR05B2D_GENERATOR_LIFECYCLE_AUDIT.md:752-839`).

## 3. Actual repository and runtime boundary

Static inspection covered `backend/app/api`, all domain packages and transactions, jobs, agents, DB helpers, `backend/scripts`, and evaluator tooling. It searched `add`, `delete`, `update`, `execute`, `flush`, `commit`, `begin_nested`, and direct writer calls.

- FastAPI mutation entries are in `backend/app/api/main.py:707-773,851-895,1326-1453,1476-1652,1701-1739,1851-2281,2447-2683`.
- Lifespan starts Scheduler unless `DISABLE_SCHEDULER=1` (`main.py:136-145`); Scheduler calls `run_once()` (`jobs/scheduler.py:25-53`).
- `GET /api/trips` is a deliberate persisted-cover cache writer: it commits after `list_user_trips` (`main.py:800-812`), which calls `ensure_trip_cover` (`trips/service.py:159-181`).
- Explicit `__main__` demo/admin DB modules, agent trace/evaluation scripts, and `backend/evals` runners are not normal application entry points. They remain operational surfaces, but are excluded from the count.
- Chat has no write authority: the chat route calls `respond_to_trip_chat` (`main.py:1082-1151; chat/service.py:84-227`), tools are read-only (`agents/tools.py:38-340`), and classifier-only code is explicitly read-only (`orchestrator.py:510-542`). `classify_only` rolls back its savepoint unconditionally (`main.py:1678-1699`).

## 4. Complete authoritative write-surface inventory

`P/L` means a protected implementation exists but is opt-in while Legacy remains the default. `U` means no protected implementation. `N/A` means no frozen Plan/Trip revision is required.

| ID | Writer | Entry/call chain and material tables | Status |
| --- | --- | --- | --- |
| W01 | Login | `POST /auth/login` -> `auth.login` -> `_start_session` (`main.py:707-730; auth.py:107-132`): `AuthSession` | N/A |
| W02 | Register | `POST /auth/register` -> `auth.register` (`main.py:732-757; auth.py:196-211`): `User`, `AuthSession` | N/A |
| W03 | Logout/revoke | `POST /auth/logout` -> account/Guest revoke (`main.py:759-773; auth.py:241-291`): session revocation | N/A |
| W04 | Manual item | `POST /plans/{id}/items` -> `manual_items.add_manual_plan_item` (`main.py:851-895; manual_items.py:37-111`): `PlanItem` | P/L |
| W05 | Create trip | `POST /trips` -> `trips.create_trip` (`main.py:1326-1363; trips/service.py:104-136`): new `Trip`, organizer membership, Plan | N/A new aggregate |
| W06 | Create/rotate invite | `POST /trips/{id}/invite` -> `create_invite` (`main.py:1365-1386; trips/service.py:243-261`): `InviteLink` | N/A |
| W07 | Invite join | `POST /invites/{token}/join` -> `join_invite` -> Guest issue (`main.py:1388-1415; trips/service.py:309-373; auth.py:145-179`): membership, optional User, GuestSession | U |
| W08 | Revoke invite | `POST /invites/{id}/revoke` -> `revoke_invite` (`main.py:1417-1432; trips/service.py:376-383`): `InviteLink` | N/A |
| W09 | Remove member | `DELETE /trips/{trip}/members/{member}` -> `remove_membership` (`main.py:1434-1453; trips/service.py:386-408`): membership status, GuestSessions | U |
| W10 | Generator | route -> Legacy `generate_plan` or protected commit (`main.py:1476-1666; generator.py:160-285; generator_commit.py:47-106`): Plan/items/changes/cache | P/L |
| W11 | Change submission | `POST /plans/items/{id}/changes` -> `propose_change` (`main.py:1701-1825; orchestrator.py:442-507`): path-dependent PlanItem/Change/Notice/Round/Proposal/Decision | P/L |
| W12 | Comment | `POST /plans/items/{id}/comments` (`main.py:1851-1871`): `PlanItemComment` | N/A |
| W13 | Booking | `PATCH /plans/items/{id}/booking` -> booking service (`main.py:1873-1980; orchestrator.py:544-612`): item/change/notice | P/L |
| W14 | Notice objection | `POST /updates/{id}/object` -> `object_to_notice` (`main.py:1982-2084; orchestrator.py:643-661`): notice/round/item | P/L |
| W15 | Vote/final settlement | `POST /rounds/{id}/votes` -> vote/settle (`main.py:2086-2198; orchestrator.py:717-797`) | P/L |
| W16 | Manual settlement | `POST /rounds/{id}/settle` -> `settle_round` (`main.py:2200-2256; orchestrator.py:757-797`) | P/L |
| W17 | Proposal decision | `POST /proposals/{id}/decisions` -> `decide_proposal` (`main.py:2258-2359; orchestrator.py:899-966`) | P/L |
| W18 | Preferences save | `PUT /trips/{id}/preferences/me` -> `save_mine` (`main.py:2447-2484; preferences/service.py:157-184`): Preference/membership/refresh | P/L |
| W19 | Constraint create | `POST /trips/{id}/constraints` -> `add_constraint` (`main.py:2486-2521; preferences/service.py:354-388`) | P/L |
| W20 | Constraint update | `PATCH /constraints/{id}` -> `update_constraint` (`main.py:2523-2558; preferences/service.py:390-406`) | P/L |
| W21 | Constraint delete | `DELETE /constraints/{id}` -> `delete_constraint` (`main.py:2560-2590; preferences/service.py:408-424`) | P/L |
| W22 | Reminder | `POST /trips/{id}/members/{id}/remind` -> `organizer.remind` (`main.py:2624-2638; organizer.py:71-110`): notice | N/A |
| W23 | Round extension | route -> `organizer.extend_round` (`main.py:2640-2653; organizer.py:115-147`): round deadline/notice | U |
| W24 | Proposal escalation | route -> `organizer.escalate` (`main.py:2655-2669; organizer.py:151-162`): proposal status | U |
| W25 | Deadlock resolution | route -> `organizer.resolve_deadlock` (`main.py:2671-2683; organizer.py:165-232`): proposal/item/change/notice | U |
| W26 | Scheduler settlement | lifespan -> `run_once` -> `settle_due_rounds` -> settle (`main.py:136-145; scheduler.py:25-35; orchestrator.py:757-842`) | U |
| W27 | Scheduler expiry | lifespan -> `run_once` -> `expire_due_proposals` (`scheduler.py:25-35; orchestrator.py:800-831`) | U |
| W28 | Dashboard cover cache | `GET /trips` -> `ensure_trip_cover` (`main.py:800-812; cover_service.py:37-69`): Trip cover data | N/A |
| W29 | Place cache upsert | Generator -> `_upsert_geoapify` (`generator.py:160-285; places/service.py:298-330`): `Place` cache | N/A |

`orchestrator.withdraw_proposal` has no non-test caller (`orchestrator.py:968-971`) and is not counted. No normal runtime endpoint changes existing Trip destination, dates, currency, role, organizer identity, or Trip status; demo/seed scripts are explicit CLIs.

## 5. Protected feature gates versus runtime coverage

All B.2C flags default to `"0"` and only exclude production when requested (`main.py:109-121,253-314`). Gate-on non-production selects the adapter; gate-off selects Legacy. No deployment configuration proves any is enabled.

| Family | Flag | Protected evidence | Default behavior |
| --- | --- | --- | --- |
| Manual item | `CADENSY_B2C_PROTECTED_MANUAL_ITEM_WRITES` | `transactions/manual_plan_item.py:78-139`; `test_protected_manual_plan_item.py:151-346` | Legacy W04 |
| Booking | `CADENSY_B2C_PROTECTED_BOOKING_WRITES` | `booking.py:70-133`; `test_protected_booking_writer.py:120-305` | Legacy W13 |
| Change proposal | `CADENSY_B2C_PROTECTED_CHANGE_PROPOSAL_WRITES` | `change_proposal.py:92-177`; tests `:209-634` | Legacy W11 |
| Notice/Vote/Settlement/Decision | `CADENSY_B2C_PROTECTED_NOTICE_OBJECTION_WRITES`, `_VOTE_`, `_ROUND_SETTLEMENT_`, `_PROPOSAL_DECISION_` | transaction modules and `test_protected_{notice_objection,vote,round_settlement,proposal_decision}_writer.py` | Legacy W14–W17 |
| Preferences/Constraints | `CADENSY_B2C_PROTECTED_PREFERENCES_WRITES`, `_CONSTRAINT_CREATE_`, `_UPDATE_`, `_DELETE_` | `trip_durable.py:95-193`; `test_trip_preferences_writers.py:139-913` | Legacy W18–W21 |
| Generator | `CADENSY_B2D_PROTECTED_GENERATOR_WRITES` | `generator_durable.py:336-550`; `test_generator_protected_http.py:296-777` | Legacy W10 unless true plus explicitly approved non-production environment |

Generator is stricter: false/missing selects Legacy; true plus missing/empty/unknown environment fails closed; only approved non-production values select protected (`main.py:317-339,1652-1666`). No gate, protected transaction module, or `execute_protected_*` entry exists for W07/W09 or W23–W27.

## 6. Planning facts, Plan, and decision coverage

Generator reads membership count (`generator.py:358-363`), organizer submission status (`:296-304`), Preferences (`:391-459`), required constraints (`:366-388`), Trip destination/dates/currency (`:179-190,327-333`), and Plan/items (`:307-324`). Therefore W07/W09 are planning-fact relevant: they change membership count/lifecycle that Generator consumes.

| Writer | Required behavior | Observed default behavior | Verdict |
| --- | --- | --- | --- |
| W07 join | Trip root; invite/server authority; facts revision and Plan revision with one Plan; duplicate rule | request transaction inserts membership/session; no root/revision/Ledger (`trips/service.py:309-373`) | P1 |
| W09 remove | same facts protocol plus atomic credential revoke | request transaction removes/revokes; no root/revision/Ledger (`trips/service.py:386-408`) | P1 |
| W18–W21 protected | fresh auth/root, facts revision, dual Plan increment and Ledger | implemented by Trip durable coordinator (`trip_durable.py:95-150`) | covered only gate-on |
| W18–W21 Legacy | same facts invalidation if route coexists with B.3 | direct request writes/no revision (`main.py:2477-2588; preferences/service.py:157-435`) | P1 at default |
| W04/W11/W13–W17 protected | Plan root/revision/Ledger | Plan kernel + Durable collaborator (`kernel.py:249-310; durable.py:330-411`) | covered only gate-on |
| W10 protected | capture/recheck facts and Plan projection, Plan-only revision/Ledger | `generator_durable.py:336-550` | covered only gate-on |
| W23–W25 | must serialize/revalidate decision state; W25 split/clear must own Plan revision | direct request writes, no protected counterpart (`organizer.py:115-232`) | P1 |
| W26/W27 | server job authority plus root/revision/job identity policy | Scheduler direct calls then commits (`scheduler.py:25-35`) | P1 |

W12 comments and W22 reminders are auxiliary. W06/W08 invite links alone are not Generator inputs. W28/W29 caches are operational and do not invalidate current Plan semantics. Cover fetch nevertheless holds a Trip row lock across Unsplash I/O (`cover_service.py:37-69`): P3 availability/lock-duration concern, not a B.3 facts violation.

## 7. Consolidated lock/revision matrix

“Observed” is the default path. Protected rows become YES only after gate selection.

| Writer IDs | Trip lock | Fresh auth/job authority | Trip facts rev | Plan rev | Ledger/idempotency | Runtime status | Verdict |
| --- | --- | --- | --- | --- | --- | --- | --- |
| W01–W03 | NOT APPLICABLE | YES auth boundary | N/A | N/A | no, session lifecycle | default | N/A |
| W04 | NO | CONDITIONAL request auth | N/A | required, NO | NO | Legacy default | PARTIAL P1 |
| W05 | N/A new root | YES account | initial 0 | initial 0 | N/A | default | N/A |
| W06/W08 | NO | YES request auth | N/A | N/A | N/A | default | N/A |
| W07/W09 | NO | join invite / organizer request | required, NO | required with one Plan, NO | NO | default | UNCOVERED P1 |
| W10 | NO | request organizer | reads only | required on state change, NO | NO | Legacy default | PARTIAL P1 |
| W11/W13–W17 | NO | request auth | N/A | required for content/settlement, NO | NO | Legacy default | PARTIAL P1 |
| W18–W21 | NO | request auth | required, NO | required with one Plan, NO | NO | Legacy default | PARTIAL P1 |
| W12/W22 | N/A | YES | N/A | N/A | N/A | default | N/A |
| W23/W24/W27 | NO | request/server | N/A | UNKNOWN pending B.3 token contract | NO | default | UNCOVERED P1 |
| W25/W26 | NO | request/server | N/A | required when Plan content changes, NO | NO | default | UNCOVERED P1 |
| W28/W29 | conditional/cache | conditional | N/A | N/A | N/A | default/cache child | N/A |

The protected Plan durable collaborator rolls back an alleged no-op callback savepoint, increments Plan revision only on state change, and writes the completed Plan Ledger (`durable.py:330-411`). The Trip collaborator has the analogous facts/dual-Plan behavior and current-plus-historical outcome validation (`trip_durable.py:95-193`). That evidence does not cover a Legacy/Scheduler bypass.

## 8. Scheduler, agent, service, script, and evaluation analysis

Scheduler is normal-runtime reachable unless disabled, and `run_once` uses one direct Session/commit for both settlement and expiry (`jobs/scheduler.py:25-35`). It has no interactive credential by design, but it also has no reviewed server identity, Trip root, Plan revision, Ledger, or durable job identity. It can race Generator or future Apply; it must not be made safe merely by permanently disabling Scheduler.

No AI write bypass was found. Chat only produces suggestions/classification; no `Session.add`, `flush`, `commit`, or writer call was found under `backend/app/agents/tools.py` or `domain/chat/service.py`. Agent-server scripts seed local/evaluation data and may call legacy Generator only when explicitly launched; DB reset/seed/upsert helpers are likewise CLI tooling. These are excluded from runtime coverage but require operational access control later.

## 9. Source-backed gaps and B.3 dependency analysis

| Finding | Conflict scenario | Minimum correction and test | Severity | B.3 impact |
| --- | --- | --- | --- | --- |
| W07/W09 membership facts gap | Preview/Generator sees member count/status; concurrent join/remove changes neither revision | membership facts slice; Generator/Preview vs Account/Guest join/remove, zero/one/duplicate Plan, revocation/rollback tests | P1 | blocks acceptance |
| Twelve Legacy defaults | Legacy Plan/facts mutation can leave a captured revision unchanged | define protected-only B.3 routing profile or migrate default selection; gate-off compatibility and no-Legacy-fallback tests | P1 | bounded coding only |
| W26/W27 Scheduler bypass | due settlement can apply content after Preview capture with no root/revision; expiry changes decision state | server-job coordinator, deterministic operation identity, `pg_blocking_pids` concurrency tests | P1 | blocks acceptance |
| W23–W25 Organizer bypass | deadlock split/clear changes PlanItem without revision; extension/escalation can affect decision lifecycle | protected Organizer slices; retain broad same-Trip escalation policy; state transition/privacy/concurrency tests | P1 | blocks acceptance |
| Missing report 43/current B.3 spec | token rule for W14/W23/W24/W27 cannot be proved | restore/freeze B.3 protocol before integration acceptance | P1 design/docs | blocks acceptance |
| Cover provider under lock | provider latency blocks unrelated Trip commands | later provider-outside-lock conditional persist design | P3 | no B.3 correctness block |

No P0 was established. There is no source-confirmed agent write authority, protected-kernel cross-Trip escape, or enabled protected route leaking secrets. The P1 findings are normal-runtime writes that directly lack the invalidation/serialization required by available frozen rules.

## 10. PostgreSQL acceptance plan (not executed)

| Proposed slice | Required real PostgreSQL cases |
| --- | --- |
| Membership facts | Generator Prepare versus Account/Guest join and removal; zero-Plan facts-only, one-Plan dual revision, duplicate fail-closed, no partial GuestSession/revision/Ledger, revoked guest rejection |
| Scheduler | settlement/expiry versus protected Plan/Trip command; server job identity; no duplicate PlanChange/Notice/revision; root-lock evidence through `pg_blocking_pids()` |
| Organizer | deadlock split/clear versus Apply, extension/escalation versus Preview, Account/Guest/same-Trip/cross-Trip policy, rollback |
| B.3 Apply | capture versus requirement writer, Plan writer, Scheduler, Organizer; stale 409; same-key replay; all child/revision/Ledger rollback; duplicate Plan failure |
| Routing profile | gate-off Legacy compatibility, B.3 profile rejects mixed runtime, gate-on path has no Legacy fallback, production remains rejected |

Use independent PostgreSQL Sessions/connections, Events/queues, and server lock observation; do not place mutually waiting barriers inside child locks. Unit tests may cover DTO/canonical preview semantics and job-key derivation only.

## 11. Minimum roadmap

| Order | Slice | Exit criterion | Readiness role |
| --- | --- | --- | --- |
| 1 | **B.2E1 — Membership planning-facts protocol** | W07/W09 use reviewed Trip root with server invite authority, revision/no-op/idempotency policy and first matrix row | before B.3 acceptance |
| 2 | **B.2E2 — Scheduler authoritative settlement** | W26/W27 have server-job coordinator, revision and durable-operation policy | before B.3 acceptance |
| 3 | **B.2E3 — Organizer actions** | W23–W25 protected; deadlock content revision and decision token policy explicit | before B.3 acceptance |
| 4 | **B.2E4 — B.3 protocol restoration/routing profile** | restore/freeze report 43 equivalent; classify decision-only tokens; define all-protected admission profile | before B.3 integration acceptance |
| 5 | B.3 Preview/Apply vertical slice | server capture/reclassification/root-owned Apply only | after 1–4 approval |
| 6 | Production activation review | keys/rotation/retention/configuration/full coverage | later only |

## 12. Final verdict and exact next task

| Decision | Verdict | Basis |
| --- | --- | --- |
| B.3 implementation readiness | **B.3 CONDITIONALLY READY** | A default-off seam can be developed only with protected-only admission and refusal of the Legacy-mixed profile. |
| B.3 full acceptance readiness | **BLOCKED** | W07/W09, W23–W27, all default Legacy fallbacks, and missing B.3 primary protocol are unresolved. |
| Production activation readiness | **BLOCKED** | Gates remain default-off/non-production; coverage and key/retention/configuration work remain incomplete. |

**Exact next authorized engineering task:** **B.2E1 — Membership Planning-Facts Protocol Design and Migration Authorization Review**, strictly limited to Account/Guest invite join and organizer member removal. It must first freeze server invite authority, revision/no-op/idempotency semantics, and the PostgreSQL acceptance cases above. No migration or B.3 work is authorized by this audit.

## 13. Deliverable coverage, non-goals, and remaining unknowns

The requested audit deliverables are mapped explicitly below so no completed
protected vertical slice is mistaken for system-wide activation evidence.

| Required report content | Location in this report |
| --- | --- |
| Executive summary; audit scope; runtime boundary | Sections 1–3 |
| Complete inventory; entry points/call graphs; HTTP coverage | Sections 4–5 |
| Trip planning facts; membership/credential; Preferences/Constraints | Section 6 and W01–W09/W18–W21 inventory rows |
| Plan/PlanItem/PlanChange; Proposal/Vote/Round/Notice; Organizer actions | Section 6 and W04/W10–W17/W23–W25 rows |
| Scheduler/background; Chat/internal tools; service/script/evaluation paths | Sections 3 and 8 |
| Canonical Trip-root lock; Trip/Plan revision; durable Ledger matrices | Section 7 |
| Feature gates versus actual runtime; source-backed severity gaps | Sections 5 and 9 |
| B.3 dependency analysis | Section 9 |
| PostgreSQL acceptance plan; minimal migration roadmap | Sections 10–11 |
| Explicit non-goals, unknowns, final readiness, next slice | Sections 2, 7, 9, and 12 |

Non-goals remain: implementation, enabling gates, migration changes, Preview/Apply
implementation, production access, Provider calls, commits, pushes, and merges.
The open technical unknown is intentionally narrow: without the missing report
43/current B.3 freeze, the exact invalidation token for decision-only W14, W23,
W24, and W27 is UNKNOWN. This audit does not invent a Plan-revision rule for
those rows.
