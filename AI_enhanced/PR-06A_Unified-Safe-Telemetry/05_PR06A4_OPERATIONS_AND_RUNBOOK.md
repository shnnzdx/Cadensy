# PR-06A.4 — Operations and Runbook

Production output is one compact JSON object per stdout line from the controlled
writer. It is intentionally compatible with a container stdout collection pipeline
such as ECS/Fargate → CloudWatch Logs, but this PR performs **no AWS operation** and
makes no retention, IAM, encryption, task-definition, log-group, alarm, dashboard,
or delivery-health claim.

Validate deployment separately by confirming the deployed runtime receives stdout,
each line parses as JSON, and the destination applies the organization-approved
retention/access policy. Do not infer physical provider HTTP delivery from a
`provider_sdk` event; it marks only the application wrapper crossing.

Rollback is a normal source revert. The sink intentionally has no file, queue, retry,
or external service dependency, so rollback creates no telemetry data migration.
