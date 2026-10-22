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
AWS/DEEPSEEK_ONLY_OLLAMA_RETIREMENT_PLAN.md
```

Use `TRIPSYNC_AWS_MASTER_CONTEXT_FINAL.md` for the full architecture and deployment history.

Use `TRIPSYNC_AWS_URLS.md` for live URLs, AWS console links, GitHub Actions links, resource names, and current blockers.

Use `CUSTOM_DOMAIN_HTTPS_RESULT.md` for the completed manual
`app.cadensy.top` HTTPS setup, Aliyun DNS records, ALB listener routing, and
the current answer to where the frontend/backend are hosted.

Use `PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md` when continuing HTTPS/custom domain work.

Use `CLOUD_FIXED_ACCOUNTS_RUNBOOK.md` when the private cloud RDS database
should contain the two fixed backend accounts without creating any demo trip.

Use `DEEPSEEK_ONLY_OLLAMA_RETIREMENT_PLAN.md` for the metadata-only legacy
inventory and the separately authorized post-Release-B retirement sequence.

Production AI activation is DeepSeek-only. Use the reviewed, protected
`.github/workflows/cd01-release-b-ai-activation.yml` from `main`; do not edit
an ECS task definition by hand or use a provider-provisioning workflow. The
workflow validates the current Release A baseline, retains its exact image,
attaches only the approved `DEEPSEEK_API_KEY` reference, enables the two
Durable Runtime gates, and has a Runtime-off rollback path.

## Current Manual AWS Workflows

Use only these current manual AWS operator entry points for cloud data and
runtime administration:

```text
.github/workflows/cloud-fixed-accounts-upsert.yml
.github/workflows/cloud-demo-purge.yml
.github/workflows/cd01-release-b-ai-activation.yml
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
DeepSeek-only production activation: .github/workflows/cd01-release-b-ai-activation.yml
Ollama retirement plan: DEEPSEEK_ONLY_OLLAMA_RETIREMENT_PLAN.md
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

If the deployed backend should activate real AI, use the protected CD-02
DeepSeek-only workflow after an authorized preflight; do not edit ECS task
definitions by hand. Release A remains Runtime-off until that separate
activation is approved.

Before running Phase 10 for any future Route 53-managed domain, provide:

```text
domain_name=<owned custom domain, for example app.example.com>
hosted_zone_id=<existing Route 53 public hosted zone ID>
```

Do not blindly re-run Phase 10 against `app.cadensy.top`; the current DNS
provider is Aliyun and future backend task definition updates must preserve the
existing AI runtime.
