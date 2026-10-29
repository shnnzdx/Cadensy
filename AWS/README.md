# TripSync AWS Documentation

Status: current operator index, with historical records linked below
Last reviewed: 2026-10-29

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

Use `PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md` only as historical Route 53 planning
context. The completed production domain uses Aliyun DNS, and the Phase 10
workflow is no longer an active operator entry point.

Use `CLOUD_FIXED_ACCOUNTS_RUNBOOK.md` when the private cloud RDS database
should contain the two fixed backend accounts without creating any demo trip.

Use `DEEPSEEK_ONLY_OLLAMA_RETIREMENT_PLAN.md` only as a historical retirement
plan; verify current metadata before acting on it.

Recorded v0.3.0 production state is Pydantic on `tripsync-backend:31`, with the
Durable Conversation production latch enabled. The current protected activation
entry point is `.github/workflows/cd03-pydantic-production-release.yml`; do not
edit ECS task definitions by hand. The older CD-01/CD-02 Release B workflow is
historical activation evidence, not the current runtime instruction. CD-03 run
`40382252075` did not execute real Pydantic provider acceptance.

## Active GitHub Actions Workflows

The authoritative workflow inventory and ownership boundaries are in
[`../.github/workflows/README.md`](../.github/workflows/README.md). Active
workflows are limited to ten CI, artifact, release, and production-operation
entry points.

Current protected manual AWS operations are:

```text
.github/workflows/cd01-pr06b-schema-migration.yml
.github/workflows/cd01-production-release.yml
.github/workflows/cd01-frontend-production-release.yml
.github/workflows/cd03-pydantic-production-release.yml
.github/workflows/cloud-fixed-accounts-upsert.yml
.github/workflows/cloud-demo-purge.yml
```

The public browser acceptance workflow remains a separate non-deployment
operation at `.github/workflows/phase9-public-e2e.yml`.

Important:

- all AWS deploy and cloud-admin workflows must be dispatched from `main`
- release and data-operation permissions remain separate intentionally
- completed setup and superseded release workflows are non-executable evidence
  under [`archive/workflows/`](archive/workflows/README.md)
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
Pydantic production activation: .github/workflows/cd03-pydantic-production-release.yml
Historical DeepSeek/Legacy activation: AWS/archive/workflows/retired-release/cd01-release-b-ai-activation.yml
Ollama retirement plan: DEEPSEEK_ONLY_OLLAMA_RETIREMENT_PLAN.md
PHASE6_RUNTIME_SECRETS_PLAN.md
```

`PHASE6_RUNTIME_SECRETS_PLAN.md` remains in the root as a stable historical
cross-reference. Its completed setup workflows are archived under
`AWS/archive/workflows/completed-setup/`.

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
to cloud RDS, use the manual `Operations — Cloud Fixed Accounts Upsert` GitHub Action
instead of opening RDS to direct laptop access.

If the old demo trip still exists in cloud RDS and should be removed while
keeping the fixed accounts, use the manual `Operations — Cloud Demo Purge` GitHub Action.

If the deployed backend runtime must change, use the protected CD-03 Pydantic
workflow from the reviewed `main` revision after an authorized preflight; do
not edit ECS task definitions by hand. First confirm the workflow's expected
baseline because the repository records Pydantic as already active.

The Phase 10 workflow is archived and must not be restored merely to change the
current domain. If a future, separately reviewed Route 53 migration is approved,
its historical inputs were:

```text
domain_name=<owned custom domain, for example app.example.com>
hosted_zone_id=<existing Route 53 public hosted zone ID>
```

Do not run the archived Phase 10 implementation against `app.cadensy.top`; the
current DNS provider is Aliyun. Any replacement workflow needs a fresh review,
and future backend task definition updates must preserve the existing AI
runtime.
