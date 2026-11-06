# MERGE-PHASE1 — Merge record for PR #49 and PR #48

Status: point-in-time task record
Event date: 2026-11-05
Recorded from GitHub and git: 2026-11-06

This file preserves what was checked for task MERGE-PHASE1. It is not a current
operating contract and it is not production evidence.

Status levels, kept separate:

| Level | State on 2026-11-06 |
| --- | --- |
| Merged to `main` | Yes. `main` is `4d3bdceb4ff96faddb1dfc8d96f641f4a89553e1`. |
| Deployed to production | No production-release workflow ran for either merge SHA. Production was not read. |
| Independently verified in production | No. |

Times below are America/Chicago. On 2026-11-05 that zone is CST (UTC-6). UTC
values are the GitHub API timestamps. A git display such as
`2026-11-05T18:03:21-05:00` is the same instant as `2026-11-05T23:03:21Z`; the
`-05:00` offset is how the commit object is stored, not the Chicago clock.

## 1. Task as assigned

Recorded from the documentation task brief. These statements were not found on
the pull requests (see [UNVERIFIED](#8-unverified)).

- Task ID: MERGE-PHASE1, assigned by Cadensy_DD.
- Owner (Rinan) approved the scope in his own words. The brief summarizes that
  scope as: merge [#49](https://github.com/shnnzdx/Cadensy/pull/49), then
  [#48](https://github.com/shnnzdx/Cadensy/pull/48), with the repository default
  method; stop if a head SHA mismatches, any check is not green, or Update
  branch is required; no branch deletion, no settings changes, no deploy.
- After the first attempt to merge #48 stopped, the brief says the owner
  approved a further scope: click Update branch (merge commit, not rebase),
  wait until CI is fully green, QA confirms the new head differs from
  `c2029747` only by changes from `main`, then merge with the default method.
- Execution identity in the brief: GitHub account `shnnzdx`, through a browser
  session operated by the Docs agent.

`merged_by` on both pull requests is `shnnzdx`. That login is verified. The
browser-session operator is not visible in the API.

## 2. PR #49

[PR #49](https://github.com/shnnzdx/Cadensy/pull/49) — `fix(backend): map insufficient role to HTTP 403`.

| Item | Verified value | Source |
| --- | --- | --- |
| State | `MERGED` | Pull request API |
| Head branch | `task/pilot-fix-03-insufficient-role-403` | Pull request API |
| Head SHA | `be9f910019eefd815840025be03ff42c4de65f63` | Pull request API and `git rev-parse` |
| Base SHA still stored | `fc495064a650a78e63e43539e2c6481122d24052` | Pull request API |
| `merged_by` | `shnnzdx` | Pull request API |
| `merged_at` | `2026-11-05T23:03:21Z` (5:03:21 PM CST) | Pull request API |
| Merge commit | [`5096e0ab3a4b347628317aa2a26b8201f0520357`](https://github.com/shnnzdx/Cadensy/commit/5096e0ab3a4b347628317aa2a26b8201f0520357) | Pull request API and `git log` |
| Merge commit parent | `fc495064a650a78e63e43539e2c6481122d24052` only | `git log` |
| Method | Squash. One parent; subject is the branch commit title plus `(#49)`. | Commit shape. The repo API allows merge commits, squash, and rebase, so it does not name a single default method. |

The squash commit subject is `fix(api): map InsufficientRole to HTTP 403 in protected trip/organizer helpers (#49)`.
Its author is `cadensy-agent-bot[bot]`, the pull request author. The merger
recorded by GitHub is `shnnzdx`.

### Checks on the #49 head

The pull request `statusCheckRollup` lists 14 completed runs: 12 `SUCCESS` and
2 `SKIPPED`. Both skipped runs are named `Documentation — Relative Link Check`.
The 14 entries are two `CI Regression Gates` runs, not 12 distinct check names.
Each run has these six successes:

- `CI — Classify Documentation or Code Change`
- `Backend — Legacy Full Regression`
- `Pydantic AI — Isolated Fake Compatibility`
- `Backend — Production Pydantic Image Proof`
- `Backend — Migration Runner Image Proof`
- `Frontend — Regression and Builds`

The runs are [42086474176](https://github.com/shnnzdx/Cadensy/actions/runs/42086474176)
and [42087087661](https://github.com/shnnzdx/Cadensy/actions/runs/42087087661).
The later run's last success completed at `2026-11-05T18:46:10Z`, before
`merged_at`.

### Caveat copied from the PR body

The current body says: `Tests: 6/6 PASS; DB-backed tests NOT RUN. No merge or deployment authorized.`
This record did not re-run those tests. The "6/6" result is the PR body's
statement.

## 3. PR #48

[PR #48](https://github.com/shnnzdx/Cadensy/pull/48) — `fix(frontend): enforce safe internal return URLs`.

| Item | Verified value | Source |
| --- | --- | --- |
| State | `MERGED` | Pull request API |
| Head branch | `task/pilot-fix-01-safe-return-url` | Pull request API |
| Head SHA at merge | `a5a1999ba874bf69f267b8a88bef5f517d74b158` | Pull request API |
| Current base SHA | `5096e0ab3a4b347628317aa2a26b8201f0520357` | Pull request API |
| `merged_by` | `shnnzdx` | Pull request API |
| `merged_at` | `2026-11-05T23:34:42Z` (5:34:42 PM CST) | Pull request API |
| Merge commit | [`4d3bdceb4ff96faddb1dfc8d96f641f4a89553e1`](https://github.com/shnnzdx/Cadensy/commit/4d3bdceb4ff96faddb1dfc8d96f641f4a89553e1) | Pull request API and `git log` |
| Merge commit parent | `5096e0ab3a4b347628317aa2a26b8201f0520357` only | `git log` |
| Method | Squash. One parent; subject is the PR title plus `(#48)`. | Commit shape |

### First attempt

The only `merged` timeline event on #48 is at `2026-11-05T23:34:42Z`. There is
no earlier merge of this pull request.

Before the update commit, head `c2029747539096c1c32248a9ade2a912dde87065` does
not contain `5096e0ab`. After #49 landed, that branch was behind `main`. The
brief says GitHub then disabled Squash and merge because branch protection
requires an up-to-date branch, and that nothing was clicked. This API token
received HTTP 403 for `main` branch protection, so the protection rule and the
disabled button were not re-read. See [UNVERIFIED](#8-unverified).

### Update branch

Commit [`a5a1999ba874bf69f267b8a88bef5f517d74b158`](https://github.com/shnnzdx/Cadensy/commit/a5a1999ba874bf69f267b8a88bef5f517d74b158)
is a merge commit, not a rebase.

- Subject: `Merge branch 'main' into task/pilot-fix-01-safe-return-url`
- Time: `2026-11-05T23:14:34Z` (5:14:34 PM CST)
- Author on the commit: `Diiixxxn`, GitHub login `shnnzdx`
- Parents: `c2029747539096c1c32248a9ade2a912dde87065` and `5096e0ab3a4b347628317aa2a26b8201f0520357`

`git diff c2029747 a5a1999` is byte-identical to `git diff fc495064 5096e0ab`.
The new head differs from `c2029747` by the #49 patch only. That comparison was
made from git on 2026-11-06. It is separate from the QA report below.

### QA report

The task brief says Cadensy_QA reported PASS around 5:16 PM CST, with the
parents above, a diff identical to FIX-01, and no conflict edits, and that
Cadensy_DD relayed that report. No comment, review, or timeline event on #48
contains that report. The git comparison above agrees with the parent SHAs and
with "no extra patch." The 5:16 PM clock time was not found as a QA artifact.

The later post-update CI run finished its last successful job at
`2026-11-05T23:16:44Z` (5:16:44 PM CST). That is a CI completion time, not the
QA report.

### Checks before the #48 merge

The pull request `statusCheckRollup` again lists 12 `SUCCESS` and 2 `SKIPPED`,
from two `CI Regression Gates` runs, with the same check names as #49. Both
skipped checks are `Documentation — Relative Link Check`. The runs are
[42128106363](https://github.com/shnnzdx/Cadensy/actions/runs/42128106363) and
[42128110528](https://github.com/shnnzdx/Cadensy/actions/runs/42128110528).
The later run's last success completed at `2026-11-05T23:16:44Z`, before
`merged_at` `2026-11-05T23:34:42Z`. The head those checks belong to is
`a5a1999ba874bf69f267b8a88bef5f517d74b158`.

The current `mergeable_state` is `unknown` because the pull request is merged.
The pre-merge value `clean` was not re-read.

### Caveat copied from the PR body

The current body says: `Frontend tests: 162/162 PASS. Real browser/E2E: NOT RUN. No merge or deployment authorized.`
This record did not re-run those tests. The "162/162" result is the PR body's
statement.

## 4. Final `main`

Verified with `git log` on 2026-11-06:

```text
4d3bdceb4ff96faddb1dfc8d96f641f4a89553e1  fix(frontend): enforce safe internal return URLs (#48)
  parent 5096e0ab3a4b347628317aa2a26b8201f0520357  fix(api): map InsufficientRole to HTTP 403 ... (#49)
    parent fc495064a650a78e63e43539e2c6481122d24052  docs: refresh current handoff paths (#47)
```

`origin/main` at the time of this record is `4d3bdceb4ff96faddb1dfc8d96f641f4a89553e1`.

Head branches were still present:

| Branch | SHA |
| --- | --- |
| `task/pilot-fix-03-insufficient-role-403` | `be9f910019eefd815840025be03ff42c4de65f63` |
| `task/pilot-fix-01-safe-return-url` | `a5a1999ba874bf69f267b8a88bef5f517d74b158` |

A current read of repository merge settings shows `delete_branch_on_merge` is
false, and merge commits, squash, and rebase are all allowed. That is a
snapshot, not proof that settings were unchanged during the task. Branch
protection for `main` returned HTTP 403 to this token.

## 5. Production deployment was not run

No `Release — Production Backend`, `Release — Production Frontend`, or
`Release — Pydantic Runtime` workflow run is recorded for `5096e0ab` or
`4d3bdceb`. The newest listed runs of those workflows predate this merge and
use older SHAs. This record did not read ECS or the live site. Production
deployment and production verification are still pending.

Automatic artifact workflows did run after each push to `main`, because they
trigger on a successful `CI Regression Gates` push. Those workflows are not
production releases. GitHub Deployment objects for them use environment `Main`
with `production_environment: false`.

| SHA | Workflow | Run | Result |
| --- | --- | --- | --- |
| `5096e0ab` | Artifacts — Immutable Backend Images | [42126864692](https://github.com/shnnzdx/Cadensy/actions/runs/42126864692) | Success. The step `Push immutable artifacts and resolve digests` succeeded. Deployment [7587671736](https://github.com/shnnzdx/Cadensy/deployments/7587671736) ended `success` at `2026-11-05T23:39:03Z`. |
| `5096e0ab` | Artifacts — Immutable Frontend Image | [42126864813](https://github.com/shnnzdx/Cadensy/actions/runs/42126864813) | Success. The publish job was skipped. |
| `4d3bdceb` | Artifacts — Immutable Backend Images | [42130727057](https://github.com/shnnzdx/Cadensy/actions/runs/42130727057) | Success. The publish job was skipped. |
| `4d3bdceb` | Artifacts — Immutable Frontend Image | [42130727120](https://github.com/shnnzdx/Cadensy/actions/runs/42130727120) | Cancelled while building the image. The push step was skipped. Deployment [7588191958](https://github.com/shnnzdx/Cadensy/deployments/7588191958) ended `error` at `2026-11-05T23:39:18Z`. |

The task scope said nothing was deployed. That matches the absence of a
production-release run. It does not match a claim that no image was published:
the backend artifact workflow pushed images for `5096e0ab`. Whether those
images are running in production was not checked.

## 6. Anomalies

- Both pull request bodies still contain `No merge or deployment authorized`.
  That sentence is still in the bodies read on 2026-11-06. The brief says it
  was written before the owner's merge approval. No later body edit was found
  in comments or reviews; there are no issue comments and no reviews on either
  pull request.
- A skipped `Documentation — Relative Link Check` was part of the green set
  that preceded each squash. The skip is the recorded conclusion. This record
  does not add a reason beyond the check name.

## 7. Follow-ups

Still open:

- Production deployment of `4d3bdceb` and an independent production check.
- DB-backed tests for #49, which the PR body says were not run.
- Real-browser / E2E coverage for #48, which the PR body says was not run.

Docs to review, not rewritten here:

- [`navigation-known-wrong-behavior.md`](navigation-known-wrong-behavior.md),
  section "Login and Return Intent" (last verified against source 2026-10-28).
  It says the login page accepts a host-level `next` when it starts with `/`,
  then redirects to `window.location.href = nextPath` after that minimal check,
  so a slash-prefixed path can win. The file does not name
  `isSafeInternalReturnPath`. On `main` at `4d3bdceb`,
  [`frontend/app/login/page.tsx`](../frontend/app/login/page.tsx) and
  [`frontend/app/signup/page.tsx`](../frontend/app/signup/page.tsx) resolve
  `next` with `isSafeInternalReturnPath` from
  [`shared/trip-navigation-policy/invite-auth-return.js`](../shared/trip-navigation-policy/invite-auth-return.js)
  before `window.location.href` or `window.location.replace`. That function
  rejects more than a missing leading slash. The known-wrong section needs
  review against this merge. This pull request does not change it.

A search of `application_docs/` on 2026-11-06 found that return-URL wording
only in that file.

## 8. UNVERIFIED

These items are from the task brief or could not be re-read. They are not
asserted as GitHub facts.

| Item | Why it is unverified |
| --- | --- |
| Task ID `MERGE-PHASE1` and assignment by Cadensy_DD | Not present on either pull request or in this repository before this file. |
| Owner's verbatim approval, including the second approval to click Update branch | No issue comments and no reviews on #48 or #49. The scope above is the brief's summary, not a quoted GitHub comment. |
| Browser session operated by the Docs agent | `merged_by` is `shnnzdx`. The API does not record who operated the browser. |
| Pre-check `mergeable_state: clean` for both pull requests | Current API value is `unknown` because both pull requests are merged. |
| #48's base SHA was `fc495064` at the pre-check | Current base SHA is `5096e0ab`. Ancestry of `c2029747` is consistent with a base of `fc495064` before #49, and `5096e0ab` is not an ancestor of `c2029747`. The old base OID is not still stored on the pull request. |
| Squash and merge was disabled by an up-to-date branch-protection rule, and nothing was clicked | Branch protection returned HTTP 403. The timeline shows the merge only at `2026-11-05T23:34:42Z`, which is consistent with no earlier merge. |
| "Repository default method" as a single configured setting | Observed method is squash. The repo currently allows merge commit, squash, and rebase. |
| Cadensy_QA PASS around 5:16 PM CST, relayed by Cadensy_DD | Not found on the pull request. Git agrees with the parent SHAs and the patch identity. The clock time has no QA artifact. |
| Settings were not changed | Current settings were read. No before/after audit was available. Branch protection was not readable. |
| Unit and frontend test counts `6/6` and `162/162` | Stated in the pull request bodies. Not re-run for this record. |
| Live production is unchanged | Inferred only from the absence of production-release workflow runs for these SHAs. ECS and the live site were not read. The backend image push for `5096e0ab` did succeed. |
