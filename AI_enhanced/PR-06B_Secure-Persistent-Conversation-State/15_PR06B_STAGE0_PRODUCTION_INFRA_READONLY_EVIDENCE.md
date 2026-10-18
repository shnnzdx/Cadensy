# PR-06B — Stage 0 Production Infrastructure Read-only Evidence

Assessment timestamp: 2026-10-18T15:53:12Z
Repository and branch: shnnzdx/Cadensy, codex/pr06b-independent-development
Baseline before this report: f0e5b5584194694aad672cb394ca97d09557e760
Scope: read-only source, GitHub metadata, and a narrowly scoped AWS identity attempt. No AWS resource inventory was performed after the identity gate failed.

## 1. Executive summary

Gate A remains PASS — CLOSED. No new, independently supported P0/P1 application defect was found.

Stage 0 cannot verify current AWS infrastructure. The AWS CLI is installed and local configured Region is us-east-1, but the authorized identity-only call failed with IncompleteSignature. Under the approved safety contract, no retry, profile replacement, account discovery, or ECS/RDS/ALB/IAM/Secrets/CloudWatch inspection followed.

Current GitHub metadata is available and improves one historical observation: the Main Environment now has required-reviewers and branch-policy protection. CD-01 source still hard-codes the pre-PR-06B Alembic head c2a4b6d8e0f1, while PR-06B requires b7d3a1e9c642. Production durable Runtime remains impossible by current application code because the gate is false whenever APP_ENV is production or prod.

Gate B remains CHANGES REQUIRED / NOT APPROVED. Stage 0 current-infrastructure verification is BLOCKED by invalid AWS authentication. No production deployment, remediation, or feature change is authorized by this result.

## 2. Authorization boundaries and method

Permitted actions used:

- Read repository source, AWS historical documentation, and workflow templates.
- Verify AWS CLI presence, then make one identity-only AWS STS request.
- Read current GitHub repository, workflow, environment, and branch-protection metadata through the already authenticated GitHub CLI.
- Create this Markdown report only.

No ECS, RDS, ELBv2, ACM, EC2, KMS, IAM, SSM, Secrets Manager, CloudWatch, EventBridge, ECR, or GitHub mutation command was issued. No AWS secret, SSM plaintext value, database data, application log event, Provider credential, or customer content was retrieved. The identity failure was not retried.

Evidence classifications:

| Classification | Meaning |
| --- | --- |
| CURRENT VERIFIED | Current, timestamped read-only metadata establishes the stated property. |
| SOURCE VERIFIED | Checked-out source or workflow establishes the stated behavior. |
| HISTORICAL ONLY | Dated repository record; not evidence of current cloud state. |
| PARTIALLY VERIFIED | Only the stated subset is established. |
| UNVERIFIED | Required evidence was not obtainable. |
| BLOCKED | A required live inspection was deliberately stopped by an authorization/safety gate. |
| CONFLICT | Material disagreement among current source, current metadata, or historical records. |

## 3. AWS identity and scope gate

Expected scope derived from repository evidence: Region us-east-1; historical project account ending in 2746; ECS cluster tripsync-cluster; backend service tripsync-backend-service; RDS instance tripsync-postgres; ALB tripsync-backend-alb. These names were not used to discover or enumerate cloud resources before identity confirmation.

| Evidence ID | Observation | Expected | Classification | Related risk | Next action |
| --- | --- | --- | --- | --- | --- |
| S0-AWS-01 | AWS CLI v2.36.9 is installed. | A supported CLI is available. | CURRENT VERIFIED | None | None. |
| S0-AWS-02 | Local configured Region resolved to us-east-1. | Repository source names us-east-1. | PARTIALLY VERIFIED | PRD-06 | Do not treat local configuration as account authorization. |
| S0-AWS-03 | aws sts get-caller-identity returned IncompleteSignature. Account/principal were not established. | Authenticated expected account and a read-only-capable principal. | BLOCKED | PRD-03, PRD-05, PRD-06, PRD-07 | Account owner supplies an authorized, valid read-only authentication context outside this task, then a new Stage 0 authorization is issued. |

The failed STS request is not evidence that the target account or resources do not exist. It is also not evidence that any deployed resource is unhealthy.

## 4. Current GitHub delivery evidence

| Evidence ID | Observation at 2026-10-18T15:53:12Z | Classification | Implication |
| --- | --- | --- | --- |
| S0-GH-01 | Remote is shnnzdx/Cadensy; the active branch is codex/pr06b-independent-development. | CURRENT VERIFIED | This report concerns the intended repository. |
| S0-GH-02 | GitHub reports 19 active workflows, including both CD-01 workflows. | CURRENT VERIFIED | Workflow inventory is current; activation alone is not deployment evidence. |
| S0-GH-03 | GitHub Main Environment exists with two protection-rule types: required_reviewers and branch_policy; protected branches are enabled. | CURRENT VERIFIED | This resolves the historical absence of an Environment reviewer rule, but does not approve a release. |
| S0-GH-04 | Main branch protection is readable; status checks and administrator enforcement are configured; pull-request-review requirement is not configured. | CURRENT VERIFIED | Environment approval remains the CD-01 release boundary; branch policy needs its own future governance review if required. |
| S0-GH-05 | Latest CD-01 build/publish run completed successfully on 2026-10-14 from a main push. No CD-01 production-release run was returned. | CURRENT VERIFIED | Current metadata does not show a CD-01 production promotion. It does not prove current ECS image or service state. |
| S0-GH-06 | GitHub Actions policy is enabled, allowed_actions is all, and SHA pinning is not required. | CURRENT VERIFIED | Supply-chain policy is outside frozen PR-06B functionality, but should be considered by release governance. |

Source review of cd01-build-publish.yml confirms a successful CI Regression Gates push on main can automatically build and push immutable ECR artifacts after Main Environment approval. It has no ECS, RDS, or Alembic mutation path. cd01-production-release.yml is workflow_dispatch only and targets Main. Therefore a source push cannot implicitly promote the ECS service through CD-01, but a qualifying main push can create ECR artifacts. Historical manual workflows remain active and are not release approval paths.

## 5. ECS, image, and configuration evidence

| Item | Current result | Classification | Evidence / implication |
| --- | --- | --- | --- |
| Backend running state, desired/running/pending counts | Not inspected. | BLOCKED | Requires authenticated ECS read metadata. |
| Active task definition and deployed image digest | Not inspected. | BLOCKED | Historical sources conflict: AWS records describe task definition 17, while CD-01 baseline records 19. Neither is current proof. |
| Health, circuit breaker, health check, capacity and Auto Scaling | Not inspected. | BLOCKED | Requires ECS service/task and Application Auto Scaling reads. |
| APP_ENV, Runtime gate, Fake Runtime, CHAT_AGENT_RUNTIME, MOCK_AI, HMAC reference | Not inspected in task-definition metadata. | BLOCKED | Secret values must remain unread; safe names and non-secret values require a future authenticated read. |
| DEV_ALLOW_MEMBERSHIP_HEADER deployed value | Not inspected. | BLOCKED | Historical AWS documentation states 1; CD-01 clone policy forces 0; application startup rejects truthy values in production. |

Relevant source controls are verified:

- backend/app/api/main.py rejects DEV_ALLOW_MEMBERSHIP_HEADER when APP_ENV is production or prod.
- _durable_conversation_runtime_enabled requires its gate to be truthy and rejects production/prod unconditionally.
- backend/app/agents/runtime_factory.py protects CADENSY_E2E_FAKE_RUNTIME from production use.
- backend/app/domain/conversations/service.py requires CONVERSATION_SUBMISSION_HMAC_KEY in production.

PRD-01 remains confirmed by source. A production code change plus separately authorized staging validation is required before durable execution can be available in production. PRD-05 remains a source-versus-historical conflict; current ECS configuration is unknown.

## 6. ALB, ACM, HTTPS, network, and RDS evidence

No current AWS resource metadata could be read after S0-AWS-03.

| Area | Historical / source evidence | Current classification | Required future read-only evidence |
| --- | --- | --- | --- |
| HTTPS and ACM | AWS/CUSTOM_DOMAIN_HTTPS_RESULT.md records an Issued ACM certificate and HTTPS:443 for app.cadensy.top on 2026-08-15. | HISTORICAL ONLY | ALB listeners/rules, ACM status/expiry, target groups/health, and HTTP redirect posture. |
| HTTP behavior | The same historical report records HTTP:80 and an HTTP health response, so redirect behavior was not established by that report. | UNVERIFIED | Listener default actions and rules. |
| API routing | Historical record says /api/* routes through the ALB. | HISTORICAL ONLY | Current listener rules and target health. |
| Security groups / subnets | Historical plans describe public ALB, private RDS, and backend-to-RDS access only. | HISTORICAL ONLY | Current ALB/backend/RDS security-group rules and subnet mappings. |
| RDS engine/status/network | Historical records name a PostgreSQL instance and private endpoint. | HISTORICAL ONLY | describe-db-instances and subnet-group metadata, without connecting to PostgreSQL. |
| Encryption and backups | D7 requires encryption at rest and encrypted backups; no permitted current RDS/KMS metadata was obtained. | BLOCKED | StorageEncrypted, KMS key metadata/status, backup retention/window, snapshot metadata, deletion protection, and restoration rehearsal record. |
| Database TLS | Repository database session code does not enforce sslmode=verify-full. RDS metadata alone would not prove client certificate verification. | SOURCE VERIFIED / UNVERIFIED operationally | Reviewed task configuration, connection policy, and a separately authorized TLS validation plan. |
| Current Alembic revision | No production database access is authorized. | UNVERIFIED | A dated, separately authorized DBA record; do not query production as part of this Stage. |

Storage encryption, backup retention, private accessibility, and a KMS key reference would be useful future metadata, but none alone proves restore readiness, database TLS verification, or application migration state.

## 7. CD-01 and migration compatibility

| Evidence ID | Observation | Classification | Risk result |
| --- | --- | --- | --- |
| S0-SRC-01 | cd01-production-release.yml sets APPROVED_ALEMBIC_HEAD to c2a4b6d8e0f1 and its dedicated task checks current/head equality; it does not run upgrade, downgrade, stamp, or init-schema. | SOURCE VERIFIED | PRD-02 remains CONFIRMED. |
| S0-SRC-02 | PR-06B migration chain ends at b7d3a1e9c642 after 9db6f1a1041b and 7a6c8e1d5f04. | SOURCE VERIFIED | CD-01 cannot promote a PR-06B schema release as written. |
| S0-SRC-03 | CD-01 uses workflow_dispatch for service promotion, immutable image digests, an existing service clone, and Main Environment approval. | SOURCE VERIFIED / CURRENT VERIFIED for Environment protection | Useful baseline; it does not establish an approved PR-06B migration procedure. |
| S0-GH-05 | No production-release workflow run was returned by current GitHub metadata. | CURRENT VERIFIED | No current CD-01 release evidence changes PRD-02. |

The permitted Stage 0 work neither queried Alembic in RDS nor ran a migration. Old/new schema compatibility, post-migration application rollback, snapshot/restore, and production revision remain unverified.

## 8. Provider, purge, observability, IAM, SSM, and Secrets

### Runtime and Provider

Runtime production execution remains disabled by source. Static limits remain request/provider/tool 30/20/5 seconds, five-minute submission lease, process-local capacity of four, eight rounds, and 120,000 reported tokens. These are implementation settings, not production capacity, timeout, Provider-cancellation, quota, or spend evidence. No paid Provider was invoked.

### Scheduler and purge

conversation_purge.py uses child-first bounded batches and PostgreSQL SKIP LOCKED. scheduler.py contains the CADENSY_CONVERSATION_PURGE_ENABLED gate; API startup starts the scheduler unless DISABLE_SCHEDULER equals 1. No current ECS task/service/EventBridge metadata could establish a unique scheduler owner, replica count, schedule, successful runs, backlog, or overdue alert. PRD-03 remains unchanged and blocking.

### Telemetry and CloudWatch

observability/telemetry.py defines content-safe telemetry primitives with opaque correlation data. The durable coordinator is not proven to emit a complete durable submission/recovery/purge signal set. No current log-group retention, metric, dashboard, alarm, SNS routing, or CloudWatch aggregate metric metadata was inspected. Generic ECS/ALB metrics must not be counted as PR-06B durable monitoring. PRD-07 remains unchanged and blocking.

### IAM, SSM, Secrets, and KMS

No task/execution role, policy, SSM parameter metadata, secret metadata, KMS metadata, rotation configuration, or access boundary could be inspected. No secret value was requested. Least privilege, separation of ECS task and execution roles, database/Provider/HMAC injection, and rotation ownership are UNVERIFIED. PRD-06 remains blocking.

## 9. PRD risk reconciliation

| Risk | Original severity | Stage 0 current evidence | Current classification | Gate B effect / required approval |
| --- | --- | --- | --- | --- |
| PRD-01 Production Runtime Gate | P1 | Production code disables durable Runtime unconditionally. | CONFIRMED, SOURCE VERIFIED | Blocker. Backend/Product/Security approve a separate enablement design and staging proof. |
| PRD-02 CD-01 Migration Contract | P1 | CD-01 still accepts c2a4 only; PR-06B head is b7d3. | CONFIRMED, SOURCE VERIFIED | Blocker. Release/DBA approve migration compatibility and procedure work. |
| PRD-03 Purge Operational Ownership | P1 | Code has a gate and SKIP LOCKED but no current owner/alert evidence. | UNCHANGED, BLOCKED live verification | Blocker. Platform/Privacy authorize read-only evidence then operations package. |
| PRD-04 Provider and Spend Guardrails | P1 | No Provider call, spend control, or live quota evidence was authorized. | UNCHANGED, UNVERIFIED | Blocker. Product/Finance/Security approve bounded staging experiment. |
| PRD-05 Historical Membership Header Conflict | P1 | Historical records say 1; current production startup rejects truthy setting; ECS not inspectable. | CONFLICT, current state BLOCKED | Blocker. Security/Backend/Release authorize task-definition metadata review. |
| PRD-06 TLS, Encryption, Backup, IAM | P1 | No current AWS metadata; client source lacks sslmode=verify-full enforcement. | PARTIAL source / BLOCKED infrastructure | Blocker. Security/DBA/Platform read-only review and remediation authority if warranted. |
| PRD-07 Durable Monitoring and Runbooks | P1 | Safe telemetry source exists; no durable operational telemetry or CloudWatch evidence. | PARTIAL source / BLOCKED infrastructure | Blocker. SRE/Platform authorize monitoring/runbook package. |
| PRD-08 Timeout, Lease, Capacity | P2 | Source constants are known; no real task/load/shutdown/Provider behavior evidence. | UNVERIFIED | Must close before Runtime rollout. Backend/Platform staging experiment. |
| PRD-09 HMAC Rotation Compatibility | P2 | Stored version has no evidenced keyring lookup for same-key replay after rotation. | CONFIRMED, SOURCE VERIFIED | Security/Backend decide rotation policy before a rotation. |

## 10. Confirmed facts, unknowns, and exact evidence gaps

Confirmed: GitHub Main protection exists; CD-01 build has run; no CD-01 production-release run was returned; CD-01 is pinned to the old schema head; durable Runtime is source-disabled in production; the local fake runtime cannot be selected in production; source has a purge mechanism and safe telemetry primitives.

Unknown or blocked: AWS account/principal; ECS service existence and health; deployed task/image/config; header setting; ALB/ACM/listeners/target health/security groups; RDS existence/status/encryption/backups/public exposure; TLS enforcement; IAM/SSM/Secrets/KMS configuration; EventBridge/scheduler ownership; CloudWatch retention/metrics/dashboards/alarms; current database revision; backup restore success; Provider behavior/cost.

The prerequisite for a repeat Stage 0 AWS audit is a valid, approved local authentication context whose identity-only STS result matches the repository's intended account and Region. That future work must again use read-only metadata only and must not request secret values or connect to production PostgreSQL.

## 11. Follow-up authorization packages

| Package | Scope and rationale | Required approval | Planned evidence / completion | Forbidden before approval |
| --- | --- | --- | --- | --- |
| A: AWS read-only evidence retry | Repair the operator authentication outside this repository, then inspect only named infrastructure metadata. Needed for PRD-03/05/06/07. | Account owner + Security/Platform read-only authorization. | Valid STS identity and dated redacted ECS/ALB/RDS/IAM/CloudWatch inventory. | Any resource mutation, secret retrieval, RDS connection. |
| B: Runtime enablement design | Address PRD-01 source gate, Provider behavior, timeouts, cost, and recovery. | Product, Security, Finance, Backend. | Reviewed code/design plus bounded synthetic staging test. | Provider call, production gate enablement, production content. |
| C: CD-01 migration compatibility | Address PRD-02 with exact chain, compatibility, snapshot/restore rehearsal, and forward rollback plan. | DBA/data owner, Release, Security. | Isolated rehearsal and approved migration-runner procedure. | Production migration, snapshot/restore, automatic downgrade. |
| D: Purge and durable observability operations | Establish one scheduler owner, alert/runbook and privacy-safe operational signals. | Platform/SRE, Privacy, Security. | Multi-replica/failover evidence, dashboards, alarm delivery, deletion/backup messaging review. | Enabling purge or scheduler in production. |
| E: Infrastructure security remediation | Only if Package A proves a configuration gap in TLS, encryption, backup, IAM, or secrets. | Security, DBA/Platform, change authority. | Approved change plan and post-change non-content evidence. | Any configuration change during assessment. |

## 12. Gate decision and repository accounting

Gate A: PASS — CLOSED.

Gate B: CHANGES REQUIRED / NOT APPROVED. Separately, Stage 0 live-infrastructure evidence collection is BLOCKED / NOT APPROVED until AWS identity can be safely verified. Current GitHub evidence narrows the release-governance picture but cannot close the confirmed PRD-01/02 blockers or the AWS-dependent PRD-03 through PRD-07 evidence gaps.

Changed file: AI_enhanced/PR-06B_Secure-Persistent-Conversation-State/15_PR06B_STAGE0_PRODUCTION_INFRA_READONLY_EVIDENCE.md only.

Read-only commands executed, with sensitive values suppressed:

    aws --version
    aws sts get-caller-identity
    gh auth status
    gh api GET repository workflow, environment, branch-protection, and workflow-run metadata
    git and rg source/document inspection commands

No full test suite was rerun because this is an evidence-only report. Before commit, validate the staged document with git diff --check. Existing untracked local PR-06B.4 test artifacts remain excluded from the commit. No external resource mutation occurred.
