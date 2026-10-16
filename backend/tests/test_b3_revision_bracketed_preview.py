"""B.3 revision-bracketed Preview characterization tests.

These tests use a small contract runtime rather than a provider.  The point is
to prove the application boundary: every tool read owns a short Session, no
Preview mutates the aggregate, and a changed revision invalidates the entire
candidate rather than returning a mixed-state verdict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from queue import Queue
from threading import Event, Thread
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents.execution import AgentExecutionConfig
from app.agents.runtime_contract import (
    AgentCandidateOption,
    AgentReplyOnly,
    AgentSuggestedChange,
    RuntimeFailure,
    RuntimeObservation,
    RuntimeRequest,
    RuntimeResult,
)
from app.api import main as api
from app.db import session as db_session
from app.db.models import (
    AuthSession,
    MutationIdempotency,
    Plan,
    PlanChange,
    PlanItem,
    Trip,
    TripMembership,
    UpdateNotice,
    User,
)
from app.domain import auth as auth_service
from app.domain.decisions import orchestrator as orch
from app.domain.preview_apply import (
    PreviewCandidate,
    PreviewRequest,
    PreviewResult,
    RevisionBracketedPreview,
    StalePreview,
    _replacement_provenance_allows,
)
from app.domain.transactions.protected_profile import b3_profile_admission
from app.domain.transactions import CredentialProof, HmacSigningKey, PreliminaryActorScope
from app.domain.transactions import change_proposal as protected_changes
from app.observability.telemetry import TerminalCategory


_created_records: list[tuple[str, str]] = []


@pytest.fixture(autouse=True)
def _cleanup_b3_records(test_engine):
    _created_records.clear()
    yield
    records, _created_records[:] = list(_created_records), []
    if not records:
        return
    trip_ids = [trip_id for trip_id, _ in records]
    user_ids = [user_id for _, user_id in records]
    plan_ids = select(Plan.id).where(Plan.trip_id.in_(trip_ids))
    with _factory(test_engine).begin() as db:
        db.query(MutationIdempotency).filter(MutationIdempotency.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(UpdateNotice).filter(UpdateNotice.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(PlanChange).filter(PlanChange.plan_id.in_(plan_ids)).delete(synchronize_session=False)
        db.query(PlanItem).filter(PlanItem.plan_id.in_(plan_ids)).delete(synchronize_session=False)
        db.query(Plan).filter(Plan.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(TripMembership).filter(TripMembership.trip_id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(Trip).filter(Trip.id.in_(trip_ids)).delete(synchronize_session=False)
        db.query(AuthSession).filter(AuthSession.user_id.in_(user_ids)).delete(synchronize_session=False)
        db.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)


def _factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@dataclass(frozen=True)
class _Seed:
    trip_id: str
    plan_id: str
    item_id: str
    membership_id: str
    account_token: str


def _seed(engine) -> _Seed:
    token = f"preview-token-{uuid4().hex}"
    with _factory(engine).begin() as db:
        user = User(name="Preview user", email=f"preview-{uuid4().hex}@example.test")
        db.add(user)
        db.flush()
        trip = Trip(name="Preview trip", destination="Chicago", created_by_user_id=user.id)
        db.add(trip)
        db.flush()
        membership = TripMembership(
            trip_id=trip.id, user_id=user.id, role="participant", status="joined"
        )
        plan = Plan(trip_id=trip.id)
        db.add_all((membership, plan))
        db.flush()
        item = PlanItem(
            plan_id=plan.id,
            day_index=1,
            day_date=date(2026, 10, 20),
            start_hour=12,
            duration_min=60,
            title="Preview lunch",
            place="Chicago",
        )
        db.add(item)
        db.flush()
        db.add(
            AuthSession(
                user_id=user.id,
                token_hash=auth_service.token_hash(token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=1),
            )
        )
        _created_records.append((trip.id, user.id))
        return _Seed(trip.id, plan.id, item.id, membership.id, token)


class _ReadThenSuggestRuntime:
    def __init__(self, item_id: str) -> None:
        self.item_id = item_id
        self.tool_result: object | None = None

    def run(self, request, capability, *, execution: AgentExecutionConfig) -> RuntimeResult:
        def use_tools(tools):
            current_plan = next(tool for tool in tools if tool.name == "get_current_plan")
            self.tool_result = current_plan.invoke(day="all")

        capability.run_with_read_only_tools(use_tools)
        return RuntimeResult(
            reply="I can prepare that change; it has not been applied.",
            outcome=AgentSuggestedChange(
                item_ref=self.item_id,
                safe_patch={"start_hour": 13.0},
            ),
            candidate_options=(),
            observation=RuntimeObservation(
                trace_id="preview-test", round_count=1, total_tokens=1, total_elapsed_ms=1
            ),
        )


class _SessionObservingRuntime(_ReadThenSuggestRuntime):
    def __init__(self, item_id: str, sessions: list[Session]) -> None:
        super().__init__(item_id)
        self._sessions = sessions

    def run(self, request, capability, *, execution: AgentExecutionConfig) -> RuntimeResult:
        # Capture has already completed before the Runtime starts.
        assert self._sessions and all(getattr(session, "b3_closed", False) for session in self._sessions)

        def use_tools(tools):
            # Tool-description construction has also completed before the
            # Runtime receives tools; only the invoked tool owns a new Session.
            assert all(getattr(session, "b3_closed", False) for session in self._sessions)
            self.tool_result = next(tool for tool in tools if tool.name == "get_current_plan").invoke(day="all")

        capability.run_with_read_only_tools(use_tools)
        return RuntimeResult(
            reply="Checked through a detached read tool.",
            outcome=AgentSuggestedChange(item_ref=self.item_id, safe_patch={"start_hour": 13.0}),
            candidate_options=(),
            observation=RuntimeObservation(trace_id="session-preview", round_count=1, total_tokens=1, total_elapsed_ms=1),
        )


class _RevisionChangingRuntime(_ReadThenSuggestRuntime):
    def __init__(self, item_id: str, factory, plan_id: str) -> None:
        super().__init__(item_id)
        self._factory = factory
        self._plan_id = plan_id

    def run(self, request, capability, *, execution: AgentExecutionConfig) -> RuntimeResult:
        result = super().run(request, capability, execution=execution)
        with self._factory.begin() as db:
            plan = db.get(Plan, self._plan_id)
            assert plan is not None
            plan.revision += 1
        return result


class _BlockingRuntime(_ReadThenSuggestRuntime):
    def __init__(self, item_id: str, entered: Event, release: Event) -> None:
        super().__init__(item_id)
        self._entered = entered
        self._release = release

    def run(self, request, capability, *, execution: AgentExecutionConfig) -> RuntimeResult:
        def use_tools(tools):
            current_plan = next(tool for tool in tools if tool.name == "get_current_plan")
            self.tool_result = current_plan.invoke(day="all")

        capability.run_with_read_only_tools(use_tools)
        self._entered.set()
        assert self._release.wait(5), "test did not release simulated provider"
        return RuntimeResult(
            reply="Prepared but not applied.",
            outcome=AgentSuggestedChange(item_ref=self.item_id, safe_patch={"start_hour": 13.0}),
            candidate_options=(),
            observation=RuntimeObservation(
                trace_id="blocking-preview", round_count=1, total_tokens=1, total_elapsed_ms=1
            ),
        )


def _preview(service: RevisionBracketedPreview, seed: _Seed):
    return service.preview(
        PreviewRequest(
            trip_id=seed.trip_id,
            actor_membership_id=seed.membership_id,
            message="Move lunch one hour later",
            selected_item_id=seed.item_id,
        )
    )


def test_preview_uses_detached_fresh_reads_and_returns_a_non_authoritative_candidate(test_engine):
    seed = _seed(test_engine)
    runtime = _ReadThenSuggestRuntime(seed.item_id)
    service = RevisionBracketedPreview(session_factory=_factory(test_engine), runtime=runtime)

    preview = _preview(service, seed)

    assert preview.plan_id == seed.plan_id
    assert preview.plan_revision == 0
    assert preview.planning_facts_revision == 0
    assert preview.authoritative is False
    assert preview.candidate is not None
    assert preview.candidate.item_id == seed.item_id
    assert preview.candidate.patch == {"start_hour": 13.0}
    assert preview.candidate.classification.path.value == "notice"
    assert preview.telemetry_terminal is TerminalCategory.SUCCEEDED
    assert isinstance(runtime.tool_result, dict)
    with _factory(test_engine)() as db:
        assert db.get(Plan, seed.plan_id).revision == 0
        assert db.scalar(select(PlanItem.start_hour).where(PlanItem.id == seed.item_id)) == 12


@pytest.mark.parametrize("reply", ["A safe runtime fallback reply.", ""])
def test_preview_runtime_degradation_has_a_structured_degraded_terminal(test_engine, reply):
    seed = _seed(test_engine)

    class FailureRuntime:
        def run(self, request, capability, *, execution: AgentExecutionConfig) -> RuntimeResult:
            return RuntimeResult(
                reply=reply,
                outcome=AgentReplyOnly(),
                candidate_options=(),
                observation=RuntimeObservation(
                    trace_id="real-runtime-trace",
                    round_count=1,
                    total_tokens=None,
                    total_elapsed_ms=1,
                    failure=RuntimeFailure(
                        normalized_kind="provider_timeout", technical_kind="TimeoutError"
                    ),
                ),
            )

    preview = RevisionBracketedPreview(
        session_factory=_factory(test_engine), runtime=FailureRuntime()
    ).preview(
        PreviewRequest(
            trip_id=seed.trip_id,
            actor_membership_id=seed.membership_id,
            message="Move lunch later",
            selected_item_id=seed.item_id,
        )
    )

    assert preview.runtime_failure_category == "provider_timeout"

    assert preview.telemetry_terminal is TerminalCategory.DEGRADED_FALLBACK
    assert preview.candidate is None
    assert preview.candidate_options == ()
    assert preview.authoritative is False


def test_preview_closes_template_and_per_tool_sessions_before_runtime_returns(test_engine):
    seed = _seed(test_engine)
    sessions: list[Session] = []

    class TrackingSession(Session):
        b3_closed = False

        def close(self) -> None:
            self.b3_closed = True
            super().close()

    maker = sessionmaker(bind=test_engine, class_=TrackingSession, expire_on_commit=False, future=True)

    def tracked_factory() -> Session:
        session = maker()
        sessions.append(session)
        return session

    preview = RevisionBracketedPreview(
        session_factory=tracked_factory,
        runtime=_SessionObservingRuntime(seed.item_id, sessions),
    ).preview(
        PreviewRequest(seed.trip_id, seed.membership_id, "Move lunch", seed.item_id)
    )

    assert preview.candidate is not None
    # Capture, template construction, one tool invocation, classification, and
    # final verification each received short independently-owned Sessions.
    assert len(sessions) >= 5
    assert all(getattr(session, "b3_closed", False) for session in sessions)


def test_preview_discards_candidate_when_revision_changes_during_runtime(test_engine):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    service = RevisionBracketedPreview(
        session_factory=factory,
        runtime=_RevisionChangingRuntime(seed.item_id, factory, seed.plan_id),
    )

    with pytest.raises(StalePreview):
        _preview(service, seed)

    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.scalar(select(PlanItem.start_hour).where(PlanItem.id == seed.item_id)) == 12


def test_preview_holds_no_trip_root_lock_during_runtime_and_discards_after_real_writer(
    test_engine,
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    entered, release = Event(), Event()
    service = RevisionBracketedPreview(
        session_factory=factory,
        runtime=_BlockingRuntime(seed.item_id, entered, release),
    )
    outcome: Queue[object] = Queue()

    thread = Thread(
        target=lambda: _run_preview_in_thread(service, seed, outcome), daemon=True
    )
    thread.start()
    assert entered.wait(5), "Preview did not reach the simulated provider boundary"

    # A real root lock is immediately obtainable while Preview is still
    # waiting.  This is stronger than merely asserting no Python lock exists:
    # it proves no PostgreSQL Trip FOR UPDATE survives Runtime latency.
    with factory.begin() as db:
        locked = db.scalar(select(Trip).where(Trip.id == seed.trip_id).with_for_update(nowait=True))
        assert locked is not None

    result = protected_changes.execute_protected_change_proposal(
        session_factory=factory,
        scope=PreliminaryActorScope(
            trip_id=seed.trip_id,
            membership_id=seed.membership_id,
            credential_kind="account",
            expected_user_id=_seed_user_id(factory, seed),
        ),
        credential=CredentialProof.from_bearer_token(seed.account_token),
        command=protected_changes.ProtectedChangeProposal(
            item_id=seed.item_id,
            idempotency_key="preview-race-real-writer",
            expected_plan_revision=0,
            patch={"title": "Committed while preview waited"},
            request="A real protected writer commits",
            reason=None,
            alternatives=(),
        ),
        keyring=protected_changes.StaticHmacKeyring(HmacSigningKey("b3-test-v1", b"b3-test-key")),
    )
    assert result.replayed is False and result.plan_revision == 1
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert isinstance(outcome.get_nowait(), StalePreview)


def _run_preview_in_thread(service: RevisionBracketedPreview, seed: _Seed, outcome: Queue[object]) -> None:
    try:
        outcome.put(_preview(service, seed))
    except BaseException as exc:
        outcome.put(exc)


def _seed_user_id(factory, seed: _Seed) -> str:
    with factory() as db:
        membership = db.get(TripMembership, seed.membership_id)
        assert membership is not None and membership.user_id is not None
        return membership.user_id


def _enable_complete_b3_profile(monkeypatch) -> None:
    for gate in b3_profile_admission(
        environment="test", configured_gates={}
    ).required_gates:
        monkeypatch.setenv(gate, "1")
    monkeypatch.setenv("CADENSY_B3_PROTECTED_PREVIEW_APPLY", "1")
    monkeypatch.setenv("CADENSY_B2C_HMAC_KEY", "b3-preview-apply-test-key")
    monkeypatch.setenv("APP_ENV", "test")


def test_b3_http_gate_uses_effective_profile_and_is_fail_closed(monkeypatch):
    monkeypatch.setenv("CADENSY_B3_PROTECTED_PREVIEW_APPLY", "1")
    monkeypatch.setenv("APP_ENV", "test")
    assert api._b3_preview_apply_gate_state() == "incomplete-profile"

    _enable_complete_b3_profile(monkeypatch)
    assert api._b3_preview_apply_gate_state() == "enabled"

    monkeypatch.setenv("CADENSY_B2C_PROTECTED_BOOKING_WRITES", "false")
    assert api._b3_preview_apply_gate_state() == "incomplete-profile"

    _enable_complete_b3_profile(monkeypatch)
    monkeypatch.setenv("APP_ENV", "production")
    assert api._b3_preview_apply_gate_state() == "production"


def test_b3_http_preview_and_apply_compose_the_existing_protected_change_writer(
    test_engine, monkeypatch
):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    _enable_complete_b3_profile(monkeypatch)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)

    # Keep the application Preview real: only the external Runtime is a
    # deterministic fake. This exercises capture, tool use, deterministic
    # classification, final verification, and HTTP serialization end to end.
    runtime = _ReadThenSuggestRuntime(seed.item_id)
    monkeypatch.setattr(chat_service_module(), "build_chat_runtime", lambda **_kwargs: runtime)
    headers = {
        "Authorization": f"Bearer {seed.account_token}",
        "X-Trip-Id": seed.trip_id,
        "Idempotency-Key": "b3-apply-key",
        "X-Expected-Plan-Revision": "0",
    }
    with TestClient(api.app) as client:
        preview = client.post(
            f"/api/trips/{seed.trip_id}/chat",
            headers=headers,
            json={"message": "Move lunch", "item_id": seed.item_id},
        )
        first = client.post(
            f"/api/plans/items/{seed.item_id}/changes",
            headers=headers,
            json={"start_hour": 13.0, "request": "Move lunch"},
        )
        replay = client.post(
            f"/api/plans/items/{seed.item_id}/changes",
            headers=headers,
            json={"start_hour": 13.0, "request": "Move lunch"},
        )

    assert preview.status_code == 200
    assert preview.json()["preview"] == {
        "plan_id": seed.plan_id,
        "plan_revision": 0,
        "planning_facts_revision": 0,
        "authoritative": False,
    }
    assert first.status_code == replay.status_code == 200
    assert first.json() == replay.json()
    with factory() as db:
        assert db.get(Plan, seed.plan_id).revision == 1
        assert db.query(MutationIdempotency).filter(MutationIdempotency.trip_id == seed.trip_id).count() == 1


def chat_service_module():
    # Keep import indirection explicit: the FastAPI layer binds this module,
    # while the test replaces only the external Runtime/Preview call.
    from app.domain.chat import service as chat_service

    return chat_service


class _OptionsRuntime(_ReadThenSuggestRuntime):
    def __init__(self, item_id: str, foreign_item_id: str, removed_item_id: str) -> None:
        super().__init__(item_id)
        self._foreign_item_id = foreign_item_id
        self._removed_item_id = removed_item_id

    def run(self, request, capability, *, execution: AgentExecutionConfig) -> RuntimeResult:
        def use_tools(tools):
            next(tool for tool in tools if tool.name == "get_current_plan").invoke(day="all")

        capability.run_with_read_only_tools(use_tools)
        return RuntimeResult(
            reply="Here are current-plan options only.",
            outcome=AgentSuggestedChange(item_ref=self.item_id, safe_patch={"start_hour": 13.0}),
            candidate_options=(
                AgentCandidateOption("keep", "Keep", "Keep the Current Plan", "", "", self.item_id, {}),
                AgentCandidateOption("valid", "Move", "Move lunch", "", "", self.item_id, {"start_hour": 13.0}),
                AgentCandidateOption("foreign", "Foreign", "Foreign", "", "", self._foreign_item_id, {"start_hour": 13.0}),
                AgentCandidateOption("removed", "Removed", "Removed", "", "", self._removed_item_id, {"start_hour": 13.0}),
                AgentCandidateOption("unknown", "Unknown", "Unknown", "", "", "not-a-plan-item", {"start_hour": 13.0}),
                AgentCandidateOption("malformed", "Bad", "Bad", "", "", self.item_id, {"start_hour": "after lunch"}),
            ),
            observation=RuntimeObservation(
                trace_id="options-preview", round_count=1, total_tokens=1, total_elapsed_ms=1
            ),
        )


def _seed_foreign_and_removed_items(engine, seed: _Seed) -> tuple[str, str]:
    factory = _factory(engine)
    with factory.begin() as db:
        membership = db.get(TripMembership, seed.membership_id)
        assert membership is not None and membership.user_id is not None
        removed = PlanItem(
            plan_id=seed.plan_id,
            day_index=1,
            day_date=date(2026, 10, 20),
            start_hour=15,
            duration_min=60,
            title="Removed preview item",
            place="Chicago",
            settledness="removed",
        )
        foreign_trip = Trip(
            name="Foreign preview trip",
            destination="Detroit",
            created_by_user_id=membership.user_id,
        )
        db.add_all((removed, foreign_trip))
        db.flush()
        foreign_plan = Plan(trip_id=foreign_trip.id)
        db.add(foreign_plan)
        db.flush()
        foreign_item = PlanItem(
            plan_id=foreign_plan.id,
            day_index=1,
            day_date=date(2026, 10, 21),
            start_hour=12,
            duration_min=60,
            title="Foreign lunch",
            place="Detroit",
        )
        db.add(foreign_item)
        db.flush()
        _created_records.append((foreign_trip.id, membership.user_id))
        return foreign_item.id, removed.id


def test_preview_omits_options_not_scoped_to_the_captured_current_plan(test_engine):
    seed = _seed(test_engine)
    foreign_item_id, removed_item_id = _seed_foreign_and_removed_items(test_engine, seed)
    preview = RevisionBracketedPreview(
        session_factory=_factory(test_engine),
        runtime=_OptionsRuntime(seed.item_id, foreign_item_id, removed_item_id),
    ).preview(
        PreviewRequest(
            trip_id=seed.trip_id,
            actor_membership_id=seed.membership_id,
            message="Please show choices",
            selected_item_id=seed.item_id,
        )
    )

    # ``keep`` is the existing Option 2/no-change semantic. It is retained only
    # because its item is still in the captured authoritative Plan.
    assert [option.id for option in preview.candidate_options] == ["keep", "valid"]
    assert all(option.item_id == seed.item_id for option in preview.candidate_options)


def test_replacement_provenance_retains_the_legacy_full_replacement_contract():
    candidate = {
        "title": "New lunch",
        "place": "River North",
        "lat": 41.9,
        "lng": -87.6,
        "price_per_person": 21.0,
    }
    trusted = ({"tool": "find_replacement_place", "output": {"candidates": [candidate]}},)

    assert _replacement_provenance_allows({"start_hour": 13.0}, ()) is True
    assert _replacement_provenance_allows({"title": "New lunch"}, trusted) is False
    assert _replacement_provenance_allows({"price_per_person": 21.0}, trusted) is False
    assert _replacement_provenance_allows(candidate, trusted) is True
    assert _replacement_provenance_allows(candidate, ()) is False


def test_b3_http_omits_foreign_and_removed_candidate_options(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    foreign_item_id, removed_item_id = _seed_foreign_and_removed_items(test_engine, seed)
    _enable_complete_b3_profile(monkeypatch)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    monkeypatch.setattr(
        chat_service_module(),
        "build_chat_runtime",
        lambda **_kwargs: _OptionsRuntime(seed.item_id, foreign_item_id, removed_item_id),
    )

    with TestClient(api.app) as client:
        response = client.post(
            f"/api/trips/{seed.trip_id}/chat",
            headers={"Authorization": f"Bearer {seed.account_token}", "X-Trip-Id": seed.trip_id},
            json={"message": "Show choices", "item_id": seed.item_id},
        )

    assert response.status_code == 200
    assert [option["id"] for option in response.json()["candidate_options"]] == ["keep", "valid"]


def test_b3_http_preview_stales_after_real_writer_and_apply_rejects_old_revision(test_engine, monkeypatch):
    seed = _seed(test_engine)
    factory = _factory(test_engine)
    _enable_complete_b3_profile(monkeypatch)
    monkeypatch.setattr(api, "SessionLocal", factory)
    monkeypatch.setattr(db_session, "SessionLocal", factory)
    entered, release = Event(), Event()
    monkeypatch.setattr(
        chat_service_module(), "build_chat_runtime", lambda **_kwargs: _BlockingRuntime(seed.item_id, entered, release)
    )
    headers = {
        "Authorization": f"Bearer {seed.account_token}",
        "X-Trip-Id": seed.trip_id,
        "Idempotency-Key": "b3-preview-race-apply",
        "X-Expected-Plan-Revision": "0",
    }
    outcome: Queue[object] = Queue()

    def request_preview() -> None:
        with TestClient(api.app) as client:
            outcome.put(client.post(f"/api/trips/{seed.trip_id}/chat", headers=headers, json={"message": "Move lunch", "item_id": seed.item_id}))

    worker = Thread(target=request_preview, daemon=True)
    worker.start()
    assert entered.wait(5), "B.3 HTTP Preview did not reach its Runtime boundary"
    committed = protected_changes.execute_protected_change_proposal(
        session_factory=factory,
        scope=PreliminaryActorScope(seed.trip_id, seed.membership_id, "account", _seed_user_id(factory, seed)),
        credential=CredentialProof.from_bearer_token(seed.account_token),
        command=protected_changes.ProtectedChangeProposal(
            seed.item_id, "b3-preview-intervening-writer", 0, {"start_hour": 14.0}, "Intervening change", None, ()
        ),
        keyring=protected_changes.StaticHmacKeyring(HmacSigningKey("b3-race", b"b3-race")),
    )
    assert committed.plan_revision == 1
    release.set()
    worker.join(5)
    assert not worker.is_alive()
    preview_response = outcome.get_nowait()
    assert getattr(preview_response, "status_code") == 409

    with TestClient(api.app) as client:
        stale_apply = client.post(
            f"/api/plans/items/{seed.item_id}/changes",
            headers=headers,
            json={"start_hour": 13.0, "request": "Old preview"},
        )
    assert stale_apply.status_code == 409
