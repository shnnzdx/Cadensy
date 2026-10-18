# PR-06B — Production Readiness Assessment and Launch Plan

Assessment date: 2026-10-18
Scope: read-only source, recorded local evidence, and historical repository documentation.
Branch baseline: codex/pr06b-independent-development at 5e85db3c86503981a4cc5bc5c46829cd6144f1c3.
Authority boundary: no AWS API call, production database access, Provider invocation, migration, deployment, task-definition change, scheduler enablement, push, or merge occurred.

## 1. Executive summary

Gate A remains PASS — CLOSED. It proves the complete local product loop on disposable PostgreSQL, real Chromium, and a constrained fake Runtime. It does not prove production Provider, ECS, RDS, TLS, backup, or operations behaviour.

Gate B is CHANGES REQUIRED / NOT APPROVED. This is not a reopened PR-06B feature defect. It follows from source-confirmed launch blockers and unverified production controls:

1. Production code deliberately prevents durable Runtime execution.
2. CD-01 deliberately accepts only pre-PR-06B Alembic head c2a4b6d8e0f1, while this branch requires b7d3a1e9c642.
3. Historical cloud documentation records DEV_ALLOW_MEMBERSHIP_HEADER=1, which current production startup rejects.
4. The purge worker exists, but there is no approved production scheduler owner/leader model, alerting, capacity evidence, or backup-lifecycle proof.
5. Privacy-safe telemetry primitives exist, but durable-submission operational metrics, alarms, dashboards, and runbooks are not demonstrated.

No P0 is established from permitted evidence. These P1 items are production-readiness gaps or source-confirmed release incompatibilities. They need separately authorized engineering, infrastructure, and operations review; they do not invalidate local Gate A.

## 2. Scope and evidence rules

| Term | Meaning |
| --- | --- |
| VERIFIED | Concrete local source or recorded test evidence supports the stated property. |
| PARTIALLY VERIFIED | Only named parts are evidenced; it is not a live-environment guarantee. |
| UNVERIFIED | Evidence is absent, stale, or needs an authorized external read/experiment. |
| Historical evidence | A dated repository record, not a claim of current AWS state. |

This report does not claim end-to-end encryption, application field encryption, immediate backup deletion, exactly-once Provider invocation, or current AWS resource state.

No separate PR-06C report exists in the checked-out repository. PR-06C mentions in PR-06B documents are used only as an out-of-scope boundary.

## 3. Existing evidence inventory

| Control / requirement | Implementation reference | Evidence date | Verified | Unverified | Classification | Live verification? |
| --- | --- | --- | --- | --- | --- | --- |
| Local durable product loop | 13_PR06B_FINAL_INTEGRATION_ACCEPTANCE.md | 2026-10-17 | J01–J10 and Runtime-off PASS on local PostgreSQL/Chromium/fake Runtime. | Real Provider, ECS/RDS, production configuration. | VERIFIED locally | Yes for Gate B. |
| Backend final baseline | backend/test-results-pr06b4-full-pytest.log | 2026-10-17 | 1182 passed, 10 known warnings, exit 0. | Production workload/configuration. | VERIFIED locally | Release image validation later. |
| Durable schema | Alembic 9db6f1a1041b, 7a6c8e1d5f04, b7d3a1e9c642 | 2026-10-16–17 | Local PostgreSQL constraints, index recovery, guarded downgrade. | Production source revision, locks/timing, backup/restore, compatibility. | PARTIALLY VERIFIED | Yes. |
| Production durable execution | api/main.py:_durable_conversation_runtime_enabled | source read 2026-10-18 | Explicit gate is false in production/prod; Fake Runtime is rejected. | Approved enablement path and real Provider result. | VERIFIED disabled / UNVERIFIED as capability | Yes plus engineering approval. |
| Legacy/Pydantic semantics | runtime_factory.py, pydantic_composition.py | local tests through 2026-10-17 | Shared server-owned context/Preview; Pydantic composition fails closed. | Real protocol, output, latency, cost, cancellation. | PARTIALLY VERIFIED | Staging test. |
| Submission HMAC | conversations/service.py | local lifecycle evidence | Production requires an HMAC key; records store a version string. | Versioned keyring/rotation replay compatibility. | PARTIALLY VERIFIED | Design/test approval. |
| HTTPS/public routing | AWS/CUSTOM_DOMAIN_HTTPS_RESULT.md | historical 2026-08-15 | Historical ACM/HTTPS listener and smoke result. | Current redirect/rules/certificate/TLS health; document also records HTTP health success. | UNVERIFIED currently | Yes, read-only ALB/ACM. |
| RDS/private secret injection | historical AWS docs and CD-01 source | 2026-08-15 / source 2026-10-18 | Historical private RDS; CD-01 requires a DATABASE_URL secret reference. | Current encryption/TLS/backups/IAM/rotation. | PARTIALLY VERIFIED | Yes. |
| Deployment contract | docs/CD01_PRODUCTION_SAFE_DELIVERY.md and CD-01 workflow | source 2026-10-18 | Digest/manual/circuit-breaker/no-auto-downgrade design. | PR-06B migration path, current protections/task state. | PARTIAL and incompatible | Yes. |
| Purge | jobs/conversation_purge.py and jobs/scheduler.py | local B.2 tests | SKIP LOCKED, child-first purge, content-free counters, opt-in gate. | Owner/leader, replicas, batch load, alerts, backups. | PARTIALLY VERIFIED | Yes. |
| Telemetry | observability/telemetry.py | source/test 2026-10-18 | Closed categorical schema excludes content/options/credentials. | Durable metrics/dashboards/alarms/runbooks. | PARTIALLY VERIFIED | Yes. |
| Historical cloud runtime | AWS/TRIPSYNC_AWS_URLS.md and AWS/BACKEND_AI_RUNTIME_RUNBOOK.md | historical 2026-08-17 | Documents then-current header bypass=1. | Whether it remains active and compatible. | UNVERIFIED, conflicting | Mandatory. |

## 4. Runtime and Provider readiness

### Actual production-code behavior

_durable_conversation_runtime_enabled() requires CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED to be truthy and rejects APP_ENV=production or prod. Therefore durable execution cannot be enabled in production without an application-code change and separate approval. The route can persist a message, but it does not invoke the coordinator when unavailable; the frontend displays saved history and disables sending when runtime_available is false.

Other source-verified fail-closed controls:

- CADENSY_E2E_FAKE_RUNTIME=1 is rejected in production.
- CHAT_AGENT_RUNTIME=pydantic requires MOCK_AI=0, a DeepSeek key/model, and the exact official HTTPS DeepSeek endpoint; it does not fall back to Legacy.
- CONVERSATION_SUBMISSION_HMAC_KEY is mandatory in production.
- Durable context is capped at 160,000 characters with 8,000 system and 16,000 output reserves.
- Existing Runtime defaults are request/provider/tool 30/20/5 seconds, 8 rounds, and 120,000 total reported tokens.

Those are static implementation limits, not an approved spend, token, latency, or capacity budget.

### Provider uncertainty and costs

Recovery may create another fenced attempt after the five-minute lease. An old attempt cannot commit another Assistant Message, but a Provider may already have received its request. The system provides at-most-one committed durable output, not exactly-once Provider invocation. No evidenced production per-user/day spend cap, global rate limit, quota alarm, or approved maximum charge exists.

HMAC rotation is a P2 design gap: same-key replay calculates with current key material and compares it to the stored digest; no keyring lookup by hmac_key_version exists. Recovery by submission ID does not recompute input HMAC, but a same-key replay after rotation can conflict.

### Provider staging plan — proposal only

| Item | Required decision/evidence |
| --- | --- |
| Approvals | Product, Security, Finance/cost, and Provider account owner approve non-production credentials and revocation owner. |
| Environment | Isolated staging, synthetic accounts/trips/messages, non-production database, no production content import. |
| Runtime | Choose one reviewed Runtime/Provider/model; record config names, image digest, and gate posture without secrets. |
| Limits | Approve request/context/output token caps, concurrency, Provider timeout, recovery attempts, and total test spend as explicit TBD values before execution. |
| Failure cases | Credential failure, 401/429/5xx, timeout, malformed output/tool call, client disconnect, task termination, recovery, quota/capacity, redaction. |
| Telemetry | Opaque IDs, categorical outcome, elapsed time, approved model ID and safely reported usage only; never text/options/patches/credentials/bodies. |
| Stop | Unexpected content exposure, unbounded/repeated call, unauthorized Provider/model, unapproved spend, or inconsistent durable state. Disable Runtime first. |

## 5. Request delivery, timeout, and recovery

    HTTP admission -> authorize/lock/persist Submission -> commit
      -> coordinator only for newly claimed attempt and enabled gate
      -> context transaction -> detached Runtime/Preview -> fenced completion
      -> durable status/history polling

Admission commits before Runtime work. A browser disconnect after commit does not erase a Submission; owner status/history and Recovery can find it. Runtime calls use separate sessions and no DB transaction remains open during Provider work. Completion rechecks active ownership, lease, attempt, and fence, so late output cannot commit after revocation/deletion/recovery.

The 202 is truthful as admission, but if execution is enabled it may not be returned promptly because the route calls the coordinator synchronously before returning. ALB/client/FastAPI/ECS timeout ordering is not verified. A task termination can abandon a claim; recovery after the five-minute lease is the stated path. This is intentionally not a queue/worker architecture.

| Failure boundary | Current fact | Production evidence / parameter | Safe outcome |
| --- | --- | --- | --- |
| Browser disconnect | Submission is committed first. | Client timeout/retry/poll cadence: TBD. | Poll; do not create a new key; recover after lease only. |
| ALB/API deadline | Execution is request-bound. | ALB, proxy, client, uvicorn timeout ordering vs 30/20/5: TBD. | Admission may persist; never infer completion from disconnect. |
| Provider timeout | 20s inside 30s request budget. | Provider cancellation semantics: TBD. | Categorical failure if persistable; otherwise lease/recovery. |
| ECS deployment/task stop | No autonomous durable worker owns claim. | Drain/grace/SIGTERM/task concurrency: TBD. | Claim expires; newer fence wins; old output discarded. |
| DB fault/exhaustion | No Provider call before failed initial commit; no false completion. | Pool/connection/transaction limits: TBD. | State unavailable; owner discovers after restoration. |
| Non-cooperative work | Process-local capacity is 4; late thread holds a slot. | Load/capacity/task-count evidence: TBD. | Fail closed at capacity; never consume late result. |

Decision: request-bound execution is not production-approved until staging measures ordered budgets and shutdown behavior. If they cannot be made coherent, a separate architecture decision is required; this assessment does not add a queue.

## 6. Migration and rollback compatibility

Required chain:

    c2a4b6d8e0f1
      -> 9db6f1a1041b  membership lifecycle/preflight
      -> 7a6c8e1d5f04  concurrent validated active-account index cutover
      -> b7d3a1e9c642  five durable conversation tables

The first revision rejects unsupported membership states. The index cutover retains the legacy guard until the exact target B-tree is valid and ready. The final revision is additive/transactional but refuses data-bearing downgrade. PR-06B.1A downgrades also refuse when quarantine/electorate/rejoin history would be lost.

CD-01 is not PR-06B-ready: build and release hard-code c2a4b6d8e0f1, preflight only permits that old current/head, and no upgrade is executed. This is a source-confirmed release-pipeline incompatibility, not a claim about the current production database.

| App/schema combination | Evidence | Status | Release rule |
| --- | --- | --- | --- |
| Old / old c2a4 | Historical CD-01 design. | PARTIALLY VERIFIED | Existing baseline only. |
| Old / new b7d3 | No compatibility proof. | UNVERIFIED | Never assume rollback safety. |
| New / new b7d3 | Local PostgreSQL migrations + 1182 final tests. | PARTIALLY VERIFIED | Candidate staging only. |
| Mixed rolling versions / new schema | No proof. | UNVERIFIED | Needs explicit compatibility decision. |
| App rollback after durable rows | Downgrades refuse; old/new compatibility unverified. | UNVERIFIED | Default freeze/roll-forward. |

### Ordered production proposal — not authorized

1. Obtain data-owner, release, DBA/RDS, security and platform approval for a read-only preflight.
2. Record current Alembic revision, active image/task configuration names, DB version/load indicators, and migration owner.
3. Obtain a named available recovery snapshot and reviewed restore procedure before schema action.
4. Build a dedicated immutable migration-runner for this reviewed chain; rehearse compatibility, index interruption, and backup/restore in isolated staging.
5. Separately approve a PR-06B migration procedure that executes the exact chain once, captures pre/post revision/catalog evidence, and stops on preflight failure.
6. Post-migration, verify revision, constraints/indexes, application health and authorization safety before Runtime enablement.

Application rollback means returning to a schema-compatible previous task definition without deleting data. Database downgrade is not the default response. After durable rows exist, freeze or roll forward is safer unless approved compatibility evidence says otherwise. Do not use init_schema, blind stamp, or automatic Alembic downgrade.

## 7. TLS, encryption, secrets, and privacy

| Area | Source-supported fact | Gate-B evidence required |
| --- | --- | --- |
| API HTTPS | Historical 2026-08-15 record has ACM/HTTPS; it also records HTTP health success. | Read-only ALB listener/rules, ACM state, and HTTP redirect verification. |
| DB TLS | DATABASE_URL code only adds UTF-8 client encoding; no sslmode=verify-full enforcement. | RDS/client CA/TLS configuration and verification policy. |
| DB encryption/backups | Required by D7 but not proven locally. | Read-only RDS/KMS/backup window/retention and restore evidence. |
| Secret injection | CD-01 requires ECS secret reference for DATABASE_URL; historical docs describe SSM. | Current parameter/IAM scope, Provider/HMAC secret injection and rotation. |
| Least privilege | Historical plans describe narrow roles only. | Current execution/task role and DB-user privilege inspection. |
| Content privacy | Durable model/context + telemetry contract are owner/categorical. | CloudWatch/ECS/Provider logging, DB admin and backup access review. |

Logical deletion does not delete backups immediately. The review must inspect raw Conversation text, historical options/safe patches, private constraints, Provider request/response logging, credentials/HMAC, DB administrator access and backup retention. PR-06B is not E2EE.

## 8. Retention, purge, and operations

Application semantics are locally proven: qualifying activity gives 180-day retention, deletion blocks access immediately, purge_due_at is within 30 days, and run_once() uses bounded SKIP LOCKED batches and content-free expired/purged/overdue counters.

The scheduler starts with every backend process unless DISABLE_SCHEDULER=1. CADENSY_CONVERSATION_PURGE_ENABLED is opt-in but has no production leader/replica guard. Therefore one-owner execution and the 30-day operational SLA are not established.

| Need | Proposed control | Evidence before enablement |
| --- | --- | --- |
| Owner | Exactly one identified scheduler owner or reviewed leader mechanism; API replicas retain DISABLE_SCHEDULER=1. | Task/service design, failover behavior, access policy and runbook. |
| Concurrency | Keep DB SKIP LOCKED; do not treat it as owner policy. | Multi-replica staging evidence. |
| Capacity | Choose cadence/batch/deadline from measurement; code max batch 1,000 is not a capacity claim. | Query plan, volume, load, catch-up/restart duration. |
| Alerts | Last success age, run failure, expired/purged, overdue > 0, backlog age/size. Thresholds/owners are TBD. | Dashboard, routing and escalation test. |
| Backup lifecycle | Separate backup/replica retention from primary purge and user deletion messaging. | RDS configuration and privacy/legal policy. |

## 9. Observability and incident readiness

observability/telemetry.py supplies a content-free event schema with opaque IDs and categorical runtime/model/tool/deadline/capacity/late-completion fields. It excludes conversation bodies, option payloads, credentials and Provider bodies.

It is insufficient for PR-06B operations: no durable-submission/purge CloudWatch metric, dashboard, alarm, or on-call runbook was found. The durable coordinator is composed without a TelemetryContext; legacy-chat telemetry cannot prove durable acceptance/completion/recovery observability.

Minimum proposed signals are accepted/completed/failed/running-age submissions; recovery/stale-fence outcomes; runtime duration/capacity/context rejection/Provider category; DB transaction/authorization category; purge run/backlog/overdue; and approved aggregate usage/cost. SLOs, thresholds, log retention, responder assignment and escalation are TBD until operational approval.

## 10. Risk register

| ID | Type | Evidence | Severity | Required closure | Proposed owner | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| PRD-01 | Confirmed release incompatibility | Production gate rejects durable Runtime. | P1 | Separate enablement design/implementation, staging proof, Provider approval. | Backend + Product/Security | Block Gate B. |
| PRD-02 | Confirmed release incompatibility | CD-01 hard-codes old Alembic head. | P1 | Migration procedure, snapshot/restore rehearsal, compatibility proof. | Release + DBA | Block Gate B. |
| PRD-03 | Readiness gap | Purge lacks scheduler ownership/alerts evidence. | P1 | Owner/leader, staging failover/load, alert/runbook, backup lifecycle. | Platform + Privacy | Block Gate B. |
| PRD-04 | Readiness gap | No real Provider validation/spend authorization; recovery can repeat external call. | P1 | Controlled staging plan, cost/rate guardrails, approved results. | Product + Finance + AI owner | Block Gate B. |
| PRD-05 | Historical config conflict | 2026-08-17 docs say header bypass=1; current prod code rejects it. | P1 | Authorized read-only task/service/log inspection; reconcile Guest flow. | Security + Backend + Release | Block Gate B. |
| PRD-06 | Readiness gap | Current TLS/encryption/backup/IAM proof absent. | P1 | Read-only ALB/RDS/KMS/IAM/backup evidence and security sign-off. | Security + DBA/Platform | Block Gate B. |
| PRD-07 | Readiness gap | No PR-06B durable metrics/alerts/runbooks demonstrated. | P1 | Metrics/dashboard/alert/runbook evidence. | SRE/Platform + Backend | Block Gate B. |
| PRD-08 | Operational assumption | 30/20/5 limits and five-minute lease have no infrastructure timing proof. | P2 | Staging timeout/load/termination experiment. | Backend + Platform | Close before Runtime rollout. |
| PRD-09 | Design gap | Stored HMAC version has no keyring lookup for replay. | P2 | Rotation policy; separate keyring design/test if needed. | Security + Backend | Close before rotation. |

## 11. Staged launch plan — planning only

| Stage | Prerequisite / approver | Activity | Pass evidence | Stop / containment | Owner |
| --- | --- | --- | --- | --- | --- |
| 0 Evidence completion | Security, DBA, Platform, Release read-only approval | Inspect current ECS/task, ALB/ACM, RDS/TLS/encryption/backups, IAM/SSM, CloudWatch, GitHub Environment. | Dated inventory resolves PRD-05/06. | Conflicting unsafe configuration: freeze, no write. | Release |
| 1 Isolated staging Provider | Product, Security, Finance, AI approval | Synthetic data and bounded Provider scenarios. | Prewritten correctness/redaction/timeout/cost outcomes. | Disable Runtime/revoke staging credential. | AI/Product |
| 2 Migration rehearsal | DBA/data-owner/release approval | Backup/restore and full chain/interruption compatibility rehearsal. | Revision/catalog/timing/restore proof. | No auto downgrade; freeze/restore/roll forward by incident lead. | DBA + Release |
| 3 Operational readiness | Platform/SRE/Security approval | Runtime posture, timeout budget, scheduler owner, metrics/alerts/runbooks. | Reviewed manifest and alert delivery proof. | Keep Runtime/purge disabled. | Platform/SRE |
| 4 Limited production proposal | Explicit change approval | Define scope/window/abort criteria; all values are TBD here. | No P1 remains; prior stages signed. | Disable Runtime first; compatible app rollback only. | Product + Release |
| 5 Expansion decision | Product/Security/Ops approval | Review bounded rollout evidence. | Written approval against pre-agreed outcomes. | Hold/roll forward. | Product |

## 12. Rollback and incident decision tree

    Signal -> preserve content-free evidence and assign incident owner
      Provider/runtime failure or cost -> disable Runtime first; retain authorized history;
        do not delete Submissions; investigate/recover by lease and fence.
      API/DB/stuck submissions -> verify service/DB and durable state; no fabricated completion.
      Privacy/credential incident -> revoke/contain under approved security response;
        do not put content in telemetry.
      Migration/performance failure -> stop rollout; retain forward schema; decide freeze,
        roll forward, or approved restore. Never automatic data-bearing downgrade.
      Purge overdue -> keep access-side expiry; investigate owner/capacity and use approved catch-up.
      Secret exposure -> approved rotation; assess HMAC replay compatibility first.

Disabling Runtime while preserving authorized history reads is a viable first containment action only after a reviewed production configuration introduces that capability. This assessment does not authorize toggling it.

## 13. Outstanding approvals

| Missing item | Separate authorization required |
| --- | --- |
| Current task/service, ALB/ACM, RDS, KMS, IAM, backup, CloudWatch and GitHub Environment inspection | Read-only AWS/infrastructure/GitHub review. |
| Current DB revision/data profile/snapshot evidence | Read-only DBA/production DB review, then separate migration/rehearsal authority. |
| Real Provider behavior/credentials/spend | Controlled staging/Provider experiment with Finance/Security/Product approval. |
| Durable Runtime production gate/observability | Separate engineering design/remediation authority. |
| PR-06B CD/migration path | Separate delivery/migration engineering and deployment approval. |
| Purge ownership/alerts/backup lifecycle | Separate platform/privacy operational sign-off. |
| Timeout/load/termination | Separate staging resilience experiment. |

## 14. Gate decisions

### Gate A — PR-06B Local Integration Acceptance: PASS — CLOSED

No concrete P0/P1 application defect from this audit reopens Gate A. Findings concern intentionally disabled production capability, delivery compatibility, or unverified external controls.

### Gate B — Production Readiness: CHANGES REQUIRED / NOT APPROVED

The evidence rejects production approval today. A written plan is not a Gate B PASS. PRD-01 through PRD-07 need their stated evidence, remediation/decision, and responsible-owner approval under separately authorized work.

## 15. Repository accounting

This is the only intended tracked change. No application/test behavior, migrations, infrastructure source, gates, AWS resource, Provider, or PR-06C file was modified. The following remain intentionally untracked and excluded from commit:

    backend/test-results-pr06b4-e2e-control/
    backend/test-results-pr06b4-e2e-fixture.json
    backend/test-results-pr06b4-full-pytest.exit
    backend/test-results-pr06b4-full-pytest.log
    backend/test-results-pr06b4-local-e2e.stderr.log
    backend/test-results-pr06b4-local-e2e.stdout.log

Before commit, run staged-document git diff --check and re-check tracked/untracked worktree state. No full 1182-test rerun is needed or claimed for this documentation-only assessment.
