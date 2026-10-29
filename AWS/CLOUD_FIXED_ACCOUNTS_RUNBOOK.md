# TripSync Cloud Fixed Accounts Runbook

Status: active.

Purpose:

```text
Create or update the two fixed backend accounts in the private cloud RDS
database without creating any demo trip or itinerary data.
```

Workflow:

```text
.github/workflows/cloud-fixed-accounts-upsert.yml
```

## Why This Exists

The deployed product now supports normal self-registration through:

```text
POST /api/auth/register
```

At the same time, the project still wants two fixed backend accounts to exist in
the real database for controlled access and verification.

Those fixed accounts must be created safely through ECS because cloud RDS
remains private:

```text
RDS publicly accessible = No
RDS port 5432 allows only tripsync-backend-sg
ECS injects DATABASE_URL from SSM SecureString
```

## What The Workflow Does

```text
GitHub Actions
-> build current backend image
-> push image to existing tripsync-backend ECR
-> register one-off Fargate task definition
-> run one-off ECS task using existing backend service subnets/security group
-> ECS task receives DATABASE_URL from existing SSM parameter
-> task runs python -m app.db.upsert_fixed_accounts
-> public API login is verified for both fixed accounts
```

The workflow does not:

```text
make RDS public
add laptop IP ingress to RDS
create any trip
create any itinerary items
write demo seed data
print DATABASE_URL
print database password
read SSM SecureString plaintext in GitHub logs
update the long-running backend ECS service
```

## Fixed Accounts

The current fixed-account defaults are:

```text
organizer@cadensy.local
participant@cadensy.local
```

Passwords are written as password hashes in the real database. They are not
printed by the workflow.

## Manual Run

In GitHub:

```text
Actions
-> Operations — Cloud Fixed Accounts Upsert
-> Run workflow
```

Expected success:

```text
Cloud fixed account task completed successfully.
Both fixed accounts authenticate through the public backend API.
```

## Verification

The workflow verifies:

```text
POST /api/auth/login works for organizer@cadensy.local
POST /api/auth/login works for participant@cadensy.local
```

This workflow does not require either account to have a trip membership.

## Security Position

This keeps the approved network model:

```text
laptop -> public ALB -> backend API
ECS task -> private RDS
no laptop -> RDS direct path
```
