# PR-06B Coordinated Full-Stack Production Release Runbook

## Purpose and boundary

This is the operator sequence for one coordinated PR-06B Backend and Frontend
release. It begins only after the corresponding release-tooling PR is merged to
`main` and both immutable images have been rebuilt for that final merge SHA.

It does not enable Durable Conversation Runtime or any paid AI Provider. The
release profile keeps `MOCK_AI=0`, the scheduler disabled,
`CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=0`, and
`CADENSY_E2E_FAKE_RUNTIME=0`. The task-definition helpers remove model-provider
credential references while retaining `DATABASE_URL`, the conversation HMAC,
and normal non-AI application secrets. A legacy AI operation with no injected
key fails locally before an external Provider invocation; it does not return a
fabricated model result.

Do not use `phase8-frontend-provision.yml` for this release. It is a historical
infrastructure-provisioning workflow and is outside this controlled cutover.

## Preconditions common to every gate

- Record the merged final `main` SHA; never reuse a pre-merge digest merely
  because its source was similar.
- Run CD-01 backend build/publish and CD-01 frontend build/publish for that
  exact SHA. Record both digest-pinned image references from their job
  summaries.
- Keep the current healthy services at desired count one until the applicable
  approval window begins. Preserve their current task-definition ARNs and image
  references as rollback evidence.
- `Main` must retain GitHub Environment required-reviewer protection. Every
  mutating workflow below stops at that independent approval boundary.
- No process or operator log may contain a SecureString value, database URL,
  bearer token, or Provider credential.

## B0 — Backend bootstrap approval and procedure

B0 is a separate authorization. It is the only step that creates the missing
conversation-submission HMAC configuration; it does not run Alembic.

1. An approved operator generates a high-entropy, dedicated HMAC value outside
   GitHub logs and stores it once as the SSM SecureString
   `/tripsync/backend/prod/conversation-submission-hmac`. The expected ECS
   `valueFrom` reference is:

   `arn:aws:ssm:us-east-1:448678332746:parameter/tripsync/backend/prod/conversation-submission-hmac`

2. Change the existing `tripsync-backend-execution` role only enough to resolve
   that one SSM parameter using `ssm:GetParameters`. If the SecureString uses a
   customer-managed KMS key, grant the exact required `kms:Decrypt` permission
   for that key as well. Preserve its current database-parameter access; do not
   grant wildcard Parameter Store access and do not change the task role.

3. Describe the running `tripsync-backend-service` task definition and resolve
   its currently running backend image to an ECR SHA-256 digest. The bootstrap
   revision must use that same digest rather than an image tag, preserving the
   exact current application bytes.

4. Run the offline helper
   `scripts/cd01/prepare_backend_bootstrap_task_definition.py` against that
   describe-task-definition JSON, supplying the resolved digest and the exact
   HMAC parameter ARN. It refuses malformed input, missing database references,
   missing production/scheduler posture, a wrong HMAC ARN, or an already-HMAC
   configured source definition.

5. Inspect the generated JSON before registration. The only intended effective
   profile change is the HMAC reference plus the existing release safety
   posture: Provider credentials are absent, `MOCK_AI=0`, scheduler and all
   protected gates are off, and Durable/Fake Runtime are both off. Database,
   network mode, CPU/memory, roles, logging, health checks, volumes, and other
   registrable fields are cloned.

6. Under the distinct B0 execution approval, register that revision and update
   only `tripsync-backend-service`, retaining desired count one and the current
   circuit-breaker configuration. Wait for ECS stability, backend target health,
   `/api/health`, login, and a non-AI Trip API smoke check. Save the previous
   task-definition ARN. If any check fails, return only the backend service to
   that previous ARN; do not run a database downgrade.

No HMAC creation, IAM change, task-definition registration, or service update
is performed by this repository PR or by the offline helper.

## M0 — Database preflight and backup approval

M0 is required after B0 and before Gate M:

1. Obtain a read-only, actual production Alembic revision using the approved
   production read path. Do not infer it from source history or task-definition
   version.
2. Independently verify backup/restore readiness and record an evidence
   reference. The Gate M workflow records this reference but does not inspect a
   backup or prove a restore itself.
3. Approve and record the write-freeze/maintenance or interruption policy and
   the named migration owner.
4. Choose Gate M's exact allowed starting revision and mode from catalog
   evidence. Stop if it is not one of the workflow's accepted states.

## Gate M — PR-06B schema migration

With a separate Gate M approval, dispatch
`CD-01 — PR-06B Manually Approved Production Schema Migration` from `main`.
Supply the final SHA, matching migration-runner digest, catalog-proven start
revision, named owner, backup/restore evidence reference, maintenance reference,
and the required confirmation text. The workflow serializes the migration,
verifies the catalog at `b7d3a1e9c642`, and does not update an ECS service.

On interruption or failure, preserve the evidence and use the documented
checkpoint/recovery process under a new approval. Never stamp or downgrade the
production database automatically.

## Gate R — Backend promotion

With a distinct Gate R approval, dispatch
`CD-01 — Manually Approved Production Backend Release` from `main`, using the
same final SHA, matching backend runtime and migration-runner digests, and the
successful Gate M run ID. The workflow verifies digest/tag provenance, schema
head, cloned task-definition contract, ECS stability, backend target health,
and `/api/health`.

Its cloned release profile refuses a missing HMAC reference and strips all known
model-Provider credential entries such as `DEEPSEEK_API_KEY` and
`OLLAMA_CLOUD_API_KEY`. It cannot silently reintroduce them from the previous
task definition. If it fails after task-definition registration but before a
healthy service, use the recorded previous backend task-definition ARN only
after checking forward-schema compatibility; do not alter the database.

## Gate F — Frontend promotion

After Gate R is healthy, obtain a separate Gate F approval and dispatch
`CD-01 — Manually Approved Production Frontend Release` from `main` with the
same final SHA and the matching frontend digest. This workflow:

- proves the SHA remains an ancestor of `main` and that the digest is tagged by
  that SHA in the existing `tripsync-frontend` repository;
- reads the active `tripsync-frontend-service` definition and uses the offline
  clone helper to replace only the frontend container image with the
  digest-pinned image;
- preserves desired count one, network and service configuration, execution
  roles, CPU/memory, environment, logging, health checks, and deployment
  circuit breaker;
- waits for stability, verifies the frontend target health, homepage, and
  `/login`; and records the previous frontend task definition and image.

The frontend build image is produced from repository root with
`frontend/Dockerfile`; its Dockerfile builds current `shared`, `trip`, and
`frontend` source, runs `npm run build:trip-preview`, then `npm run build`, and
uses `https://app.cadensy.top` as the API base URL. The build workflow verifies
`/login` and `/trip-app/index.html` in a local container before publishing. It
never pushes `latest` and never updates ECS.

## Post-release acceptance and rollback boundary

Perform the approved browser checks for homepage, login, Trip navigation,
conversation creation/history UI, and browser console. Verify that a submission
reports the explicit Runtime-unavailable state and does not remain loading;
Round 1 does not claim a functional real-model chat experience.

If the frontend release fails, roll back only the frontend service to the
recorded previous frontend task-definition ARN. If the backend release fails,
use the corresponding previous backend ARN only after confirming compatibility
with the forward-only PR-06B schema. No automated workflow rolls back or
downgrades the database. Real Provider and Durable Runtime activation remain a
separate, unapproved production round.

Actual Gate M/R/F production evidence and final Runtime-off browser acceptance
are kept in `19_PR06B_PRODUCTION_ACTIVATION_EXECUTION_RECORD.md`. The read-only
release-closeout inventory, ongoing-cost estimate, and future cleanup boundary
are kept in `20_PR06B_RELEASE_CLOSEOUT_AWS_INVENTORY.md`.
