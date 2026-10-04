# Cadensy PR-00.3 — Pull Request CI Gate Verification

Status: **passed on a GitHub Draft Pull Request.** No merge, force push, change to
`main`, branch-protection change, AWS/RDS operation, deployment, or real
provider request was performed.

## Draft Pull Request

| Item | Value |
| --- | --- |
| Draft PR | [#2 — PR-00: reviewed safety baseline and CI regression gates](https://github.com/shnnzdx/Cadensy/pull/2) |
| Source branch | `ci/pr00-remote-validation` |
| Target branch | `main` |
| Source SHA tested by the PR event | `f1f777b435fe1152b81a4da8015b70f6b292e5bb` |
| PR event run | [37235981057](https://github.com/shnnzdx/Cadensy/actions/runs/37235981057) |
| Event | `pull_request` |
| PR state | Open Draft; `CLEAN`; `MERGEABLE` |

## Complete Diff review

The comparison against the fetched `origin/main` has 111 changed files,
9,387 insertions, and 639 deletions. The GitHub API's default PR file field
returned 100 entries, so the PR files endpoint was paginated explicitly. Its
complete 111 paths exactly matched local `origin/main...HEAD`; there were no
extra remote or missing local paths.

The scope matches the previously accepted sequence: Auth/Guest safety and
characterization, Alembic baseline, Agent lifecycle isolation, Evaluation
Foundation, isolated Pydantic AI PoC, Organizer deadlock alignment, CI
foundation/hardening, and their audit records. Expected generated/evidence
files are limited to:

- versioned local evaluation reports under `backend/evals/reports/`; and
- the derived embedded Trip preview under `frontend/public/trip-app/`, linked
  to the repository's `build:trip-preview` command.

`git diff --check` passed. No database file, private key, certificate,
token, real `.env`, `node_modules`, virtual environment, or `dist`
directory was in the PR. `backend/.env.example` is the only env-like path;
it contains the reviewed safe default
`DEV_ALLOW_MEMBERSHIP_HEADER=0`. The real `backend/.env` remains ignored.

A non-value-printing high-risk credential scan found only false-positive
`sk-` substrings within temporary `task-definition` filenames in existing
workflow commands; these are not keys. The locally modified AWS runbook files
remained uncommitted and are not part of the PR.

## Pull Request CI result

| Exact Job / check name | Result | PR-run evidence |
| --- | --- | --- |
| `Backend — Legacy Full Regression` | success | disposable PostgreSQL 16, Python 3.13.5, Legacy regression, JUnit zero-skip validation, evaluation provenance/privacy validation, and artifact upload all completed. |
| `Pydantic AI — Isolated Fake Compatibility` | success | isolated Python 3.13.5 and PostgreSQL 16 environment; locked Pydantic PoC installation and selected fake compatibility tests completed. |
| `Frontend — Regression and Builds` | success | Node 22.13.0, independent Trip/frontend dependency installs, preview sync, production build, and frontend regression completed. |

The workflow catalog display still says **Build Validation** because the default
branch has that workflow display label. The actual PR-run workflow name was
**CI Regression Gates**, and the three Job/check names above are the stable
names for gating.

## Status-check review

The PR rollup contains six successful entries: the three checks from the
current `pull_request` run and three historical successes from the prior
`push` run for the same SHA. This is expected because the workflow deliberately
listens to both events. There were no failed, cancelled, queued, neutral, or
unexpected extra checks after the PR run completed.

For future Branch Protection/Rulesets, configure only these exact Job check
names, after separately confirming their selector entries in the GitHub UI:

- `Backend — Legacy Full Regression`
- `Pydantic AI — Isolated Fake Compatibility`
- `Frontend — Regression and Builds`

Do not use the default-branch catalog display label `Build Validation` as a
substitute for the Job checks. The push/PR duplicate history should not be
treated as six independent requirements.

## Activation recommendation

**Ready for separately authorized Required Check activation.** Before applying
a Ruleset or Branch Protection configuration, use a non-production test branch
to verify that a deliberately failing PR blocks merge and that the GitHub UI
selects the three exact Job names above. That enforcement test and every
protection setting remain outside PR-00.3.

