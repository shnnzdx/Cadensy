# Cadensy PR-02R.1 — Organizer Deadlock Policy Alignment

Status: **locally implemented and verified against a disposable PostgreSQL
database.** This is not a deployment, production-data, AWS/RDS, DeepSeek, Git
push, or merge assertion.

All database commands used the explicit local test target
`cadensy_pr02r_test`, with `DISABLE_SCHEDULER=1`, `MOCK_AI=1`, and a blank
Geoapify key. The test fixtures validate the local test target before creating
or cleaning their synthetic rows.

## Product decision

The authoritative product decision is that an Organizer may resolve an
escalated Confirm deadlock using exactly these public actions:

1. `keep`
2. `split`
3. `clear`

`keep` rejects the pending proposal and preserves the live Current Plan item.
It does not apply the proposed patch, restore an older `before` snapshot, cast
a member decision, or give the Organizer a unilateral adoption power.

This replaces the prior PR-02R policy gate that treated `keep` as unresolved.
The prior report retains its historical evidence and now points to this record.

## Updated PRODUCT.md contract

`docs/PRODUCT.md` and its maintained class copy now define the three actions
and their distinct outcomes:

| Public action | Result | Proposal / audit outcome |
| --- | --- | --- |
| `keep` | Leaves the live PlanItem unchanged; rejects the pending patch. | `resolved_by_organizer`; `deadlock_keep` with `{}`. |
| `split` | Replaces the disputed block with the existing split-group resolution. | `resolved_by_organizer`; `deadlock_split`. |
| `clear` | Removes the disputed activity through the existing removal resolution. | `resolved_by_organizer`; historical audit origin `deadlock_remove` with `{"remove": true}`. |

`deadlock_remove` is retained only as an append-only historical audit origin.
It is not a fourth public action. Direct domain callers receive `NothingToDo`
for `remove`; HTTP payload validation rejects it with `422`.

The product and proposal documents explicitly state that `keep` is a neutral
rejection, not an extra Organizer vote or an adoption of the blocked proposal.
The product contract also states that `keep` preserves existing required,
settled, and booked state rather than bypassing those guards.

## Domain implementation review

### Decision authority and state transition

`backend/app/domain/decisions/organizer.py::resolve_deadlock` remains the
single resolution seam. It requires an Organizer and an `escalated` proposal
before performing any action. It does not invoke
`orchestrator.decide_proposal`, so it cannot use the pending proposal patch to
adopt the proposed change.

For `keep`, the implementation:

- leaves every PlanItem field untouched;
- changes the proposal only to `resolved_by_organizer`;
- appends one `PlanChange(origin="deadlock_keep", patch={})` and one neutral
  UpdateNotice; and
- rejects a repeated resolution before an additional audit record can be
  appended.

The implementation operates on the live PlanItem and never copies the proposal
snapshot back to it. The stale-state characterization therefore proves the
limited, safe property available in this scope: a later valid live update is
not overwritten by `keep`. Existing API trip-scoping and authorization are
unchanged.

### API and frontend contract

- `DeadlockRequest` accepts only `keep`, `split`, and `clear`.
- The route still scopes the proposal to the authenticated caller's trip before
  calling the domain action, then applies the existing Organizer-only check.
- `trip/src/final/FinalApp.jsx` presents **Keep current**, **Split group**, and
  **Clear activity** and sends `clear`, never the old internal `remove` value.
- `TripAppState` remains a thin request caller; no session, navigation, or
  decision authority was moved into the UI.

### Constraint and decision boundary

`keep` touches neither the proposed patch nor the item settledness. The focused
test uses a booked item and verifies that its booked state and timestamp are
preserved. The existing constraint engine and normal confirmation path retain
their decision authority; this PR adds no Organizer voting capability and no
model/Agent authority.

## Test changes and reasoning

The test seam was fixed before implementation at the existing deterministic
`resolve_deadlock(...)` domain function and the public
`POST /api/proposals/{proposal_id}/deadlock` contract.

The initial characterization intentionally failed in two places: a direct
`remove` action was accepted, and the HTTP payload received `200`. After the
public-contract correction, the focused suite passed.

New or adjusted assertions prove that:

1. `keep` is allowed and resolves the pending proposal.
2. The proposed 20:00 patch does not change the original 19:00 booked item.
3. No PlanItem fields, settledness, timestamp, or ProposalDecision records are
   changed by `keep`.
4. The correct `deadlock_keep` append-only audit record and neutral notice are
   created.
5. A second `keep` call raises `NothingToDo` and produces no second change or
   notice.
6. A simulated later valid live PlanItem update is preserved instead of being
   overwritten by the pending proposal snapshot.
7. `apply`, `accept`, and a raw proposed value remain invalid Organizer actions;
   the retained `test_the_organizer_can_never_adopt_the_proposal` continues to
   protect against unilateral adoption.
8. The legacy internal `remove` payload is rejected at both domain and HTTP
   boundaries.

No assertion was deleted, skipped, or converted to XFail to obtain the green
baseline.

## Regression results

| Verification | Result |
| --- | ---: |
| Focused Organizer domain + deadlock route | 20 passed |
| Decision / constraint regression (`test_paths.py`) | 38 passed |
| Plan generation regression (`test_plan_generation.py`) | 35 passed |
| Trips API regression (`test_trips.py`) | 50 passed |
| PR-01B authentication security range | 39 passed |
| PR-01C lifecycle failure-injection range | 25 passed |
| PR-02 evaluation contracts | 4 passed |
| PR-02 Fake Provider evaluation runner | 8 / 8 cases; 0 safety violations |
| Full backend pytest | **477 passed in 29.91s** |
| Trip frontend production build | passed; 55 modules transformed |

The regenerated evaluation evidence is in
`backend/evals/reports/legacy_baseline_pr02r1/` (`manifest.json`,
`summary.json`, and `cases.jsonl`). It uses the existing Fake Provider and
synthetic fixtures; it makes no real provider request.

## Documentation alignment

Updated documents are:

- `docs/PRODUCT.md` and `docs/class/PRODUCT.md` — authoritative policy and
  maintained class copy;
- `docs/PROPOSAL_EN.md` and `docs/class/PROPOSAL_EN.md` — decision-ownership
  language;
- `docs/backend/yuming/BACKEND_HANDOFF_2026-08-21.md` — public action list and
  historical audit-origin clarification; and
- `18_PR02R_FULL_BACKEND_REGRESSION.md` — a clearly labelled historical-policy
  supersession note, without erasing its original evidence.

## Changed files summary

| Area | Files |
| --- | --- |
| Product and supporting docs | The six documentation files listed above; this report. |
| Public API / domain contract | `backend/app/api/main.py`; `backend/app/domain/decisions/organizer.py`. |
| Regression coverage | `backend/tests/test_organizer.py`; `backend/tests/test_trips.py`. |
| Organizer UI action value and copy | `trip/src/final/FinalApp.jsx`. |
| Reproducible evaluation evidence | `backend/evals/reports/legacy_baseline_pr02r1/*`. |

No schema, migration, authentication behavior, Agent runtime, Pydantic AI
dependency, production configuration, or external service was changed by this
policy-alignment work.

## Remaining risks

- This PR proves that `keep` does not restore or overwrite a stale snapshot; it
  does not add an optimistic-concurrency/revision protocol to PlanItem. Any
  broader concurrent-edit policy remains governed by the existing state and
  authorization contract.
- `clear` retains its pre-existing removal behavior and historical audit
  origin; this PR did not reinterpret it as a generic free-time rewrite.
- The separately tracked PR-01B independent-session persistence and corrupt
  GuestSession fail-closed tests, browser bearer XSS/shared-device hardening,
  and `change_proposal.extended_at` drift remain outside scope.
- Real DeepSeek compatibility, deployment/AWS verification, and Pydantic AI
  runtime integration remain unperformed and unauthorized here.

## PR-04 readiness verdict

**Not authorized to start.** This policy alignment removes the former
Organizer-deadlock regression gate and is compatible with a future A/B
integration because it changes neither the Custom Runtime nor deterministic
decision authority. It is not a PR-04 approval: the separate integration
authorization and its retained PR-03/provider and production gates are still
required.

## Final recommendation

Accept PR-02R.1 as the local Organizer Deadlock Policy Alignment: `keep`,
`split`, and `clear` now have one consistent product, frontend, API, domain,
audit, and regression contract. Stop at this completed scope and await a new,
explicit authorization before beginning PR-03R or PR-04.
