from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import CHAR, inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.models import MutationIdempotency, Plan, Trip, TripMembership


def _completed_ledger_row(
    *,
    trip_id: str,
    actor_membership_id: str,
    idempotency_key: str,
    resource_id: str,
    resulting_plan_revision: int | None = 0,
    aggregate_scope: str = "plan",
    resulting_trip_planning_facts_revision: int | None = None,
) -> MutationIdempotency:
    now = datetime.now(timezone.utc)
    return MutationIdempotency(
        trip_id=trip_id,
        actor_membership_id=actor_membership_id,
        idempotency_key=idempotency_key,
        operation_code="proposal.apply",
        resource_kind="plan_item",
        resource_id=resource_id,
        fingerprint_version=1,
        request_hmac="a" * 64,
        hmac_key_version="fixture-v1",
        outcome_contract_version=1,
        response_status=200,
        outcome_json={"kind": "notice", "message": "A safe stored response."},
        aggregate_scope=aggregate_scope,
        resulting_plan_revision=resulting_plan_revision,
        resulting_trip_planning_facts_revision=resulting_trip_planning_facts_revision,
        completed_at=now,
        expires_at=now + timedelta(days=1),
    )


def test_plan_revision_defaults_to_zero_and_is_nonnegative(db, trip_setup) -> None:
    existing_plan = trip_setup["plan"]
    assert existing_plan.revision == 0

    new_plan = Plan(trip_id=trip_setup["trip"].id)
    db.add(new_plan)
    db.flush()
    assert new_plan.revision == 0

    db.add(Plan(trip_id=trip_setup["trip"].id, revision=-1))
    with pytest.raises(IntegrityError):
        db.flush()


def test_trip_planning_facts_revision_defaults_to_zero_and_is_nonnegative(db, trip_setup) -> None:
    existing_trip = trip_setup["trip"]
    assert existing_trip.planning_facts_revision == 0

    new_trip = Trip(
        name="Fresh facts trip",
        destination="Austin",
        created_by_user_id=trip_setup["organizer"].user_id,
    )
    db.add(new_trip)
    db.flush()
    assert new_trip.planning_facts_revision == 0

    db.add(
        Trip(
            name="Invalid facts trip",
            destination="Austin",
            created_by_user_id=trip_setup["organizer"].user_id,
            planning_facts_revision=-1,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_ledger_composite_key_is_scoped_to_trip_and_actor(db, trip_setup) -> None:
    trip = trip_setup["trip"]
    organizer = trip_setup["organizer"]
    participant = trip_setup["participant"]

    first = _completed_ledger_row(
        trip_id=trip.id,
        actor_membership_id=organizer.id,
        idempotency_key="same-key",
        resource_id=trip_setup["item"].id,
    )
    db.add(first)
    db.flush()

    db.add(
        _completed_ledger_row(
            trip_id=trip.id,
            actor_membership_id=participant.id,
            idempotency_key="same-key",
            resource_id=trip_setup["item"].id,
        )
    )
    db.flush()

    db.add(
        _completed_ledger_row(
            trip_id=trip.id,
            actor_membership_id=organizer.id,
            idempotency_key="same-key",
            resource_id="another-resource",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_same_actor_key_is_independent_in_a_different_trip(db, trip_setup) -> None:
    original_trip = trip_setup["trip"]
    organizer = trip_setup["organizer"]
    db.add(
        _completed_ledger_row(
            trip_id=original_trip.id,
            actor_membership_id=organizer.id,
            idempotency_key="same-key",
            resource_id=trip_setup["item"].id,
        )
    )
    db.flush()

    other_trip = Trip(
        name="Independent trip",
        destination="Austin",
        created_by_user_id=organizer.user_id,
    )
    db.add(other_trip)
    db.flush()
    other_membership = TripMembership(
        trip_id=other_trip.id,
        user_id=organizer.user_id,
        role="organizer",
    )
    db.add(other_membership)
    db.flush()

    db.add(
        _completed_ledger_row(
            trip_id=other_trip.id,
            actor_membership_id=other_membership.id,
            idempotency_key="same-key",
            resource_id="resource-in-other-trip",
        )
    )
    db.flush()


def test_ledger_jsonb_round_trips_safe_outcome_and_expiry_index_exists(db, trip_setup) -> None:
    ledger = _completed_ledger_row(
        trip_id=trip_setup["trip"].id,
        actor_membership_id=trip_setup["organizer"].id,
        idempotency_key="jsonb-key",
        resource_id=trip_setup["item"].id,
    )
    db.add(ledger)
    db.flush()
    db.expire(ledger)

    assert ledger.outcome_json == {
        "kind": "notice",
        "message": "A safe stored response.",
    }
    indexes = inspect(db.bind).get_indexes("mutation_idempotency")
    assert {index["name"] for index in indexes} >= {"ix_mutation_idempotency_expires_at"}


def test_ledger_completed_outcome_contract_is_non_nullable(db) -> None:
    columns = {
        column["name"]: column
        for column in inspect(db.bind).get_columns("mutation_idempotency")
    }

    required = {
        "trip_id",
        "actor_membership_id",
        "idempotency_key",
        "operation_code",
        "resource_kind",
        "resource_id",
        "fingerprint_version",
        "request_hmac",
        "hmac_key_version",
        "outcome_contract_version",
        "response_status",
        "outcome_json",
        "completed_at",
        "expires_at",
    }
    assert all(not columns[name]["nullable"] for name in required)
    assert columns["resulting_plan_revision"]["nullable"] is True
    assert columns["resulting_trip_planning_facts_revision"]["nullable"] is True
    assert columns["aggregate_scope"]["nullable"] is False
    assert isinstance(columns["request_hmac"]["type"], CHAR)

    trip_columns = {
        column["name"]: column for column in inspect(db.bind).get_columns("trip")
    }
    assert trip_columns["planning_facts_revision"]["type"].__class__.__name__ == "BIGINT"


def test_ledger_scope_requires_exactly_one_nonnegative_result_revision(db, trip_setup) -> None:
    base = {
        "trip_id": trip_setup["trip"].id,
        "actor_membership_id": trip_setup["organizer"].id,
        "resource_id": trip_setup["item"].id,
    }

    db.add(
        _completed_ledger_row(
            **base,
            idempotency_key="trip-scope-valid",
            aggregate_scope="trip_planning_facts",
            resulting_plan_revision=None,
            resulting_trip_planning_facts_revision=3,
        )
    )
    db.flush()

    invalid_rows = (
        _completed_ledger_row(
            **base,
            idempotency_key="invalid-scope",
            aggregate_scope="not-a-scope",
        ),
        _completed_ledger_row(
            **base,
            idempotency_key="both-results",
            resulting_trip_planning_facts_revision=3,
        ),
        _completed_ledger_row(
            **base,
            idempotency_key="neither-result",
            resulting_plan_revision=None,
        ),
        _completed_ledger_row(
            **base,
            idempotency_key="negative-plan-result",
            resulting_plan_revision=-1,
        ),
        _completed_ledger_row(
            **base,
            idempotency_key="negative-trip-result",
            aggregate_scope="trip_planning_facts",
            resulting_plan_revision=None,
            resulting_trip_planning_facts_revision=-1,
        ),
    )
    for row in invalid_rows:
        savepoint = db.begin_nested()
        db.add(row)
        with pytest.raises(IntegrityError):
            db.flush()
        savepoint.rollback()


def test_ledger_scope_cannot_bypass_existing_trip_actor_key(db, trip_setup) -> None:
    base = {
        "trip_id": trip_setup["trip"].id,
        "actor_membership_id": trip_setup["organizer"].id,
        "resource_id": trip_setup["item"].id,
        "idempotency_key": "scope-does-not-expand-key",
    }
    db.add(_completed_ledger_row(**base))
    db.flush()
    db.add(
        _completed_ledger_row(
            **base,
            aggregate_scope="trip_planning_facts",
            resulting_plan_revision=None,
            resulting_trip_planning_facts_revision=0,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_trip_planning_facts_revision_allows_positive_and_rejects_null(db, trip_setup) -> None:
    trip = trip_setup["trip"]
    trip.planning_facts_revision = 4
    db.flush()
    assert trip.planning_facts_revision == 4

    savepoint = db.begin_nested()
    with pytest.raises(IntegrityError):
        db.execute(
            text("update trip set planning_facts_revision = null where id = :trip_id"),
            {"trip_id": trip.id},
        )
    savepoint.rollback()


def test_rollback_leaves_no_durable_ledger_row(db, trip_setup) -> None:
    row = _completed_ledger_row(
        trip_id=trip_setup["trip"].id,
        actor_membership_id=trip_setup["organizer"].id,
        idempotency_key="rolled-back-key",
        resource_id=trip_setup["item"].id,
    )
    savepoint = db.begin_nested()
    db.add(row)
    db.flush()
    savepoint.rollback()

    assert db.query(MutationIdempotency).filter_by(idempotency_key="rolled-back-key").count() == 0
