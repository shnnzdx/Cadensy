# Archived GitHub Actions Workflows

Status: non-executable historical evidence
Last reviewed: 2026-10-29

These YAML files were moved out of `.github/workflows/` so GitHub no longer presents them as runnable Actions. Their contents are preserved for infrastructure history, audit reconstruction, and rollback context. Do not copy them back into the active workflow directory without a fresh architecture and security review.

## Completed setup workflows

| Workflow | Historical purpose | Last recorded use |
| --- | --- | --- |
| `completed-setup/main.yml` | Initial AWS identity and environment-secret proof | 2026-08-10 |
| `completed-setup/database-readiness.yml` | Disposable PostgreSQL readiness proof | 2026-08-10 |
| `completed-setup/frontend-hosting-proof.yml` | Initial frontend build/hosting proof | 2026-08-10 |
| `completed-setup/frontend-container-readiness.yml` | Initial frontend container proof | 2026-08-10 |
| `completed-setup/phase5-backend-provision.yml` | Initial backend/VPC/ECS/RDS infrastructure provisioning | 2026-08-24 |
| `completed-setup/phase6-runtime-provision.yml` | Initial runtime database and secret provisioning | 2026-08-10 |
| `completed-setup/runtime-secrets-readiness.yml` | Initial secret-hygiene readiness proof | 2026-08-10 |
| `completed-setup/phase7-backend-runtime-config.yml` | Initial frontend/backend runtime wiring | 2026-08-10 |
| `completed-setup/phase8-frontend-provision.yml` | Initial frontend ECS provisioning | 2026-08-25 |
| `completed-setup/phase10-https-custom-domain.yml` | Route 53-oriented HTTPS plan; production DNS was completed manually with Aliyun | Never dispatched |

## Superseded release workflows

| Workflow | Superseded by | Last recorded use |
| --- | --- | --- |
| `retired-release/r5-manual-ecr-build.yml` | `.github/workflows/cd01-build-publish.yml` | 2026-10-14 |
| `retired-release/cd01-release-b-ai-activation.yml` | `.github/workflows/cd03-pydantic-production-release.yml` | 2026-10-22 |

Historical static contract tests intentionally reference the two retired release files at their archive paths. This preserves evidence without exposing obsolete production actions in the GitHub Actions UI.
