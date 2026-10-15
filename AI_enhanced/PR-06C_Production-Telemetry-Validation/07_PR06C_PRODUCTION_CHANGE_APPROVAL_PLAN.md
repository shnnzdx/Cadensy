# PR-06C.1B — Production Change Approval Plan

**Current state:** **BLOCKED — RECOVERY SAFETY NOT PROVEN**
**This is an approval plan, not a runbook authorization.**

## Known baseline and bounded proposal

| Field | Required value / status |
| --- | --- |
| Account / region | `448678332746` / `us-east-1` |
| Cluster / service | `tripsync-cluster` / `tripsync-backend-service` |
| Expected baseline task definition | `arn:aws:ecs:us-east-1:448678332746:task-definition/tripsync-backend:19` |
| Expected baseline image | `sha256:c51782b88887c71e640687d14097f93d5111f2dcdf7cfff748053e4247e6ba34` |
| Expected baseline count | `0` |
| Proposed recovery count | `1`, only after service-owner approval |
| Intended runtime digest | `sha256:d2b0c580845c2b1afb738260c788f1cadb01416ba1018493fe0e9f2d133dd83c` |
| Intended migration digest | `sha256:7bf23c5afd772b85228fe0c3a592eeef8469095a7e4e78ccdab9f394bb30f4af` |
| Source evidence | `b589fc7786c097d4bfe0eafe430afd90b00ca2b7` and its recorded CI/build evidence |
| Rollback evidence SHA | Must be the exact reviewed commit on `main` containing `05_PR06C_ROLLBACK_THREAT_MODEL.md`; it cannot be invented or pre-filled before review. |
| Recovery Safety Acceptance Manifest | `08_PR06C_RECOVERY_SAFETY_ACCEPTANCE_MANIFEST.json` is currently `PENDING`; zero-capacity recovery is therefore unavailable. |

## Required approvals, in order

1. An independent reviewer must approve this design, threat model, contract,
   tests, and the isolated-environment rollback experiment. The reviewer must
   approve a completed Acceptance Manifest rather than only the Threat Model.
2. The database owner must attest that both the intended runtime and any
   possible old `:19` runtime are compatible with the current forward schema;
   no automatic downgrade is allowed.
3. The service owner must explicitly accept desired count one, the Fargate
   compute estimate, temporary rollout capacity, and resumption of ALB/log
   traffic. The known compute-only estimate is $0.0123425/hour, $0.2962200/day,
   or $9.0100250 for a 730-hour month; ALB LCU, CloudWatch, transfer, and
   provider cost remain UNKNOWN.
4. A release owner must provide a bounded observation window and named incident
   decision-maker. No concurrent console/CLI/automation update may be active.
5. Only then may a GitHub `Main` Environment required reviewer approve one
   manually dispatched CD-01 run using the exact reviewed inputs above.
6. Any synthetic telemetry or CloudWatch validation has separate explicit
   authorization; it is not granted by capacity recovery approval.

No experiment is authorized by this plan. Before one is proposed, its separate
plan must state non-production account/environment, least-privilege roles,
fixed cost ceiling, teardown/stop conditions, no production database/secrets,
and no paid provider calls. Only an approved experiment may replace the
Manifest's PENDING fields with real evidence.

## Change boundaries and stop conditions

The only proposed mutation, after all approvals, is the reviewed CD-01 path
that registers its digest-pinned candidate and issues one guarded service
update with desired count one. It must not use a legacy Phase 5/6 workflow,
direct `aws ecs update-service`, database migration/downgrade, IAM/secret/gate
change, or manual retry.

Stop before service mutation if any input/provenance/baseline/gate/schema check
fails, the isolated proof is absent, reviewer protection is absent, or an
external mutation is detected. Stop after service mutation if ECS fails to
converge to exactly one new task, ALB target health is not healthy,
`GET /api/health` fails, cost/security/business symptoms appear, or telemetry
validation reveals a defect. Preserve evidence and use the approved incident
process; do not automatically retry, scale, roll back, or downgrade.

## Required observation record

The eventual approved release record must retain UTC timestamps, dispatch
inputs, baseline/recheck/final service counts, task-definition and image digests,
deployment events, ALB health, API health, bounded log-query window, and the
human approvals. It must distinguish ECS documented facts from operator
inference and must not claim CloudWatch delivery or production validation until
those checks actually occur.
