"""PR-06B.1A membership-policy primitives remain distinct and explicit."""

from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.db.models import TripMembership
from app.domain.membership_policy import (
    access_authorized_membership_clause,
    current_participant_membership_clause,
    frozen_electorate_membership_clause,
    is_current_participant,
    is_frozen_electorate_member,
    is_membership_access_authorized,
)


def _membership(
    *,
    status: str = "joined",
    account: bool = False,
    quarantined: bool = False,
) -> TripMembership:
    membership = TripMembership(status=status, user_id="account-a" if account else None)
    if quarantined:
        # The predicate only needs the access-revocation marker; the database
        # check constraint is independently covered by the schema contract.
        membership.quarantined_at = object()  # type: ignore[assignment]
    return membership


def _postgres_sql(clause: object) -> str:
    return str(
        clause.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_access_and_current_participant_exclude_removed_and_quarantined_memberships() -> None:
    assert is_membership_access_authorized(_membership(account=True)) is True
    assert is_current_participant(_membership(account=True)) is True
    assert is_membership_access_authorized(_membership(status="preferences_submitted")) is True
    assert is_membership_access_authorized(
        _membership(status="preferences_submitted", account=True)
    ) is False
    for status in ("invited", "removed", "corrupted"):
        assert is_membership_access_authorized(_membership(status=status)) is False
        assert is_current_participant(_membership(status=status)) is False
    assert is_membership_access_authorized(_membership(quarantined=True)) is False
    assert is_current_participant(_membership(quarantined=True)) is False


def test_frozen_electorate_uses_persisted_identity_not_current_membership_state() -> None:
    quarantined = _membership(quarantined=True)
    quarantined.id = "membership-a"

    assert is_current_participant(quarantined) is False
    assert is_frozen_electorate_member(quarantined.id, {"membership-a"}) is True
    assert is_frozen_electorate_member(quarantined.id, {"membership-b"}) is False


def test_sql_predicates_keep_current_state_and_historical_snapshot_separate() -> None:
    access_sql = _postgres_sql(access_authorized_membership_clause())
    current_sql = _postgres_sql(current_participant_membership_clause())
    frozen_sql = _postgres_sql(frozen_electorate_membership_clause({"membership-a"}))

    assert "trip_membership.status IN ('joined')" in access_sql
    assert "trip_membership.status IN ('joined', 'preferences_submitted')" in access_sql
    assert "trip_membership.quarantined_at IS NULL" in access_sql
    assert current_sql == access_sql
    assert frozen_sql == "trip_membership.id IN ('membership-a')"
    assert "quarantined_at" not in frozen_sql
    assert "status" not in frozen_sql
