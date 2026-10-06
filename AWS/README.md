# TripSync AWS Documentation

This folder is the AWS source-of-truth area for the TripSync capstone deployment.

## Start Here

Read these files first:

```text
AWS/TRIPSYNC_AWS_MASTER_CONTEXT_FINAL.md
AWS/TRIPSYNC_AWS_URLS.md
AWS/CUSTOM_DOMAIN_HTTPS_RESULT.md
AWS/PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md
AWS/CLOUD_FIXED_ACCOUNTS_RUNBOOK.md
AWS/BACKEND_AI_RUNTIME_RUNBOOK.md
AWS/CLOUD_DEEPSEEK_CLI_RUNBOOK.md
AWS/CLOUD_DUAL_AI_CLI_RUNBOOK.md
```

Use `TRIPSYNC_AWS_MASTER_CONTEXT_FINAL.md` for the full architecture and deployment history.

Use `TRIPSYNC_AWS_URLS.md` for live URLs, AWS console links, GitHub Actions links, resource names, and current blockers.

Use `CUSTOM_DOMAIN_HTTPS_RESULT.md` for the completed manual
`app.cadensy.top` HTTPS setup, Aliyun DNS records, ALB listener routing, and
the current answer to where the frontend/backend are hosted.

Use `PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md` when continuing HTTPS/custom domain work.

Use `CLOUD_FIXED_ACCOUNTS_RUNBOOK.md` when the private cloud RDS database
should contain the two fixed backend accounts without creating any demo trip.

Use `BACKEND_AI_RUNTIME_RUNBOOK.md` when the deployed backend should switch from
`MOCK_AI=1` to a real provider and the AI runtime secret may need to be
provisioned first.

Important on Monday, August 17, 2026:

```text
Guest invite links use bounded Guest bearer credentials. Cloud runtime must keep
DEV_ALLOW_MEMBERSHIP_HEADER=0; a production process configured truthy fails fast.
```

Use `CLOUD_DEEPSEEK_CLI_RUNBOOK.md` when you want the exact local PowerShell +
AWS CLI operator steps for switching the current deployed backend to a
DeepSeek-only runtime by hand.

Use `CLOUD_DUAL_AI_CLI_RUNBOOK.md` when you want the exact local PowerShell +
AWS CLI operator steps for switching the deployed backend to the newer dual-AI
runtime with Ollama Cloud for chat and DeepSeek for planner/explainer.

If the backend starts using the Geoapify place provider in cloud runtime, the
deployed ECS backend task also needs `GEOAPIFY_API_KEY` injected from AWS SSM.
The current `backend-ai-secret-provision.yml` and
`backend-ai-runtime-config.yml` workflows are the source of truth for that.

## Current Manual AWS Workflows

Use only these current manual AWS operator entry points for cloud data and
runtime administration:

```text
.github/workflows/cloud-fixed-accounts-upsert.yml
.github/workflows/cloud-demo-purge.yml
.github/workflows/backend-ai-runtime-config.yml
```

Important:

- all AWS deploy and cloud-admin workflows must be dispatched from `main`
- the workflows now hard-fail if they are started from any other branch
- treat `main` as the only cloud release branch for this repository

## Local AWS CLI Credentials

On this machine, the local operator copy of the AWS CLI credentials lives in:

```text
backend/.env
```

The current local file contains these AWS keys:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
```

Important:

- the repo root `.env` is currently absent on this machine
- AWS CLI does not automatically read `backend/.env`
- a plain `aws sts get-caller-identity` can still fail with `NoCredentials`
  unless the current shell process loads those variables first
- never print, commit, or paste the secret values into docs or chat

PowerShell example for loading only the AWS variables into the current process:

```powershell
Get-Content backend/.env | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $name, $value = $_ -split '=', 2
  if ($name -in 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_REGION', 'AWS_DEFAULT_REGION') {
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
  }
}

aws sts get-caller-identity
```

## Current Root Files

```text
README.md
TRIPSYNC_AWS_MASTER_CONTEXT_FINAL.md
TRIPSYNC_AWS_URLS.md
CUSTOM_DOMAIN_HTTPS_RESULT.md
PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md
Cloud Fixed Accounts Runbook: CLOUD_FIXED_ACCOUNTS_RUNBOOK.md
Backend AI Runtime Runbook: BACKEND_AI_RUNTIME_RUNBOOK.md
Cloud DeepSeek CLI Runbook: CLOUD_DEEPSEEK_CLI_RUNBOOK.md
Cloud Dual AI CLI Runbook: CLOUD_DUAL_AI_CLI_RUNBOOK.md
PHASE6_RUNTIME_SECRETS_PLAN.md
```

`PHASE6_RUNTIME_SECRETS_PLAN.md` intentionally remains in the root because the validation workflow references this exact path.

## Archived Completed Phases

Completed Phase 3-9 planning and result documents are stored in:

```text
AWS/archive/completed-phases/
```

These are kept for audit history and rollback context. They are not the main entry point for new AWS work.

The retired demo cleanup runbook now lives at:

```text
AWS/archive/CLOUD_DEMO_PURGE_RUNBOOK.md
```

## Current Pause Point

AWS HTTPS custom domain setup has been completed manually for:

```text
https://app.cadensy.top
```

The domain is registered and DNS-managed in Aliyun, not Route 53. See
`AWS/CUSTOM_DOMAIN_HTTPS_RESULT.md` before changing HTTPS, ALB listener, or
custom-domain settings.

Cloud RDS remains private. If the two fixed backend accounts need to be written
to cloud RDS, use the manual `Cloud Fixed Accounts Upsert` GitHub Action
instead of opening RDS to direct laptop access.

If the old demo trip still exists in cloud RDS and should be removed while
keeping the fixed accounts, use the manual `Cloud Demo Purge` GitHub Action.

If the deployed backend should start using a real AI provider, use the manual
`Backend AI Runtime Config` GitHub Action instead of editing ECS task
definitions by hand.

Historical guest-invite cloud access note (pre-PR-01B; not a verification of
the current deployed task definition):

```text
Backend AI Runtime Config successful repair run:
https://github.com/shnnzdx/cap_stone/actions/runs/31996161940

Backend task definition recorded by that historical repair:
tripsync-backend:17

Required source configuration for a future, separately authorized PR-01B
rollout:
DEV_ALLOW_MEMBERSHIP_HEADER=0
```

Before running Phase 10 for any future Route 53-managed domain, provide:

```text
domain_name=<owned custom domain, for example app.example.com>
hosted_zone_id=<existing Route 53 public hosted zone ID>
```

Do not blindly re-run Phase 10 against `app.cadensy.top`; the current DNS
provider is Aliyun and future backend task definition updates must preserve the
existing AI runtime.
