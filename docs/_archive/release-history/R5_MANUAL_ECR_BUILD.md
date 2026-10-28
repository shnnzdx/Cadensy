# R5 Manual ECR Image Build

After the associated pull request is merged, a release operator can open the
**R5 — Manual ECR Build & Push** workflow in GitHub Actions and dispatch it
from `main`.

The workflow builds application source pinned to
`6daf90ed4f1130171ff6e84c11241f374dc5aeeb`, verifies the existing
`tripsync-backend` ECR repository, and publishes two immutable images only:

- Runtime: `<ECR URI>:6daf90ed4f1130171ff6e84c11241f374dc5aeeb`
- Migration runner: `<ECR URI>:6daf90ed4f1130171ff6e84c11241f374dc5aeeb-migration-<tooling SHA>`

The action summary records the final URI, tags, digests, source SHA, tooling
SHA, and run ID. It refuses an existing tag instead of replacing it. It does
not deploy ECS, run database migrations, or change application configuration.
