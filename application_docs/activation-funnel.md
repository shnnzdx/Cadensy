# Activation Funnel

Status: current contract
Last reviewed: 2026-11-17

This is the P0 measurement contract for trip creation, invites, joins, first
preferences, the first plan change, settled group decisions, and notice
auto-apply. It is not a production analytics vendor and it does not send email.

## Storage

Alembic revision `c8e1d4a90b27` revises `b7d3a1e9c642`.

- Table `activation_event`: `id`, `event_name`, `trip_id`, `invite_id`,
  `membership_id`, `actor_kind` (`account`, `guest`, or `system`), `occurred_at`
  (server UTC), unique `dedup_key`, and JSON `metadata`.
- `preference.first_submitted_at` is set on the first save and is not
  overwritten. `submitted_at` remains the latest save. A legacy row that
  already has `submitted_at` keeps that timestamp as `first_submitted_at` on
  the next save and does not emit a new `preferences_submitted` event.
- Same-trip foreign keys reject an invite or membership from another trip.
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
| `first_plan_created` | The first `plan_change` written for the trip | `first_plan_created:{trip_id}` | `origin` when it is a known plan-change origin | system |
| `first_decision_completed` | The first counted group outcome below | `first_decision_completed:{trip_id}` | `outcome` | system |
| `notice_auto_applied` | The first notice direct-apply for the trip | `notice_auto_applied:{trip_id}` | `source=notice` | system |

Client share requests cannot supply metadata. A non-organizer receives 403. An
invite from another trip, or a revoked invite, receives 404 and no row.

## `first_decision_completed`

Counted only when a group decision reaches a final determined outcome.

1. **Settled decision round.** `settle_round` transitions `decision_round.status`
   from `open` to `closed`, sets `decision_round.settled_at`, and sets a
   non-empty `decision_round.winning_option_id`.
   - `winning_option_id = 'keep'` (`KEEP_CURRENT`) emits `outcome=round_rejected`.
     This is a settled rejection: ties, rounds with no votes, and reopen rounds
     that miss the majority threshold all keep the itinerary content and still
     count. Calling `settle_round` again on a closed round does not emit again.
   - Any other winning option emits `outcome=round_applied`. The winning patch
     is applied, including a removal.
2. **Resolved deadlock.** `resolve_deadlock` moves `change_proposal.status`
   from `escalated` to `resolved_by_organizer` for `keep`, `split`, or `clear`
   (audit origins `deadlock_keep`, `deadlock_split`, `deadlock_remove`).
   Outcome is `deadlock_resolved`. `keep` leaves the itinerary unchanged and
   still counts.

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
- Proposal creation (`waiting_affected_members`) and opening a round.
- Notice auto-apply (`_do_notice`). It emits `notice_auto_applied` once per
  trip and does not emit `first_decision_completed`. The event stores no title
  and no patch.
- Bookings (`set_booking_status_for_actor`, plan-change origin `booking`).
- Confirm path. `decide_proposal` reaching `applied` or `declined` is not a
  decision round and not a deadlock resolution, so it does not emit
  `first_decision_completed`. The existing confirm rules are unchanged.

## Metrics

`backend/analytics/activation_funnel.sql` reports per trip and overall.
`backend/analytics/run_activation_funnel.py` prints that query for `DATABASE_URL`.
The sample flag is `样本不足` when a denominator is below 20.

Invitation acceptance rate:

- Denominator: invites with a successful public open (`invite_opened` dedup key
  `invite_opened:{invite_id}`). That write happens only when the invite is not
  revoked and not expired at open time.
- Numerator: those invites with at least one `member_joined` row attributed to
  the same invite (`invite_guest` or `invite_account`).

72-hour preference completion rate:

- Denominator: `invite_guest` and `invite_account` joins at least 72 hours
  before the cutoff. Organizer/creator joins are excluded.
- Numerator: those memberships whose first `preferences_submitted` event is
  within 72 hours after joining.
- `invite_guest`, `invite_account`, and `combined` are reported separately.
- Duplicate guest memberships cannot be merged. Guest rates count memberships,
  not proven unique people.

## Production rollout

Do not treat this revision as applied in production. The production migration
and release workflows remain pinned to `b7d3a1e9c642`:

- `.github/workflows/cd01-pr06b-schema-migration.yml` (`PR06B_ALEMBIC_HEAD`)
- `.github/workflows/cd01-production-release.yml` (`APPROVED_ALEMBIC_HEAD`)
- `.github/workflows/cd01-build-publish.yml` (`BUILD_ALEMBIC_HEAD`)

A production rollout needs an owner-approved change to those pins, the
existing backup and maintenance-window evidence, and this order:

1. Migrate the production database from `b7d3a1e9c642` to `c8e1d4a90b27`.
2. Deploy the backend that writes `activation_event` and
   `preference.first_submitted_at`.
3. Deploy the frontend that reports clipboard and WeChat-template copies.

Deploying the backend before the migration will fail those writes. The funnel
SQL is a manual read. It is not an admin endpoint and it does not add a
credential, email provider, or external service. Historical rows are not
backfilled into `activation_event`.
