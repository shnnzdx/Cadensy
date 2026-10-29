# TripSync AWS Phase 6 Runtime Secrets Plan

Status: historical Phase 6 record; repository readiness completed, no AWS secrets or parameters created.

Date: 2026-08-10

Region: us-east-1

Phase 6 defines how TripSync runtime configuration should be handled after the first infrastructure-only backend proof. It does not create AWS Secrets Manager secrets, SSM parameters, IAM permissions, or ECS task definition changes.

The workflows named below completed their purpose and are no longer active
GitHub Actions entry points. They are retained as historical evidence under
`AWS/archive/workflows/completed-setup/`.

Repository readiness workflow (archived):

```text
AWS/archive/workflows/completed-setup/runtime-secrets-readiness.yml
```

The workflow is manually triggered and validation-only. It does not configure AWS credentials, read GitHub Secrets, read AWS secrets, or print environment variables.

Readiness result:

```text
AWS/archive/completed-phases/PHASE6_RUNTIME_SECRETS_READINESS_RESULT.md
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31349738285
result: success
```

Runtime provisioning plan and workflow (archived):

```text
AWS/archive/completed-phases/PHASE6_RUNTIME_PROVISION_PLAN.md
AWS/archive/workflows/completed-setup/phase6-runtime-provision.yml
```

Runtime provisioning result:

```text
AWS/archive/completed-phases/PHASE6_RUNTIME_PROVISION_RESULT.md
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31350032734
result: success
```

---

## 1. Scope Boundary

Do not run:

```text
aws secretsmanager get-secret-value
aws secretsmanager batch-get-secret-value
aws secretsmanager create-secret
aws ssm put-parameter
aws iam create-policy
aws iam attach-role-policy
```

The Phase 6 readiness workflow is allowed to scan tracked repository files for high-confidence secret patterns and accidentally tracked `.env` files.

Do not store secret values in:

```text
Git
AWS/*.md
workflow YAML plaintext
Docker images
frontend source or build output
Codex messages
terminal logs
```

---

## 2. Current Backend Runtime Inputs

Current backend configuration keys from `backend/.env.example`:

```text
DATABASE_URL
TEST_DATABASE_URL
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
MOCK_AI
SETTLE_TICK_SECONDS
DISABLE_SCHEDULER
DEV_ALLOW_MEMBERSHIP_HEADER
FRONTEND_BASE_URL
CORS_ORIGINS
```

---

## 3. First Proof Runtime

The Phase 5 first proof remains infrastructure-only:

```text
DISABLE_SCHEDULER=1
MOCK_AI=1
desiredCount=1
GET /api/health through ALB
```

The first proof does not require real RDS or OpenAI secrets.

---

## 4. Future Secret Classification

Sensitive runtime values:

```text
DATABASE_URL
OPENAI_API_KEY
future JWT/session/signing secrets
future OAuth/client secrets
```

Non-sensitive runtime values:

```text
APP_ENV
OPENAI_BASE_URL
OPENAI_MODEL
MOCK_AI
SETTLE_TICK_SECONDS
DISABLE_SCHEDULER
FRONTEND_BASE_URL
CORS_ORIGINS
DEV_ALLOW_MEMBERSHIP_HEADER
```

`TEST_DATABASE_URL` is local/CI-only and should not be deployed to the production ECS task.

---

## 5. Proposed Parameter Names

Use one application namespace:

```text
/tripsync/backend/prod/
```

Proposed sensitive values:

```text
/tripsync/backend/prod/database-url
/tripsync/backend/prod/openai-api-key
/tripsync/backend/prod/session-secret
```

Proposed plain ECS environment values:

```text
APP_ENV=production
DISABLE_SCHEDULER=1 for first proof
MOCK_AI=1 for first proof
OPENAI_MODEL=gpt-4o-mini or approved deployed model
SETTLE_TICK_SECONDS=60
FRONTEND_BASE_URL=<future frontend URL>
CORS_ORIGINS=<future frontend origin list>
DEV_ALLOW_MEMBERSHIP_HEADER=0
```

---

## 6. ECS Injection Rule

Sensitive values should be injected through the ECS task definition `secrets` field.

Plain values should be injected through the ECS task definition `environment` field.

Do not pass `AWS_ACCESS_KEY_ID` or `AWS_SECRET_ACCESS_KEY` into the application container. ECS should use:

```text
executionRoleArn for image pull, logs, and secret retrieval
taskRoleArn for application AWS API permissions only if later required
```

---

## 7. IAM Planning Rule

The ECS execution role should receive the narrowest permission needed to read only the specific runtime secret/parameter ARNs used by the task definition.

The ECS task role should remain minimal because the current FastAPI application does not need to call AWS APIs directly for the first proof.

Do not attach broad policies such as:

```text
AdministratorAccess
SecretsManagerReadWrite
AmazonSSMFullAccess
```

---

## 8. Secret Handling Rule for Agents

Agents must not fetch or print secret values.

Allowed:

```text
confirm secret/parameter names
confirm whether a secret/parameter exists
plan IAM permissions
reference secret ARNs or parameter ARNs
```

Not allowed:

```text
read plaintext secret values
echo secrets
print env
run printenv
commit .env files
paste real DATABASE_URL or API keys into docs
```

---

## 9. Approval Gate

Before creating AWS secrets, SSM parameters, or IAM policy changes, require explicit human approval for Phase 6 resource changes.

Recommended next resource step:

```text
Create the minimal RDS PostgreSQL instance first, then create DATABASE_URL as a runtime secret/parameter, then update the ECS task definition to inject it.
```

Reason:

```text
DATABASE_URL cannot be finalized until the RDS endpoint, database name, database user, and database password are known.
```

This plan does not override the Phase 5 approval gate:

```text
Approve Phase 5 backend resource creation
```
