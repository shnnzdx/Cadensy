"""The three organizer actions, and the line none of them may cross."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.db.models import (
    ChangeProposal,
    DecisionRound,
    PlanChange,
    ProposalDecision,
    UpdateNotice,
)
from app.domain.decisions import orchestrator as orch
from app.domain.decisions import organizer as org
from app.domain.constraints.types import Settledness


def _stuck_proposal(db, full_trip):
    outcome = orch.propose_change(
        db, full_trip["dinner"], {"start_hour": 20.0}, full_trip["me"].id
    )
    return db.get(ChangeProposal, outcome.proposal_id)


def _open_round(db, full_trip):
    full_trip["art"].settledness = Settledness.TOUCHED.value
    db.flush()
    outcome = orch.propose_change(db, full_trip["art"], {"title": "Shopping"}, full_trip["me"].id)
    return db.get(DecisionRound, outcome.round_id)


# ————————————————————— Only the organizer —————————————————————


@pytest.mark.parametrize("action", ["remind", "extend", "deadlock"])
def test_a_participant_cannot_do_organizer_things(db, full_trip, action):
    participant = full_trip["members"][1]
    if action == "remind":
        with pytest.raises(org.OrganizerOnly):
            org.remind(db, participant, full_trip["members"][2].id)
    elif action == "extend":
        round_ = _open_round(db, full_trip)
        with pytest.raises(org.OrganizerOnly):
            org.extend_round(db, participant, round_.id)
    else:
        proposal = _stuck_proposal(db, full_trip)
        org.escalate(db, participant, proposal.id)
        with pytest.raises(org.OrganizerOnly):
            org.resolve_deadlock(db, participant, proposal.id, "split")


# ————————————————————— Reminders are private —————————————————————


def test_a_reminder_reaches_only_the_person_it_is_for(db, full_trip):
    """Naming someone in front of the group turns silence into pressure."""
    target = full_trip["members"][2]
    notice = org.remind(db, full_trip["me"], target.id)

    assert notice.recipient_membership_id == target.id
    # and never carries who sent it
    assert not hasattr(notice, "actor_membership_id")


def test_you_cannot_nag(db, full_trip):
    target = full_trip["members"][2]
    org.remind(db, full_trip["me"], target.id)

    with pytest.raises(org.TooSoonToRemind):
        org.remind(db, full_trip["me"], target.id)


def test_nobody_reminds_someone_who_already_answered(db, full_trip):
    target = full_trip["members"][2]
    target.status = "preferences_submitted"
    db.flush()

    with pytest.raises(org.NothingToDo):
        org.remind(db, full_trip["me"], target.id)


# ————————————————————— Extending a round —————————————————————


def test_extending_buys_time_and_tells_everyone(db, full_trip):
    round_ = _open_round(db, full_trip)
    before = round_.deadline

    org.extend_round(db, full_trip["me"], round_.id)

    assert round_.deadline > before
    notice = db.query(UpdateNotice).filter_by(kind="round").order_by(
        UpdateNotice.created_at.desc()
    ).first()
    assert "extended" in notice.body


def test_a_round_can_only_be_extended_once(db, full_trip):
    """More time is fine. Never settling is not."""
    round_ = _open_round(db, full_trip)
    org.extend_round(db, full_trip["me"], round_.id)

    with pytest.raises(org.AlreadyExtended):
        org.extend_round(db, full_trip["me"], round_.id)


def test_extending_does_not_change_what_silence_means(db, full_trip):
    round_ = _open_round(db, full_trip)
    org.extend_round(db, full_trip["me"], round_.id)
    orch.cast_vote(db, round_, full_trip["members"][1].id, "requested")
    round_.deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()

    orch.settle_due_rounds(db)
    # one vote out of six still settles on that vote; the other five are
    # no preference, never agreement
    assert round_.winning_option_id == "requested"


# ————————————————————— The deadlock exit —————————————————————


def test_any_affected_member_can_escalate_not_just_the_proposer(db, full_trip):
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["members"][3], proposal.id)
    assert proposal.status == "escalated"


@pytest.mark.parametrize(
    "action, expected_title, expected_settledness",
    [
        ("keep", "Birthday dinner", "booked"),
        ("split", "Split for this block", "settled"),
        # `clear` records a remove patch. The item must
        # leave the shared itinerary, not be relabeled as a fictitious event.
        ("clear", "Birthday dinner", "removed"),
    ],
)
def test_all_deadlock_exits_close_without_adopting_the_pending_proposal(
    db, full_trip, action, expected_title, expected_settledness
):
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["me"], proposal.id)

    item = org.resolve_deadlock(db, full_trip["me"], proposal.id, action)

    assert item.title == expected_title
    assert item.settledness == expected_settledness
    assert proposal.status == "resolved_by_organizer"


def test_keep_rejects_the_pending_proposal_without_mutating_the_current_plan(
    db, full_trip
):
    """Keep is a neutral deadlock exit, not organizer adoption or a vote."""
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["me"], proposal.id)
    dinner = full_trip["dinner"]
    original_item = (
        dinner.title,
        dinner.place,
        dinner.start_hour,
        dinner.settledness,
        dinner.settled_at,
    )
    decisions_before = sorted(
        (decision.trip_membership_id, decision.status)
        for decision in db.query(ProposalDecision)
        .filter_by(proposal_id=proposal.id)
        .all()
    )

    item = org.resolve_deadlock(db, full_trip["me"], proposal.id, "keep")

    assert (
        item.title,
        item.place,
        item.start_hour,
        item.settledness,
        item.settled_at,
    ) == original_item
    assert item.start_hour == 19.0
    assert proposal.status == "resolved_by_organizer"
    assert sorted(
        (decision.trip_membership_id, decision.status)
        for decision in db.query(ProposalDecision)
        .filter_by(proposal_id=proposal.id)
        .all()
    ) == decisions_before

    change = db.query(PlanChange).filter_by(source_proposal_id=proposal.id).one()
    notice = db.query(UpdateNotice).filter_by(plan_item_id=item.id).one()
    assert change.origin == "deadlock_keep"
    assert change.patch == {}
    assert "not decided either way" in change.reason
    assert notice.title == "The current block was kept"
    assert "Current Plan unchanged" in notice.body

    with pytest.raises(org.NothingToDo):
        org.resolve_deadlock(db, full_trip["me"], proposal.id, "keep")
    assert db.query(PlanChange).filter_by(source_proposal_id=proposal.id).count() == 1
    assert db.query(UpdateNotice).filter_by(plan_item_id=item.id).count() == 1


def test_keep_preserves_live_state_instead_of_restoring_a_stale_snapshot(db, full_trip):
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["me"], proposal.id)
    dinner = full_trip["dinner"]
    dinner.title = "Birthday dinner (confirmed separately)"
    dinner.start_hour = 19.5
    db.flush()

    item = org.resolve_deadlock(db, full_trip["me"], proposal.id, "keep")

    assert item.title == "Birthday dinner (confirmed separately)"
    assert item.start_hour == 19.5
    assert db.query(PlanChange).filter_by(source_proposal_id=proposal.id).one().patch == {}


def test_the_organizer_can_never_adopt_the_proposal(db, full_trip):
    """This is the line. An organizer who can pick a side is a super user,
    and that breaks "the organizer's preferences carry no extra weight"."""
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["me"], proposal.id)
    original_hour = full_trip["dinner"].start_hour

    for attempt in ("apply", "accept", "20.0"):
        with pytest.raises(org.NothingToDo):
            org.resolve_deadlock(db, full_trip["me"], proposal.id, attempt)

    assert full_trip["dinner"].start_hour == original_hour


def test_remove_is_not_a_public_organizer_resolution_action(db, full_trip):
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["me"], proposal.id)

    with pytest.raises(org.NothingToDo):
        org.resolve_deadlock(db, full_trip["me"], proposal.id, "remove")

    assert proposal.status == "escalated"


def test_breaking_a_deadlock_is_written_into_the_log(db, full_trip):
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["me"], proposal.id)
    org.resolve_deadlock(db, full_trip["me"], proposal.id, "split")

    entry = db.query(PlanChange).filter_by(source_proposal_id=proposal.id).one()
    assert entry.origin == "deadlock_split"
    assert "could not agree" in entry.reason


def test_an_escalated_proposal_still_expires(db, full_trip):
    """If the organizer never acts, the block must not stay occupied forever."""
    proposal = _stuck_proposal(db, full_trip)
    org.escalate(db, full_trip["me"], proposal.id)
    proposal.deadline = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.flush()

    assert orch.expire_due_proposals(db) == [proposal.id]
    assert full_trip["dinner"].start_hour == 19.0
