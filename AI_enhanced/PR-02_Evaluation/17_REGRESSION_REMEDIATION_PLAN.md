# Cadensy — Regression Remediation Plan

Status: planned independently of PR-03. This document does not authorize an
authentication fallback, a change to Organizer rules, or an assertion removal.

## Baseline and objective

The local disposable-PostgreSQL baseline on 2026-10-04 was:

| Result | Count |
|---|---:|
| Passed at PR-02 handoff | 401 |
| Passed after PR-03 adds isolated tests | 413 |
| Failed | 60 |
| Restored insecure header fallback | 0 |

PR-04 integration and a full Legacy-versus-Pydantic A/B acceptance may begin
only after all 60 failures are either repaired with evidence or explicitly
superseded by an approved, behavior-preserving replacement contract. A manual
success of a selected suite is not a substitute for this gate.

## Workstream A — Retire raw Membership Header positives

### A1. Plan generation fixtures — 9 failures

Affected file: `backend/tests/test_plan_generation.py`.

Each API-positive test must create an account through the public auth seam,
obtain a valid account Bearer token, and send the requested `X-Trip-Id`.
Organizer/participant behavior assertions then run after authentication rather
than being pre-empted by `401`.

Acceptance criteria:

* all nine cases reach their intended planner behavior;
* no positive case sends a raw `X-Membership-Id` as authentication;
* a named negative security case proves a raw Membership Header is rejected;
* no `DEV_ALLOW_MEMBERSHIP_HEADER` behavior is restored.

### A2. Trip route fixtures — 49 failures

Affected file: `backend/tests/test_trips.py`.

Split migration by route family: account trip listing/creation; authenticated
trip reads; change/proposal/round routes; organizer-only routes; and Guest
read paths. Account paths use an account Bearer plus requested trip header.
Guest paths use only a real scoped Guest Bearer obtained through the invite/join
fixture; anonymous requests remain negative cases.

Acceptance criteria:

* every positive route uses the appropriate Bearer credential;
* every foreign-trip test authenticates first, then proves the intended `403`
  or `404` scope behavior instead of a generic `401`;
* fixture setup uses disposable PostgreSQL and unique synthetic accounts;
* no Guest bearer is fabricated from a membership ID.

## Workstream B — Organizer domain evidence — 2 failures

Affected cases:

* `test_both_exits_decline_to_decide[clear-Free time]`
* `test_the_organizer_can_never_adopt_the_proposal`

Before changing code or tests, produce a focused domain evidence record:

1. reproduce each case with the full proposal, vote, plan item, and decision
   transition trace;
2. map the observed transition to `docs/PRODUCT.md`'s equal-vote and deadlock
   rules;
3. decide whether the test expectation or the deterministic Organizer rule is
   inconsistent with that product rule;
4. add a narrow regression that states the approved rule in business language;
5. change only the rule or assertion proven wrong by that evidence.

Deleting the assertions, weakening them, or granting Organizers extra voting
authority is not an acceptable remediation.

## Execution order and CI gate

1. Complete A1 and its focused test command.
2. Complete A2 one route family at a time, keeping old headers only in explicit
   negative tests.
3. Complete the Workstream B evidence decision and the resulting narrow fix.
4. Run full `pytest -q` on an explicitly named disposable PostgreSQL database.
5. Require the full suite to pass before making it a PR-04 A/B acceptance gate.

Until the repository Required Checks are independently configured and verified,
these are local reproducible gates, not claimed CI enforcement.
