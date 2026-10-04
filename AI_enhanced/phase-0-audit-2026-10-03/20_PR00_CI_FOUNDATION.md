# Cadensy PR-00 — GitHub Actions CI Foundation and Regression Gates

Status: **implemented locally; GitHub-hosted execution and Required Check
activation remain pending.** No workflow was dispatched, no branch protection
was changed, and no GitHub, AWS/RDS, deployment, real DeepSeek, Git push, or
merge operation was performed.

## Inspection findings

The prior `.github/workflows/build-validation.yml` had only a
`workflow_dispatch` trigger. It installed the mutable
`backend/requirements.txt`, combined runtime and test database URLs, emitted no
evaluation artifacts, and included a standalone container health check rather
than the approved regression partitions.

Repository inspection shows `main` is the only tracked primary branch
(`origin/main`); the remaining local `codex/*` branches are backup/development
branches, not a documented protected development lane. The replacement CI
therefore listens to every `push` and `pull_request`, plus
`workflow_dispatch`, rather than filtering out future A/B branches.

`frontend/` and `trip/` have distinct `package-lock.json` files and are kept as
separate installs. The Legacy and Pydantic PoC Python locks are also distinct:
the Legacy lock intentionally excludes `pydantic-ai-slim`, while the PoC lock
pins it.

## Workflow architecture

`build-validation.yml` is now **CI Regression Gates** with these independent,
parallel jobs. The workflow has only `contents: read` permission, no secret
references, no AWS action, no deployment action, and no repository write
permission.

| Job / Required Check name | Dependencies and scope | Result policy |
| --- | --- | --- |
| `Backend — Legacy Full Regression` | Python 3.13.5, `requirements-legacy-regression.lock.txt`, isolated PostgreSQL 16 service, `pytest -q -rs`, Fake Provider evaluation report. | Any failure fails the job. The one Pydantic PoC module skip is explicit and reported by pytest. |
| `Pydantic AI — Isolated Fake Compatibility` | Python 3.13.5, `requirements-pydantic-ai-poc.lock.txt`, separate PostgreSQL 16 service, `test_pydantic_ai_poc.py` plus `test_evaluation_foundation.py`. | Requires importing Pydantic AI; no real model/provider request is allowed. |
| `Frontend — Regression and Builds` | Node 22.13.0; independent `npm ci` in `trip/` and `frontend/`; Trip build, embedded-preview sync, frontend production build and existing Node tests. | Any installation, build, or test failure fails the job. |

The old generic backend-container-health job was removed from this quality gate
because it did not verify the approved regression baseline or an isolated
PostgreSQL test path. Container/deployment checks remain a separate concern and
are not silently treated as regression evidence.

## Database isolation and provider safety

Each backend job declares a different PostgreSQL service database:

| Job | `TEST_DATABASE_URL` database | `DATABASE_URL` database |
| --- | --- | --- |
| Legacy | `cadensy_ci_legacy_test` | `cadensy_ci_legacy_runtime` |
| Pydantic PoC | `cadensy_ci_pydantic_test` | `cadensy_ci_pydantic_runtime` |

Both test database names end in `_test`, use only `localhost`, and are checked
before pytest runs. This is compatible with the test harness, which has the
additional authority to drop/recreate only explicitly test-named databases and
then internally redirects the application session to `TEST_DATABASE_URL`.
The distinct initial runtime database names make an accidental environment
collapse visible instead of configuring both URLs to the same target.

Both backend jobs pin the following safety variables:

```text
DISABLE_SCHEDULER=1
MOCK_AI=1
GEOAPIFY_API_KEY=""
DEEPSEEK_API_KEY=""
DEV_ALLOW_MEMBERSHIP_HEADER=0
```

No DeepSeek credential, AWS credential, RDS URL, real Geoapify value, or raw
Membership Header compatibility is supplied by this workflow.

## Evaluation artifacts and retention

The Legacy job runs the frozen `chat-change-preview-v1` Golden Dataset after
the full regression and writes to a unique path containing GitHub run ID and
attempt. It never targets an archived baseline directory. It validates before
upload that:

- `manifest.json`, `summary.json`, and `cases.jsonl` all exist;
- the manifest contains dataset hash, Fake Runtime configuration, dependency
  lock name and SHA-256, grader version, and Git source commit;
- the summary is 8 total / 8 passed / 0 failures / 0 safety violations; and
- the artifact does not contain the synthetic private-text sentinel,
  `original_text`, a DeepSeek key marker, or an authorization bearer marker.

The artifact is uploaded only as `legacy-evaluation-<run>-<attempt>` and
retained for 14 days. The check is a concrete synthetic privacy safeguard, not
a claim that it is a universal PII detector.

`backend/evals/runner.py` now includes `dependency_configuration` in every
manifest: lock filename, SHA-256, and Python version. `backend/evals/README.md`
now directs local reports to a non-archival output directory and documents the
same provenance contract.

## Local verification results

| Verification | Result |
| --- | --- |
| Workflow YAML structure, triggers, three job IDs, read-only permission, lock references, and `_test` service names | passed through local YAML parser and assertions |
| Legacy isolated environment | Python 3.13.5; `pip check` clean; all 46 lock-pinned distributions matched exactly |
| Legacy full regression | **466 passed / 1 expected module skip** in 27.20s; skip reason: Pydantic PoC lock intentionally absent |
| Legacy evaluation report | 8 / 8 passed; 0 safety violations; manifest dependency lock SHA-256 verified; synthetic privacy checks passed |
| Pydantic isolated environment | Python 3.13.5; `pip check` clean; Pydantic AI import present; all 53 lock-pinned distributions matched exactly |
| Pydantic Fake compatibility + evaluation tests | **15 passed** in 2.02s |
| Trip lock installation and build | `npm ci` passed; `npm run build` passed |
| Frontend lock installation | `npm ci` passed |
| Preview sync, frontend production build, and Node regression suite | passed; **13 passed / 0 failed / 0 skipped** |

The local Legacy report is kept at
`backend/evals/reports/ci_local_pr00_legacy/`, separate from historical
baselines. Its manifest records dataset hash
`e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`,
Legacy Fake Runtime, `chat-golden-grader-v1`, source commit, Python 3.13.5, and
the Legacy lock hash.

## Expected runtime and dependency differences

The old local development `.venv` has already accumulated prior work and can
run the broader 477-pass baseline. CI deliberately does not use it:

| Environment | Purpose | Pydantic AI expectation |
| --- | --- | --- |
| Legacy CI lock | Stable Custom Runtime regression and evaluation | Not installed; exactly the isolated `test_pydantic_ai_poc.py` module skip is expected. |
| Pydantic CI lock | PR-03 Fake compatibility evidence | Installed; selected PoC tests must execute, not skip. |

The local Node runtime used during this review was 24.19.0, while CI pins the
repository-supported 22.13.0. GitHub-hosted execution on the pinned Node
version is therefore still required before treating the frontend gate as
remotely proven.

## Failure and skip policy

No job has `continue-on-error`, and no workflow code changes a failure into a
skip. The Legacy job uses `-rs` so its sole expected Pydantic module skip is
visible. The Pydantic job imports `pydantic_ai` before running tests, so a
missing PoC dependency cannot become a false green skip. Evaluation artifacts
are uploaded with `if: always()` for failure diagnosis, but their validation
step must pass before a successful job can report a valid baseline.

## Changed files

| Area | Files |
| --- | --- |
| CI workflow | `.github/workflows/build-validation.yml` |
| Evaluation provenance | `backend/evals/runner.py`, `backend/tests/test_evaluation_foundation.py`, `backend/evals/README.md` |
| Local non-archival evidence | `backend/evals/reports/ci_local_pr00_legacy/{manifest.json,summary.json,cases.jsonl}` |
| Required embedded-preview sync from the verified Trip build | `frontend/public/trip-app/*` generated output only; no frontend source logic changed. |
| Decision record | this document |

## Required Checks activation guide — remote authorization required

1. Push this workflow through the normal reviewed path; do not treat local YAML
   parsing as a GitHub Actions run.
2. Confirm a `push` or pull request run produces all three named checks and
   that the Legacy artifact can be downloaded and read.
3. In GitHub repository Rulesets/Branch Protection for `main`, select these
   exact successful check names: `Backend — Legacy Full Regression`, `Pydantic
   AI — Isolated Fake Compatibility`, and `Frontend — Regression and Builds`.
4. Require branches to be up to date if that is the repository's chosen merge
   policy, then test the rule with a deliberately failing non-production branch.
5. Record the GitHub run URLs and actual enforcement result in a separately
   authorized follow-up. `workflow_dispatch` success alone is not Required
   Check activation evidence.

## Remaining risks

- GitHub-hosted service startup, action-cache behavior, artifact upload, trigger
  execution, and branch-protection enforcement are pending remote verification.
- Python locks pin package versions but do not yet use package hashes; they give
  deterministic version selection, not a full artifact-integrity guarantee.
- Local `npm ci` reported pre-existing audit findings: Trip has 5
  vulnerabilities (3 moderate, 2 high); frontend has 31 (1 low, 6 moderate,
  23 high, 1 critical). No dependency upgrade or `npm audit fix` was performed
  in this scope. The frontend build also retains its existing large-chunk
  warning.
- The CI gate intentionally performs no real Provider compatibility check. Real
  DeepSeek, AWS/RDS, production migration, deployment, and branch-protection
  changes remain outside this authorization.

## PR-03R readiness verdict

**Not authorized to start.** PR-00 now provides a local, reproducible CI design
for the Legacy and isolated Pydantic baselines, but it is not remotely proven
until a GitHub-hosted run and separate Required Check activation authorization
complete. It does not authorize PR-03R or PR-04 work.
