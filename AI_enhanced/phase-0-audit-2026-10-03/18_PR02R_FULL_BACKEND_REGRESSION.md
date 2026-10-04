# Cadensy PR-02R — Full Backend Regression Remediation

Status: **authentication fixture remediation complete; full-suite gate blocked by one documented Organizer domain-policy conflict.**

All commands used only the explicit local disposable PostgreSQL database `cadensy_pr02r_test`, with scheduler disabled, mock AI enabled, and Geoapify blank. No AWS/RDS, production database, deployment, real DeepSeek request, Git push, or merge was performed.

> **Historical-policy note (superseded).** This report accurately records the
> unresolved policy at its completion. The later authorized PR-02R.1 decision
> approves `keep` as a neutral Confirm-deadlock resolution. Its implementation
> and final regression evidence are recorded in
> `19_PR02R_ORGANIZER_POLICY_ALIGNMENT.md`; the evidence below is retained
> unchanged as the preceding baseline.

## Before / after

| Verification target | Before | After | Result |
|---|---:|---:|---|
| Full backend suite, existing development environment | 413 passed / 60 failed | 472 passed / 1 failed | One product-rule decision remains; not made green artificially. |
| Legacy-only reproducible environment | Not available | 461 passed / 1 failed / 1 skipped PR-03 module | Same Organizer failure; the existing isolated PoC module skips correctly without its dependency. |
| Plan-generation HTTP suite | 9 Header-auth failures | 35 passed | Complete. |
| Trip HTTP suite | 49 Header-auth failures | 50 passed | Complete. |
| Organizer direct suite | 2 discrepancies | 14 passed / 1 failed | `clear` expectation corrected; `keep` remains a product-policy gate. |

The prior 60 failures were re-run rather than copied from the earlier report. The 58 Header-related failures reached their intended route behavior through legitimate credentials; the two original Organizer discrepancies were then assessed against fixture and transition evidence.

## Workstream A — authentication fixture migration

### Account paths

`tests/test_plan_generation.py` and `tests/test_trips.py` now establish every positive account request through public `/api/auth/login`. The helper sets a password only on a synthetic account in the disposable fixture, exchanges it for a bearer, and sends:

```text
Authorization: Bearer <synthetic account token>
X-Trip-Id: <fixture membership trip>
```

The migration covers account trip listing/creation, trip reads, plan changes, comments, booking, proposal and round routes, preferences and constraints, and organizer-only operations. Cross-trip negatives authenticate as a Trip A member and retain Trip A in `X-Trip-Id`, then request a Trip B resource. They now prove their intended `403` or privacy-preserving `404`, rather than a generic `401`.

`rg` confirms neither migrated file retains `X-Membership-Id`. No compatibility fallback or `DEV_ALLOW_MEMBERSHIP_HEADER` behavior was restored.

### Guest paths

Guest fixtures use the real synthetic flow: organizer account login, organizer invite creation, `/api/invites/{token}/join`, then the returned bounded `guest_token` plus its trip context. This removed fabricated anonymous `TripMembership` identities from positive Guest tests.

Valid Guest Bearers receive `401` on account-only `/api/trips` listing and creation. That is the intended account-authentication boundary, not an invalid Guest credential. The named test `tests/test_chat.py::test_chat_rejects_raw_membership_header_as_an_explicit_security_negative` remains and passed, proving a raw Membership Header alone is rejected before Agent work.

## Workstream B — Organizer domain evidence

### Fixture and transition trace

`full_trip` creates a booked `Birthday dinner` at 19:00. The proposal requests `start_hour: 20.0`, enters `waiting_affected_members`, and is escalated before the Organizer action.

| Case | Actual deterministic transition | Disposition |
|---|---|---|
| `clear` | `clear` normalizes to `remove`; title is retained historically, `settledness` becomes `removed`, proposal becomes `resolved_by_organizer`, and a `deadlock_remove` PlanChange has `{"remove": true}`. | Old `Free time` title expectation was stale. The test now verifies the genuine removal rather than a fictitious event. |
| `keep` | `app/domain/decisions/organizer.py` accepts `keep`, resolves the proposal, and creates `deadlock_keep` without applying the requested 20:00 change. | **Unresolved product-policy conflict; no code/test weakening authorized.** |

The `clear` correction follows the implementation's explicit legacy alias and its text that removes an activity rather than turning it into free time. It does not let the Organizer choose the blocked proposal.

The `keep` discrepancy cannot be silently normalized. `docs/PRODUCT.md` authorizes only `split` or `clear` after a confirm deadlock and forbids imposing one side's choice. `organizer.py`, however, documents and permits `keep`. The preserved failing `test_the_organizer_can_never_adopt_the_proposal` disallows `keep`, matching the product document. This is a release gate, not a skip, xfail, deleted assertion, or role-permission relaxation.

### Required independent decision

Choose one policy in a separately authorized change:

1. Preserve `docs/PRODUCT.md` as authoritative and remove the `keep` deadlock action from the domain/API; or
2. Amend product, UX/API contract, and test to approve `keep` as a neutral deadlock exit.

PR-02R did neither. It did not grant an Organizer a vote, apply the blocked proposal, or modify business source.

## Verification evidence

| Command / range | Result |
|---|---:|
| `pytest -q tests/test_plan_generation.py` | 35 passed |
| `pytest -q tests/test_trips.py` | 50 passed |
| `pytest -q tests/test_organizer.py` | 14 passed / 1 failed — preserved `keep` gate |
| `pytest -q` in `.venv-regression` | 461 passed / 1 failed / 1 existing PR-03 module skip |
| `pytest -q` in existing `.venv` | 472 passed / 1 failed |
| PR-01B security range: Auth, PR-01A characterization, PR-01B credentials, invites, named Header-negative | 33 passed |
| PR-01C lifecycle range | 11 passed |
| PR-02 evaluation contracts | 4 passed |
| PR-02 Legacy report runner | 8/8 Golden cases; 0 safety violations |
| PR-03 isolated Fake compatibility plus evaluation | 15 passed |

The preserved report at `backend/evals/reports/legacy_baseline_pr02r/` records dataset `chat-change-preview-v1`, SHA-256 `e2f631dbc2b26ef06f2d51a226ee1c780f50743b32cd3806ac4bfae85dc1c32f`, Fake Provider, independent worker-session visibility, and null token/cost fields.

## Dependency environment verification

The pre-existing `.venv` passed `pip check`, but it contains Pydantic AI components installed during PR-03 investigation. It was used only for complete collection verification and is not presented as a framework comparison.

New local-only `.venv-regression` uses Python 3.13.5 and `backend/requirements-legacy-regression.lock.txt`. The lock pins the Legacy closure, intentionally excludes `pydantic-ai-slim`, and passed `pip check`. It is the reproducible Legacy target. `.venv-pydantic-poc` remains untouched with its PR-03 lock and also passed `pip check`. No global installation occurred. `requirements.txt` remains range-based and is not claimed to be an exact lock.

## PR-03 integration follow-up tracking

Not implemented or reclassified here:

* per-provider-invocation remaining deadline;
* `PlanItemOutput.start_hour` nullability reconciliation;
* Pydantic exception mapping to PR-02 taxonomy;
* A/B source snapshot and diff hash;
* actual Pydantic instrumentation privacy under integration;
* real DeepSeek compatibility, only under separately approved bounded credentials and tests.

Also retained: PR-01B independent-session verification, corrupt GuestSession binding fail-closed test, `change_proposal.extended_at` drift, production AWS/deployment verification, and browser bearer XSS/shared-device hardening.

## CI and PR-04 readiness

These are reproducible local gates, not proof that Required Checks are active. PR-00 still needs independently verified pull-request/push triggers, disposable PostgreSQL CI service, required-check configuration, and artifact upload.

**PR-04 is not ready.** PR-03 remains an isolated Fake PoC, real DeepSeek compatibility is pending, and the full backend suite has the preserved product-policy failure. Do not start PR-03R or PR-04 until the Organizer decision is separately authorized and the full-suite gate is green.

## Changed files summary

| Area | Files |
|---|---|
| Plan generation bearer fixtures | `backend/tests/test_plan_generation.py` |
| Trip account/Guest bearer fixtures and cross-trip negatives | `backend/tests/test_trips.py` |
| Organizer removal expectation correction; preserved `keep` policy test | `backend/tests/test_organizer.py` |
| Reproducible Legacy dependency closure | `backend/requirements-legacy-regression.lock.txt` |
| Reproducible Legacy report | `backend/evals/reports/legacy_baseline_pr02r/manifest.json`, `summary.json`, `cases.jsonl` |
| Audit handoff | this document |

No production business source file was modified by PR-02R.
