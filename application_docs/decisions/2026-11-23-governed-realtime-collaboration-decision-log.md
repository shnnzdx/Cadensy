# Governed Real-Time Collaboration — Decision and Trade-off Log

**Status:** Point-in-time product/technical decision log. **Design direction approved for investigation; NOT implemented, tested, or authorized for production by this record.**
**Recorded:** 2026-11-23 (America/Chicago)
**Code evidence baseline:** `main` at `27c3489936ec34bebbbc9562a1d99e8717df6751` (2026-11-23).
**Roles:** Human Owner makes final business, technical, spending, merge and production decisions; ChatGPT independently challenges technical and business trade-offs; Grok DD coordinates implementation and targeted QA.
**Purpose:** Preserve context, candidate technologies, why options were chosen/rejected, assumptions, risks, and retrospective questions. Later implementation evidence must be recorded separately.

**Evidence vocabulary:** VERIFIED = inspected repository code/document/config; REPORTED = team/agent or prior research conclusion not independently reproduced; DESIGN = current agreed direction/recommendation, not deployed fact; UNKNOWN / NOT VERIFIED = insufficient evidence; DEFERRED = explicitly held for later. A "technical review PASS" never authorizes release, ECR, migration, production or new paid resources.

## 1. Why build this? Product and commercial decision

**Background / thesis (REPORTED):** AI itinerary generation is increasingly commoditized; collaborative travel planners already exist, so merely adding "AI planning" or "realtime" is not demonstrated differentiation. Cadensy's proposed value is managing group changes fairly **after** an itinerary exists. No assertion that competitors universally lack this behavior has been independently proven in this record.

**Owner-endorsed direction:** Make minor edits **feel like easy shared editing** while keeping consequential changes subject to Cadensy's deterministic governance. "AI proposes; rules protect; humans decide." AI should become optional for ordinary structured changes, not the only way to move time/day or remove an item.

**Intended example:** A shifts Tokyo Tower from 14:00 to 15:00; B sees the *committed* result quickly. If it touches a booked activity, a settled decision, another member's required constraint, or a pending decision, it must **not** skip approval because the UI now supports drag-and-drop or manual form editing.

**Not the product:** A Google-Docs-like unrestricted, character-by-character shared document, or a system where faster transport means "last writer wins."

## 2. Repository findings and uncertainty

| Item | Evidence / state | Why it matters |
| --- | --- | --- |
| A manual add-stop route already exists at `POST /api/plans/{plan_id}/items`. | **VERIFIED:** [API](../../backend/app/api/main.py), [manual_items.py](../../backend/app/domain/plans/manual_items.py), [TripAppState.jsx](../../trip/src/final/TripAppState.jsx). | It directly inserts a `PlanItem` without going through ordinary change classification or creating a `PlanChange` in the inspected writer. **Governance/audit bypass to fix at P0.** This is not proof that every other potential side effect was exhaustively audited. |
| Existing change mechanism has Notice / Round / Reopened Round / Confirm and backend structured change patches. | **VERIFIED:** [orchestrator.py](../../backend/app/domain/decisions/orchestrator.py), [constraints/engine.py](../../backend/app/domain/constraints/engine.py), [PRODUCT.md](../PRODUCT.md). | Manual/drag/AI submissions must share a **server-owned** decision policy. Existing-item classifier may need adaptation for **new-item** impact. |
| Frontend lacks convenient direct UI for some change fields (time, day, duration, delete). | **REPORTED** from DD; existing `submitChange` and raw patch API **VERIFIED**. Full authenticated browser audit not performed. | Prioritize forms before elaborate realtime dragging. |
| Current Plan UI refreshes every five seconds on successful cycles, backs off to ten/thirty seconds on failures; comments have separate five-second polling. | **VERIFIED:** [TripAppState.jsx](../../trip/src/final/TripAppState.jsx), [usePlanInteractionRuntime.js](../../trip/src/final/plan-feature/usePlanInteractionRuntime.js). | Replace routine repeated full fetch with event-driven targeted invalidation; keep a recovery path. |
| `Plan.revision` and protected transaction/idempotency primitives exist. | **VERIFIED:** [models.py](../../backend/app/db/models.py), [transactions/kernel.py](../../backend/app/domain/transactions/kernel.py). | A field or dormant protected path alone does **not** prove safe production concurrent mutation. |
| Production release preparation sets protected writer feature gates to off. | **VERIFIED as deployment script**, [prepare_ecs_task_definition.py](../../scripts/cd01/prepare_ecs_task_definition.py). **Actual live ECS environment not inspected.** | Do **not** enable a gate as a shortcut to "realtime safe." |
| Redis may be remembered from other work; no direct active Redis backend integration established in Cadensy repo. | Repository search / [requirements.txt](../../backend/requirements.txt) **VERIFIED**; actual AWS resources **UNKNOWN**. | Don't price Redis as free or already provisioned for Cadensy. |
| ECS Fargate / ALB / private PostgreSQL RDS are documented core infrastructure. | **VERIFIED as repository documentation:** [root README](../../README.md); live task sizing and headroom **UNKNOWN**. | Existing infra is preferred *if* performance and cost checks pass. |

**Frozen coding boundaries:** Respect [root AGENTS.md](../../AGENTS.md): navigation in `shared/trip-navigation-policy`, session identity/header logic in `shared/session-runtime`, Plan interactions behind `PlanFeature` / `usePlanInteractionRuntime`. SSE cannot justify duplicating session plumbing in UI or moving Plan logic back into `FinalApp`.

## 3. "Small" versus "large": decided by actual impact, not edited field

| Condition | Intended result | Important qualification |
| --- | --- | --- |
| Loose/unsettled slot; no schedule/required-constraint/booking conflict | **Notice:** apply quickly, write audit history, notify group; members may object as existing contract allows | "Direct" does not mean ungoverned. |
| Contested/touched slot | **Round:** collective choice | No personal veto bypass or privileged organizer vote. |
| Previously settled by group | **Reopened Round:** reason and stronger threshold | Previous outcome deserves protection. |
| Confirmed booking or violation of affected member's required constraint | **Confirm:** required affected members approve before applying | Do not expose private identity/text/reasoning. |
| Active, incompatible round/confirmation | Conflict/blocked or fresh re-preview | Never silently replace an in-progress decision. |
| New stop / delete / reordering / move day | Evaluate **all actual effects**, including adjacent schedule, settledness, time overlap, booking and required constraints | Do not assume an additive or drag gesture is always safe. |

This policy applies equally to direct form, drag-and-drop, AI-generated suggestion, organizer actions and backend APIs. Only the server determines the final path.

Previously deferred product question: **joining a Trip during an active decision** (existing 409 block) is **not** part of this initiative; do not change electorate rules opportunistically.

## 4. Separate the layers before selecting a technology

1. **Transport:** browser learns an authoritative change exists (short polling, long polling, SSE, WebSocket, WebTransport).
2. **Cross-process/event distribution:** changes reach SSE subscribers connected to *other* ECS tasks/processes (PostgreSQL `LISTEN/NOTIFY`, Redis Pub/Sub, managed fanout).
3. **Mutation and conflict integrity:** requests are authorized, classified, checked against fresh state, serialized/conditional, deduplicated and committed (revision + short transaction/locks + idempotency). **SSE never does this automatically.**

### Transport, framework and hosted-service trade-offs

| Candidate | Upside | Trade-off / reason for decision | Decision |
| --- | --- | --- | --- |
| **Existing short polling (5s)** | Few moving parts; works with current REST auth | Delay until next poll; repeated fetch while idle; backoff after failure | Keep for fallback/reconciliation, not primary realtime UX. |
| HTTP long polling | Faster than fixed-interval polling when change occurs | Held requests and repeated cycles; less suitable than SSE for a steady event stream | Not preferred. |
| **FastAPI SSE** | Near-immediate server-to-client events; REST continues to own writes; reuse ECS/ALB | Persistent connections, auth, recovery, task fanout and backpressure are our responsibility | **Owner chose the self-built SSE direction** for the next development line. |
| Native WebSocket | Full duplex, richer presence/typing/cursor and continuous drag feedback | More connection/message lifecycle and mutation-channel complexity than needed for discrete edits | Deferred; reconsider if bidirectional needs become central. |
| Socket.IO | Rooms, event abstraction, reconnect ecosystem | Extra framework/compatibility work in Python/FastAPI stack, no implicit exactly-once delivery | Deferred. |
| WebTransport | Flexible low-latency transports | Overkill and broader compatibility/ops considerations | Not V1. |
| AWS AppSync Events | Managed pub/sub and connection fanout | New AWS service, per-usage charge, channel auth and vendor dependency | Strong managed alternative, not chosen now. |
| API Gateway WebSocket | AWS managed connection service | Routes/permissions/fanout/integrations still require implementation and costs | Not chosen now. |
| Supabase Realtime | Broadcast, Presence and change-stream ecosystem | Existing app uses AWS RDS rather than a native Supabase project; bridge/self-host/replication/permissions required | Not chosen now. |
| Ably / Pusher | Outsource connection scale and fanout | Third-party account, auth, billing, dependency and migration | Reassess if self-host maintenance outweighs benefits. |
| Firebase Firestore listeners | Simple datastore-driven updates | Introducing a second authoritative state alongside RDS/decision rules is risky | Not chosen. |
| Liveblocks, CRDT/OT (Yjs, Automerge) | Excellent for collaborative text/state merging and presence | Does not supply vote/booking/constraint authority; higher complexity | Defer Google-Docs-grade field-level editing. |

**Clarification from technical review:** SSE is **not inherently slower** than WebSocket for server-push notifications. The old 5-second lag comes from *polling*, not SSE. The correct metric is DB commit → visible, newly fetched authoritative state; verify empirically.

### Cross-ECS distribution options

| Candidate | Advantage | Limitation | Direction |
| --- | --- | --- | --- |
| **RDS PostgreSQL `LISTEN/NOTIFY`** | Existing Postgres; `NOTIFY` delivered after transaction commit; lightweight one-listener-per-process topology | Events not durable, reconnect and `LISTEN` startup race, RDS connections/fanout load | **Preferred V1 design hypothesis**, needs local multi-process validation. |
| Redis Pub/Sub | Fast lightweight broadcast | New/unknown Redis resource; messages lost while offline; separate monitoring | Do not provision just for V1. |
| Redis Streams | History/replay and consumer tracking | More infrastructure and operational complexity | Later if needed. |
| NATS / Kafka | Large-scale event infrastructure | Disproportionate operational surface for current personal-team scale | Reject for V1. |
| Transactional Outbox + worker | Reliable publication and replay | Extra DB schema, delivery worker, cleanup, duplicate handling | Future upgrade if lost events cannot be tolerated with refresh fallback. |

### Conflict-control options

- **Recommended:** server-side optimistic revision check plus short transaction/appropriate row locks and durable idempotency. Reject stale concurrent writes (e.g., HTTP 409), preserve user draft and re-preview against current governance.
- **Pessimistic DB locking:** useful inside a bounded write transaction, not a long-lived browser editing lock.
- **OT/CRDT:** concurrent document data merges do not resolve normative questions like whether a booked item may be moved or a private required constraint overridden. Explicitly outside V1.
- Actual protection requires *every relevant writer* to obey compatible rules, not merely an optional header on one endpoint.

## 5. Financial trade-offs (historical estimates, not purchase approval)

**Self-host SSE:** No new managed realtime service subscription is planned; this does **not** mean zero incremental AWS bill. Open streams consume backend memory/connections, network and heartbeat CPU; ALB may bill additional LCU, RDS listeners consume connections, and Fargate may need additional tasks/vCPU/memory if tests show insufficient headroom. Conversely, if existing capacity suffices, Fargate compute charge need not increase merely because a process is busier.

**One PG listener per Backend worker/process; never one per browser.** A hundred clients may share one listening DB connection per serving process, plus normal request sessions. Real task/process counts must be measured.

Earlier research provided **indicative, NOT independently rechecked for purchase** rates: AppSync Events approx. $1/1M operations plus $0.08/1M connection-minutes; API Gateway WebSocket approx. $1/1M messages plus $0.25/1M connection-minutes; Supabase has a limited free plan and a Pro tier starting around $25/month. Verify pricing, region, bandwidth, operation/message accounting, eligible promotional credits and free tier directly with [AWS AppSync](https://aws.amazon.com/appsync/pricing/), [API Gateway](https://aws.amazon.com/api-gateway/pricing/) and [Supabase](https://supabase.com/pricing) before any actual financial choice. No new paid service or AWS scale-up has been authorized.

Commercial trade-off: pay-as-you-go managed events may eventually be cheaper *in total* than maintaining our own fanout/recovery. Include engineering, QA, incident and vendor-lock-in costs, not only AWS line items.

## 6. Preferred SSE + PostgreSQL design (PROPOSED, NOT IMPLEMENTED)

### End-to-end flow

1. React submits a structured edit intention to an **existing authenticated REST writer**; realtime is not a bypass-write path.
2. FastAPI validates Trip membership, actor permissions, decision impact, current Plan revision and idempotency in a correct transactional boundary.
3. PostgreSQL commits the authoritative Plan/decision/audit state; a successful committed change generates a **safe invalidation notification**.
4. Each Backend process maintains a separate, persistent PostgreSQL listening connection, serving a local dispatcher keyed by Trip. Different ECS processes receive notifications and fan out only to their **authorized** local browser subscriptions.
5. Browser gets an SSE signal such as `event: trip.invalidate` and `data: {"areas":["plan","decisions"]}`. This is a *hint to refresh*, not the full Plan; do not put private constraints, votes, emails, tokens or personally identifying payload in broadcasts.
6. React refreshes relevant protected REST reads and renders the authoritative state. Disconnected clients refresh after reconnect regardless of missed events.

**Candidate endpoint:** `GET /api/trips/{trip_id}/events` with `text/event-stream`. Proposed implementation: FastAPI streaming response, process-local scoped distributor, bounded/coalescing per-client queues, async listener connection and graceful shutdown.

### Authentication and authorization

Current browser request identity is Bearer plus Trip context, assembled by `shared/session-runtime`. Native `EventSource` does not support arbitrary custom headers. Prefer **`fetch()` streaming with SSE parsing** so existing header auth may be reused without putting a Bearer token in a query string or inventing another session model.

- Authenticate on subscribe and ensure requested Trip matches active membership, including Guest support.
- On logout, Trip switch or session invalidation, immediately close old subscriptions; revocation/member removal must terminate access even on a long connection (periodic short-lived revalidation and/or targeted invalidation).
- Ensure no cross-Trip events and avoid leaking even aggregate private facts through payloads. Apply explicit allowed-origin/CORS policy.
- Avoid holding a normal SQLAlchemy request Session/transaction open for the entire stream. Use short auth reads, and a **dedicated** PG listener independent from request pool.
- Consider intermediary buffering, heartbeat, Stream cancellation, queue overflow, memory caps and browser tab duplication.

### Notification design is NOT frozen

Two candidates: instrument every successful writer with transactional `pg_notify()`, or have narrowly scoped Postgres triggers for relevant tables. Trigger approach may catch legacy/protected API and background Scheduler paths automatically, but may over-notify, require duplicate suppression, increase DB load, or mishandle scope if poorly designed. DD should compare both against a writer inventory; do not assume only the HTTP edit endpoint changes a Trip.

Candidate changes: `plan_item`, `plan_change`, `decision_round`, `change_proposal`, `update_notice`, comments, and only scoped membership invalidation when required. A transaction can change several: merge multiple notices into minimal `trip.invalidate` events. The dispatcher must never queue unbounded messages.

**Reliability contract:** PostgreSQL NOTIFY is **ephemeral**, not a durable queue. It may be missed while a listener or client is down. Establish subscription, perform a fresh authoritative read, handle startup races and monitor/repair listener reconnect. Retain low-frequency reconciliation polling, and restore existing faster polling on degraded SSE. Consider a durable Outbox only if later SLAs demand replay.

### ALB / ECS / RDS constraints

Reuse current ECS Backend, HTTPS ALB, PostgreSQL RDS. Verify actual ALB idle timeout, proxy buffering, streaming behavior, Backend worker and Task counts, vCPU/memory, connection limits and RDS pressure. A roughly **15-second heartbeat** is an initial design candidate; do not assume the current ALB uses the often-cited 60-second default without inspection. SSE can be efficient at low fanout but **performance is not guaranteed before load testing**.

Keep API responses and data permission checks authoritative; SSE tells the UI to refresh, it does not directly update private data into shared browser state.

## 7. Concurrency and governance prerequisites

**Highest-priority technical risk:** manual-add bypass and mixed Legacy/Protected writer revision contracts. Existing `Plan.revision` and transaction kernels are implementation assets, not production acceptance proof.

Include in impact/revision audit: manual inserts, structured edit/delete/move, AI suggestion apply, booking changes, vote/round/confirm/reopen and objection, background settlement, preferences/constraints, generation, organizer/system writers. Do not blindly turn on production Protected Feature Gates. They have deliberate failure/approval boundaries; production activation is a **separate Critical** decision and requires validation and rollback.

In a collision (A and B both read revision 12; A successfully commits revision 13; B submits revision 12), reject B rather than "last writer wins," preserve their input, refresh Plan and present a new validation/approval outcome.

## 8. Implementation roadmap and required evidence

| Stage | Scope | Acceptance evidence |
| --- | --- | --- |
| **S0: Write integrity & baseline** | Writer inventory, manual add governance/audit repair, strict safe mutation contract, capture current 5-second polling and resource baseline | Targeted **Critical QA**: booked/settled/private constraint protection; no bypass; no lost update; scope and audit. |
| **S1: SSE Foundation** | Protected subscription endpoint, independent async PG listener, per-Trip local fanout, fetch streaming client, incremental refresh | Two separate backend processes + isolated Postgres prove fanout; unauthorized Trip/Guest denied; no private payload. |
| **S2: Recovery & concurrency** | Reauth/revoke, restart and listener loss, queues/coalescing, low-frequency reconciliation fallback, revision conflict behavior | Tests for 409 without overwrite, active decision, network drop, listener interruption, ECS process restart, revoked member. |
| **S3: Staging load/release** | Measure actual p95 latency, CPU, memory, RDS connections, ALB/LCU, reconnects, operational alerts; draft rollout and rollback | Calibrate at 10/50/100/200 active SSE streams; report cost and reliability **from tests**. Human approval required for Merge where applicable, migrations, releases, production changes, capacity costs. |

**UI sequence:** after S0, first add explicit direct forms (time/day/duration/delete) and only later drag-and-drop. A drag action is an edit proposal, not permission to bypass rules. Defer realtime cursors, character-level merging, generalized CRDT/OT server, and new dedicated realtime microservices.

**Design goals, not observations:** commit-to-authoritative-UI-update P95 ≤ 1 s under agreed tests; zero silent write overwrites, cross-Trip disclosures, private-constraint leaks or governance bypass; success rate of edits completed without AI; conflict and decision-completion metrics. Real user benefit must be measured rather than inferred from SSE connect success.

## 9. Unresolved decisions and reversal triggers

| Question | Status / future approach |
| --- | --- |
| Trigger-based or app-code-based NOTIFY? | **Unresolved design detail:** let DD compare completeness, scoped event safety and performance in prototype. |
| Actual ECS capacity / ALB idle timeout / RDS limit? | **UNKNOWN:** read-only AWS inspection and S3 load tests needed. No assertion that capacity is sufficient. |
| Redis actually deployed for Cadensy? | **UNKNOWN live AWS**, not supported by repo evidence; do not add or reuse without confirmation. |
| Strict production concurrency guarantees across all writers? | **NOT VERIFIED:** S0/2 QA and separate gate/rollout approval. |
| Full event replay vs invalidation + refresh? | V1 accepts ephemeral events with authoritative recovery. Revisit Outbox/Streams if incidents or measured recovery requirements exceed this model. |
| When to switch to WebSocket or managed AppSync? | Revisit if continuous bidirectional presence becomes essential, connection management dominates maintenance, RDS notification load becomes high, or managed cost wins in measured TCO. |
| Active-decision joining restriction (409)? | **DEFERRED backlog**, explicitly not changed here. |
| Production release, database migration, paid services, enabling gates? | **Human Owner only**. A strategy decision never grants operation authorization. |

## 10. Retrospective checklist — revisit after implementation

1. Did ordinary direct editing reduce AI dependency, time to modify a Plan and user friction?
2. Did manual add and every other writer stop bypassing governance, audit and version rules?
3. What *measured* commit→UI p50/p95 replaced the former 5-second polling experience?
4. Did SSE + authoritative REST reads remain simpler than WebSocket, or create refresh storms?
5. Were multi-task notifications and reconnection reliable enough without a durable Outbox?
6. What changed in actual monthly ECS/ALB/RDS cost and in agent maintenance/QA time?
7. Did protected privacy and decision fairness hold under concurrent and adversarial scenarios?
8. What assumptions proved wrong, and what evidence would justify moving to Redis, WebSocket, AppSync Events or CRDT?

## 11. Source and history map

- [Application documentation index](../README.md) · [Product contract](../PRODUCT.md) · [Repo architecture](../../README.md) · [Repository working rules](../../AGENTS.md).
- [Manual writer](../../backend/app/domain/plans/manual_items.py) · [HTTP API](../../backend/app/api/main.py) · [Decision orchestrator](../../backend/app/domain/decisions/orchestrator.py) · [Constraint engine](../../backend/app/domain/constraints/engine.py).
- [Plan models/revisions](../../backend/app/db/models.py) · [Transaction kernel](../../backend/app/domain/transactions/kernel.py) · [Backend deployment preparation](../../scripts/cd01/prepare_ecs_task_definition.py).
- [Trip app polling and changes](../../trip/src/final/TripAppState.jsx) · [Plan interaction runtime](../../trip/src/final/plan-feature/usePlanInteractionRuntime.js).
- For future implementation checks: [PostgreSQL NOTIFY](https://www.postgresql.org/docs/current/sql-notify.html), [LISTEN](https://www.postgresql.org/docs/current/sql-listen.html), [MDN SSE](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events), [AWS ALB docs](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/application-load-balancers.html).

**Historical-record rule:** Keep this account of decisions and assumptions intact. When code, QA or production reality changes, create a later dated evidence/retrospective record and update *current* documentation. Do not retroactively portray this design as an implementation or deployment success.
