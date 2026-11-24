# Activation Funnel

Status: current contract
Last reviewed: 2026-11-18

This is the P0 measurement contract for trip creation, invites, joins, first
preferences, the first generated plan, settled group decisions, and notice
auto-apply. It is not a production analytics vendor and it does not send email.

## Storage

The activation-event revision is `c8e1d4a90b27` (revises `b7d3a1e9c642`).
The repository head after Option C is `c0b2f9e5a6d3`. That head is not the
production schema. Production migration, including Rev 2 revoke-in-place
cleanup, has not been run.

- Table `activation_event`: `id`, `event_name`, `trip_id`, `invite_id`,
  `membership_id`, `actor_kind` (`account`, `guest`, or `system`), `occurred_at`
  (server UTC), unique `dedup_key`, and JSON `metadata`.
- `preference.first_submitted_at` is set on the first save and is not
  overwritten. `submitted_at` remains the latest save. A legacy row that
  already has `submitted_at` keeps that timestamp as `first_submitted_at` on
  the next save and does not emit a new `preferences_submitted` event.
- Same-trip foreign keys reject an invite or membership from another trip.
  Deleting a trip, invite, or membership removes the activation rows that
  reference it. Product revoke does not delete those rows.
- Inserts use `ON CONFLICT (dedup_key) DO NOTHING` inside the business
  transaction. Rollback removes the event. A retry does not add a second row.

`metadata` is an object of allowlisted enum values. It must not contain tokens,
token hashes, emails, names, IP addresses, user agents, preference or budget
values, constraint text, or URLs. `invite_sent` is reserved and has no writer.

There is no persistent anonymous browser id and no tracking cookie.

## Event dictionary

| Event | Trigger | Dedup key | Metadata | Actor |
| --- | --- | --- | --- | --- |
| `trip_created` | `create_trip` commits a trip and organizer membership | `trip_created:{trip_id}` | none | account |
| `invite_created` | `create_invite` commits a new invite | `invite_created:{invite_id}` | none | account |
| `invite_link_copied` | Organizer-only `POST /api/trips/{trip_id}/activation-events` after a successful clipboard write | `invite_link_copied:{invite_id}:{membership_id}` | `channel=clipboard` | account |
| `invite_shared_wechat_template` | Same endpoint after the WeChat template is copied | `invite_shared_wechat_template:{invite_id}:{membership_id}` | `channel=wechat_template` | account |
| `invite_sent` | Reserved for a future email provider | none | none | not emitted |
| `invite_opened` | Successful public invite lookup. First open per invite, plus one row per authenticated account user per invite. Anonymous repeats add nothing | `invite_opened:{invite_id}` and `invite_opened:{invite_id}:account:{user_id}` | `auth_state` `anonymous`, `account`, or `guest` | account, guest, or system |
| `member_joined` | A new invite membership row is created. Idempotent account re-join does not emit | `member_joined:{membership_id}` | `join_method` `invite_guest` or `invite_account` | guest or account |
| `preferences_submitted` | First preference save for a membership | `preferences_submitted:{membership_id}` | none | account or guest |
| `preferences_updated` | The first later edit for that membership. Further edits do not emit | `preferences_updated:{membership_id}` | none | account or guest |
| `first_plan_created` | The first usable itinerary written by plan generation (`_write_items`, origin `ai_generate` or `rule_generate`) | `first_plan_created:{trip_id}` | `origin` `ai_generate` or `rule_generate` | system |
| `first_decision_completed` | The first counted group outcome below | `first_decision_completed:{trip_id}` | `outcome` | system |
| `notice_auto_applied` | The first notice direct-apply for the trip | `notice_auto_applied:{trip_id}` | `source=notice` | system |

Client share requests cannot supply metadata. A non-organizer receives 403. An
invite from another trip, or a revoked invite, receives 404 and no row.

## `first_decision_completed`

Counted only when a group decision reaches a final determined outcome.

1. **Settled decision round.** `settle_round` transitions `decision_round.status`
   from `open` to `closed`, sets `decision_round.settled_at`, and sets a
   non-empty `decision_round.winning_option_id`.
   - `winning_option_id = 'keep'` (`KEEP_CURRENT`) emits `outcome=round_rejected`
     only when the round has at least one vote. Ties and reopen rounds that
     miss the majority threshold keep the itinerary content and still count.
     A round that settles with zero votes is the auto-keep fallback. It still
     closes with `winning_option_id = 'keep'` and `settled_at` set, and it
     emits nothing. There is no separate event for that fallback. Calling
     `settle_round` again on a closed round does not emit again.
   - Any other winning option emits `outcome=round_applied`. The winning patch
     is applied, including a removal.
2. **Resolved deadlock.** `resolve_deadlock` moves `change_proposal.status`
   from `escalated` to `resolved_by_organizer` for `keep`, `split`, or `clear`
   (audit origins `deadlock_keep`, `deadlock_split`, `deadlock_remove`).
   Outcome is `deadlock_resolved`. `keep` leaves the itinerary unchanged and
   still counts.
3. **Confirm applied or declined.** `decide_proposal` is the group confirm
   mechanism: every affected member must accept, and any decline is final.
   When `change_proposal.status` becomes `applied`, the outcome is
   `confirm_applied`. When it becomes `declined`, the outcome is
   `confirm_declined`. A proposal whose affected set is only the proposer
   still reaches `applied` through that same unanimous rule, so it counts.
   The dedup key remains one row per trip.

Not counted:

- In progress: `decision_round.status = 'open'` and `settled_at` is null,
  including after `cast_vote` or `_do_round`.
- Undetermined: `settle_due_rounds` skips an open round whose
  `electorate_count` is null, so it stays open with no `settled_at`. A row
  already marked `closed` without `settled_at` and without
  `winning_option_id` is returned unchanged by `settle_round` and does not
  emit. `change_proposal.status = 'expired'` is not a determined round.
- Withdrawn: `change_proposal.status = 'withdrawn'`. `decision_round.status`
  is only `open` or `closed`. This state machine has no withdrawn, cancelled,
  or revoked round. Those proposal states do not emit.
- A settled round with zero votes. `settle_round` still auto-keeps the item.
  That path does not emit `first_decision_completed` and does not emit another
  event.
- Proposal creation (`waiting_affected_members`) and opening a round.
- Notice auto-apply (`_do_notice`). It emits `notice_auto_applied` once per
  trip and does not emit `first_decision_completed`. The event stores no title
  and no patch.
- Bookings (`set_booking_status_for_actor`, plan-change origin `booking`).
- A confirm proposal that is still `waiting_affected_members`, becomes
  `escalated` because of a schedule conflict, `expired`, or `withdrawn`.
  Those are not a determined applied or declined result.

## `first_plan_created`

Emitted once per trip, from `generator._write_items`, when the first usable
itinerary is written. The metadata origin is `ai_generate` or `rule_generate`.

Notice, booking, round, confirm, deadlock, and other ordinary `plan_change`
rows do not emit it, including when they happen before any generation. An
empty `plan` row created with the trip is not a generated itinerary. A second
generation for the same trip does not emit again.

## Metrics

`backend/analytics/activation_funnel.sql` reports per trip and overall.
`backend/analytics/run_activation_funnel.py` prints that query for `DATABASE_URL`.
The sample flag is `样本不足` when a rate denominator is below 20.
`invite_members_joined` uses `count` because that row has no denominator.

`invite_links_with_at_least_one_join_rate` is the share of opened invite links
that brought at least one member. It is not a person-level acceptance rate.
Reusable links and the absence of an anonymous visitor id mean one open is
not one person.

- Denominator: invites with a successful public open (`invite_opened` dedup key
  `invite_opened:{invite_id}`). That write happens only when the invite is not
  revoked and not expired at open time.
- Numerator: those invites with at least one `member_joined` row attributed to
  the same invite (`invite_guest` or `invite_account`).

`invite_members_joined` is the plain count of new invite memberships
(`invite_guest` and `invite_account`), per trip and overall. The numerator is
that count. Denominator and rate are null, and `sample_flag` is `count`. Two
joins on one link count as two. A join on an invite with no open row is
included. Organizer and creator joins are excluded. This is a membership
count, not a count of unique people.

`preference_completion_72h` is preference completion among joined members. Its
numerator and denominator are the plain counts:

- Denominator: `invite_guest` and `invite_account` joins at least 72 hours
  before the cutoff. Organizer/creator joins are excluded.
- Numerator: those memberships whose first `preferences_submitted` event is
  within 72 hours after joining.
- `invite_guest`, `invite_account`, and `combined` are reported separately.
- Duplicate guest memberships cannot be merged. Guest rates count memberships,
  not proven unique people.

## Invite reuse

Opening the invite page reads `GET /api/trips/{trip_id}/invite` and does not
create a token. The response has no raw token. The organizer's browser stores
the raw link under `cadensy.inviteLink.v1`, keyed by trip id and invite id,
and clears it on revoke and logout. `生成新链接` creates one invite, sets a
30-day `expires_at`, and revokes every previous unrevoked invite for that trip.
Invites that already have a null `expires_at` keep working until then.

Public lookup failures are distinct and contain no trip details:
`invite_invalid` (404), `invite_revoked` (410), and `invite_expired` (410).
The same live guest credential joining the same trip returns the existing
membership and does not emit another `member_joined`. A credential for a
different trip does not reuse that membership. A new join is still refused
with `membership_change_pending_decision` while a vote or confirmation is open.

Preference saves reject `ideal_budget` above `maximum_budget` with HTTP 400
and detail `ideal_budget_exceeds_maximum`. Members can edit preferences after
a plan exists. The plan is marked `needs_refresh` and settled decisions stay
as they were. The first form asks for dates and an optional budget. Pace,
interests, visibility, and private constraints stay behind “稍后补充”.

## Production rollout

The production schema migration completed on 2026-11-22 from
`b7d3a1e9c642` to `c8e1d4a90b27`. Backend and frontend promotion remain
separate owner-gated operations:

- `AWS/archive/workflows/retired-release/release-pr06b-schema-migration.yml` preserves the completed historical migration contract.
- `.github/workflows/release-pydantic-backend.yml` (`APPROVED_ALEMBIC_HEAD`) is the active Pydantic-preserving backend release gate.
- `.github/workflows/artifact-backend-images.yml` (`BUILD_ALEMBIC_HEAD`) publishes the immutable candidate images.

A production rollout still needs the existing backup and maintenance-window
evidence and a separate owner-gated execution, in this order:

1. Migrate the production database from `b7d3a1e9c642` to `c8e1d4a90b27`. Gate M recovery mode accepts start revision `b7d3a1e9c642` and upgrades only to `c8e1d4a90b27`. Release evidence requires `Running upgrade b7d3a1e9c642 -> c8e1d4a90b27`.
2. Deploy the backend that writes `activation_event` and
   `preference.first_submitted_at`.
3. Deploy the frontend that reports clipboard and WeChat-template copies.

Deploying the backend before the migration will fail those writes. The funnel
SQL is a manual read. It is not an admin endpoint and it does not add a
credential, email provider, or external service. Historical rows are not
backfilled into `activation_event`.
