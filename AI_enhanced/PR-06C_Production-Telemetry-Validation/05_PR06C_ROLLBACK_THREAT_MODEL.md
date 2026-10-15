# PR-06C.1B — Zero-Capacity Rollback Threat Model

**Status:** **BLOCKED — RECOVERY SAFETY NOT PROVEN**
**Scope:** offline threat model; no production experiment or recovery occurred.

The current service is `tripsync-backend:19`, desired/running/pending all zero,
with old digest `sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34`.
The intended new runtime digest is
`sha256:d2b0c580845c2b1afb738260c788f1cadb01416ba1018493fe0e9f2d133dd83c`.

ECS documents rollback to the last completed service revision after a circuit
breaker failure. [ECS service revisions](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/service-revision.html)
The published revision-property list does not establish desired-count behavior
for a combined task-definition-and-capacity update. Therefore no production
claim can be made about whether a rollback can start old `:19`.

| Threat / required question | Current evidence | Status | Mandatory stop, mitigation, or approval |
| --- | --- | --- | --- |
| A. New task definition fails registration | CD-01 uses `set -euo pipefail`; `UpdateService` follows registration. | Contractually guarded | Stop; no service update may be attempted. Inspect the failed candidate only under separate authorization. |
| B. New task fails to start | Circuit breaker is enabled and rollback is true. | Documented mechanism, outcome at zero **UNKNOWN** | Stop the release on failure. Do not assume rollback capacity; incident owner decides after isolated proof. |
| C. New task starts but fails ALB health | CD-01 requires at least one healthy target after ECS stable. | Guarded after mutation | Stop and preserve evidence. No retry or manual scale action without an incident approval. |
| D. New task passes ALB but `/api/health` fails | CD-01 requires read-only health success. | Guarded after mutation | Stop; classify application/runtime fault before any action. |
| E. Old image can run after failed combined update | Prior completed revision is `:19` with old digest; desired-count rollback semantics are not documented here. | **UNKNOWN / blocking** | Isolated environment experiment plus old-image forward-schema compatibility approval are required first. |
| F. Rollback results in non-zero desired count | Desired count is not among documented revision properties. | **UNKNOWN / blocking** | Same experiment must record desired/running/pending counts and target health for success and failure paths. |
| G. Database schema is forward-only | CD-01 performs only a read-only Alembic-head check; no downgrade is permitted. | Compatibility **UNKNOWN** | Database owner must approve new and old runtime compatibility at `c2a4b6d8e0f1`; otherwise do not release. |
| H. Telemetry/privacy defect appears after rollout | PR-06A code tests exist, but production delivery is unverified. Circuit breaker is deployment-health scoped. | Not automatically protected | Separate incident decision and separately authorized bounded validation; never use automatic rollback as a privacy response. |
| I. Concurrent external ECS mutation | GitHub concurrency does not lock AWS. | Residual risk | Second pre-mutation read rejects detected drift. Any detected or suspected concurrent actor stops the release and invalidates approval inputs. |
| J. Cost and traffic resume unexpectedly | Desired count one starts Fargate/ALB/logging and could receive traffic. | Partially bounded | Service owner accepts cost cap and observation window; stop on cost/security/business anomaly. |

## Required isolated experiment

Before production authorization, a non-production environment with equivalent
ECS deployment settings must exercise a baseline desired zero, a combined
task-definition plus desired-one update, a deliberately non-starting candidate,
and a health-failing candidate. The record must show service events,
deployment/revision identifiers, task definitions, desired/running/pending
counts, target health, and whether old code was started. It must not use
production database, secrets, or paid model traffic.

Only an experiment that answers E and F can change their status from UNKNOWN.
Passing source tests, GitHub approval, or an image build cannot substitute for
that evidence.

## Machine acceptance gate

The P1-A correction adds a committed, machine-checked
`08_PR06C_RECOVERY_SAFETY_ACCEPTANCE_MANIFEST.json`. Its current state is
`PENDING`, so it is negative evidence and must reject recovery. An accepted
future Manifest must bind experiment evidence to `rollback_evidence_sha`, prove
old-runtime forward-schema compatibility, record explicit approval evidence,
and snapshot the service configuration used for recovery. A Threat Model file
alone is never sufficient evidence.

## Post-completion failure policy

After the deployment has completed, telemetry, privacy, domain, provider, or
business anomalies are not circuit-breaker failures. The release operator must
stop automatic action, preserve bounded non-sensitive evidence, and obtain
incident/database-owner approval before choosing a forward fix, traffic stop,
or rollback. Database downgrade is never an automatic option.
