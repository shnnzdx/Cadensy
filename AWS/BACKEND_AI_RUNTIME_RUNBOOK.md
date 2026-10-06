# TripSync Backend AI Runtime Runbook

Status: current dual-provider workflow.

Purpose:

```text
Switch the deployed backend between MOCK_AI demo mode and the current
dual-provider runtime without changing the private RDS network model.
```

Workflows:

```text
.github/workflows/backend-ai-secret-provision.yml
.github/workflows/backend-ai-runtime-config.yml
```

## Current Runtime Shape

The backend now supports provider-specific runtime inputs:

```text
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
OLLAMA_CLOUD_API_KEY
OLLAMA_CLOUD_BASE_URL
OLLAMA_CLOUD_MODEL
CHAT_AI_PROVIDER
PLANNER_AI_PROVIDER
EXPLAINER_AI_PROVIDER
AI_FALLBACK_PROVIDER
MOCK_AI
```

Current recommended route split:

```text
chat      -> ollama_cloud
planner   -> deepseek
explainer -> deepseek
```

Legacy `OPENAI_*` variables still exist in backend code as a local compatibility
path, but the AWS runtime should now use the provider-specific variables above.

## Required GitHub Secrets

Preferred `Main` environment secrets:

```text
TRIPSYNC_DEEPSEEK_API_KEY
TRIPSYNC_OLLAMA_CLOUD_API_KEY
```

Temporary DeepSeek fallback names still supported by the provisioning workflow:

```text
TRIPSYNC_AI_API_KEY
TRIPSYNC_OPENAI_API_KEY
```

Preferred secret migration outcome:

```text
use TRIPSYNC_DEEPSEEK_API_KEY for DeepSeek
use TRIPSYNC_OLLAMA_CLOUD_API_KEY for Ollama Cloud
retire TRIPSYNC_AI_API_KEY and TRIPSYNC_OPENAI_API_KEY after migration
```

## Required SSM Parameters

Expected SecureString parameter names:

```text
/tripsync/backend/prod/deepseek-api-key
/tripsync/backend/prod/ollama-cloud-api-key
/tripsync/backend/prod/database-url
```

The provisioning workflow writes the provider keys into SSM and updates the ECS
execution role inline policy so the backend task can read all required runtime
parameters.

## What The Runtime Workflow Does

```text
GitHub Actions
-> read the current backend ECS task definition
-> keep current image, logs, DATABASE_URL secret, frontend URL, CORS, scheduler flag
-> force secure backend runtime with DEV_ALLOW_MEMBERSHIP_HEADER=0
-> register a new backend task definition revision
-> inject DEEPSEEK_API_KEY and OLLAMA_CLOUD_API_KEY from SSM
-> set DEEPSEEK_* and OLLAMA_CLOUD_* runtime env vars
-> set CHAT_AI_PROVIDER / PLANNER_AI_PROVIDER / EXPLAINER_AI_PROVIDER
-> optionally set AI_FALLBACK_PROVIDER
-> remove legacy OPENAI_* cloud injection from the task definition
-> update the existing backend ECS service
-> wait for ECS service stability
-> verify /api/health through the public ALB
```

The workflows do not:

```text
print provider API keys
print DATABASE_URL
read plaintext SecureString values into logs
change RDS networking
change the frontend ECS service
change HTTPS/custom-domain resources
```

## Provision Secrets First

Run:

```text
Actions
-> Backend AI Secret Provision
-> Run workflow
```

Typical inputs:

```text
deepseek_api_key_parameter=/tripsync/backend/prod/deepseek-api-key
ollama_cloud_api_key_parameter=/tripsync/backend/prod/ollama-cloud-api-key
```

## Configure Runtime

Run:

```text
Actions
-> Backend AI Runtime Config
-> Run workflow
```

Typical current production inputs:

```text
mock_ai=false
deepseek_api_key_parameter=/tripsync/backend/prod/deepseek-api-key
deepseek_base_url=https://api.deepseek.com
deepseek_model=deepseek-v4-flash
ollama_cloud_api_key_parameter=/tripsync/backend/prod/ollama-cloud-api-key
ollama_cloud_base_url=https://ollama.com/v1/
ollama_cloud_model=qwen3.5:cloud
chat_ai_provider=ollama_cloud
planner_ai_provider=deepseek
explainer_ai_provider=deepseek
ai_fallback_provider=
```

PR-01B supersedes the historical header-based Guest flow: Guest join returns a
bounded bearer credential, so production must keep
`DEV_ALLOW_MEMBERSHIP_HEADER=0`. A truthy value fails application startup in a
production profile.

Rollback to mock mode:

```text
mock_ai=true
deepseek_api_key_parameter=/tripsync/backend/prod/deepseek-api-key
ollama_cloud_api_key_parameter=/tripsync/backend/prod/ollama-cloud-api-key
```

## Intended AWS Runtime After Success

```text
MOCK_AI=0
DEEPSEEK_API_KEY injected through SSM Parameter Store
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
OLLAMA_CLOUD_API_KEY injected through SSM Parameter Store
OLLAMA_CLOUD_BASE_URL=https://ollama.com/v1/
OLLAMA_CLOUD_MODEL=qwen3.5:cloud
CHAT_AI_PROVIDER=ollama_cloud
PLANNER_AI_PROVIDER=deepseek
EXPLAINER_AI_PROVIDER=deepseek
DEV_ALLOW_MEMBERSHIP_HEADER=0
```

Current verified cloud runtime on Monday, August 17, 2026:

```text
Backend AI Runtime Config repair run:
https://github.com/shnnzdx/cap_stone/actions/runs/31996161940

Live backend task definition after repair:
tripsync-backend:17

Live backend provider routing after repair:
CHAT_AI_PROVIDER=deepseek
PLANNER_AI_PROVIDER=deepseek
EXPLAINER_AI_PROVIDER=deepseek
```

Important on Thursday, August 13, 2026:

```text
normal frontend/backend product deploy workflows preserve the existing backend AI runtime
they do not switch providers by themselves
changing backend/.env locally does not update the AWS backend
changing either cloud key still requires:
1. updating the GitHub Main environment secrets
2. running Backend AI Secret Provision
3. running Backend AI Runtime Config
```

## Verification

The runtime workflow verifies:

```text
ECS service reaches stable
GET /api/health returns {"ok":true}
```

After a successful real-AI run, planner and explainer should stop using mock
responses through DeepSeek, and chat should stop using mock responses through
Ollama Cloud.

## Common Failures

`SSM parameter /tripsync/backend/prod/deepseek-api-key was not found`

```text
The DeepSeek key parameter does not exist yet, or the workflow input name is
wrong. Run Backend AI Secret Provision first, then re-run Backend AI Runtime Config.
```

`SSM parameter /tripsync/backend/prod/ollama-cloud-api-key was not found`

```text
The Ollama Cloud key parameter does not exist yet, or the workflow input name is
wrong. Run Backend AI Secret Provision first, then re-run Backend AI Runtime Config.
```

`DATABASE_URL secret is not present on the current backend task definition`

```text
The current backend task definition drifted away from the runtime-secrets layout.
Inspect the active ECS task definition and restore DATABASE_URL secret injection first.
```

`Backend health check passed after AI runtime config update` is missing

```text
The ECS deployment did not stabilize or the backend is unhealthy after startup.
Check ECS service events and the /ecs/tripsync-backend CloudWatch Logs group.
```

`guest invite join succeeds but the next cloud trip request returns 401 Login required`

```text
inspect the live backend ECS task definition first
verify the browser has adopted the `guest_token` returned by join
verify the task definition keeps DEV_ALLOW_MEMBERSHIP_HEADER=0
verify the result with a fresh guest join and a bearer-authenticated GET /api/trips/{trip_id}
```

## Current Product Reality

Real-time product behavior is still:

```text
database writes happen immediately through API requests
frontend refreshes current state through polling
no websocket/SSE transport is deployed yet
```

Turning `MOCK_AI=0` enables real model calls. It does not change the transport
model for comments, votes, proposals, or trip updates.
