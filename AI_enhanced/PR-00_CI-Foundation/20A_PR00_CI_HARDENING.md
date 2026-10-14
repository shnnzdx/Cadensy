# Cadensy PR-00.1 — CI Evidence & Safety Hardening

Status: **implemented and locally verified.** This supplement supersedes the
artifact-upload and Legacy-skip statements in `20_PR00_CI_FOUNDATION.md`.
No GitHub Actions workflow was dispatched; no branch protection, AWS/RDS,
DeepSeek, deployment, Git push, or merge operation was performed.

## Modified workflow sections

The existing three independent jobs are retained without new dependencies:

| Job | PR-00.1 change |
| --- | --- |
| `Backend — Legacy Full Regression` | Explicitly ignores only `tests/test_pydantic_ai_poc.py`, writes JUnit XML, rejects every collected skip, verifies the frozen dataset hash and standalone-runner isolation evidence, and conditionally uploads the evaluation artifact. |
| `Pydantic AI — Isolated Fake Compatibility` | Unchanged execution scope; its existing lock install/import check and actual PoC test invocation remain the only place that runs the PoC module. |
| `Frontend — Regression and Builds` | Unchanged. It remains a distinct Node job with independent `trip/` and `frontend/` installs. |

The Legacy job now has `LEGACY_JUNIT_XML=test-results/legacy-junit.xml` and
uses:

```text
python -m pytest -q --ignore=tests/test_pydantic_ai_poc.py --junitxml="$LEGACY_JUNIT_XML"
```

It then parses the report and fails unless it contains at least one collected
test and **zero** `<skipped>` elements. The Pydantic module is therefore an
explicitly uncollected cross-environment exclusion, not an allowed Legacy
skip. Any other collected skip is a CI failure.

## Artifact upload condition

The privacy/provenance step now has the stable ID
`validate_evaluation_artifact`. The upload step has exactly:

```yaml
if: ${{ steps.validate_evaluation_artifact.outcome == 'success' }}
```

`if: always()` is no longer used for the Legacy evaluation artifact. A partial
or unvalidated report is intentionally not uploaded. The validation itself
requires all three report files, approved provenance, the expected summary,
the isolation evidence below, and absence of the defined synthetic sensitive
markers. This is a targeted synthetic privacy gate, not a claim to detect all
possible PII.

## Frozen V1 dataset integrity

The workflow calculates SHA-256 from the checked-in file
`backend/evals/datasets/chat_change_preview_v1.json` and requires this three
way equality:

```text
computed dataset file SHA-256
  = manifest.dataset_hash
  = approved V1 hash
  = e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f
```

The file was not changed in this PR. Any future dataset content change must be
introduced as an explicit version bump with a separately approved hash; it
cannot be made green by changing the workflow constant.

## Standalone evaluation-runner database isolation evidence

`python -m evals.runner` now records non-secret `database_isolation` metadata
in its manifest. Its CLI requires `TEST_DATABASE_URL`, performs the existing
local PostgreSQL / test-name guard, creates its engine from that value, and
does not bind to `DATABASE_URL` or an inherited pytest Session. The manifest
records the source, PostgreSQL backend, localhost host, disposable database
name, and `runtime_database_url_used: false`.

The Legacy CI validation requires the following CI-specific evidence:

```json
{
  "source": "TEST_DATABASE_URL",
  "backend": "postgresql",
  "host": "localhost",
  "database": "cadensy_ci_legacy_test",
  "runtime_database_url_used": false
}
```

It also requires `fixture_visibility` evidence of a real independent worker
Session calling `get_current_plan`, `get_trip_facts`, and `classify_change`
against the synthetic trip and obtaining the known synthetic facts and
deterministic `notice` classification.

The local subprocess characterization test deliberately supplied a reachable,
disposable `TEST_DATABASE_URL` and an unreachable `DATABASE_URL` host. The
runner completed successfully, emitted the expected isolation/fixture
evidence, and a committed unrelated sentinel `User` row survived. The worker
did not inherit pytest's connection; only runner-owned synthetic rows are
removed by the fixture cleanup. This proves the stated local invariant, not a
universal guarantee about any future tool implementation.

## Local verification results

| Check | Result |
| --- | --- |
| Frozen dataset file SHA-256 and manifest comparison | passed; matched the approved V1 hash above |
| Standalone Runner with unreachable `DATABASE_URL` | passed; used only disposable `TEST_DATABASE_URL`; synthetic worker read evidence present; unrelated sentinel row preserved |
| CI workflow static contracts plus evaluation foundation tests | **11 passed** in 1.83s |
| Legacy lock and full regression command with explicit PoC exclusion | **474 passed / 0 skipped** in 28.52s; JUnit contained 474 test cases and zero skip elements |
| Legacy standalone evaluation report and privacy/provenance checks | **8 / 8 passed**, 0 failures, 0 safety violations; local temporary output only |
| Legacy / PoC lock environment | Python **3.13.5** in both environments; `pip check` clean in both |
| Pydantic isolated install plus PoC/evaluation tests | `pydantic_ai` import succeeded; **16 passed** in 2.94s |
| Workflow static structure | YAML parsed; all three jobs have no `needs` or `continue-on-error`; Python 3.13.5, Node 22.13.0, PostgreSQL 16 services, local URLs, stable step ID, and exact upload expression are contract-tested |

The static test covers the repository-owned YAML and the literal GitHub Actions
expression. GitHub's server-side expression parsing, service startup, cache
behavior, artifact transfer, trigger delivery, and Required Check enforcement
remain remote verification items.

## Changed files

| File | Change |
| --- | --- |
| `.github/workflows/build-validation.yml` | Artifact gate, frozen-hash and isolation validation, explicit Legacy PoC exclusion, and JUnit zero-skip contract. |
| `backend/evals/runner.py` | Emits non-secret standalone database-binding evidence in the generated manifest. |
| `backend/tests/test_evaluation_foundation.py` | Adds subprocess-level Runner binding, independent worker visibility, and synthetic-only cleanup characterization. |
| `backend/tests/test_ci_workflow_contract.py` | Adds static workflow, dataset, upload-expression, runtime/service, failure-propagation, and PoC-execution contracts. |
| `AI_enhanced/phase-0-audit-2026-10-03/20A_PR00_CI_HARDENING.md` | This correction and evidence record. |

## Runtime compatibility and dependency-security backlog

The local frontend commands previously ran on Node **24.19.0** with npm
11.17.0. CI pins Node **22.13.0**; `frontend/package.json` declares
`>=22.13.0`, while `trip/package.json` has no equivalent engine declaration
and its lockfile is version 3. The workflow's pinned Node setting and lock
paths were statically verified, but a GitHub-hosted Node 22.13.0 run is still
required for actual compatibility evidence.

Existing `npm audit` findings remain a separate dependency-security backlog:
Trip reported 5 findings (3 moderate, 2 high) and frontend reported 31 (1 low,
6 moderate, 23 high, 1 critical) in the prior local review. No automatic
upgrade or `npm audit fix` was run in this scope.

## Remaining GitHub-hosted verification items

1. Obtain separate authorization to push and observe a `pull_request` or
   `push` run. Confirm all three named jobs, PostgreSQL 16 services, Python
   3.13.5 lock installation, Node 22.13.0 frontend execution, JUnit zero-skip
   check, artifact condition, and artifact contents.
2. Confirm that a deliberate evaluation provenance/privacy failure suppresses
   the Legacy artifact upload rather than uploading a partial report.
3. Only after a successful observed run, separately authorize and configure
   branch-protection/Ruleset Required Checks. A local check or a manual
   dispatch is not proof of enforcement.

## PR-03R readiness verdict

**Not authorized.** PR-00.1 strengthens the local CI evidence chain but does
not substitute for the first GitHub-hosted run or Required Check activation,
and it does not authorize PR-03R or PR-04.
