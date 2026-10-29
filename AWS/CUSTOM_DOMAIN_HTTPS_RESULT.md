# TripSync AWS Custom Domain HTTPS Result

Status: completed manually.

Date: 2026-08-15

Region: us-east-1

Public domain:

```text
https://app.cadensy.top
```

This document records the current custom-domain deployment shape for the
TripSync/Cadensy AWS environment and answers where the frontend, backend, and
database are hosted.

## Summary

TripSync is not deployed as loose files on a traditional server.

The cloud runtime uses this path:

```text
GitHub repository
-> GitHub Actions build
-> AWS ECR Docker images
-> AWS ECS Fargate services
-> AWS ALB public entry
```

Current public routing:

```text
https://app.cadensy.top/
https://app.cadensy.top/login
https://app.cadensy.top/trip
https://app.cadensy.top/trip-app/index.html
  -> ALB HTTPS listener 443
  -> tripsync-frontend-tg
  -> tripsync-frontend-service

https://app.cadensy.top/api/*
  -> ALB HTTPS listener 443
  -> tripsync-backend-tg
  -> tripsync-backend-service
```

## Frontend Location

Source code:

```text
frontend/
trip/
```

The standalone Trip workspace is built from `trip/` and copied into:

```text
frontend/public/trip-app/
```

Cloud image repository:

```text
ECR repository: tripsync-frontend
```

Cloud runtime:

```text
ECS cluster: tripsync-cluster
ECS service: tripsync-frontend-service
Task definition family: tripsync-frontend
Container port: 3000
Target group: tripsync-frontend-tg
```

## Backend Location

Source code:

```text
backend/
```

Cloud image repository:

```text
ECR repository: tripsync-backend
```

Cloud runtime:

```text
ECS cluster: tripsync-cluster
ECS service: tripsync-backend-service
Task definition family: tripsync-backend
Container port: 8000
Target group: tripsync-backend-tg
```

The backend source code is not exposed through the public website. Public users
can call exposed API routes such as `/api/health`, but source visibility depends
on GitHub repository permissions, not on ECS.

## Database Location

Cloud database:

```text
RDS instance: tripsync-postgres
Engine: PostgreSQL
Endpoint: tripsync-postgres.cqv0oqgogc0p.us-east-1.rds.amazonaws.com
```

Network position:

```text
RDS publicly accessible: No
Access path: backend ECS service -> private RDS
No direct public browser -> RDS path
No direct laptop -> RDS path
```

## Domain And Certificate

Domain registrar and DNS provider:

```text
Alibaba Cloud / Aliyun
Domain: cadensy.top
Application subdomain: app.cadensy.top
Nameservers:
dns7.hichina.com
dns8.hichina.com
```

Aliyun DNS records:

```text
app.cadensy.top
CNAME -> tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com

_bb94dd027c89d4770bffe29d49bef15c.app.cadensy.top
CNAME -> _136accfd7705ab5ee9791cb019ea9e53.jkddzztszm.acm-validations.aws
```

AWS certificate:

```text
ACM region: us-east-1
Certificate domain: app.cadensy.top
Certificate status: Issued
Certificate source: AWS managed
Export option: Disabled
```

Important:

```text
The domain is not registered in Route 53.
Do not use "Create records in Route 53" for this certificate.
ACM DNS validation is handled manually through Aliyun DNS.
```

## ALB Configuration

Load balancer:

```text
ALB: tripsync-backend-alb
DNS name: tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com
Security group: tripsync-alb-sg
```

Listeners:

```text
HTTP:80
Default action: forward to tripsync-frontend-tg
Rule: /api/* -> tripsync-backend-tg

HTTPS:443
Default action: forward to tripsync-frontend-tg
Rule priority 10: /api/* -> tripsync-backend-tg
Certificate: app.cadensy.top from ACM
```

ALB security group inbound rules:

```text
HTTP  TCP 80   0.0.0.0/0
HTTPS TCP 443  0.0.0.0/0
```

## Validation

Local read-only smoke checks completed:

```text
GET https://app.cadensy.top
Result: HTTP/2 200

GET https://app.cadensy.top/api/health
Result: {"ok":true}

GET http://app.cadensy.top/api/health
Result: {"ok":true}
```

DNS checks completed:

```text
app.cadensy.top
CNAME -> tripsync-backend-alb-2124233156.us-east-1.elb.amazonaws.com

_bb94dd027c89d4770bffe29d49bef15c.app.cadensy.top
CNAME -> _136accfd7705ab5ee9791cb019ea9e53.jkddzztszm.acm-validations.aws
```

Recommended final browser check:

```text
GitHub Actions
-> Operations — Public Production E2E
-> public_url=https://app.cadensy.top
```

## Current Caution

The repository's existing workflow:

```text
.github/workflows/phase10-https-custom-domain.yml
```

was designed for a Route 53 hosted zone input. This deployment was completed
manually because the domain is registered and managed in Aliyun DNS.

Do not re-run Phase 10 blindly for this domain without reviewing the workflow
first. In particular, preserve the current backend AI runtime when making any
future backend task definition update.

## Operator Explanation

Short answer for teammates:

```text
The frontend and backend are hosted on AWS ECS Fargate, not in normal server
folders. The source lives in GitHub. GitHub Actions builds Docker images and
pushes them to ECR. ECS runs tripsync-frontend-service on port 3000 and
tripsync-backend-service on port 8000. The public entry is ALB
tripsync-backend-alb. https://app.cadensy.top points to the ALB. /api/* goes to
backend ECS, and all other paths go to frontend ECS. The database is private
RDS PostgreSQL.
```
