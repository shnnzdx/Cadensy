from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import main as api
from app.agents import base, legacy_runtime
from app.db.models import ChangeProposal, DecisionRound, PlanItem, TripMembership, User
from app.domain import auth


@pytest.fixture(autouse=True)
def _adapter_tool_surface_without_cross_transaction_fixture_reads(monkeypatch):
    """Keep HTTP behavior fixtures out of an independent worker Session.

    The route's request Session remains separate from the adapter worker. These
    tests assert HTTP/application semantics with fake Agent responses; the
    read-tool capability and worker Session lifecycle are covered in the
    dedicated lifecycle suite.
    """

    monkeypatch.setattr(
        legacy_runtime, "build_read_only_trip_tools", lambda *_args, **_kwargs: ()
    )


@contextmanager
def _client(db: Session):
    api.app.dependency_overrides[api.get_session] = lambda: db
    try:
        with TestClient(api.app) as client:
            yield client
    finally:
        api.app.dependency_overrides.clear()


def _account_headers(
    client: TestClient, db: Session, membership: TripMembership, trip_id: str
) -> dict[str, str]:
    """Authenticate this HTTP fixture through the public account-bearer flow."""
    user = db.get(User, membership.user_id)
    assert user is not None
    user.email = f"chat-{membership.id}@example.test"
    user.password_hash = auth.hash_password("correct-horse")
    db.flush()
    login = client.post(
        "/api/auth/login",
        json={"email": user.email, "password": "correct-horse"},
    )
    assert login.status_code == 200
    return {
        "Authorization": f"Bearer {login.json()['token']}",
        "X-Trip-Id": trip_id,
    }


def _snapshot(db: Session, model) -> list[tuple]:
    rows = db.scalars(select(model).order_by(model.id)).all()
    cols = list(model.__table__.columns)
    return [
        tuple((col.name, _stable(getattr(row, col.name))) for col in cols)
        for row in rows
    ]


def _stable(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return repr(value)
    return value


def test_chat_rejects_raw_membership_header_as_an_explicit_security_negative(
    monkeypatch, db: Session, full_trip: dict
):
    calls: list[dict] = []
    monkeypatch.setattr(base, "call_agent", lambda **kwargs: calls.append(kwargs))

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers={"X-Membership-Id": full_trip["me"].id},
            json={"message": "Move this to 3 PM", "item_id": full_trip["art"].id},
        )

    assert response.status_code == 401
    assert calls == []


def test_chat_mock_flow_returns_reply_change_and_verdict(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I can prepare moving this later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 15.5},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "Move this to 3:30 PM",
                "item_id": full_trip["art"].id,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert not re.search(r"[\u4e00-\u9fff]", body["reply"])
    assert body["proposed_change"]["item_id"] == full_trip["art"].id
    assert body["proposed_change"]["item_title"] == "Art Institute of Chicago"
    assert body["proposed_change"]["patch"]["start_hour"] == 15.5
    assert body["proposed_change"]["verdict"]["path"] == "notice"


def test_chat_resolves_delegated_downtown_cafe_replacement_from_history(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    lula = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=1,
        day_date=date(2026, 8, 14),
        start_hour=19.0,
        duration_min=90,
        title="Lula Cafe",
        place="Logan Square",
        price_per_person=35.0,
        lat=41.9265,
        lng=-87.7085,
        settledness="loose",
    )
    db.add(lula)
    db.flush()

    def fake_call_agent(**kwargs):
        assert kwargs["history"] == (
            {"role": "user", "content": "replace Lula Cafe with another place downtown"},
            {"role": "assistant", "content": "Do you want a specific place?"},
            {"role": "user", "content": "你选个在密歇根大道的咖啡店"},
        )
        assert "Lula Cafe" in kwargs["user"]
        return base.AgentRunResult(
            content=(
                "I suggest replacing Lula Cafe with Starbucks Reserve Chicago "
                "Roastery. It will only be submitted after you click Apply."
            ),
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "find_replacement_place",
                    "arguments": {"item_id": lula.id, "keywords": ["michigan", "cafe"]},
                    "output": {
                        "candidates": [
                            {
                                "title": "Starbucks Reserve Chicago Roastery",
                                "place": "Michigan Avenue",
                                "price_per_person": 15.0,
                                "duration_min": 90,
                                "opens": 7.0,
                                "closes": 22.0,
                                "lat": 41.8942,
                                "lng": -87.6243,
                                "tags": ["food", "coffee", "cafe", "evening"],
                            }
                        ]
                    },
                    "guard_rejected": False,
                },
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": lula.id},
                        "proposed_patch": {
                            "title": "Starbucks Reserve Chicago Roastery",
                            "place": "Michigan Avenue",
                            "price_per_person": 15.0,
                            "lat": 41.8942,
                            "lng": -87.6243,
                        },
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "随便",
                "item_id": lula.id,
                "history": [
                    {"role": "user", "text": "replace Lula Cafe with another place downtown"},
                    {"role": "assistant", "text": "Do you want a specific place?"},
                    {"role": "user", "text": "你选个在密歇根大道的咖啡店"},
                ],
            },
        )

    assert response.status_code == 200
    proposed = response.json()["proposed_change"]
    assert proposed is not None
    assert proposed["item_id"] == lula.id
    assert proposed["patch"] == {
        "title": "Starbucks Reserve Chicago Roastery",
        "place": "Michigan Avenue",
        "price_per_person": 15.0,
        "lat": 41.8942,
        "lng": -87.6243,
    }
    assert "click Apply" in response.json()["reply"]
    assert "has changed" not in response.json()["reply"].lower()


def test_chat_does_not_choose_random_place_without_a_preference(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="What would you like to change about that item?",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "Replace this",
                "item_id": full_trip["art"].id,
            },
        )

    assert response.status_code == 200
    assert response.json()["proposed_change"] is None
    assert "what would you like to change" in response.json()["reply"].lower()


def test_chat_is_read_only(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I can prepare that change.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 15.5},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    before = {
        PlanItem: _snapshot(db, PlanItem),
        DecisionRound: _snapshot(db, DecisionRound),
        ChangeProposal: _snapshot(db, ChangeProposal),
    }

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "Move this to 3:30 PM",
                "item_id": full_trip["art"].id,
            },
        )

    assert response.status_code == 200
    assert response.json()["proposed_change"]["verdict"]["path"] == "notice"
    assert {
        PlanItem: _snapshot(db, PlanItem),
        DecisionRound: _snapshot(db, DecisionRound),
        ChangeProposal: _snapshot(db, ChangeProposal),
    } == before


def test_chat_asks_when_it_cannot_identify_the_item(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="Which itinerary item should I check for that shopping change?",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={"message": "I want to go shopping on Tuesday afternoon"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed_change"] is None
    assert "which itinerary item" in body["reply"].lower()
    assert not re.search(r"[\u4e00-\u9fff]", body["reply"])


def test_chat_prompt_does_not_include_identity_or_private_wording(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    prompts: list[str] = []

    def capturing_call_agent(**kwargs):
        prompts.append(kwargs["user"])
        return base.AgentRunResult(
            content="I can prepare that change.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 8.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", capturing_call_agent)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "Move Art Institute to 8 AM",
                "item_id": full_trip["art"].id,
            },
        )

    assert response.status_code == 200
    blob = "\n".join(prompts)
    assert "No activities before 9:00 AM" not in blob
    for membership in full_trip["members"]:
        assert membership.id not in blob
    assert "M0" not in blob
    assert "M1" not in blob


def test_chat_degrades_when_deepseek_is_unavailable_and_classify_still_works(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.delenv("MOCK_AI", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with _client(db) as client:
        chat_response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "Move this to 3:30 PM",
                "item_id": full_trip["art"].id,
            },
        )
        classify_response = client.post(
            f"/api/plans/items/{full_trip['art'].id}/classify",
                headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={"start_hour": 15.5, "request": "Move this to 3:30 PM"},
        )

    assert chat_response.status_code == 200
    assert chat_response.json()["proposed_change"] is None
    assert "could not check that reliably" in chat_response.json()["reply"].lower()
    assert "art institute of chicago" in chat_response.json()["reply"].lower()
    assert "current plan has not changed" in chat_response.json()["reply"].lower()
    assert not re.search(r"[\u4e00-\u9fff]", chat_response.json()["reply"])
    assert classify_response.status_code == 200
    assert classify_response.json()["path"] == "notice"


def test_chat_selecting_previous_candidate_is_read_only(
    monkeypatch, db: Session, full_trip: dict
):
    def unexpected_call_agent(**kwargs):
        raise AssertionError("call_agent should not run for follow-up option selection")

    monkeypatch.setattr(base, "call_agent", unexpected_call_agent)

    before = {
        PlanItem: _snapshot(db, PlanItem),
        DecisionRound: _snapshot(db, DecisionRound),
        ChangeProposal: _snapshot(db, ChangeProposal),
    }

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "Option 2.",
                "history": [
                    {
                        "role": "assistant",
                        "text": "Here are a few options.",
                        "candidate_options": [
                            {
                                "id": "keep",
                                "label": "Keep current",
                                "title": "Keep current",
                                "body": "No change.",
                                "tradeoff": "Wednesday stays busy.",
                                "item_id": full_trip["art"].id,
                                "patch": {},
                            },
                            {
                                "id": "move-later",
                                "label": "Move later",
                                "title": "Move the museum later",
                                "body": "Shift Art Institute of Chicago to 3:00 PM.",
                                "tradeoff": "Dinner has less buffer afterward.",
                                "item_id": full_trip["art"].id,
                                "patch": {"start_hour": 15.0},
                            },
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed_change"] is not None
    assert body["proposed_change"]["item_id"] == full_trip["art"].id
    assert body["proposed_change"]["patch"] == {"start_hour": 15.0}
    assert body["proposed_change"]["verdict"]["path"] == "notice"
    assert body["candidate_options"] == []
    assert "click Apply" in body["reply"]
    assert {
        PlanItem: _snapshot(db, PlanItem),
        DecisionRound: _snapshot(db, DecisionRound),
        ChangeProposal: _snapshot(db, ChangeProposal),
    } == before


def test_chat_bare_number_selects_previous_candidate_without_running_agent(
    monkeypatch, db: Session, full_trip: dict
):
    def unexpected_call_agent(**kwargs):
        raise AssertionError("call_agent should not run for follow-up option selection")

    monkeypatch.setattr(base, "call_agent", unexpected_call_agent)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "2",
                "history": [
                    {
                        "role": "assistant",
                        "text": "Here are a few options.",
                        "candidate_options": [
                            {
                                "id": "keep",
                                "label": "Keep current",
                                "title": "Keep current",
                                "body": "No change.",
                                "tradeoff": "Wednesday stays busy.",
                                "item_id": full_trip["art"].id,
                                "patch": {},
                            },
                            {
                                "id": "move-later",
                                "label": "Move later",
                                "title": "Move the museum later",
                                "body": "Shift Art Institute of Chicago to 3:00 PM.",
                                "tradeoff": "Dinner has less buffer afterward.",
                                "item_id": full_trip["art"].id,
                                "patch": {"start_hour": 15.0},
                            },
                        ],
                    }
                ],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed_change"] is not None
    assert body["proposed_change"]["item_id"] == full_trip["art"].id
    assert body["proposed_change"]["patch"] == {"start_hour": 15.0}
    assert body["proposed_change"]["verdict"]["path"] == "notice"
    assert body["candidate_options"] == []
    assert "click Apply" in body["reply"]


def test_chat_stale_selected_item_fails_safely_without_a_404(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("call_agent should not run")),
    )

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "Move this one later",
                "item_id": "missing-item-id",
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed_change"] is None
    assert "which item" in body["reply"].lower()


def test_chat_selected_item_relative_time_request_uses_selected_item(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        assert 'selected on screen' in kwargs["user"]
        assert full_trip["art"].title in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving that to 4 PM.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 16.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "move to 4pm",
                "item_id": full_trip["art"].id,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed_change"] is not None
    assert body["proposed_change"]["item_id"] == full_trip["art"].id
    assert body["proposed_change"]["patch"] == {"start_hour": 16.0}


def test_chat_change_time_clarification_response_is_plain_text(
    monkeypatch, db: Session, full_trip: dict
):
    grand_park = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=47,
        day_date=date(2026, 9, 29),
        start_hour=10.0,
        duration_min=90,
        title="Gloria Molina Grand Park",
        place="Downtown Los Angeles",
        settledness="loose",
    )
    db.add(grand_park)
    db.flush()

    monkeypatch.setattr(
        base,
        "call_agent",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("call_agent should not run")),
    )

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
                headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={
                "message": "change time",
                "item_id": grand_park.id,
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed_change"] is None
    assert body["candidate_options"] == []
    assert body["reply"] == (
        "Gloria Molina Grand Park is currently scheduled for 10:00 AM on "
        "September 29. What time would you like to move it to?"
    )
    assert "**" not in body["reply"]
    assert "_" not in body["reply"]
