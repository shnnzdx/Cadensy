# R5 Release Handoff

## Scope and acceptance

R5 completes the approved B.1–B.3 engineering scope:

- B.1 schema foundation;
- B.2A Trip-first transaction kernels;
- B.2B durable idempotency and versioned HMAC verification;
- B.2C protected Plan, decision, requirements and organizer writers;
- B.2D protected Generator prepare/validate/commit path;
- B.2E membership, Scheduler and writer-profile coverage; and
- B.3 revision-bracketed Preview/Apply.

The final code acceptance is recorded in [Report 65](../AI_enhanced/phase-0-audit-2026-10-03/65_PR05B_R5_REVIEW3_FINAL_SYSTEM_ACCEPTANCE.md). R5 is accepted only for its default-off, protected, non-production engineering scope.

**Production activation is not authorized.**

## Architecture summary

Protected interactive commands use the canonical order:

```text
Trip -> credential/authority -> membership -> Plan -> ledger -> scoped children
```

`PlanTransactionKernel` and `TripTransactionKernel` own fresh short-lived Sessions. Durable kernels own canonical HMAC verification, locked replay, revision movement and safe response persistence. Domain/orchestrator logic remains authoritative for classification and mutation.

B.3 Preview captures Plan and Trip facts revisions, uses fresh read-only Sessions for Runtime tools, classifies deterministically and verifies revisions again before returning an advisory response. Apply does not trust Preview metadata: it reauthorizes, validates the expected revision and runs the existing protected Change Proposal writer under locks.

Generator separates detached/provider work from final Trip-root validation and commit. Scheduler and Organizer writers use the same protected revision discipline where enabled.

## Main implementation areas

- `backend/app/domain/transactions/`: Plan/Trip kernels, durable ledger kernels and protected writers.
- `backend/app/domain/idempotency.py`: canonical payload and HMAC normalization.
- `backend/app/domain/preview_apply.py`: B.3 read-only revision-bracketed Preview.
- `backend/app/domain/plans/generator_{commit,durable,external_work,lifecycle}.py`: Generator lifecycle and durable commit.
- `backend/app/api/main.py`: default-off feature-gate routing and safe error adapters.
- `backend/app/domain/chat/service.py`, `backend/app/agents/legacy_runtime.py`: PR-04-compatible Runtime composition.
- `backend/alembic/versions/6f77e775d201_pr05b1_dormant_schema_foundation.py` and `c2a4b6d8e0f1_pr05b_phase1_trip_ledger_schema.py`: schema foundation.

## Feature gates

All protected writer gates and `CADENSY_B3_PROTECTED_PREVIEW_APPLY` are default-off. B.3 admission requires every B.2C/B.2D/B.2E required writer gate to be enabled in a recognized non-production environment. Missing, invalid, partial and production configurations fail closed.

Keep `DEV_ALLOW_MEMBERSHIP_HEADER=0`. A protected Apply also requires `CADENSY_B2C_HMAC_KEY`, `Idempotency-Key`, and `X-Expected-Plan-Revision`; a protected request never falls back to Legacy after selection.

## Reproducible non-production validation

Use local disposable PostgreSQL databases only. `TEST_DATABASE_URL` must be localhost, explicitly test-only and distinct from `DATABASE_URL`. Set:

```powershell
$env:DISABLE_SCHEDULER='1'
$env:MOCK_AI='1'
$env:GEOAPIFY_API_KEY=''
$env:DEEPSEEK_API_KEY=''
$env:DEV_ALLOW_MEMBERSHIP_HEADER='0'
```

Legacy environment:

```powershell
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-regression\Scripts\python.exe -m pytest -q `
  --ignore=tests/test_pydantic_ai_poc.py `
  --ignore=tests/test_pydantic_runtime.py `
  --ignore=tests/test_pydantic_composition.py `
  --ignore=tests/test_symmetric_evaluation_harness.py `
  --ignore=tests/test_formal_ab_runner.py `
  --ignore=tests/test_provider_smoke_harness.py `
  --ignore=tests/test_live_provider_smoke_adapter.py
```

Isolated Pydantic environment:

```powershell
C:\Users\zdxzh\Desktop\capstone\New\backend\.venv-pydantic-poc\Scripts\python.exe -m pytest -q `
  tests/test_pydantic_ai_poc.py tests/test_pydantic_composition.py `
  tests/test_pydantic_runtime.py tests/test_symmetric_evaluation_harness.py `
  tests/test_formal_ab_runner.py tests/test_runtime_composition.py `
  tests/test_provider_smoke_harness.py tests/test_live_provider_smoke_adapter.py `
  tests/test_evaluation_foundation.py
```

The final accepted evidence is 989 Legacy, 119 isolated Pydantic, 15 migration/schema, 505 focused PostgreSQL/security, and 13 frontend regression tests. See Reports 61-65 for exact artifacts and commands.

## Docker and CI

`.github/workflows/build-validation.yml` already runs:

- Legacy Full Regression;
- isolated Pydantic fake compatibility;
- `backend-production-pydantic-image-proof`, which builds `backend/Dockerfile` and verifies Pydantic imports with `--network none`; and
- Trip/frontend build and regression.

The Docker image proof was not run locally because Docker is unavailable on the review machine. It remains a required Hosted CI result for release artifact promotion; no new workflow is needed.

## Local smoke procedure

1. Start local PostgreSQL and point runtime to a local disposable development database.
2. Keep every protected gate off for Legacy compatibility smoke testing.
3. For protected non-production testing, set the complete writer profile plus B.3 gate and a local HMAC key; do not use production `APP_ENV`.
4. Verify Preview returns advisory data only, then Apply with `Idempotency-Key` and `X-Expected-Plan-Revision`.
5. Confirm a repeated identical Apply replays its safe response and a stale revision is rejected.

## Deployment prerequisites and rollback

Before any separate production-activation decision, provide:

1. HMAC provisioning/rotation and Ledger retention/cleanup procedure.
2. Migration compatibility preflight, backup and rollback plan.
3. Fleet-wide HTTP/Scheduler effective-gate consistency.
4. Hosted CI green results, including the Docker image proof.
5. Monitoring and incident handling for lock, stale revision and durable replay failures.
6. Explicit production activation authorization.

Rollback must disable protected gates rather than attempting to rewrite durable history. Do not remove ledger or revision records as a rollback mechanism.

## Known limitations

- No production activation or deployment authorization.
- Fleet-wide gate consistency is not proven by single-process local tests.
- No synthetic post-acknowledged-commit network-disconnect chaos test exists; pre-commit rollback and durable retry are covered.
- The local Docker image proof is CI-only evidence.
