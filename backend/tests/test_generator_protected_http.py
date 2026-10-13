"""Route-level contracts for the default-off protected Generator adapter.

These tests deliberately call the real FastAPI route with real PostgreSQL
credentials.  They do not construct a trusted protected scope in the test;
the route must obtain it from the bearer and close that preliminary Session
before the B.2D4 lifecycle begins.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from threading import Event, Thread
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from app.agents import planner
from app.api import main as api
from app.db import session as db_session
from app.db.models import (
    AuthSession,
    GuestSession,
    MemberConstraint,
    MemberConstraintPrivate,
    MutationIdempotency,
    Plan,
    PlanChange,
    PlanItem,
    Place,
    Preference,
    Trip,
    TripMembership,
    User,
)
from app.domain import auth as auth_service
from app.domain.places import geoapify
from app.domain.transactions import CredentialProof, HmacSigningKey, PreliminaryActorScope
from app.domain.transactions import constraint_create
from app.domain.transactions.trip_preferences_common import StaticHmacKeyring


_seed_records: list[tuple[str, str, str]] = []


@dataclass(frozen=True)
class Seed:
    trip_id: str
    organizer_id: str
    organizer_user_id: str
    organizer_token: str
    guest_membership_id: str | None = None
    guest_token: str | None = None
    guest_session_id: str | None = None
    city: str = ""


class TrackingSession(Session):
    """Expose real session closure to the route-level transaction test."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.closed_by_owner = False

    def close(self) -> None:
        self.closed_by_owner = True
        super().close()


class FailingPreliminaryAuthenticationSession(TrackingSession):
    """Fail only the preliminary auth query while retaining close evidence."""

    def scalar(self, *_args, **_kwargs):
        raise OperationalError(
            "SELECT auth_sessions", {}, RuntimeError("test database unavailable")
        )


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _tracking_factory(engine, sessions: list[TrackingSession]):
    maker = sessionmaker(
        bind=engine, class_=TrackingSession, expire_on_commit=False, future=True
    )

    def create() -> TrackingSession:
        session = maker()
        sessions.append(session)
        return session

    return create


def _seed(engine, *, with_plan: bool = False, include_guest: bool = False) -> Seed:
    stamp = uuid4().hex
    token = f"generator-http-{stamp}"
    start = date(2027, 9, 10)
    city = f"Generator HTTP City {stamp[:8]}"
    with _factory(engine).begin() as session:
        user = User(name="Generator HTTP organizer", email=f"generator-http-{stamp}@example.test")
        session.add(user)
        session.flush()
        trip = Trip(
            name="Generator HTTP",
            destination=city,
            preferred_start_date=start,
            preferred_end_date=start,
            currency="USD",
            created_by_user_id=user.id,
        )
        session.add(trip)
        session.flush()
        organizer = TripMembership(
            trip_id=trip.id,
            user_id=user.id,
            role="organizer",
            status="preferences_submitted",
        )
        session.add(organizer)
        session.flush()
        session.add(
            Preference(
                trip_membership_id=organizer.id,
                preferred_start_date=start,
                preferred_end_date=start,
                available_start_date=start,
                available_end_date=start,
                ideal_budget=100.0,
                maximum_budget=150.0,
                currency="USD",
                top_interests=["culture", "food"],
                submitted_at=datetime.now(timezone.utc),
            )
        )
        session.add(
            AuthSession(
                user_id=user.id,
                token_hash=auth_service.token_hash(token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        guest_membership_id = None
        guest_token = None
        guest_session_id = None
        if include_guest:
            guest_token = f"gst_generator-http-{stamp}"
            guest = TripMembership(
                trip_id=trip.id,
                user_id=None,
                guest_display_name="Generator HTTP guest",
                role="participant",
                join_method="invite_guest",
                status="joined",
            )
            session.add(guest)
            session.flush()
            guest_session = GuestSession(
                membership_id=guest.id,
                trip_id=trip.id,
                token_hash=auth_service.token_hash(guest_token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
            session.add(guest_session)
            session.flush()
            guest_membership_id = guest.id
            guest_session_id = guest_session.id
        if with_plan:
            session.add(Plan(trip_id=trip.id, revision=0, currency="USD"))
        _seed_records.append((trip.id, user.id, city))
        return Seed(
            trip.id,
            organizer.id,
            user.id,
            token,
            guest_membership_id,
            guest_token,
            guest_session_id,
            city,
        )


@pytest.fixture(autouse=True)
def _clean_seeded_rows(test_engine, monkeypatch):
    _seed_records.clear()
    monkeypatch.delenv("CADENSY_B2D_PROTECTED_GENERATOR_WRITES", raising=False)
    monkeypatch.delenv("CADENSY_B2C_HMAC_KEY", raising=False)
    monkeypatch.setenv("APP_ENV", "test")
    yield
    records = tuple(_seed_records)
    _seed_records.clear()
    if not records:
        return
    trip_ids = [trip_id for trip_id, _user_id, _city in records]
    user_ids = [user_id for _trip_id, user_id, _city in records]
    cities = [city for _trip_id, _user_id, city in records]
    with _factory(test_engine).begin() as session:
        plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids))
        membership_ids = select(TripMembership.id).where(TripMembership.trip_id.in_(trip_ids))
        constraint_ids = select(MemberConstraint.id).where(
            MemberConstraint.trip_membership_id.in_(membership_ids)
        )
        session.execute(delete(MutationIdempotency).where(MutationIdempotency.trip_id.in_(trip_ids)))
        session.execute(delete(GuestSession).where(GuestSession.trip_id.in_(trip_ids)))
        session.execute(delete(PlanChange).where(PlanChange.plan_id.in_(plan_ids)))
        session.execute(delete(PlanItem).where(PlanItem.plan_id.in_(plan_ids)))
        session.execute(delete(Plan).where(Plan.trip_id.in_(trip_ids)))
        session.execute(delete(MemberConstraintPrivate).where(MemberConstraintPrivate.constraint_id.in_(constraint_ids)))
        session.execute(delete(MemberConstraint).where(MemberConstraint.trip_membership_id.in_(membership_ids)))
        session.execute(delete(Preference).where(Preference.trip_membership_id.in_(membership_ids)))
        session.execute(delete(TripMembership).where(TripMembership.trip_id.in_(trip_ids)))
        session.execute(delete(Trip).where(Trip.id.in_(trip_ids)))
        session.execute(delete(AuthSession).where(AuthSession.user_id.in_(user_ids)))
        session.execute(delete(User).where(User.id.in_(user_ids)))
        session.execute(delete(Place).where(Place.city.in_(cities)))


def _headers(seed: Seed, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {seed.organizer_token}",
        "X-Trip-Id": seed.trip_id,
    }
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def _provider_places(city: str) -> tuple[geoapify.GeoapifyPlace, ...]:
    attractions = tuple(
        geoapify.GeoapifyPlace(
            provider_place_id=f"generator-http-attraction-{index}",
            name=f"Generator HTTP attraction {index}",
            english_name=f"Generator HTTP attraction {index}",
            local_name=None,
            city=city,
            country="United States",
            latitude=41.80 + index / 1000,
            longitude=-87.60 - index / 1000,
            category="tourism.attraction",
            address=f"Attraction {index}, {city}",
            image_url=None,
            opening_hours=None,
        )
        for index in range(8)
    )
    meals = tuple(
        geoapify.GeoapifyPlace(
            provider_place_id=f"generator-http-meal-{index}",
            name=f"Generator HTTP meal {index}",
            english_name=f"Generator HTTP meal {index}",
            local_name=None,
            city=city,
            country="United States",
            latitude=41.81 + index / 1000,
            longitude=-87.61 - index / 1000,
            category="catering.restaurant",
            address=f"Meal {index}, {city}",
            image_url=None,
            opening_hours=None,
        )
        for index in range(6)
    )
    return attractions + meals


def _planner(payload: planner.PlanDayInput) -> planner.PlanDayResult:
    attractions = tuple(
        candidate
        for candidate in payload.candidates
        if "restaurant" not in {tag.casefold() for tag in candidate.tags}
    )
    return planner.PlanDayResult(
        picks=tuple(
            planner.Pick(candidate_id=candidate.candidate_id, start_hour=hour)
            for candidate, hour in zip(attractions[:3], (10.0, 14.0, 16.0), strict=True)
        ),
        used_ai=False,
        planner_note="Deterministic B.2D5 HTTP planner.",
    )


def _enable_protected_generator(monkeypatch, factory) -> None:
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(api, "_generator_http_provider_fetch", _provider_places)
    monkeypatch.setattr(api, "_generator_http_planner_call", _planner)
    monkeypatch.setenv("CADENSY_B2D_PROTECTED_GENERATOR_WRITES", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "generator-http-test-key")


def test_feature_on_requires_idempotency_key_before_legacy_generation(test_engine, monkeypatch):
    """The protected gate cannot silently fall through to the Legacy writer."""

    seed = _seed(test_engine)
    factory = _factory(test_engine)
    calls: list[str] = []

    def legacy_must_not_run(*_args, **_kwargs):
        calls.append("legacy")
        raise AssertionError("protected missing-key request reached Legacy Generator")

    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(api.plan_generator, "generate_plan", legacy_must_not_run)
    monkeypatch.setenv("CADENSY_B2D_PROTECTED_GENERATOR_WRITES", "1")

    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed))

    assert response.status_code == 428
    assert calls == []


@pytest.mark.parametrize("gate_value", (None, "0"))
def test_feature_off_keeps_real_legacy_generation_and_ignores_protected_only_headers(
    test_engine, monkeypatch, gate_value
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(api.plan_generator.place_service, "places_for_planner", lambda *_args: ())
    if gate_value is not None:
        monkeypatch.setenv("CADENSY_B2D_PROTECTED_GENERATOR_WRITES", gate_value)

    def coordinator_must_not_be_built():
        raise AssertionError("feature-off request accessed protected HMAC/coordinator configuration")

    monkeypatch.setattr(api, "_generator_protected_coordinator", coordinator_must_not_be_built)
    with TestClient(api.app) as client:
        response = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate",
            headers={
                **_headers(seed, key="not a valid idempotency key"),
                "X-Expected-Plan-Revision": "not-an-integer",
                "X-Expected-Trip-Facts-Revision": "also-not-an-integer",
            },
        )

    assert response.status_code == 200
    assert set(response.json()) == {
        "plan_id",
        "status",
        "days",
        "blocked_reason",
        "blocked_code",
        "generated_by",
        "used_ai",
        "planner_note",
    }
    with factory() as session:
        assert session.scalar(
            select(MutationIdempotency.id).where(MutationIdempotency.trip_id == seed.trip_id)
        ) is None


@pytest.mark.parametrize("with_plan", (False, True))
def test_feature_on_account_generation_uses_b2d4_and_persists_one_plan_outcome(
    test_engine, monkeypatch, with_plan
):
    seed = _seed(test_engine, with_plan=with_plan)
    factory = _factory(test_engine)
    _enable_protected_generator(monkeypatch, factory)

    with TestClient(api.app) as client:
        response = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate",
            headers=_headers(seed, key=f"generator-http-success-{with_plan}"),
        )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {
        "plan_id",
        "status",
        "days",
        "blocked_reason",
        "blocked_code",
        "generated_by",
        "used_ai",
        "planner_note",
    }
    assert payload["status"] == "active"
    assert payload["days"]
    with factory() as session:
        trip = session.get(Trip, seed.trip_id)
        plan = session.get(Plan, payload["plan_id"])
        ledger = session.scalar(
            select(MutationIdempotency).where(
                MutationIdempotency.trip_id == seed.trip_id,
                MutationIdempotency.idempotency_key == f"generator-http-success-{with_plan}",
            )
        )
        assert trip is not None and trip.planning_facts_revision == 0
        assert plan is not None and plan.revision == 1
        assert session.scalar(select(PlanItem.id).where(PlanItem.plan_id == plan.id)) is not None
        assert session.scalar(select(PlanChange.id).where(PlanChange.plan_id == plan.id)) is not None
        assert ledger is not None
        assert ledger.aggregate_scope == "plan"
        assert ledger.resulting_plan_revision == 1


def test_feature_on_replay_returns_historical_body_without_external_work(
    test_engine, monkeypatch
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    _enable_protected_generator(monkeypatch, factory)
    key = "generator-http-replay"
    with TestClient(api.app) as client:
        first = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )
    assert first.status_code == 200
    first_body = first.json()
    with factory.begin() as session:
        plan = session.get(Plan, first_body["plan_id"])
        trip = session.get(Trip, seed.trip_id)
        assert plan is not None and trip is not None
        plan.revision += 1
        trip.planning_facts_revision += 1

    def external_work_must_not_run(*_args, **_kwargs):
        raise AssertionError("historical Generator replay invoked external work")

    monkeypatch.setattr(api, "_generator_http_provider_fetch", external_work_must_not_run)
    monkeypatch.setattr(api, "_generator_http_planner_call", external_work_must_not_run)
    with TestClient(api.app) as client:
        replay = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )

    assert replay.status_code == 200
    assert replay.json() == first_body
    with factory() as session:
        assert session.scalar(
            select(MutationIdempotency).where(
                MutationIdempotency.trip_id == seed.trip_id,
                MutationIdempotency.idempotency_key == key,
            )
        ) is not None
        assert session.scalar(
            select(Plan.revision).where(Plan.id == first_body["plan_id"])
        ) == 2
        assert session.scalar(
            select(Trip.planning_facts_revision).where(Trip.id == seed.trip_id)
        ) == 1


@pytest.mark.parametrize(
    ("gate", "environment", "expected", "route"),
    (
        (None, None, 200, "legacy"),
        ("0", None, 200, "legacy"),
        ("1", None, 503, "closed"),
        ("1", "", 503, "closed"),
        ("1", "ambiguous-environment", 503, "closed"),
        ("1", "production", 503, "closed"),
        ("1", "prod", 503, "closed"),
        ("1", "test", 428, "protected"),
        ("1", "development", 428, "protected"),
        ("1", "staging", 428, "protected"),
        ("not-a-boolean", "test", 503, "closed"),
    ),
)
def test_generator_gate_requires_explicit_approved_non_production_environment(
    test_engine, monkeypatch, gate, environment, expected, route
):
    """A true B.2D flag must not turn an absent APP_ENV into development."""

    seed = _seed(test_engine)
    factory = _factory(test_engine)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "generator-http-test-key")
    monkeypatch.delenv("APP_ENV", raising=False)
    if gate is not None:
        monkeypatch.setenv("CADENSY_B2D_PROTECTED_GENERATOR_WRITES", gate)
    if environment is not None:
        monkeypatch.setenv("APP_ENV", environment)
    calls: list[str] = []

    def provider_must_not_run(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("gate validation reached external work")

    def planner_must_not_run(*_args, **_kwargs):
        calls.append("planner")
        raise AssertionError("gate validation reached external work")

    def legacy_marker(*_args, **_kwargs):
        calls.append("legacy")
        return {
            "plan_id": "legacy-plan",
            "status": "active",
            "days": [],
            "blocked_reason": None,
            "blocked_code": None,
            "generated_by": "legacy",
            "used_ai": False,
            "planner_note": "legacy marker",
        }

    monkeypatch.setattr(api, "_generator_http_provider_fetch", provider_must_not_run)
    monkeypatch.setattr(api, "_generator_http_planner_call", planner_must_not_run)
    monkeypatch.setattr(api, "_generate_trip_plan_legacy", legacy_marker)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed)
        )

    assert response.status_code == expected
    if route == "legacy":
        assert calls == ["legacy"]
    else:
        assert calls == []
    with factory() as session:
        assert session.scalar(select(Plan.id).where(Plan.trip_id == seed.trip_id)) is None
        assert session.scalar(
            select(MutationIdempotency.id).where(MutationIdempotency.trip_id == seed.trip_id)
        ) is None


def test_preliminary_authentication_database_failure_is_closed_and_maps_to_503(
    test_engine, monkeypatch
):
    """The short route-auth session has the same safe infra boundary as the kernel."""

    seed = _seed(test_engine)
    sessions: list[TrackingSession] = []
    maker = sessionmaker(
        bind=test_engine,
        class_=FailingPreliminaryAuthenticationSession,
        expire_on_commit=False,
        future=True,
    )

    def failing_factory() -> TrackingSession:
        session = maker()
        sessions.append(session)
        return session

    _enable_protected_generator(monkeypatch, failing_factory)
    coordinator_calls: list[str] = []
    external_calls: list[str] = []

    def coordinator_must_not_execute(*_args, **_kwargs):
        coordinator_calls.append("execute")
        raise AssertionError("preliminary authentication failure reached the coordinator")

    def external_must_not_run(*_args, **_kwargs):
        external_calls.append("external")
        raise AssertionError("preliminary authentication failure reached external work")

    monkeypatch.setattr(
        api.protected_generator_commit,
        "execute_protected_plan_generation",
        coordinator_must_not_execute,
    )
    monkeypatch.setattr(api, "_generator_http_provider_fetch", external_must_not_run)
    monkeypatch.setattr(api, "_generator_http_planner_call", external_must_not_run)

    with TestClient(api.app, raise_server_exceptions=False) as client:
        response = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate",
            headers=_headers(seed, key="generator-preliminary-db-failure"),
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "Protected Generator authentication is temporarily unavailable"}
    assert "database unavailable" not in response.text
    assert coordinator_calls == []
    assert external_calls == []
    assert len(sessions) == 1
    assert sessions[0].closed_by_owner is True
    with _factory(test_engine)() as session:
        assert session.scalar(select(Plan.id).where(Plan.trip_id == seed.trip_id)) is None
        assert session.scalar(
            select(MutationIdempotency.id).where(MutationIdempotency.trip_id == seed.trip_id)
        ) is None


def test_feature_on_rejects_malformed_key_and_missing_hmac_before_external_work(
    test_engine, monkeypatch
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    _enable_protected_generator(monkeypatch, factory)
    calls: list[str] = []

    def provider_must_not_run(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("invalid protected request reached external work")

    monkeypatch.setattr(api, "_generator_http_provider_fetch", provider_must_not_run)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        malformed = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate",
            headers=_headers(seed, key="not a valid key"),
        )
    assert malformed.status_code == 422
    monkeypatch.delenv("CADENSY_B2C_HMAC_KEY", raising=False)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        missing_hmac = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate",
            headers=_headers(seed, key="missing-hmac"),
        )
    assert missing_hmac.status_code == 503
    assert calls == []


def test_guest_cannot_impersonate_organizer_or_read_another_actor_generator_ledger(
    test_engine, monkeypatch
):
    seed = _seed(test_engine, include_guest=True)
    assert seed.guest_token is not None and seed.guest_membership_id is not None
    factory = _factory(test_engine)
    _enable_protected_generator(monkeypatch, factory)
    key = "generator-http-actor-isolation"
    with TestClient(api.app) as client:
        owner = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )
    assert owner.status_code == 200
    calls: list[str] = []

    def provider_must_not_run(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("non-organizer Guest reached Generator external work")

    monkeypatch.setattr(api, "_generator_http_provider_fetch", provider_must_not_run)
    with TestClient(api.app) as client:
        guest_headers = {
            "Authorization": f"Bearer {seed.guest_token}",
            "X-Trip-Id": seed.trip_id,
            "X-Membership-Id": seed.organizer_id,
            "Idempotency-Key": key,
        }
        guest = client.post(f"/api/trips/{seed.trip_id}/plans/generate", headers=guest_headers)

    assert guest.status_code == 403
    assert guest.json() != owner.json()
    assert calls == []
    with factory() as session:
        rows = session.scalars(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id)
        ).all()
        assert len(rows) == 1 and rows[0].actor_membership_id == seed.organizer_id


def test_revoked_or_removed_account_cannot_replay_a_completed_generator_outcome(
    test_engine, monkeypatch
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    _enable_protected_generator(monkeypatch, factory)
    key = "generator-http-replay-auth"
    with TestClient(api.app) as client:
        first = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )
    assert first.status_code == 200
    calls: list[str] = []

    def provider_must_not_run(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("revoked account reached Generator external work")

    monkeypatch.setattr(api, "_generator_http_provider_fetch", provider_must_not_run)
    with factory.begin() as session:
        auth_session = session.scalar(select(AuthSession).where(AuthSession.user_id == seed.organizer_user_id))
        assert auth_session is not None
        auth_session.revoked_at = datetime.now(timezone.utc)
    with TestClient(api.app) as client:
        revoked = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )
    assert revoked.status_code == 401
    with factory.begin() as session:
        auth_session = session.scalar(select(AuthSession).where(AuthSession.user_id == seed.organizer_user_id))
        membership = session.get(TripMembership, seed.organizer_id)
        assert auth_session is not None and membership is not None
        auth_session.revoked_at = None
        membership.status = "removed"
    with TestClient(api.app) as client:
        removed = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )
    assert removed.status_code == 403
    assert calls == []


def test_protected_existing_items_and_duplicate_plan_aggregates_fail_before_external_work(
    test_engine, monkeypatch
):
    populated = _seed(test_engine, with_plan=True)
    duplicate = _seed(test_engine)
    factory = _factory(test_engine)
    _enable_protected_generator(monkeypatch, factory)
    with factory.begin() as session:
        plan = session.scalar(select(Plan).where(Plan.trip_id == populated.trip_id))
        assert plan is not None
        session.add(
            PlanItem(
                plan_id=plan.id,
                day_index=1,
                day_date=date(2027, 9, 10),
                start_hour=10.0,
                duration_min=60,
                title="Existing protected item",
                place="Existing place",
            )
        )
        session.add_all((Plan(trip_id=duplicate.trip_id), Plan(trip_id=duplicate.trip_id)))
    calls: list[str] = []

    def provider_must_not_run(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("ineligible protected Generator request reached Provider")

    monkeypatch.setattr(api, "_generator_http_provider_fetch", provider_must_not_run)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        populated_response = client.post(
            f"/api/trips/{populated.trip_id}/plans/generate",
            headers=_headers(populated, key="generator-http-existing-items"),
        )
        duplicate_response = client.post(
            f"/api/trips/{duplicate.trip_id}/plans/generate",
            headers=_headers(duplicate, key="generator-http-duplicate-plans"),
        )

    assert populated_response.status_code == duplicate_response.status_code == 409
    assert calls == []
    assert "existing" not in populated_response.text.lower()
    assert "duplicate" not in duplicate_response.text.lower()
    with factory() as session:
        assert session.scalar(
            select(MutationIdempotency.id).where(
                MutationIdempotency.trip_id.in_((populated.trip_id, duplicate.trip_id))
            )
        ) is None


@pytest.mark.parametrize("tamper", ("expired", "outcome", "hmac-version"))
def test_protected_replay_failures_are_controlled_and_do_not_restart_generation(
    test_engine, monkeypatch, tamper
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    _enable_protected_generator(monkeypatch, factory)
    key = f"generator-http-replay-{tamper}"
    with TestClient(api.app) as client:
        first = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )
    assert first.status_code == 200
    with factory.begin() as session:
        ledger = session.scalar(
            select(MutationIdempotency).where(
                MutationIdempotency.trip_id == seed.trip_id,
                MutationIdempotency.idempotency_key == key,
            )
        )
        assert ledger is not None
        if tamper == "expired":
            ledger.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        elif tamper == "outcome":
            ledger.outcome_json = {**ledger.outcome_json, "private_diagnostic": "never return"}
        else:
            ledger.hmac_key_version = "unknown-generator-hmac-version"
    calls: list[str] = []

    def provider_must_not_run(*_args, **_kwargs):
        calls.append("provider")
        raise AssertionError("unsafe replay reached Provider")

    monkeypatch.setattr(api, "_generator_http_provider_fetch", provider_must_not_run)
    with TestClient(api.app, raise_server_exceptions=False) as client:
        replay = client.post(
            f"/api/trips/{seed.trip_id}/plans/generate", headers=_headers(seed, key=key)
        )

    assert replay.status_code == 409
    assert "private_diagnostic" not in replay.text
    assert "unknown-generator-hmac-version" not in replay.text
    assert calls == []


def test_protected_http_closes_request_sessions_before_provider_and_allows_constraint_commit(
    test_engine, monkeypatch
):
    """A blocked Provider cannot retain the request Session or a Trip root lock."""

    seed = _seed(test_engine)
    sessions: list[TrackingSession] = []
    factory = _tracking_factory(test_engine, sessions)
    _enable_protected_generator(monkeypatch, factory)
    provider_entered = Event()
    release_provider = Event()
    provider_session_state: list[bool] = []
    response: list[object] = []

    def blocked_provider(city: str):
        # Admission, preflight, Prepare, and cache-read sessions must all have
        # closed before an external Provider is allowed to begin.
        provider_session_state.append(
            bool(sessions) and all(item.closed_by_owner and not item.in_transaction() for item in sessions)
        )
        provider_entered.set()
        assert release_provider.wait(5), "test did not release Provider"
        return _provider_places(city)

    monkeypatch.setattr(api, "_generator_http_provider_fetch", blocked_provider)

    def issue_http_request() -> None:
        with TestClient(api.app, raise_server_exceptions=False) as client:
            response.append(
                client.post(
                    f"/api/trips/{seed.trip_id}/plans/generate",
                    headers=_headers(seed, key="generator-http-provider-block"),
                )
            )

    worker = Thread(target=issue_http_request, daemon=True)
    worker.start()
    assert provider_entered.wait(5), "HTTP request never reached the fake Provider"
    try:
        # This is the accepted Phase 4 writer, using an independent fresh
        # PostgreSQL Session while the HTTP request remains inside external
        # work.  It must not block behind an HTTP-held Trip root lock.
        constraint = constraint_create.execute_protected_constraint_create(
            session_factory=factory,
            scope=PreliminaryActorScope(
                trip_id=seed.trip_id,
                membership_id=seed.organizer_id,
                credential_kind="account",
                expected_user_id=seed.organizer_user_id,
            ),
            credential=CredentialProof.from_bearer_token(seed.organizer_token),
            keyring=StaticHmacKeyring(HmacSigningKey("http-boundary-v1", b"http-boundary-key")),
            command=constraint_create.ProtectedConstraintCreate(
                "generator-http-external-constraint",
                0,
                "avoid_tag",
                {"tags": ["museum"]},
                "required",
                "private test text",
                "planning_only",
            ),
        )
        assert constraint.replayed is False
        assert constraint.trip_planning_facts_revision == 1
    finally:
        release_provider.set()
        worker.join(10)

    assert not worker.is_alive()
    assert provider_session_state == [True]
    assert len(response) == 1
    assert getattr(response[0], "status_code") == 409
    with _factory(test_engine)() as session:
        trip = session.get(Trip, seed.trip_id)
        assert trip is not None and trip.planning_facts_revision == 1
        assert session.scalar(select(Plan.id).where(Plan.trip_id == seed.trip_id)) is None
        ledgers = session.scalars(
            select(MutationIdempotency).where(MutationIdempotency.trip_id == seed.trip_id)
        ).all()
        assert [row.operation_code for row in ledgers] == ["constraint.create.v1"]
