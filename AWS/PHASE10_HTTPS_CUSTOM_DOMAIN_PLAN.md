# TripSync AWS Phase 10 HTTPS Custom Domain Plan

Status: historical Route 53 plan; not an active production workflow.

Date: 2026-08-10

The production custom domain is currently operated through Aliyun DNS. The
unused Route 53 implementation is preserved as historical evidence and must not
be treated as the current DNS runbook.

Archived workflow:

```text
AWS/archive/workflows/completed-setup/phase10-https-custom-domain.yml
```

This phase is only for HTTPS and custom domain configuration.

---

## Required Inputs

The workflow requires:

```text
domain_name
hosted_zone_id
keep_localhost_cors
```

Example:

```text
domain_name=app.example.com
hosted_zone_id=Z1234567890ABC
keep_localhost_cors=true
```

The hosted zone must already exist in Route 53 and must be the public hosted zone for the domain. The workflow does not create a hosted zone because hosted zones have their own ongoing cost and often require registrar nameserver changes.

---

## What The Workflow Creates Or Modifies

Create or reuse:

```text
ACM public certificate in us-east-1
ACM DNS validation CNAME record in Route 53
Route 53 A alias record from domain_name to tripsync-backend-alb
ALB HTTPS listener on port 443
HTTPS /api/* listener rule to backend target group
```

Modify existing:

```text
ALB security group allows inbound 443
HTTP listener redirects to HTTPS
frontend ECS service is redeployed with HTTPS API base URL
backend ECS runtime config uses HTTPS FRONTEND_BASE_URL and CORS_ORIGINS
```

Does not create:

```text
new ALB
new VPC
new subnet
new NAT Gateway
new RDS
new IAM user
new hosted zone
```

---

## Final Routing

```text
https://<domain_name>/ -> frontend
https://<domain_name>/login -> frontend
https://<domain_name>/trip -> frontend iframe shell
https://<domain_name>/trip-app/index.html#/ -> embedded Trip app
https://<domain_name>/api/* -> backend
http://<domain_name>/* -> 301 redirect to https://<domain_name>/*
```

---

## Validation

The workflow validates:

```text
GET https://<domain_name>/login
GET https://<domain_name>/trip-app/index.html
GET https://<domain_name>/api/health
```

After it passes, run:

```text
Phase 9 Public E2E
public_url=https://<domain_name>
```

---

## Cost Notes

This phase avoids a second ALB and does not create a hosted zone.

Expected additional direct hourly cost:

```text
none from ACM public certificate attached to ALB
none from a Route 53 alias record itself
```

Possible usage-based costs:

```text
Route 53 hosted zone cost already applies if the hosted zone exists.
Route 53 DNS queries are usage-based.
ALB LCU usage may increase slightly with HTTPS traffic.
```

---

## Rollback

```text
1. Re-run backend runtime config with the previous HTTP ALB URL if needed.
2. Re-run frontend provision with the previous HTTP ALB API base URL if needed.
3. Modify HTTP listener default action back to frontend forwarding if needed.
4. Delete HTTPS listener if abandoning HTTPS.
5. Delete the Route 53 alias record if abandoning the custom domain.
6. Keep or delete the ACM certificate depending on whether the domain will be reused.
```

---

## Current Blocker

Need the actual domain and existing Route 53 public hosted zone ID before running the workflow.
