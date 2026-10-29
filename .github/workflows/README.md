# Active GitHub Actions Workflows

Status: current operator map
Last reviewed: 2026-10-29

Only workflows that remain valid CI, artifact, release, or production-operation entry points belong in this directory. Completed setup workflows and superseded release paths are preserved under [`../../AWS/archive/workflows/`](../../AWS/archive/workflows/README.md), where GitHub will not expose them as runnable Actions.

## Active workflow inventory

| Category | Workflow | Trigger | Responsibility |
| --- | --- | --- | --- |
| CI | `build-validation.yml` | pull request, push, manual | Required regression, build, migration-runner, and Pydantic compatibility gates |
| Artifacts | `cd01-build-publish.yml` | successful main CI, manual rebuild | Build and publish digest-addressed backend and migration-runner images; no deployment |
| Artifacts | `cd01-frontend-build-publish.yml` | successful main CI, manual rebuild | Build and publish a digest-addressed frontend image; no deployment |
| Release | `cd01-pr06b-schema-migration.yml` | protected manual dispatch | Run the explicitly approved production schema migration gate |
| Release | `cd01-production-release.yml` | protected manual dispatch | Promote a validated backend image with rollback checks |
| Release | `cd01-frontend-production-release.yml` | protected manual dispatch | Promote a validated frontend image with rollback checks |
| Release | `cd03-pydantic-production-release.yml` | protected manual dispatch | Validate, activate, or roll back the Pydantic runtime profile |
| Operations | `cloud-fixed-accounts-upsert.yml` | protected manual dispatch | Upsert the two fixed cloud accounts through an isolated ECS task |
| Operations | `cloud-demo-purge.yml` | protected manual dispatch | Remove obsolete demo data through an isolated ECS task |
| Operations | `phase9-public-e2e.yml` | manual dispatch | Run browser acceptance against the public production URL |

## Frozen boundaries

- CI validates source; it does not publish or deploy.
- Artifact workflows publish immutable-addressed build outputs; they do not update ECS, run migrations, or change data.
- Release workflows are separate because their approval, rollback, and evidence contracts differ. Do not combine them into one unrestricted shell job.
- Data operations remain separate from release operations so an account repair or demo purge cannot inherit deployment permissions accidentally.
- All AWS mutation workflows must be dispatched from `main` and use the protected `Main` environment where specified.

## Consolidation policy

Do not add a new top-level workflow for a one-time setup phase. Prefer an existing release/operations entry point, a reviewed script invoked by it, or a dated archived runbook. A new workflow is justified only when it owns a distinct permission, approval, trigger, rollback, or evidence boundary.

The next safe optimization candidate is the artifact layer: backend and frontend builds can eventually share source-selection and provenance helpers. That work should preserve independent image failures and must add path-aware behavior before changing current automatic publication.
