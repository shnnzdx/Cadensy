# CD-02 — Controlled DeepSeek Production Activation

## Purpose

CD-02 adds a reviewed, manually dispatched Release B control plane. It does
not activate production AI by itself. A separate Main-environment approval and
an explicit live-operation authorization remain required.

## Starting evidence

- `origin/main` at implementation start: `7a9e977910b2ebfe61ee81e58bacf6251d906ad6`.
- Release A baseline reported before this work: backend `tripsync-backend:25`,
  frontend `tripsync-frontend:18`.
- Release A has both Durable Conversation gates set to `0`, `MOCK_AI=0`,
  `CADENSY_E2E_FAKE_RUNTIME=0`, and no Provider credential reference.

## Decision summary

The existing `backend-ai-runtime-config.yml` and
`backend-ai-secret-provision.yml` are historical Ollama-era workflows. They
are not used by CD-02. The implementation instead clones the currently
deployed, Runtime-off backend task definition, preserves Database, HMAC, IAM,
networking, logging, and application configuration, then permits only one
additional ECS secret reference: `DEEPSEEK_API_KEY`.

The generated activation profile is exact:

```ini
APP_ENV=production
CHAT_AGENT_RUNTIME=legacy
MOCK_AI=0
CADENSY_E2E_FAKE_RUNTIME=0
CADENSY_DURABLE_CONVERSATION_RUNTIME_ENABLED=1
CADENSY_DURABLE_CONVERSATION_PRODUCTION_ACTIVATION_ENABLED=1
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
DEEPSEEK_THINKING=disabled
```

All other known Provider credentials and Provider configuration prefixes are
rejected. The workflow never retrieves a secret or parameter value.

## Delivered files

- `.github/workflows/cd01-release-b-ai-activation.yml`
- `scripts/cd01/prepare_deepseek_activation_task_definition.py`
- `backend/tests/test_cd02_release_b_activation.py`

## Safety contract

1. The workflow accepts only `workflow_dispatch` on `main`, and requires both
   a confirmation phrase and the protected GitHub `Main` Environment approval.
2. Activation checks the fixed source SHA, ECR digest/tag provenance, current
   ECS task definition, account, region, service stability, and circuit breaker
   settings before it registers a new revision.
3. The DeepSeek reference may be only an approved-account/region SSM
   SecureString ARN or Secrets Manager ARN. Metadata is inspected without
   `GetParameter`, `GetSecretValue`, decryption, workflow output, or summary
   exposure. The execution role access and any customer-managed KMS decrypt
   condition are fail-closed checks.
4. The `runtime_off_rollback` operation needs no DeepSeek reference. It restores
   an exact Release A task definition, validates both gates are off and no
   Provider credential is attached, then verifies ECS, ALB, and API health.
5. Any activation deployment or post-deployment health failure triggers the
   same Runtime-off rollback attempt. Neither operation changes schema, RDS,
   IAM policy, networking, or Protected Apply behavior.

## Validation record

- Offline helper/workflow/CD-01/CI contract tests: **81 passed**.
- Local PostgreSQL Durable Runtime/API/Idempotency regression: **64 passed**.
- Local PostgreSQL CD-02/CD-01/Runtime Gate/lease-concurrency regression:
  **98 passed**.
- Python compilation of the new helper: **passed**.
- `git diff --check`: **passed**.
- Changed-file high-confidence secret scan: **passed**.
- PostgreSQL evidence used two newly created local databases with names ending
  in `_test`; both were deleted after successful verification. No production or
  non-local database was used.

## Explicitly not performed

- No AWS read or write operation for CD-02.
- No Secret value read, copy, or creation.
- No real DeepSeek request.
- No production activation, deployment, migration, commit, merge, or push at
  the time of this record.

## Post-merge authorization boundary

Merging CD-02 only makes the controlled workflow available. Before Release B
execution, an authorized operator must supply the current Release A baseline
ARN, the approved existing DeepSeek secret reference, fixed source SHA/image
digest, and approve the protected `Main` deployment. Bounded production chat
verification is a separate authorization after infrastructure activation.
