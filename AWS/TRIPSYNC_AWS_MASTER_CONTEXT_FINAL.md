# TripSync Capstone 鈥?AWS Access, Security, GitHub Actions, Cost Guardrails & Deployment Master Context

**Document status:** Master handoff / source of truth for the current AWS setup and the next deployment phases  
**Primary AWS Region:** `us-east-1`  
**AWS Account ID:** `448678332746`  
**Project:** TripSync Capstone  
**Last updated:** 2026-08-09, final v1.1 master-context freeze

---

# 1. Purpose of This Document

This document records the complete working context for the TripSync Capstone AWS environment so that any teammate, Codex session, or future deployment task understands:

1. who currently has AWS access;
2. what 鈥渆qual long-term access鈥?means for the three human project members;
3. how the GitHub Actions AWS identity is separated from human accounts;
4. what has already been validated;
5. what must not be changed accidentally;
6. the current application architecture;
7. the preferred candidate AWS deployment architecture and pending proof gates;
8. cost and AWS Free Plan constraints;
9. the required deployment sequence;
10. the security rules for passwords, access keys, GitHub secrets, and application secrets.

This is intentionally more complete than a deployment checklist. It is meant to be reusable as background context when asking Codex to work on the repository.

---

# 2. Executive Summary

The TripSync team currently uses one standalone AWS account.

The required long-term human access target is:

```text
Root
  -> account-owner / recovery only
  -> not counted as a daily project identity

Capstone-Admins
  -> AdministratorAccess
  |
  |-- Human Admin 1 / ProjectOwnerAdmin
  |-- Carina
  `-- Dixon
```

However, an important AWS distinction applies:

- An AWS **root user can never be made literally equivalent to an IAM administrator**.
- Root always remains the account-owner identity and has some root-only capabilities.
- Therefore, if one of the three humans continues to use Root while the other two use IAM users, the three people are **project-resource equivalent for normal administration**, but they are **not identity-level identical**.
- The target model for this project is fixed: three daily human IAM administrator users in the same `Capstone-Admins` group, while Root is retained only as the account-owner/emergency identity.

For this project, there is also a separate machine identity:

```text
github-actions-deploy
```

This account is **not one of the three human accounts**. It exists only so GitHub Actions can authenticate programmatically to AWS.

The GitHub Actions authentication path is already configured using long-term IAM access keys:

```text
GitHub Actions
  -> GitHub Environment: Main
  -> AWS_ACCESS_KEY_ID
  -> AWS_SECRET_ACCESS_KEY
  -> AWS_REGION=us-east-1
  -> AWS IAM user: github-actions-deploy
  -> AWS STS
```

The identity path has been validated with:

```bash
aws sts get-caller-identity
```

The expected AWS account identity is:

```text
Account: 448678332746
Arn: arn:aws:iam::448678332746:user/github-actions-deploy
```

The AWS identity-check workflow must stay separate from future build/deployment workflows.

Current frontend integration track status:

```text
Integration Stage A route/embed contract alignment: complete
Integration Stage B shared product/demo data extraction: complete for first pass
Integration Stage C deeper runtime merge: paused intentionally
AWS deployment: not started
```

Latest integration commit pushed to `main`:

```text
ea4036e Integrate Trip frontend contracts and shared content
```

This commit updates local application structure only. It does not create, modify, or deploy AWS resources.

---

# 3. AWS Account Plan and Critical Free Plan Constraint

This is a new AWS Free Plan account.

Current AWS Free Plan rules for new customers:

- `$100` AWS credits are granted at sign-up.
- Up to another `$100` can be earned through eligible AWS activities.
- The Free Plan lasts for up to **6 months**, or until available Free Tier credits are exhausted, whichever happens first.
- The Free Plan has access to a limited set of AWS services/features.
- The account should not be upgraded unintentionally.

## 3.1 DO NOT create or join AWS Organizations

This is a hard project constraint.

For a Free Plan account, creating or joining AWS Organizations automatically upgrades the account to a Paid Plan and causes remaining Free Tier credits to expire immediately.

Therefore, during this Capstone phase:

```text
DO NOT:
- Create AWS Organizations
- Join AWS Organizations
- Enable an IAM Identity Center organization instance
- Set up AWS Control Tower
```

This is why the project currently uses standalone IAM users rather than an AWS Organizations + IAM Identity Center workforce architecture.

---

# 4. Current Human AWS Access

## 4.1 Current known structure

```text
AWS Account 448678332746
|
|-- Root user
|    `-- retained by project account owner
|
`-- IAM
     |
     `-- Group: Capstone-Admins
          |
          |-- Carina
          `-- Dixon
```

The intended policy attached to `Capstone-Admins` is:

```text
AWS managed policy:
AdministratorAccess
```

`AdministratorAccess` grants full access to AWS services and resources and allows delegation of permissions.

## 4.2 What the two IAM administrators should be able to manage

With `AdministratorAccess`, the two IAM administrators are intended to have the same project-level AWS administration capability for services such as:

```text
IAM
EC2
ECS
Fargate
ECR
Elastic Load Balancing
RDS
S3
CloudWatch
Lambda
API Gateway
Systems Manager
SSM Parameter Store
Amplify-related AWS configuration where available
Security Groups
VPC resources
CloudFormation
Budgets/Cost APIs where billing access is enabled
```

This means that for ordinary TripSync infrastructure work, both IAM users should be treated as peer administrators.

---

# 5. The 鈥淭hree Humans Have the Same Long-Term Access鈥?Requirement

This section is the most important identity-design clarification.

## 5.1 Current model: Root + 2 IAM administrators

If the three humans are:

```text
Human 1 -> Root
Human 2 -> IAM user Carina -> Capstone-Admins
Human 3 -> IAM user Dixon  -> Capstone-Admins
```

then they are **not literally identical identities**.

Why:

```text
Root
  -> complete account-owner authority
  -> root-only account operations
  -> cannot be restricted by normal IAM identity policies

AdministratorAccess IAM user
  -> effectively full AWS service/resource administration
  -> cannot perform some root-only account operations
```

Therefore the correct description of the current model is:

> The three project members have effectively equal permissions for normal project infrastructure administration, while the Root user retains unavoidable account-owner/root-only privileges.

Do not describe Root and IAM administrators as cryptographically or identity-level identical.

## 5.2 Target model: three daily IAM administrators plus separate Root

The project requirement is:

> All three humans should use the same type of long-term daily project account and inherit the same IAM policy.

Therefore the target model is:

```text
Root
  -> emergency/account-owner only
  -> not counted as one of the three daily project accounts

Capstone-Admins
  -> AdministratorAccess
  |
  |-- ProjectOwnerAdmin / Human Admin 1
  |-- Carina
  `-- Dixon
```

The project owner should create or use a normal IAM administrator user for daily work. Example neutral names:

```text
ProjectOwnerAdmin
Dixin
```

All three human IAM users would then have:

```text
same IAM group
same AdministratorAccess policy
same project-resource authorization model
independent username
independent password
independent MFA
```

This is the cleanest way to satisfy literal long-term day-to-day equality while keeping the Root identity separate.

Root remains available for account-owner and recovery operations, but Root should no longer be counted as one of the three daily project working accounts.

## 5.3 What 鈥渓ong-term鈥?means here

AWS distinguishes long-term and temporary credentials.

For human IAM users, long-term credentials can include:

```text
IAM username
IAM console password
optional IAM access key pair
```

Passwords/access keys remain valid until they are changed, disabled, or deleted.

For this project:

- The **console password** is enough for human AWS Console access.
- Do **not** create programmatic access keys for every human merely to make them 鈥渆qual.鈥?
- If a teammate genuinely needs AWS CLI/SDK access, create a unique access key for that individual IAM user.
- Never share one human access key between multiple people.

Equality should come from the shared IAM group/policy, **not from sharing credentials**.

---

# 6. Root User Policy

The Root user remains the AWS account-owner identity.

## 6.1 Root must remain separate

Root should not be shared with teammates.

Never share:

```text
root email
root password
root MFA device / recovery codes
root access key
```

## 6.2 Root MFA

Root must have MFA enabled.

## 6.3 Root access keys

Do not create Root access keys.

GitHub Actions, local CLI use, and application workloads must not use Root programmatic credentials.

## 6.4 Root use under the target project model

The project owner may continue using Root temporarily while the `ProjectOwnerAdmin` / Human Admin 1 IAM user is being established.

Once `ProjectOwnerAdmin` / Human Admin 1 is confirmed:

```text
daily project work
-> ProjectOwnerAdmin / Human Admin 1

Root
-> account-owner operations
-> recovery/emergency operations
-> not counted as one of the three daily project identities
```

The target human working model is fixed at three normal IAM administrator users in `Capstone-Admins`, with Root kept separate.

---

# 7. Billing and Cost Console Equality

There is one AWS-specific exception that commonly causes confusion.

Even an IAM principal with `AdministratorAccess` cannot necessarily access the Billing and Cost Management console by default.

The Root user must first activate:

```text
IAM User and Role Access to Billing Information
```

Console path:

```text
Root login
-> Account
-> IAM User and Role Access to Billing Information
-> Edit
-> Activate IAM Access
```

After this account-level switch is enabled, IAM policies can control Billing access.

If the goal is for the two IAM administrators to monitor cost and budgets similarly to Root, this account-level billing access should be activated and the appropriate Billing permissions confirmed.

This does not make IAM users Root-equivalent; it only delegates Billing console access.

---

# 8. Human Credential Rules

Each human must have independent credentials.

Phase 4 readiness status:

```text
code-level readiness implemented
awaiting manual GitHub Actions run
no AWS database resources created
```

Current Phase 4 proof files:

```text
AWS/archive/completed-phases/PHASE4_DATABASE_READINESS.md
.github/workflows/database-readiness.yml
backend/app/db/init_schema.py
backend/tests/test_db_safety.py
```

Current rule:

```text
Do not create RDS yet.
Do not run demo seed automatically against RDS.
Use backend/app/db/init_schema.py for non-destructive empty-database schema proof.
Use Alembic or another real migration system before production-style schema evolution.
```

Required:

```text
unique IAM username
unique password
unique MFA
```

Forbidden:

```text
shared IAM password
shared MFA seed
shared access key
root credentials shared in Discord/Teams/email
credentials committed into git
credentials pasted into source code
credentials stored in .env committed to GitHub
```

The `Capstone-Admins` group is the permissions source. Individual users should not receive random extra administrator policies unless there is a documented reason.

---

# 9. GitHub Actions AWS Machine Identity

GitHub Actions uses a separate IAM user:

```text
github-actions-deploy
```

This is a machine/service identity, not a human team member.

## 9.1 Programmatic long-term credentials

The current design intentionally uses:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

These are long-term IAM credentials.

The team has consciously chosen this method for now instead of OIDC.

AWS generally recommends temporary credentials where possible, but this project is intentionally retaining access-key authentication during the Capstone deployment phase.

## 9.2 Secret location

The AWS keys must exist only in protected secret storage.

On this machine as of Thursday, August 13, 2026, there is also a local
operator copy in:

```text
backend/.env
```

That local file currently contains these AWS key names:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
AWS_REGION
```

This does not mean the AWS CLI auto-discovers them. Future operators and AI
agents should assume:

- the repo root `.env` may be absent
- `aws` does not automatically read `backend/.env`
- `NoCredentials` from `aws sts get-caller-identity` may simply mean the shell
  process never imported the variables yet
- the values must never be printed into logs, docs, chat, or commits

Current GitHub configuration:

```text
Workflow deployment environment:
Main

Active AWS secret scope:
Main environment secrets
```

Secrets:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

Variable:

```text
AWS_REGION=us-east-1
```

Verified on 2026-08-09:

```text
Main environment secrets:
- AWS_ACCESS_KEY_ID
- AWS_SECRET_ACCESS_KEY

Main environment variable:
- AWS_REGION=us-east-1

Repository-level entries also exist:
- AWS_ACCESS_KEY
- AWS_SECRET_ACCESS_KEY
- AWS_REGION
```

The active identity-check workflow uses `environment: Main` and references `secrets.AWS_ACCESS_KEY_ID`, so the `Main` environment secret named `AWS_ACCESS_KEY_ID` is the active access key source. The repository-level `AWS_ACCESS_KEY` name is not used by the current workflow.

### 9.2.1 GitHub credential cleanup task

After confirming the `Main` environment workflow continues to succeed, clean up duplicate repository-level AWS entries so future maintainers do not have to guess which secret scope is active.

```text
[ ] Re-run AWS Identity Check using environment Main
[ ] Confirm the run succeeds
[ ] Remove unused repository secret AWS_ACCESS_KEY
[ ] Remove duplicate repository AWS_SECRET_ACCESS_KEY if no workflow references it
[ ] Remove duplicate repository AWS_REGION if no workflow references it
[ ] Keep Main environment secrets/variable as the authoritative AWS Actions configuration
```

Do not delete any repository-level entry until repository/workflow search confirms it is unused.

Never store actual values in:

```text
README.md
docs/
source code
Dockerfile
docker-compose files
committed .env files
workflow YAML plaintext
frontend code
backend code
```

## 9.3 IAM user identity validation

The AWS machine identity has been validated with:

```bash
aws sts get-caller-identity
```

Known target identity:

```json
{
  "Account": "448678332746",
  "Arn": "arn:aws:iam::448678332746:user/github-actions-deploy"
}
```

The STS check proves which AWS principal GitHub is authenticating as.

It does **not**, by itself, prove what IAM policies are attached to that user.

Before deployment begins, explicitly inspect the attached policies for `github-actions-deploy`.

## 9.4 AWS provisioning blocker: machine IAM authorization

Authentication is complete, but authorization is not yet proven.

```text
aws sts get-caller-identity
-> authentication confirmed

deployment policy review
-> still required before AWS resource provisioning
```

Before any AWS phase creates resources, record:

```text
[ ] github-actions-deploy attached managed policies
[ ] github-actions-deploy inline policies
[ ] whether github-actions-deploy currently has AdministratorAccess
[ ] exact intended deployment permission boundary
[ ] access key status = Active
```

Do not start AWS resource provisioning until this blocker is resolved.

---

# 10. Important Permission Separation: Humans vs GitHub

Do not combine the two concepts.

Human administration:

```text
Capstone-Admins
  -> humans
  -> AWS Console
  -> AdministratorAccess
```

Automation:

```text
github-actions-deploy
  -> GitHub Actions
  -> access key
  -> deployment automation
```

The automation IAM user does not need a Console password.

Long term, the deployment user should receive only the permissions needed for the actual deployment path. During initial bring-up it may temporarily use broader permissions, but the final target should be reduced.

Do not put `github-actions-deploy` into the human administrator group simply to make configuration easier unless that choice is explicitly documented.

---

# 11. GitHub Actions Current State

The AWS identity workflow must remain a validation-only workflow.

Current workflow path:

```text
.github/workflows/main.yml
```

Displayed workflow name:

```text
AWS Identity Check
```

Current design:

```text
trigger:
workflow_dispatch only
```

Current job uses GitHub Environment:

```text
Main
```

Current AWS credential action:

```text
aws-actions/configure-aws-credentials@v6
```

It reads:

```text
${{ secrets.AWS_ACCESS_KEY_ID }}
${{ secrets.AWS_SECRET_ACCESS_KEY }}
${{ vars.AWS_REGION }}
```

It validates:

```bash
aws sts get-caller-identity
```

It does not deploy AWS resources.

## 11.1 Do not turn this file into the deployment workflow

Keep identity validation separate.

Future workflows should be separate files, for example:

```text
.github/workflows/main.yml
  -> AWS Identity Check

.github/workflows/build-validation.yml
  -> local/CI build validation only

.github/workflows/deploy-backend.yml
  -> future ECS deployment

frontend hosting workflow/config
  -> future Amplify deployment
```

---

# 12. Current Repository Architecture

The TripSync repository is a monorepo.

```text
/
|-- .github/workflows/
|-- frontend/
|-- trip/
|-- backend/
|-- shared/
|-- docs/
|-- AWS/
`-- ...
```

Current source-of-truth summary:

```text
frontend/
  -> main public site
  -> Vinext/Next-style app routes
  -> owns /trip iframe shell

trip/
  -> standalone React + Vite Trip workspace
  -> HashRouter workspace routes
  -> builds static assets

shared/
  -> cross-app contracts
  -> route helpers
  -> shared product workflow/principle content
  -> shared Trip demo fallback data

frontend/public/trip-app/
  -> generated static copy of trip/dist
  -> loaded by frontend/app/trip/page.tsx

backend/
  -> FastAPI API
  -> PostgreSQL persistence layer
```

## 12.1 frontend/

`frontend/` is the main TripSync public-facing site/product shell.

It currently uses Vinext/Next-style application routing.

Important build behavior:

```text
npm run build
-> node scripts/run-vinext.mjs build
-> Vinext CLI
```

The frontend should **not** be assumed to be a standard vanilla Next.js build.

Current frontend routes include:

```text
/
/login
/signup
/how-it-works
/faq
/privacy
/product
/trip
/trip-app/
```

`/trip` is the user-facing shell route. `/trip-app/` is the static embedded workspace output and should be treated as generated frontend asset content.

## 12.2 trip/

`trip/` is a standalone React + Vite Trip workspace.

Build command:

```bash
npm run build
```

It produces static output.

The active Trip workspace uses hash routing. Current supported Trip hash routes are:

```text
#/
#/create
#/account/profile
#/account/travel
#/account/notifications
#/account/settings
#/trip/:tripId/plan
#/trip/:tripId/chat
#/trip/:tripId/conflict
#/trip/:tripId/updates
#/trip/:tripId/preferences
#/trip/:tripId/members
#/trip/:tripId/invite
#/join/:token
```

Do not use the older `#/organizer`, `#/participant`, or `#/t/:slug` route assumptions for new work.

## 12.3 Current frontend/trip integration

The intended existing integration is:

```text
trip/
  -> Vite build
  -> trip/dist
  -> sync
  -> frontend/public/trip-app/
  -> embedded by the main frontend
```

The Trip workspace should not become a separate Amplify application unless the architecture is intentionally redesigned.

Current embed contract:

```text
shared/tripsync-preview-contract.js
  tripPreviewBasePath = /trip-app
  tripPreviewDefaultHashRoute = #/
  tripPreviewWorkspaceTitle = TripSync workspace

frontend/app/trip/preview-config.ts
  imports the shared preview contract
  builds the iframe src
  reads frontend/public/trip-app/embed-manifest.json when available

frontend/app/trip/page.tsx
  renders the iframe shell
  src = /trip-app/index.html#/

frontend/scripts/sync-trip-preview.mjs
  optionally builds trip/
  copies trip/dist into frontend/public/trip-app/
  writes embed-manifest.json
```

The sync command is:

```bash
cd frontend
npm run build:trip-preview
```

The script has been updated so this command works on Windows local development as well as Linux CI runners.

## 12.4 shared/

`shared/` is now an intentional integration layer, not a scratch folder.

Current shared modules:

```text
shared/tripsync-preview-theme.css
  -> shared visual tokens for the embedded workspace shell

shared/tripsync-preview-contract.js
  -> /trip-app embed path and default iframe route

shared/tripsync-domain.js
  -> current route helper functions for workspace, account, trip sections, and invite links
  -> legacy organizer/participant helper names are retained as compatibility aliases

shared/tripsync-product-content.js
  -> product workflow steps and product principles used by frontend

shared/tripsync-demo-data.js
  -> Trip workspace demo/fallback trip, members, days, updates, comments, and guest draft data
```

The current rule is:

```text
Put stable cross-app constants and fallback data in shared/.
Do not move full Trip workspace pages into frontend/app yet.
```

Integration Stage C deeper runtime merge is paused, so `frontend/` and `trip/` remain separate apps for now.

## 12.5 backend/

`backend/` is:

```text
Python
FastAPI
SQLAlchemy
PostgreSQL
```

The existing backend already exposes:

```text
GET /api/health
```

Expected result:

```json
{"ok": true}
```

Do not add another duplicate `/health` endpoint.

---

# 13. Existing Backend Cloud-Readiness Findings

## 13.1 Health check

Use this future ALB health check:

```text
/api/health
```

not:

```text
/health
```

## 13.2 CORS

Current CORS is development-oriented and allows localhost origins.

Before production deployment, move allowed origins into an environment variable, for example:

```text
CORS_ORIGINS
```

Local defaults can remain:

```text
http://localhost:3000
http://localhost:5173
```

Future production origin will include the actual Amplify domain.

## 13.3 Existing runtime environment variables

The deployment documentation should track at least:

```text
DATABASE_URL
TEST_DATABASE_URL
OPENAI_API_KEY
OPENAI_BASE_URL
OPENAI_MODEL
MOCK_AI
SETTLE_TICK_SECONDS
DISABLE_SCHEDULER
FRONTEND_BASE_URL
CORS_ORIGINS
```

No real secret values belong in git.

---

# 14. Database Safety Rule

The current development seed/reset flow is destructive.

Current code includes development behavior equivalent to:

```python
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)
```

Therefore:

```text
NEVER run destructive seed/reset automatically against RDS.
```

The production database must not depend on:

```text
python -m app.db.seed
```

as an automatic deployment step.

A safe migration strategy such as Alembic must be introduced before production-style RDS schema evolution.

The eventual split should be:

```text
schema migration
-> safe / versioned / repeatable

demo seed
-> optional / manual / non-production
```

---

# 15. Scheduler / ECS Constraint

The FastAPI application starts a scheduler unless:

```text
DISABLE_SCHEDULER=1
```

This matters when ECS scales to more than one task.

If multiple API containers start with the scheduler enabled:

```text
Task 1 -> scheduler
Task 2 -> scheduler
Task 3 -> scheduler
```

the same scheduling behavior can execute multiple times.

Initial Capstone production model:

```text
ECS desiredCount = 1
```

Do not enable ECS autoscaling initially.

For CI and container validation:

```text
DISABLE_SCHEDULER=1
```

This must be injected by the runtime environment, CI job, or ECS task definition.
It should not be baked into the Docker image because the image should not decide scheduler policy.

A future scale-out architecture should separate API tasks and scheduled work.

---

# 16. Preferred Candidate AWS Architecture, Pending Final Frontend Hosting Decision

This is the preferred candidate architecture, not yet the final approved deployment architecture.

AWS Phase 3 proved that the current frontend build is not a pure static hosting artifact. It emits both `dist/client` and `dist/server`, so the final frontend hosting decision still requires an SSR-compatible compute path.

Candidate architecture:

```text
GitHub
  |
  |-- identity/build/deploy workflows
  |
  v

AWS us-east-1

Frontend:
Preferred candidate before Phase 3: Amplify Hosting
  -> main frontend
  -> embedded trip static output
Current status: SSR hosting path still undecided

Backend:
Internet
  -> Application Load Balancer
  -> ECS Fargate
  -> FastAPI

Database:
ECS
  -> RDS PostgreSQL

Logging:
ECS
  -> CloudWatch Logs

Runtime secrets:
ECS Task
  -> SSM Parameter Store SecureString
```

This is intentionally a student/demo architecture rather than a highly redundant production platform.

## 16.1 Frontend hosting decision status

```text
Preferred candidate:
AWS Amplify Hosting

Final hosting decision:
NOT YET LOCKED

Required before approval:
SSR hosting path decision for Vinext / Nitro / Next.js 16.x output
```

Codex must not skip the AWS Phase 3 frontend hosting proof simply because Amplify is listed as the preferred candidate.

## 16.2 Initial no-NAT network candidate

The initial backend network candidate is designed to avoid a NAT Gateway while still allowing the FastAPI container to call external services such as OpenAI.

```text
VPC
|
|-- Public Subnet A
|   |-- ALB node
|   `-- ECS Fargate task, Assign Public IP = ENABLED
|
|-- Public Subnet B
|   `-- ALB node
|
|-- Private DB Subnet A
|
`-- Private DB Subnet B
    `-- RDS PostgreSQL
```

Fargate:

```text
networkMode = awsvpc
public subnet placement for initial demo
Assign Public IP = ENABLED
desiredCount = 1
```

Security groups:

```text
ALB Security Group:
Inbound:
- HTTP/HTTPS from internet, as approved for the demo

ECS Security Group:
Inbound:
- backend container port, for example 8000, only from ALB Security Group
Outbound:
- internet allowed for OpenAI and required AWS APIs

RDS Security Group:
Inbound:
- PostgreSQL 5432 only from ECS Security Group
Outbound:
- default or tightly scoped as required
```

RDS:

```text
Publicly Accessible = No
Single-AZ database instance
DB subnet group must still contain subnets in at least two Availability Zones
```

Important AWS networking rules:

```text
ALB:
- Select subnets from at least two Availability Zones.

RDS DB subnet group:
- Include at least one subnet in at least two Availability Zones in the Region.

Fargate without NAT:
- Public subnet + Assign Public IP allows outbound internet access.
- Private subnet without NAT or required VPC endpoints will not have ordinary internet egress.
```

This design intentionally accepts public IPs on initial Fargate tasks to avoid NAT Gateway cost. Inbound access is still restricted by security groups so the ALB remains the only backend ingress path.

### 16.2.1 Direct ECS ingress is forbidden

A public IP on the initial Fargate task exists for **outbound internet connectivity**, not to create a second public API endpoint.

```text
Allowed:

Internet
-> ALB
-> ECS :8000

Forbidden:

Internet
-> ECS public IP :8000
```

The ECS security group must **not** allow the application port (for example `8000`) from `0.0.0.0/0` or `::/0`.

The application port should be reachable only from the ALB security group.

---

# 17. Cost Guardrails

The project is intentionally cost-controlled.

Initial constraints:

```text
Region: us-east-1

ECS:
- desiredCount = 1
- no autoscaling initially
- smallest practical CPU/memory

RDS:
- PostgreSQL
- Single-AZ
- smallest appropriate Free Plan/credit-compatible instance
- minimum practical storage
- no Multi-AZ
- no Provisioned IOPS
- publicly accessible = No

Networking:
- NO NAT Gateway initially unless explicitly approved
- Fargate public IPs are allowed only for the initial no-NAT backend candidate
- avoid any other unnecessary Elastic/Public IPv4 resources
- no complex multi-AZ enterprise topology for the first demo

Security/edge:
- no WAF initially
- no unnecessary paid observability products

Deployment:
- workflow_dispatch/manual gate first
```

A NAT Gateway must never be silently created by Codex or infrastructure automation.

---

# 18. Budget and Cost Monitoring

Before significant AWS provisioning:

1. Review the AWS Free Plan credit balance.
2. Record the Free Plan expiration date.
3. Create or confirm cost monitoring/budget alerts if available for the account configuration.
4. Monitor:
   - credits remaining;
   - current month estimated usage;
   - RDS;
   - ALB;
   - Fargate;
   - public IPv4;
   - CloudWatch usage.

The Free Plan does not mean resources have no underlying price. Credits/free-plan protections are a billing layer over service usage.

---

# 19. Runtime Application Secrets

Future application runtime secrets should not reuse GitHub AWS credentials.

Examples:

```text
DATABASE_URL
OPENAI_API_KEY
```

Recommended future storage:

```text
AWS Systems Manager Parameter Store
SecureString
```

Then:

```text
ECS Task Role
-> retrieve runtime parameters
```

Do not bake runtime secrets into Docker images.

Do not pass `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` into the application container unless the application has a specific documented reason to call AWS.

---

# 20. Deployment Phase Plan

All phases in this section are AWS phases.

Do not confuse these with the frontend integration track:

```text
Integration Stage A: route/embed contract alignment, complete
Integration Stage B: shared product/demo data extraction, complete for first pass
Integration Stage C: deeper runtime merge, paused
```

When asking Codex to continue this plan, use names such as `AWS Phase 2` or `AWS Build Validation CI`.

## Phase 0 鈥?Identity and Account Guardrails

Status: mostly complete. AWS resource provisioning remains blocked until the machine-IAM authorization checks below are resolved.

```text
AWS account created
Free Plan recognized
AWS Organizations avoided
Capstone-Admins created
human IAM users added
GitHub deployment IAM user created
GitHub AWS secrets/region configured
STS identity validation completed
```

Remaining identity checks:

```text
- confirm AdministratorAccess is attached to Capstone-Admins
- confirm both human IAM users are members
- confirm MFA for human accounts
- confirm Root MFA
- create or confirm ProjectOwnerAdmin / Human Admin 1 IAM user for daily project work
- confirm ProjectOwnerAdmin / Human Admin 1, Carina, and Dixon are all in Capstone-Admins
- verify github-actions-deploy attached policies
- verify github-actions-deploy inline policies
- confirm whether github-actions-deploy currently has AdministratorAccess
- define intended deployment permission boundary
- confirm github-actions-deploy access key status is Active
- activate delegated Billing access if team needs it
```

## Phase 1 鈥?Cloud Readiness

No AWS infrastructure creation.

Current status:

```text
frontend integration portion: complete
backend cloud-readiness portion: Phase 1 local preparation in progress
AWS infrastructure creation: not started
```

Completed frontend/shared work:

```text
shared preview contract aligned to /trip-app/index.html#/
shared domain route helpers aligned to active Trip routes
frontend /trip iframe shell reads shared preview config
Trip static output regenerated into frontend/public/trip-app/
shared product workflow/principle content extracted
shared Trip demo fallback data extracted
Windows-compatible build:trip-preview script confirmed
README.md updated for current architecture
INTEGRATION-ROADMAP.md updated for Integration Stage A/B status
```

Validated locally:

```bash
cd frontend
npm run build:trip-preview
npm test
```

Expected result:

```text
frontend build succeeds
trip build succeeds through build:trip-preview
frontend tests pass
Vite may print a chunk-size warning; that warning is not currently a failure
```

Remaining backend cloud-readiness work:

Backend:

```text
/api/health exists and must remain available
environment-driven CORS added through CORS_ORIGINS with localhost defaults
.env.example expanded with DISABLE_SCHEDULER, FRONTEND_BASE_URL, and CORS_ORIGINS
backend Dockerfile added for local/ECS candidate container validation
backend Docker image does not bake DISABLE_SCHEDULER; CI/ECS must inject scheduler policy explicitly
backend .dockerignore added so local secrets and virtual environments are not copied into images
Docker local build/run/health validation is still pending because Docker CLI is not installed on the current machine
this does not block Phase 2 because GitHub Actions ubuntu-latest runners can run docker build/run/health checks
```

Database:

```text
document destructive seed behavior
plan safe migration strategy
do not run production seed
```

Frontend:

```text
validate Vinext hosting requirements
validate trip build/sync
validate frontend build
```

The frontend build/sync/test portion is currently validated locally. It still needs GitHub Actions build-validation coverage.

Backend Phase 1 validation notes:

```text
targeted backend configuration test passed: backend/tests/test_api_config.py
Python compile check passed: python -m compileall app
local PostgreSQL setup documented separately in C:\Users\ROG\Desktop\PostgreSQL_Database_Setup.md
full backend pytest can run locally by injecting TEST_DATABASE_URL with the local PostgreSQL password
full backend pytest passed locally after using tripsync_test through the documented local PostgreSQL setup
Docker CLI is not installed locally, so container build/run/health validation should be moved into Phase 2 GitHub Actions
```

## Phase 2 鈥?Build Validation CI

Status: workflow created, awaiting manual GitHub Actions run.

Create:

```text
.github/workflows/build-validation.yml
```

Trigger:

```text
workflow_dispatch only
```

It should:

```text
checkout
build trip and sync embedded trip output
build frontend and run frontend integration tests
run backend tests against a GitHub Actions PostgreSQL service container
docker build backend image
run backend container with DISABLE_SCHEDULER=1 injected at runtime
curl /api/health
```

Frontend-specific CI expectations:

```text
cd trip
npm ci
npm run build

cd frontend
npm ci
npm run build:trip-preview
npm test
```

The workflow may build generated assets inside CI, but it should not commit them back to the repository.

The workflow is validation-only and uses no AWS credentials. It does not call STS, ECR, ECS, RDS, Amplify, CloudFormation, Terraform, or any other AWS mutation path.

Phase 2 container note:

```text
The backend image must include backend/data because plan generation imports data.poi_chicago at API import time.
Do not exclude backend/data from the Docker build context.
The CI container health job keeps the container available for logs on failure instead of using docker run --rm.
```

It must not:

```text
configure AWS credentials
push ECR
deploy ECS
create RDS
create Amplify
change IAM
mutate AWS
```

## Phase 3 鈥?Frontend Hosting Proof

Status: completed.

Investigate/validate:

```text
Vinext
-> Amplify-compatible build path
-> Nitro if required
```

Current Phase 3 proof files:

```text
AWS/archive/completed-phases/PHASE3_FRONTEND_HOSTING_PROOF.md
.github/workflows/frontend-hosting-proof.yml
frontend/scripts/aws-hosting-proof.mjs
frontend package script: npm run hosting:proof
```

Current proof conclusion:

```text
frontend/dist/client contains embedded Trip static output
frontend/dist/server is also produced
main app routes are not emitted as complete static HTML route files
current frontend should not be assumed to be pure static S3/CloudFront hosting ready
Amplify managed SSR compatibility is not assumed because the app uses Vinext with Next.js 16.x
```

Use the main frontend application as the hosting unit.

The Trip static build remains embedded.

Do not attempt to absorb `trip/src/final/*` pages into `frontend/app` during AWS deployment preparation unless Integration Stage C is explicitly restarted.

## Phase 4 鈥?Database

Status: database readiness validation completed.

Create minimal RDS PostgreSQL only after the schema migration approach is approved for AWS.

Required:

```text
Single-AZ
private
smallest practical instance
safe schema creation/migration
no destructive automatic seed
```

## Phase 5 鈥?Backend

Status: provisioned and verified.

Provisioning entrypoint:

```text
.github/workflows/phase5-backend-provision.yml
```

Provision result:

```text
AWS/TRIPSYNC_AWS_URLS.md
AWS/archive/completed-phases/PHASE5_BACKEND_PROVISION_RESULT.md
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31349402435
deployed commit: 9ff14d1fa7f5a4babf4b5a50107287c69fde1d21
backend ALB URL: http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
health endpoint: http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com/api/health
verified response: {"ok":true}
```

Reason for GitHub Actions provisioning:

```text
local AWS CLI exists
local Docker CLI is not installed
local AWS credentials may be present in backend/.env, but the shell must load them first
GitHub Actions has AWS credentials through environment Main
GitHub Actions ubuntu-latest runner has Docker
```

Detailed plan:

```text
AWS/archive/completed-phases/PHASE5_BACKEND_DEPLOYMENT_PLAN.md
```

First proof mode:

```text
GitHub/manual image build
-> ECR
-> ECS Fargate
-> ALB
-> GET /api/health
```

Initial proof runtime:

```text
DISABLE_SCHEDULER=1
MOCK_AI=1
desiredCount=1
```

The first proof is infrastructure-only.

It does not require:

```text
functional RDS connection
OpenAI integration
production frontend/backend integration
```

Do not describe the full TripSync backend as production-functional until RDS, runtime secrets, frontend API base URL, and production CORS are connected later.

Cost warning:

```text
Phase 5 resources now exist and may incur charges while running.
Main cost drivers are ALB, Fargate runtime, public IPv4 addresses, CloudWatch Logs, and ECR storage.
Use the cleanup order in AWS/archive/completed-phases/PHASE5_BACKEND_DEPLOYMENT_PLAN.md when the proof is no longer needed.
```

Exact initial VPC topology:

```text
one VPC
Public Subnet A in AZ A
Public Subnet B in AZ B
Private DB Subnet A in AZ A
Private DB Subnet B in AZ B
Internet Gateway
public route table with 0.0.0.0/0 -> Internet Gateway
ALB uses both public subnets
initial Fargate service uses public subnet networking with Assign Public IP = ENABLED
future RDS remains in the private DB subnet group
```

Proposed resource names after explicit approval:

```text
VPC: tripsync-vpc
Public Subnet A: tripsync-public-subnet-a
Public Subnet B: tripsync-public-subnet-b
Private DB Subnet A: tripsync-private-db-subnet-a
Private DB Subnet B: tripsync-private-db-subnet-b
Public route table: tripsync-public-rt
Private DB route table: tripsync-private-db-rt
Internet Gateway: tripsync-igw
ALB security group: tripsync-alb-sg
Backend security group: tripsync-backend-sg
Future RDS security group: tripsync-rds-sg
CloudWatch log group: /ecs/tripsync-backend
ECR repository: tripsync-backend
ECS cluster: tripsync-cluster
ECS task definition family: tripsync-backend
ECS service: tripsync-backend-service
ALB: tripsync-backend-alb
Target group: tripsync-backend-tg
ECS task execution role: tripsync-ecs-execution-role
ECS task role: tripsync-backend-task-role
```

Initial service shape:

```text
launch type=Fargate
networkMode=awsvpc
desiredCount=1
cpu=256
memory=512 MiB
health path=/api/health
deployment circuit breaker rollback=enabled
healthCheckGracePeriodSeconds=60
Assign Public IP=ENABLED
```

Upgrade trigger:

```text
increase to cpu=512 and memory=1024 MiB only if:
container exits with code 137
CloudWatch metrics show memory pressure
/api/health becomes unstable under normal demo traffic
application startup routinely exceeds the health grace period
```

ECR rule:

```text
repository=tripsync-backend
deployment tag=<commit-sha>
optional convenience tag=latest
recommend lifecycle policy to expire old untagged images and cap retained old images
initial applied lifecycle policy expires untagged images after 7 days
```

CloudWatch Logs rule:

```text
log group=/ecs/tripsync-backend
retention=7 days
do not log secrets, tokens, passwords, full database URLs, or sensitive request payloads
```

Estimated first-proof monthly cost:

```text
about $37-$44/month before AWS Free Plan credits
main drivers: ALB hourly charge, Fargate runtime, public IPv4 charges
recheck current us-east-1 AWS pricing immediately before provisioning
```

Rollback rule:

```text
use ECS deployment circuit breaker rollback
keep previous task definition revision
keep previous commit-SHA ECR image
do not rely only on latest
```

Cleanup order is recorded in `AWS/archive/completed-phases/PHASE5_BACKEND_DEPLOYMENT_PLAN.md`.

The first proof avoids NAT Gateway cost and avoids requiring all private-subnet ECR/logs VPC endpoints.

Production-hardening path after proof:

```text
move Fargate tasks to private subnets
use NAT Gateway or required VPC endpoints for ecr.dkr, ecr.api, s3, and logs
add ssmmessages endpoint if ECS Exec is enabled
```

Phase 5 approval gate:

```text
Do not create ECR, ECS, ALB, CloudWatch, IAM roles, or networking resources until the user explicitly approves Phase 5 backend resource creation.
```

## Phase 6 鈥?Runtime Secrets

Status: repository readiness completed.

Current Phase 6 files:

```text
AWS/PHASE6_RUNTIME_SECRETS_PLAN.md
AWS/archive/completed-phases/PHASE6_RUNTIME_SECRETS_READINESS_RESULT.md
AWS/archive/completed-phases/PHASE6_RUNTIME_PROVISION_PLAN.md
AWS/archive/completed-phases/PHASE6_RUNTIME_PROVISION_RESULT.md
.github/workflows/runtime-secrets-readiness.yml
.github/workflows/phase6-runtime-provision.yml
```

Move runtime application secrets to SSM Parameter Store SecureString or Secrets Manager only after the resource creation path is approved.

No secret values belong in Git, Codex chat, workflow YAML plaintext, Docker images, or frontend bundles.

Current Phase 6 readiness workflow:

```text
workflow_dispatch only
no AWS credentials
no GitHub Secrets access
no AWS secret reads
no env or printenv
checks tracked files for accidentally committed .env files
checks high-confidence secret patterns
confirms Phase 6 runtime rules are documented
```

Readiness result:

```text
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31349738285
checked commit: 9fb73a619b73a311b30459e1a9c8c1936c851889
result: success
```

The workflow does not prove that future AWS SSM/Secrets Manager parameters exist. It only protects the repository before runtime secrets are created later.

Next required runtime step:

```text
Create minimal RDS PostgreSQL first.
Then create DATABASE_URL as a runtime secret/parameter.
Then update ECS task definition to inject the secret.
Then switch backend from infrastructure proof toward real runtime mode.
```

Do not create RDS, SSM parameters, Secrets Manager secrets, IAM policy changes, or ECS secret injection without explicit human approval.

Phase 6 RDS/runtime secret provisioning has been approved by the user.

Runtime provision result:

```text
RDS PostgreSQL: tripsync-postgres
RDS endpoint: tripsync-postgres.cqv0oqgogc0p.us-east-1.rds.amazonaws.com
DB subnet group: tripsync-private-db-subnet-group
SSM SecureString: /tripsync/backend/prod/database-url
ECS task definition: inject DATABASE_URL through secrets field
schema init: python -m app.db.init_schema from one-off ECS task
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31350032734
result: success
local env sync targets:
  C:\Users\ROG\Desktop\capstone\cap_stone-main\backend\.env
  C:\Users\ROG\Desktop\capstone\cap_stone-main\.env
```

The deployed backend now has `DATABASE_URL` injected from SSM Parameter Store, but remains in proof/demo mode with `DISABLE_SCHEDULER=1` and `MOCK_AI=1`.

## Phase 7 鈥?Frontend/Backend Integration

Status: partial completion; frontend container readiness added.

Current files:

```text
AWS/archive/completed-phases/PHASE7_FRONTEND_BACKEND_INTEGRATION.md
AWS/archive/completed-phases/PHASE7_FRONTEND_BACKEND_INTEGRATION_RESULT.md
AWS/archive/completed-phases/PHASE7_FRONTEND_CONTAINER_READINESS.md
AWS/archive/completed-phases/PHASE7_FRONTEND_CONTAINER_READINESS_RESULT.md
.github/workflows/phase7-backend-runtime-config.yml
.github/workflows/frontend-container-readiness.yml
frontend/Dockerfile
.dockerignore
```

Local ignored env files have been synced so the frontend can call the AWS backend ALB:

```text
frontend/.env.local -> NEXT_PUBLIC_API_BASE_URL
trip/.env.local -> VITE_API_BASE_URL
```

Current AWS backend endpoint:

```text
http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
```

Backend runtime config workflow is ready to set:

```text
FRONTEND_BASE_URL
CORS_ORIGINS
DEV_ALLOW_MEMBERSHIP_HEADER=0
```

Final production CORS and invite URL remain blocked until the frontend has a final public hosting URL.

Phase 7 first runtime config result:

```text
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31352265951
FRONTEND_BASE_URL=http://localhost:3000
CORS_ORIGINS=http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173
DEV_ALLOW_MEMBERSHIP_HEADER=0
health result={"ok":true}
```

Frontend container readiness:

```text
workflow: .github/workflows/frontend-container-readiness.yml
trigger: workflow_dispatch only
AWS resources created: none
image pushed to ECR: no
purpose: prove the merged frontend + trip app can run as a Vinext SSR container
container port: 3000
start command: npm run start -- --hostname 0.0.0.0 --port 3000
validated paths: /login and /trip-app/index.html
default backend API base URL: http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
result: success
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31353334323
validated commit: f4dc8772ae9b373a21cc2cbd1551c1c9ca85488e
```

This frontend container readiness step is not a deployment. It does not prove a public frontend URL, ALB routing, frontend ECR, HTTPS, custom domain, or production CORS.

The GitHub run showed npm audit warnings during dependency installation. They did not block the readiness proof, but dependency audit triage should be handled separately before a production release.

The next AWS resource creation step requires a separate explicit approval gate. Candidate approval phrase:

```text
Approve frontend ECS service creation
```

## Phase 8 鈥?Deployment Automation

Frontend ECS service creation was approved with:

```text
Approve frontend ECS service creation
```

Current Phase 8 frontend files:

```text
AWS/archive/completed-phases/PHASE8_FRONTEND_ECS_PROVISION_PLAN.md
AWS/archive/completed-phases/PHASE8_FRONTEND_ECS_PROVISION_RESULT.md
.github/workflows/phase8-frontend-provision.yml
```

First frontend AWS architecture:

```text
reuse existing ALB: tripsync-backend-alb
/api/* -> tripsync-backend-tg
default -> tripsync-frontend-tg
ECR repository -> tripsync-frontend
ECS service -> tripsync-frontend-service
task definition family -> tripsync-frontend
CloudWatch log group -> /ecs/tripsync-frontend
security group -> tripsync-frontend-sg
desiredCount=1
cpu=256
memory=512 MiB
Assign Public IP=ENABLED
```

This creates an additional frontend Fargate task and related frontend resources, but does not create a second ALB, NAT Gateway, VPC, subnet, RDS instance, or IAM user.

Phase 8 frontend provision result:

```text
status: success
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31353851142
backend runtime config update run: https://github.com/shnnzdx/cap_stone/actions/runs/31354040217
frontend public URL: http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
backend health URL: http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com/api/health
local public checks:
  GET /login -> 200
  GET /trip-app/index.html -> 200
  GET /api/health -> {"ok":true}
```

Current ALB routing:

```text
/api/* -> tripsync-backend-tg
default -> tripsync-frontend-tg
```

Backend runtime config now uses the ALB frontend URL:

```text
FRONTEND_BASE_URL=http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
CORS_ORIGINS=http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com,http://localhost:3000,http://localhost:5173,http://127.0.0.1:5173
DEV_ALLOW_MEMBERSHIP_HEADER=0
```

Only now create the actual deployment workflow.

Keep it manually triggered first.

Example high-level flow:

```text
workflow_dispatch
-> build
-> login to ECR
-> docker build
-> docker push
-> create/update ECS task definition
-> update ECS service
-> wait for deployment
-> health verification
```

Do not merge this into the identity-check workflow.

## Phase 9 鈥?Demo / Operations Runbook

Status: public browser E2E validated.

Current Phase 9 files:

```text
AWS/archive/completed-phases/PHASE9_PUBLIC_E2E_RUNBOOK.md
AWS/archive/completed-phases/PHASE9_PUBLIC_E2E_RESULT.md
.github/workflows/phase9-public-e2e.yml
frontend/tests/aws-public-e2e.mjs
```

Public browser E2E result:

```text
GitHub Actions run: https://github.com/shnnzdx/cap_stone/actions/runs/31355307955
screenshot artifact: https://github.com/shnnzdx/cap_stone/actions/runs/31355307955/artifacts/9050425261
validated public URL: http://tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
validated:
  GET /
  GET /login
  GET /trip
  iframe src = /trip-app/index.html#/
  GET /trip-app/index.html
  GET /trip-app/index.html#/
  GET /api/health -> {"ok":true}
```

Phase 9 found and fixed a deployed Trip iframe issue:

```text
old iframe src: /trip-app/#/
new iframe src: /trip-app/index.html#/
frontend redeploy run: https://github.com/shnnzdx/cap_stone/actions/runs/31354836829
```

Known non-blocking browser console noise:

```text
[vinext] RSC prefetch setup error
```

This is currently ignored by E2E because the public pages, iframe shell, embedded Trip static entry, and backend health endpoint pass.

## Phase 10 - HTTPS / Custom Domain

Status: workflow ready; blocked on domain input.

Current files:

```text
AWS/PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md
.github/workflows/phase10-https-custom-domain.yml
```

Required inputs:

```text
domain_name=<owned custom domain, for example app.example.com>
hosted_zone_id=<existing Route 53 public hosted zone ID>
keep_localhost_cors=true or false
```

The workflow does not create a hosted zone and does not create a second ALB.

Planned final routing:

```text
https://<domain_name>/ -> frontend
https://<domain_name>/api/* -> backend
http://<domain_name>/* -> 301 redirect to https://<domain_name>/*
```

The workflow creates or modifies only HTTPS/custom-domain-related resources:

```text
ACM public certificate
ACM DNS validation record in Route 53
Route 53 A alias to existing ALB
ALB security group inbound 443
ALB HTTPS listener
HTTP-to-HTTPS redirect
frontend ECS redeploy with HTTPS API base URL
backend ECS runtime config with HTTPS FRONTEND_BASE_URL and CORS_ORIGINS
```

Document:

```text
how to deploy
how to rollback
how to inspect CloudWatch logs
how to stop/delete resources
how to check cost/credits
how to rotate GitHub access keys
how to recover from a failed ECS deployment
```

---

# 21. What Codex Must Never Do Without Explicit Approval

```text
Create/join AWS Organizations
Upgrade the AWS account plan
Create a NAT Gateway
Enable Multi-AZ RDS
Create large RDS/Fargate instances
Create Provisioned IOPS
Enable ECS autoscaling
Create WAF
Create new long-term AWS access keys
Create Root access keys
Store secrets in source control
Run destructive DB seed/reset against RDS
Replace the identity-check workflow with a deployment workflow
Automatically deploy on every push before manual deployment is proven
Delete AWS resources without explicit approval
Change human IAM permissions without explicit approval
```

---

# 22. Equality Checklist for the Three Human Members

Target human access state:

```text
Root
-> account-owner / recovery only
-> not counted as one of the three daily project identities

Capstone-Admins
-> AdministratorAccess
   |-- ProjectOwnerAdmin / Human Admin 1
   |-- Carina
   `-- Dixon
```

Target checklist:

```text
[ ] Root MFA enabled
[ ] Root has no access keys
[ ] ProjectOwnerAdmin / Human Admin 1 exists as an IAM user
[ ] ProjectOwnerAdmin / Human Admin 1 is in Capstone-Admins
[ ] Carina is in Capstone-Admins
[ ] Dixon is in Capstone-Admins
[ ] Capstone-Admins has AdministratorAccess
[ ] Each IAM user has an independent password
[ ] Each IAM user has independent MFA
[ ] No shared IAM credentials
[ ] Billing IAM access enabled if delegated billing visibility is required
```

Result:

```text
Three human daily IAM identities:
same long-term authorization model

Root-only account ownership:
separate by design
```

---

# 23. Access Key Policy

## Human users

Default:

```text
Console password + MFA
```

Only create an access key if that person actually needs CLI/SDK programmatic access.

If created:

```text
one unique key per IAM user
never shared
rotate/revoke when no longer needed
```

## GitHub

`github-actions-deploy` owns the deployment access key.

GitHub Environment `Main` stores:

```text
AWS_ACCESS_KEY_ID
AWS_SECRET_ACCESS_KEY
```

Do not copy this deployment key to teammates' local `.env` files unless there is a specific operational reason.

The machine identity should remain auditable as a machine identity.

---

# 24. Source-of-Truth Rules for Future Codex Sessions

When giving this document to Codex:

1. Codex must inspect the repository before modifying workflows.
2. Existing AWS identity verification is considered complete and separate.
3. No deployment should begin until build validation is green.
4. No destructive database step is allowed.
5. No cost-bearing architecture expansion is allowed without explicit approval.
6. The current Free Plan / no-Organizations constraint overrides generic AWS best-practice suggestions that would create an Organization.
7. The current deliberate use of long-term GitHub AWS access keys must be respected unless the team explicitly decides to migrate to OIDC.
8. 鈥淭hree human accounts equal鈥?means same day-to-day IAM administration policy; Root remains a separate account-owner concept.
9. Do not assume a successful STS identity check means the deployment user has every deployment permission.
10. Always report exactly which AWS resources a proposed step will create and which of them can incur ongoing hourly/storage/network costs before provisioning.

---

# 25. Identity Diagrams

## 25.1 Current state 鈥?2026-08-09

```text
                     AWS Account 448678332746
                              |
             +----------------+----------------+
             |                                 |
          Root user                            IAM
     account owner identity                    |
                                               |
                                +--------------+------------------+
                                |                                 |
                         Capstone-Admins                  github-actions-deploy
                                |                          machine identity
                         AdministratorAccess                    |
                                |                               |
                          +-----+-----+                         |
                          |           |                         |
                       Carina       Dixon                       |
                                                              |
                                                       Access Key Pair
                                                              |
                                                              v
                                                     GitHub Environment
                                                           Main
                                                              |
                                           +------------------+------------------+
                                           |                                     |
                                AWS_ACCESS_KEY_ID                    AWS_SECRET_ACCESS_KEY
                                           |
                                           +------------------+
                                                              |
                                                        AWS_REGION
                                                        us-east-1
                                                              |
                                                              v
                                                       GitHub Actions
                                                              |
                                                              v
                                                    sts:GetCallerIdentity
                                                              |
                                                              v
                                                     Account 448678332746
```

This is the **current** state. The project owner still has Root as the human account-owner identity, and the third daily IAM administrator is not yet confirmed.

## 25.2 Target daily human access state

```text
                     AWS Account 448678332746
                              |
             +----------------+----------------+
             |                                 |
          Root user                            IAM
 account-owner / recovery only                 |
 not a daily project identity                  |
                                               |
                                +--------------+------------------+
                                |                                 |
                         Capstone-Admins                  github-actions-deploy
                                |                          machine identity
                         AdministratorAccess                    |
                                |                               |
                   +------------+------------+                  |
                   |            |            |                  |
          ProjectOwnerAdmin   Carina       Dixon                |
           / Human Admin 1                                     |
                   |            |            |                  |
                   +------------+------------+                  |
                                |                               |
                       same daily IAM policy                    |
                                                              |
                                                       Access Key Pair
                                                              |
                                                              v
                                                     GitHub Environment
                                                           Main
```

Target result:

```text
Three human daily IAM identities:
-> same IAM user type
-> same Capstone-Admins group
-> same AdministratorAccess policy
-> independent password
-> independent MFA

Root:
-> separate account-owner/recovery identity

github-actions-deploy:
-> separate machine identity
```

---

# 26. Public Repository Hygiene

The AWS Account ID and IAM ARN recorded in this master context are operational identifiers, not authentication secrets. They do not by themselves grant access to AWS.

However, if this document is committed to a **public** GitHub repository, prefer redacting those identifiers in the public copy:

```text
AWS Account ID:
<CAPSTONE_AWS_ACCOUNT_ID>

IAM ARN:
arn:aws:iam::<CAPSTONE_AWS_ACCOUNT_ID>:user/github-actions-deploy
```

Keep the real account ID in a private/local operational note when public disclosure is unnecessary.

This is a hygiene recommendation, not a credential-rotation event.

---

# 27. AWS Documentation Layout

The AWS folder has been consolidated so the root directory stays readable.

Current root entry points:

```text
AWS/README.md
AWS/TRIPSYNC_AWS_MASTER_CONTEXT_FINAL.md
AWS/TRIPSYNC_AWS_URLS.md
AWS/PHASE10_HTTPS_CUSTOM_DOMAIN_PLAN.md
AWS/CLOUD_FIXED_ACCOUNTS_RUNBOOK.md
GitHub CD-02 DeepSeek-only activation workflow
AWS/PHASE6_RUNTIME_SECRETS_PLAN.md
```

Completed Phase 3-9 plan/result/runbook files are archived under:

```text
AWS/archive/completed-phases/
```

`AWS/PHASE6_RUNTIME_SECRETS_PLAN.md` intentionally remains in the root because `.github/workflows/runtime-secrets-readiness.yml` validates that exact file path.

Historical note:

As of Thursday, August 13, 2026, the older cloud seeding paths were retired.
The only current cloud data administration entry points are:

```text
AWS/CLOUD_FIXED_ACCOUNTS_RUNBOOK.md
.github/workflows/cloud-fixed-accounts-upsert.yml
```

For backend AI runtime activation, use:

```text
.github/workflows/cd01-release-b-ai-activation.yml
```

This protected, DeepSeek-only path keeps the current private RDS model,
preserves the deployed backend service shape and exact Release A image, and
updates only the approved runtime profile after a Main-environment approval.

Backend AI runtime activation completed successfully:

```text
https://github.com/shnnzdx/cap_stone/actions/runs/31406205586
```

Historical deployed AI runtime evidence (superseded by CD-02):

```text
MOCK_AI=0
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
OPENAI_API_KEY injected through SSM Parameter Store
```

---

# 28. Official AWS References

AWS Root user best practices:
https://docs.aws.amazon.com/IAM/latest/UserGuide/root-user-best-practices.html

AWS IAM security best practices:
https://docs.aws.amazon.com/IAM/latest/UserGuide/best-practices.html

AWS security credentials:
https://docs.aws.amazon.com/IAM/latest/UserGuide/security-creds.html

AWS managed AdministratorAccess job-function policy:
https://docs.aws.amazon.com/IAM/latest/UserGuide/access_policies_job-functions.html

IAM access keys:
https://docs.aws.amazon.com/IAM/latest/UserGuide/id_credentials_access-keys.html

AWS Billing IAM access:
https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/control-access-billing.html

AWS Free Plan:
https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-plans.html

AWS Free Tier FAQs:
https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/free-tier-FAQ.html

IAM Identity Center enablement / Free Plan warning:
https://docs.aws.amazon.com/singlesignon/latest/userguide/enable-identity-center.html

Amazon ECS outbound internet networking:
https://docs.aws.amazon.com/AmazonECS/latest/developerguide/networking-outbound.html

Application Load Balancer creation and subnet/AZ requirements:
https://docs.aws.amazon.com/elasticloadbalancing/latest/application/create-application-load-balancer.html

RDS DB subnet group creation and AZ coverage requirement:
https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_CreateDBSubnetGroup.html

---

# 29. One-Sentence Policy

> TripSync uses one standalone AWS Free Plan account; the fixed target daily human model is three independent IAM administrators in `Capstone-Admins` with the same `AdministratorAccess` policy while Root remains separate for account-owner/recovery use; GitHub Actions uses the separate long-term `github-actions-deploy` machine credential stored in GitHub Environment `Main`; and no AWS resource provisioning begins until build validation, machine-IAM authorization, database-safety, and cost guardrails are satisfied.
