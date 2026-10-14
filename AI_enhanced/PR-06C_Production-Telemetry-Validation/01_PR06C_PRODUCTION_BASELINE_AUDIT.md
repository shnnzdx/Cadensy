# PR-06C — Production Baseline Audit (C0)

**Audit timestamp:** 2026-10-14T23:39:48Z
**Scope:** read-only production-delivery baseline for PR-06A safe telemetry.
**Result:** **BLOCKED — the approved PR-06A image is not running; no delivery validation is possible.**

## Audit boundary

This document distinguishes repository/GitHub evidence from live AWS evidence.
Only read-only AWS APIs were used. No AWS resource was created, updated,
deleted, deployed, or queried for raw data. No SSM secret values, raw
CloudWatch log events, or business-user data were read.

| Check | Status | Evidence |
| --- | --- | --- |
| Intended release source is merged `main` | PASS | Local branch was created from `origin/main` at `b589fc7786c097d4bfe0eafe430afd90b00ca2b7`. |
| Approved deployment target is defined | PASS | `.github/workflows/cd01-production-release.yml` pins account `448678332746`, region `us-east-1`, repository `tripsync-backend`, cluster `tripsync-cluster`, and service `tripsync-backend-service`. This is source configuration, not a live-resource assertion. |
| Local CLI can authenticate to the approved AWS account | PASS | At 2026-10-14T23:39:48Z, credentials were loaded only into the audit subprocess from the user-designated `backend/.env`; STS returned account `448678332746` for explicit region `us-east-1`. Credential values were neither printed nor persisted. |
| AWS region/account baseline is live-verified | PASS | STS account `448678332746`; command region `us-east-1`, matching CD-01's approved target. |
| ECR repository exists in the approved account | PASS | Both approved image digests were returned by authenticated `ecr describe-images` from `tripsync-backend`. |
| Runtime image is present and carries the source tag | PASS | Digest `sha256:d2b0c580845c2b1afb738260c788f1cadb01416ba1018493fe0e9f2d133dd83c` has exact tag `b589fc7786c097d4bfe0eafe430afd90b00ca2b7`; pushed 2026-10-14T23:22:15-05:00. |
| Migration image is present and carries the source tag | PASS | Digest `sha256:7bf23c5afd772b85228fe0c3a592eeef8469095a7e4e78ccdab9f394bb30f4af` has exact tag `b589fc7786c097d4bfe0eafe430afd90b00ca2b7-migration`; pushed 2026-10-14T23:22:18-05:00. |
| ECS service is active and identifies its current task definition | PASS | `tripsync-backend-service` is `ACTIVE` and points to `tripsync-backend:19`. It has `desiredCount=0`, `runningCount=0`, and `pendingCount=0`; this is not a running production runtime. |
| Current ECS task definition retains `awslogs` and its log group/stream prefix | PASS | `tripsync-backend:19` is Fargate/`awsvpc` with task and execution roles, container log driver `awslogs`, group `/ecs/tripsync-backend`, region `us-east-1`, and prefix `ecs`. |
| ECS rollback circuit breaker and healthy deployment posture | FAIL | Circuit breaker is enabled with rollback, `minimumHealthyPercent=100`, and `maximumPercent=200`; however the completed primary deployment has zero desired and zero running tasks, so there is no healthy runtime to protect or observe. |
| Production task uses `DEV_ALLOW_MEMBERSHIP_HEADER=0` and all R5 gates `0` | PASS | The current task definition is `APP_ENV=production`, `DISABLE_SCHEDULER=1`, `DEV_ALLOW_MEMBERSHIP_HEADER=0`, and all 17 configured `CADENSY_B*` protected gates are `0`. `DATABASE_URL` is present only as a secret name; its value was not read. |
| Live schema is at Alembic `c2a4b6d8e0f1` | UNKNOWN | No read-only migration-preflight task was run. That task is a release-phase operation, not C0. |
| CloudWatch log group and retention | PASS | `/ecs/tripsync-backend` exists with `RetentionInDays=7` (metadata-only `describe-log-groups` query). |
| CloudWatch permission and PR-06A telemetry delivery behavior | UNKNOWN | IAM policy detail and raw events were deliberately not read. More importantly, zero application tasks are running and the active task definition references the old image, so no PR-06A stdout delivery can exist yet. |

## GitHub build provenance (not a replacement for ECR verification)

| Check | Status | Evidence |
| --- | --- | --- |
| Main CI regression gate | PASS | GitHub run [38653316630](https://github.com/shnnzdx/Cadensy/actions/runs/38653316630), `CI Regression Gates`, completed successfully for push SHA `b589fc7786c097d4bfe0eafe430afd90b00ca2b7`. |
| Immutable image build | PASS | GitHub run [38653488998](https://github.com/shnnzdx/Cadensy/actions/runs/38653488998), `CD-01 — Build and Publish Immutable Backend Images`, completed successfully for the same SHA. |
| Runtime digest produced by that build | PASS | Build log records `sha256:d2b0c580845c2b1afb738260c788f1cadb01416ba1018493fe0e9f2d133dd83c` for tag `b589fc7786c097d4bfe0eafe430afd90b00ca2b7`. |
| Migration digest produced by that build | PASS | Build log records `sha256:7bf23c5afd772b85228fe0c3a592eeef8469095a7e4e78ccdab9f394bb30f4af` for tag `b589fc7786c097d4bfe0eafe430afd90b00ca2b7-migration`. |

## Safe telemetry delivery contract (source audit)

| Check | Status | Evidence |
| --- | --- | --- |
| Telemetry payload schema is closed | PASS | `backend/app/observability/telemetry.py` permits only `_APPROVED_EVENT_TYPES` and serializes only `_APPROVED_EVENT_PAYLOAD_FIELDS`. |
| Default telemetry output is single-line JSON stdout without logging fallback | PASS | `_default_sink()` uses a direct `sys.stdout.write` and `flush`; `SafeTelemetryEmitter.emit()` catches failures and returns `False`. |
| Legacy lifecycle recorder is accurately characterized | PASS | `backend/app/agents/trace.py:record_agent_lifecycle()` is a compatibility no-op. It does not itself deliver lifecycle telemetry. |
| New safe lifecycle event wiring exists in source | PASS | `backend/app/agents/execution.py` maps `AgentLifecycleEvent` facts to closed safe events through `_emit_lifecycle_event()` when a telemetry context is supplied. |
| New lifecycle event delivery in the live service | UNKNOWN | `tripsync-backend:19` uses `sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34`, not the PR-06A runtime digest, and zero tasks are running. Source wiring is not production-delivery proof. |

## Fail-closed disposition

**C0 is complete and C1 is blocked.** The release workflow does not increase
ECS desired count. A separately authorized operational decision must first
establish a non-zero intended service capacity; then an explicitly approved
CD-01 release can create the PR-06A task revision and validate it. Until that
separate authorization, do not dispatch CD-01, register a task definition,
update ECS, run a task, query raw CloudWatch events, or make any AWS change.
