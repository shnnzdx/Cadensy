# Cadensy PR-00.2 — First GitHub-hosted CI Verification

Status: **passed on GitHub-hosted runners.** This work did not modify branch
protection, merge any branch, dispatch a deployment workflow, access AWS/RDS,
or invoke DeepSeek.

## Validation branch and commits

| Item | Value |
| --- | --- |
| Validation branch | `ci/pr00-remote-validation` |
| Initial reviewed baseline commit | `116b525f9b83afea72f2a745aa791536efdbc585` |
| Minimal correction commit | `345c2e28f001e16699084c84bcf3f4c0ac746ffa` |
| Successful GitHub Actions run | [37234828654](https://github.com/shnnzdx/Cadensy/actions/runs/37234828654) |
| Successful run source SHA | `345c2e28f001e16699084c84bcf3f4c0ac746ffa` |
| Trigger | ordinary `push` to the validation branch |

The repository workflow catalog still labels the file as **Build Validation**
because that is the default-branch workflow display name. The executed run's
name was **CI Regression Gates** and its three run-time job names matched the
approved PR-00 design.

## Hosted job results

| Job | Result | Hosted evidence |
| --- | --- | --- |
| `Backend — Legacy Full Regression` | success, 1m 25s | Python 3.13.5; PostgreSQL 16 service; `474 passed in 32.73s`; JUnit validation succeeded; evaluation validation and upload succeeded. |
| `Pydantic AI — Isolated Fake Compatibility` | success, 55s | Python 3.13.5; separate PostgreSQL 16 service; the locked install printed `pydantic_ai_available`; selected PoC/evaluation tests reported `16 passed in 2.71s`. |
| `Frontend — Regression and Builds` | success, 48s | `node: v22.13.0`; separate Trip and frontend `npm ci`; Trip and frontend builds/sync passed; Node tests reported 13 passed, 0 failed, 0 skipped. |

The two backend services were separate disposable PostgreSQL 16 containers:
`cadensy_ci_legacy_test` and `cadensy_ci_pydantic_test`. Each job's
pre-test boundary assertion passed.

## Legacy JUnit, evaluation, and artifact evidence

The Legacy job ran:

```text
python -m pytest -q --ignore=tests/test_pydantic_ai_poc.py --junitxml="$LEGACY_JUNIT_XML"
```

It reported 474 passing tests. The next JUnit step succeeded while asserting a
nonempty collection and zero `<skipped>` elements. The Pydantic job
independently ran its real selected PoC tests rather than becoming a
dependency-missing skip.

The privacy/provenance validation step completed before upload. The uploaded
artifact was:

| Item | Value |
| --- | --- |
| Name | `legacy-evaluation-37234828654-1` |
| Artifact ID | `11315336911` |
| Size | 1,829 bytes (three report files) |
| Upload ZIP SHA-256 | `bc150d623bef8b3b5dfd77904d0f435a4ad6a782e98d314ffd9ef83cf6199e8f` |
| Retention | 14 days |

The downloaded artifact was parsed locally. It contained the approved V1 hash
`e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`,
`chat-change-preview-v1`, Legacy Fake Runtime, Python 3.13.5 lock
provenance, and `8 total / 8 passed / 0 failed / 0 safety violations`.

Its database evidence was:

```json
{
  "source": "TEST_DATABASE_URL",
  "backend": "postgresql",
  "host": "localhost",
  "database": "cadensy_ci_legacy_test",
  "runtime_database_url_used": false
}
```

It recorded an independent worker Session calling `get_current_plan`,
`get_trip_facts`, and `classify_change` against the synthetic fixture, with
the expected facts and `notice` classification. A post-download scan found
none of the defined private-text, `original_text`, DeepSeek-key, or bearer
authorization markers.

This verifies the positive upload path: validation completed and the artifact
uploaded afterwards. The negative path (suppression after an intentional
privacy/provenance failure) remains a PR-00.1 static-condition contract; no
hosted failure injection was run.

## First-run failure and correction

The first run, [37234697642](https://github.com/shnnzdx/Cadensy/actions/runs/37234697642),
failed only in `Frontend — Regression and Builds`. Its security regression
test found that three manually dispatched runtime workflow definitions still
contained `DEV_ALLOW_MEMBERSHIP_HEADER="1"`.

This was not a Node 22 or product-domain failure. The sole correction was the
already-reviewed `1 → 0` setting in:

- `.github/workflows/backend-ai-runtime-config.yml`
- `.github/workflows/phase7-backend-runtime-config.yml`
- `.github/workflows/phase10-https-custom-domain.yml`

Those workflows have only `workflow_dispatch` triggers. The ordinary Push did
not run them or access AWS; no AWS runbook/document was committed. The first
workflow concluded as a failure, proving the frontend test was not made a soft
failure. The second run passed all jobs.

## Remaining remote risks

- GitHub warns that some Actions use a deprecated Node 20 action runtime and
  are forced to Node 24. This does not change the application job runtime:
  the frontend job itself used Node 22.13.0. Track action-runtime upgrades
  separately.
- `ubuntu-latest` announced a future Ubuntu 26 transition; repeat the gate
  after that runner-image change.
- The successful artifact path is proven, but hosted upload suppression after
  an intentional validation failure has not been experimentally demonstrated.
- This is a validation branch only. Production deployment, real-provider
  compatibility, AWS/RDS operation, and branch protection remain out of scope.

## Required Checks activation readiness

**Ready for separately authorized configuration, not yet activated.** A real
`push` run produced all three checks at the approved source SHA. A later
authorization may configure these exact job names on the intended protected
branch:

- `Backend — Legacy Full Regression`
- `Pydantic AI — Isolated Fake Compatibility`
- `Frontend — Regression and Builds`

Before changing a Ruleset or Branch Protection, confirm the names shown in the
GitHub UI and verify enforcement with an intentionally failing non-production
branch. No such setting or test was performed here.

