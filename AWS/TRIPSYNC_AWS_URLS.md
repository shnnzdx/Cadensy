# TripSync AWS URL Index

Date: 2026-08-17

Region: us-east-1

This is the compact entry point for TripSync AWS URLs, console links, workflow links, resource names, and current AWS documentation.

## Live Application

```text
Frontend public URL:
https://app.cadensy.top

Legacy ALB HTTP URL:
http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com

Frontend login route:
https://app.cadensy.top/login

Legacy ALB login route:
http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com/login

Embedded Trip static entry:
https://app.cadensy.top/trip-app/index.html

Legacy ALB embedded Trip static entry:
http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com/trip-app/index.html

Backend health endpoint:
https://app.cadensy.top/api/health

Legacy ALB backend health endpoint:
http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com/api/health

Expected health response:
{"ok":true}
```

## GitHub

```text
Repository:
https://github.com/shnnzdx/cap_stone

GitHub Actions:
https://github.com/shnnzdx/cap_stone/actions

Phase 9 successful public E2E:
https://github.com/shnnzdx/cap_stone/actions/runs/31355307955

Phase 9 screenshot artifact:
https://github.com/shnnzdx/cap_stone/actions/runs/31355307955/artifacts/9050425261

Cloud Fixed Accounts Upsert workflow:
https://github.com/shnnzdx/cap_stone/actions/workflows/cloud-fixed-accounts-upsert.yml

Cloud Fixed Accounts Upsert successful run:
https://github.com/shnnzdx/cap_stone/actions/runs/31662909315

Cloud Demo Purge workflow:
https://github.com/shnnzdx/cap_stone/actions/workflows/cloud-demo-purge.yml

Cloud Demo Purge successful run:
https://github.com/shnnzdx/cap_stone/actions/runs/31663298728

Backend AI Runtime Config workflow:
https://github.com/shnnzdx/cap_stone/actions/workflows/backend-ai-runtime-config.yml

Backend AI Runtime Config successful run:
https://github.com/shnnzdx/cap_stone/actions/runs/31406205586

Backend AI Runtime Config guest-invite repair run:
https://github.com/shnnzdx/cap_stone/actions/runs/31996161940
```

## Local AWS Operator Credential Source

On this machine, the local AWS CLI credential copy is stored in:

```text
backend/.env
```

Present key names verified on Thursday, August 13, 2026:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
```

Important:

- the repo root `.env` is currently absent
- AWS CLI does not auto-read `backend/.env`
- a raw `aws sts get-caller-identity` can therefore return `NoCredentials`
  until the shell process imports those variables
- do not print or commit the credential values

PowerShell loader for the current shell only:

```powershell
Get-Content backend/.env | ForEach-Object {
  if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
  $name, $value = $_ -split '=', 2
  if ($name -in 'AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_SESSION_TOKEN', 'AWS_REGION', 'AWS_DEFAULT_REGION') {
    [Environment]::SetEnvironmentVariable($name, $value, 'Process')
  }
}
```

## Current AWS Console Links

```text
AWS Console home:
https://us-east-1.console.aws.amazon.com/console/home?region=us-east-1

ECS cluster:
https://us-east-1.console.aws.amazon.com/ecs/v2/clusters/tripsync-cluster/services?region=us-east-1

Backend service:
https://us-east-1.console.aws.amazon.com/ecs/v2/clusters/tripsync-cluster/services/tripsync-backend-service/health?region=us-east-1

Frontend service:
https://us-east-1.console.aws.amazon.com/ecs/v2/clusters/tripsync-cluster/services/tripsync-frontend-service/health?region=us-east-1

Backend ECR:
https://us-east-1.console.aws.amazon.com/ecr/repositories/private/tripsync-backend?region=us-east-1

Frontend ECR:
https://us-east-1.console.aws.amazon.com/ecr/repositories/private/tripsync-frontend?region=us-east-1

Backend CloudWatch Logs:
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/$252Fecs$252Ftripsync-backend

Frontend CloudWatch Logs:
https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/$252Fecs$252Ftripsync-frontend

Application Load Balancers:
https://us-east-1.console.aws.amazon.com/ec2/home?region=us-east-1#LoadBalancers:

Target Groups:
https://us-east-1.console.aws.amazon.com/ec2/home?region=us-east-1#TargetGroups:

Route 53 hosted zones:
https://us-east-1.console.aws.amazon.com/route53/v2/hostedzones

ACM certificates:
https://us-east-1.console.aws.amazon.com/acm/home?region=us-east-1#/certificates/list

Billing and Cost Management:
https://us-east-1.console.aws.amazon.com/billing/home

Public IPv4 Insights:
https://us-east-1.console.aws.amazon.com/vpcconsole/home?region=us-east-1#PublicIpInsights:
```

## Current AWS Resource Names

```text
VPC: tripsync-vpc
ALB: tripsync-backend-alb
ALB security group: tripsync-alb-sg

ECS cluster: tripsync-cluster
Backend ECS service: tripsync-backend-service
Frontend ECS service: tripsync-frontend-service

Backend target group: tripsync-backend-tg
Frontend target group: tripsync-frontend-tg

Backend ECR repository: tripsync-backend
Frontend ECR repository: tripsync-frontend

Backend CloudWatch log group: /ecs/tripsync-backend
Frontend CloudWatch log group: /ecs/tripsync-frontend

Execution role: tripsync-ecs-execution-role
Backend task role: tripsync-backend-task-role

RDS endpoint:
tripsync-postgres.cqv0oqgogc0p.us-east-1.rds.amazonaws.com
```

## Current Documentation

```text
AWS/README.md
AWS/TRIPSYNC_AWS_MASTER_CONTEXT_FINAL.md
AWS/TRIPSYNC_AWS_URLS.md
AWS/CUSTOM_DOMAIN_HTTPS_RESULT.md
AWS/PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md
AWS/CLOUD_FIXED_ACCOUNTS_RUNBOOK.md
AWS/BACKEND_AI_RUNTIME_RUNBOOK.md
AWS/CLOUD_DEEPSEEK_CLI_RUNBOOK.md
AWS/CLOUD_DUAL_AI_CLI_RUNBOOK.md
AWS/PHASE6_RUNTIME_SECRETS_PLAN.md
```

Completed Phase 3-9 documents:

```text
AWS/archive/completed-phases/
```

## Current Pause Point

HTTPS custom domain setup has been completed manually for:

```text
https://app.cadensy.top
```

The domain is registered and DNS-managed in Aliyun, not Route 53. See:

```text
AWS/CUSTOM_DOMAIN_HTTPS_RESULT.md
```

Do not blindly re-run `Phase 10 HTTPS Custom Domain` against
`app.cadensy.top`; the existing workflow assumes a Route 53 hosted zone and
future backend task definition updates must preserve the current AI runtime.

Cloud RDS remains private. Use `AWS/CLOUD_FIXED_ACCOUNTS_RUNBOOK.md` and the
manual `Cloud Fixed Accounts Upsert` workflow if the real database should
contain the fixed backend accounts:

```text
organizer@cadensy.local
participant@cadensy.local
```

Use `AWS/BACKEND_AI_RUNTIME_RUNBOOK.md` and the manual `Backend AI Runtime
Config` workflow if the deployed backend should stop using `MOCK_AI=1` and start
using a real OpenAI-compatible provider.

Historical-only demo cleanup notes are archived at:

```text
AWS/archive/CLOUD_DEMO_PURGE_RUNBOOK.md
```

Historical backend AI runtime record (pre-PR-01B; not checked or changed in
this PR):

```text
MOCK_AI=0
CHAT_AI_PROVIDER=deepseek
PLANNER_AI_PROVIDER=deepseek
EXPLAINER_AI_PROVIDER=deepseek
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
OLLAMA_CLOUD_BASE_URL=https://ollama.com/v1/
OLLAMA_CLOUD_MODEL=qwen3.5:cloud
DEV_ALLOW_MEMBERSHIP_HEADER=1
```

Historical Guest invite cloud verification on Monday, August 17, 2026
(pre-PR-01B header-based flow):

```text
before repair:
guest join completed, then GET /api/trips/{trip_id} returned 401 {"detail":"Login required"}

after repair:
same guest join flow returned 200 for GET /api/trips/{trip_id}

backend task definition recorded at that time:
tripsync-backend:17
```

For a future, separately authorized PR-01B rollout, the checked-in deployment
templates require `DEV_ALLOW_MEMBERSHIP_HEADER=0` and Guest trip reads use the
bounded bearer returned from invite join. This document does not assert that
the live task definition has changed.

Current backend non-AI provider secret expected in cloud runtime:

```text
GEOAPIFY_API_KEY -> /tripsync/backend/prod/geoapify-api-key
```

Current manual DeepSeek CLI operator guide:

```text
AWS/CLOUD_DEEPSEEK_CLI_RUNBOOK.md
```

Current manual dual-AI CLI operator guide:

```text
AWS/CLOUD_DUAL_AI_CLI_RUNBOOK.md
```

Cloud branch policy:

```text
Only main may deploy to or mutate the live AWS environment.
All current manual cloud workflows hard-fail when dispatched from any branch other than main.
```

## Cost Reminder

The live proof can incur charges while running.

Main cost drivers:

```text
ALB hourly charge
Fargate task runtime
public IPv4 addresses
CloudWatch Logs usage
ECR image storage
RDS runtime/storage if left active
Route 53 hosted zone if created later
```

Use the cleanup order in `AWS/archive/completed-phases/PHASE5_BACKEND_DEPLOYMENT_PLAN.md` when the proof is no longer needed.
