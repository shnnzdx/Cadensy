# PR-00.4 — Main Branch Required Checks Activation

**Status:** completed; no pull request was merged.

## Read-only preflight

- Repository: `shnnzdx/Cadensy`; default branch: `main`.
- The authenticated repository permission included `admin`.
- Before this change, `main` was not branch-protected and the repository returned no Rulesets. There were therefore no existing protections to remove, replace, or weaken.

## Activated protection

`main` now has required status checks with `strict: true`; the three contexts are bound to the GitHub Actions application (`app_id: 15368`):

| Required context | Result source |
| --- | --- |
| `Backend — Legacy Full Regression` | Legacy isolated PostgreSQL regression job |
| `Pydantic AI — Isolated Fake Compatibility` | Isolated Fake compatibility job |
| `Frontend — Regression and Builds` | Trip/frontend build and regression job |

The protection also has `enforce_admins: true`, preventing an administrator from bypassing the required-check experiment. No pull-request-review requirement or branch restriction was added; force pushes and branch deletion remain disabled. `Build Validation` is only the workflow display name and is deliberately **not** a required context.

## Enforcement probe

- Validation PR: [#3](https://github.com/shnnzdx/Cadensy/pull/3)
- Source / target: `ci/pr00-required-checks-probe` -> `main`
- Deliberate-failure commit: `c8731648055b10e7b591bbf75d7322fd4f3f7182`
- Pull-request failure run: [37236705904](https://github.com/shnnzdx/Cadensy/actions/runs/37236705904)

The probe added only `backend/tests/test_ci_required_checks_probe.py`, whose single assertion deliberately failed. The run produced `474 passed, 1 failed`; the only failure was the labelled probe. The Backend required context failed, while the Pydantic and Frontend contexts passed. After changing the PR from Draft to ready for review, GitHub returned `mergeStateStatus: BLOCKED`. No merge was attempted.

## Recovery verification

- Probe-removal commit: `e83d3fd10765e8b06f546c00e9c6d79f83e24978`
- Pull-request recovery run: [37236852508](https://github.com/shnnzdx/Cadensy/actions/runs/37236852508)

The temporary test file was removed in the recovery commit. The recovery `pull_request` run succeeded for all three required contexts. GitHub then returned `mergeStateStatus: CLEAN` for PR #3. The duplicated successful contexts in the PR rollup are expected: the workflow intentionally triggers for both `push` and `pull_request`; they are two observations of the same three configured contexts, not six requirements.

## Result and limits

The required-check gate is active on `main` and has been verified to block a ready-for-review PR when one required job fails, then clear after that failure is reverted. Draft PR #2 was not modified or merged. No force push, branch-protection/ruleset bypass, AWS/RDS action, deployment, real DeepSeek request, or production operation was performed.

The temporary validation PR remains open and unmerged as an auditable record. Any future change to branch-protection scope (for example review requirements, merge queue, or direct-push restrictions) requires separate authorization.
