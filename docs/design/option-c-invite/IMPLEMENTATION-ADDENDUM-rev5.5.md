# Option C Invite Architecture — Implementation Addendum to rev5.5

Normative baseline: `DESIGN-INVITE-RETRIEVE-C-rev5.5.md` (sha256 c4f2cdeaae852493164dd870277cc8c3e76b4e14e68d71d98d52a1b7a0d871f3).
QA baseline: `QA-DESIGN-INVITE-C-rev5.5-c4f2cdea.md` (sha256 25db38e66e3466b7a781461c54e3327d69e46ca0ae4333a10af2868a72b63406).
Owner decision: 2026-11-23 18:48 CST, given directly by the Human Owner (DECIDED; not REPORTED). This addendum overrides rev5.5 where they differ.

## 1. Owner decisions — status DECIDED (direct)
- G-1 RESOLVED (YES). Invite closes early only when ALL hold:
  1. The trip has successfully created its first HMAC invite (first_invite_created_at IS NOT NULL).
  2. There are >= 2 current active participants, at least 1 of whom is not the Organizer.
  3. Every current active participant has a final approval bound to the CURRENT plan.itinerary_revision.
  - Inviters (as such), removed members and quarantined members are not current participants.
  - Members may approve before conditions are met; that approval never closes the invite by itself.
  - Re-evaluate the close condition after: successful approve, member removal, member quarantine.
  - Join only RECOGNIZES a closed invite (returns invite_closed); join never triggers closure.
  - Once final_plan_approved_at is set, invite closure is terminal. Later itinerary edits do NOT reopen the invite.
- 2026-11-23 pre-launch cleanup decisions DECIDED (direct): no 30-day legacy compat; legacy invites + test approvals are disposable; no 180-day backfill; first_invite_created_at / invite_window_ends_at start NULL for pre-launch trips; first successful HMAC invite creation sets the immutable 180-day window; replace = new invite id + token, trip id / first-created / final expiry unchanged; production revocation history and activation events preserved; cleanup is revoke-in-place (never DELETE invite rows, FK cascades to activation_event); Rev2 first apply keeps the final_plan_approval-empty assertion and aborts (no silent delete).

## 2. NEW: Final Plan Approval Readiness Guard (Owner-required)
Approve (`POST /api/trips/{trip_id}/final-plan/approve`) is allowed only when, evaluated from existing DB state inside the approve transaction (after the trip lock, per rev5.5 lock order):
- `plan.status = 'active'`;
- at least one valid PlanItem exists for the plan (exclude soft-deleted/removed/quarantined/non-itinerary rows per existing model semantics — document the exact predicate);
- no open DecisionRound for the trip/plan (status not settled/closed — use existing status values);
- no pending ChangeProposal for the trip/plan (use existing status values).
Otherwise return 409 with a distinct machine code `final_plan_not_ready` and a `reasons` array (e.g. `plan_not_active`, `no_plan_items`, `open_decision_round`, `pending_change_proposal`) plus a user-facing message. Do NOT redesign the decision engine; read-only predicates only. Readiness is also exposed on the final-plan status read so the UI can disable the button proactively. Tests for each reason, and for readiness flipping when a round settles / proposal resolves.

### UX (frontend)
- Final-plan panel shows: readiness state; per-participant approval status for the current itinerary_revision; the Approve button (disabled with the reason text when not ready; enabled otherwise).
- On approve success: show "You approved this itinerary"; if this approval closed the invite, show that the invite link is now closed.
- On 409 `itinerary_revision_stale`: refetch, show "The itinerary changed since you last looked — please review and approve again."
- On 409 `final_plan_not_ready`: show reasons in plain language.
- After an itinerary change (revision bump) before closure: prior approvals show as "approved an earlier version — needs re-approval"; members are prompted to review and re-approve.
- After closure (final_plan_approved_at set): panel shows "Final plan approved by all participants on <date>"; invite UI shows closed (no copy/replace); later edits do not reopen it (show a note that the invite remains closed).

## 3. QA LOW findings → mandatory implementation requirements
- N-5a: `save_mine` acquires trip FOR KEY SHARE at the very START of the function, before creating/adding the Preference (prevent autoflush writing preference/membership before the trip lock).
- M-a: Rev1 downgrade REFUSES by default when final_plan_approval has any rows (in addition to the hmac_v1 refusal); no silent data loss.
- M-b: Cleanup runbook (docs) states: single transaction; `SET LOCAL lock_timeout` / `statement_timeout` first; lock order trip -> invite_link -> plan; marker-row precondition check; any post-go-live re-run requires explicit Owner authorization at execution time.
- M-c: Marker table `cadensy_invite_cleanup_marker` is Alembic-managed: either a model with explicit handling or excluded from autogenerate (document which, add an autogenerate no-diff check). Cleanup/marker/downgrade tests run on an ALEMBIC-BUILT PostgreSQL schema (not create_all): Rev2 down->up with live data; step-7 abort leaves no marker; Rev1 downgrade drops marker; re-run preserves live HMAC links + approvals.
- S-1a: Any multi-PlanItem writer locks plan_item rows in deterministic id order; if it relies on a trip lock instead, that lock must be self-conflicting (FOR NO KEY UPDATE or FOR UPDATE), never FOR KEY SHARE.

## 4. Acceptance slices (one shared architecture; no extra approval rounds)
- Slice 1 — Invite Recovery: HMAC keyring + recoverable token, GET retrieve / POST ensure / POST replace (Idempotency-Key), 180-day lifecycle, unique active invite, non-HMAC non-joinable, status precedence, access-log redaction, body/header token APIs + frontend fallback, no-store, activation events, Rev1/Rev2 migrations + marker + cleanup.
- Slice 2 — Final Plan Approval: itinerary_revision + triggers (N-3, N-4 lock triggers, AFTER bump with ::jsonb, P-1), after_create parity, approve endpoint + readiness guard, early-close (G-1) re-evaluation on approve/remove/quarantine, N-5/N-5a, frontend UX above.
- Separate small PR — Release pipeline: restore Gate M workflow `cd01-pr06b-schema-migration.yml` from archive (PR #58 / 3c3ce0f6 moved it) and rebind the release evidence check in `release-pydantic-backend.yml` to the archived evidence chain (`release-runtime-off-backend.yml:256-302` pattern), with Owner gate preserved; update BUILD_ALEMBIC_HEAD pin to the new head. Code/PR only — never executed against production.

## 5. Phase B QA focus (real PostgreSQL evidence mandatory for 6–8 and lock/trigger/migration claims)
1 HMAC cross-device recovery; 2 concurrent ensure/replace never yields >1 active link; 3 replace keeps 180-day deadline; 4 180-day expiry + early all-approve close; 5 approval ↔ itinerary_revision consistency; 6 N-3/N-4/N-5 lock order + deadlock tests; 7 Alembic vs create_all trigger parity; 8 cleanup marker first-apply/re-run/downgrade; 9 invite token absent from access logs; 10 trip isolation, membership authorization, activation funnel regression.

## 6. Boundaries
Authorized: branches, implementation, local isolated PostgreSQL tests (synthetic data only), PRs, independent QA.
NOT authorized: merge, ECR publication, production migration, production cleanup, HMAC secret creation / ECS injection, Gate M production run, any AWS production change, deployment. Escalate only Critical architecture/security/data-integrity issues.
