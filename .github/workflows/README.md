# Active GitHub Actions Workflows

Status: current operator map
Last reviewed: 2026-11-24

Only workflows that remain valid CI, artifact, release, or production-operation entry points belong in this directory. Completed setup workflows and superseded release paths are preserved under [`../../AWS/archive/workflows/`](../../AWS/archive/workflows/README.md), where GitHub will not expose them as runnable Actions.

## Active workflow inventory

| Category | Workflow | Trigger | Responsibility |
| --- | --- | --- | --- |
| CI | `build-validation.yml` | pull request, push, manual | Classifies documentation versus code changes; validates changed Markdown links and runs required regression gates only for code changes |
| Artifacts | `artifact-backend-images.yml` | successful relevant main CI, manual rebuild | Build and publish digest-addressed backend and schema-verifier images only when backend build inputs change; no deployment |
| Artifacts | `artifact-frontend-images.yml` | successful relevant main CI, manual rebuild | Build and publish a digest-addressed frontend image only when frontend build inputs change; no deployment |
| Release | `release-frontend.yml` | protected manual dispatch | Promote a validated frontend image with rollback checks |
| Release | `release-pydantic-runtime.yml` | protected manual dispatch | Validate, activate, or roll back the Pydantic runtime profile |
| Release | `release-pydantic-backend.yml` | protected manual dispatch | Promote a digest-pinned backend code image while preserving the active Pydantic Runtime profile after a read-only schema preflight and Gate M evidence bound to that source commit and `APPROVED_ALEMBIC_HEAD` |
| Release | `cd01-pr06b-schema-migration.yml` | protected manual dispatch | Gate M: run one approved production schema migration to the pinned Alembic head. It does not update the ECS service and does not run on push or pull request |
| Operations | `cloud-fixed-accounts-upsert.yml` | protected manual dispatch | Upsert the two fixed cloud accounts through an isolated ECS task |
| Operations | `cloud-demo-purge.yml` | protected manual dispatch | Remove obsolete demo data through an isolated ECS task |
| Operations | `operation-public-production-e2e.yml` | manual dispatch | Run browser acceptance against the public production URL |

## Frozen boundaries

- CI validates source; it does not publish or deploy.
- Pure documentation changes run the `Documentation — Relative Link Check` job. Required code jobs are intentionally skipped with a successful conclusion, preserving `main` branch protection without consuming application-test runners.
- Artifact workflows publish immutable-addressed build outputs; they do not update ECS, run migrations, or change data.
- Artifact workflows re-check changed build inputs before requesting the protected `Main` environment. `docs/`, `application_docs/`, `AI_enhanced/`, `AWS/`, Markdown, tests, and workflow-only changes never receive AWS credentials or push ECR images; a workflow-only rebuild is manual and controlled.
- Release workflows are separate because their approval, rollback, and evidence contracts differ. Do not combine them into one unrestricted shell job.
- Gate M stays `workflow_dispatch` only, on `main`, with the protected `Main` environment. Backend release evidence must be a successful Gate M run for the same source commit, schema-verifier digest, and `APPROVED_ALEMBIC_HEAD`. A historical run for a different commit or revision does not qualify.
- Data operations remain separate from release operations so an account repair or demo purge cannot inherit deployment permissions accidentally.
- All AWS mutation workflows must be dispatched from `main` and use the protected `Main` environment where specified.

## Consolidation policy

Do not add a new top-level workflow for a one-time setup phase. Prefer an existing release/operations entry point, a reviewed script invoked by it, or a dated archived runbook. A new workflow is justified only when it owns a distinct permission, approval, trigger, rollback, or evidence boundary.

The next safe optimization candidate is the artifact layer: backend and frontend builds can eventually share source-selection and provenance helpers. That work should preserve independent image failures and must add path-aware behavior before changing current automatic publication.
