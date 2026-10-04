from __future__ import annotations

from contextlib import contextmanager
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.api import main as api
from app.agents import base
from app.agents import chat as chat_agent
from app.db.models import TripMembership, User
from app.domain.chat import service as chat_service
from app.domain.constraints.types import Classification, Path
from app.domain import auth


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
    user = db.get(User, membership.user_id)
    assert user is not None
    user.email = f"chat-safety-{membership.id}@example.test"
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


@pytest.mark.parametrize(
    ("message", "expected"),
    (
        ("下午3点", 15.0),
        ("下午3点半", 15.5),
        ("上午9点", 9.0),
        ("早上8点", 8.0),
        ("晚上7点", 19.0),
        ("中午12点", 12.0),
        ("凌晨1点", 1.0),
        ("3点", None),
        ("下午3:30", 15.5),
        ("十五点", None),
        ("3pm", 15.0),
        ("3:30 PM", 15.5),
    ),
)
def test_hour_from_text_parses_explicit_time_boundaries(message: str, expected: float | None):
    assert chat_agent._hour_from_text(message) == expected


def test_explain_replaces_claim_that_change_already_took_effect(monkeypatch):
    unsafe_reply = "可以，把 Art Institute 改到下午 3 点，已生效。"

    def fake_call_model(**kwargs):
        return {"reply": unsafe_reply}

    monkeypatch.setattr(base, "call_model", fake_call_model)
    verdict = Classification(
        path=Path.NOTICE,
        headline="No conflict",
        detail="This change can be submitted without a group round.",
    )

    reply = chat_agent.explain(
        chat_agent.ReplyInput(
            message="把周三的 Art Institute 改到下午 3 点",
            item=chat_agent.ItemContext(
                id="item-art",
                title="Art Institute of Chicago",
                place="Michigan Avenue",
                day_date=date(2026, 8, 19),
                start_hour=14.0,
                duration_min=150,
            ),
            patch={"start_hour": 15.0},
            verdict=verdict,
        )
    )

    assert "已生效" not in reply.text
    assert "点击 Apply 后才会提交" in reply.text
    assert "当前行程还没有改变" in reply.text


def test_chat_api_serializes_candidate_options_without_changing_proposed_change(
    monkeypatch, db: Session, full_trip: dict
):
    verdict = Classification(
        path=Path.NOTICE,
        headline="No conflict",
        detail="This change can be submitted without a group round.",
    )
    expected_proposed = {
        "item_id": full_trip["art"].id,
        "item_title": "Art Institute of Chicago",
        "patch": {"start_hour": 15.0},
        "verdict": api._classification_out(verdict),
    }

    def fake_respond_to_trip_chat(*args, **kwargs):
        return chat_service.ChatResult(
            reply="I can prepare a few options.",
            proposed_change=chat_service.ProposedChatChange(
                item_id=full_trip["art"].id,
                item_title="Art Institute of Chicago",
                patch={"start_hour": 15.0},
                verdict=verdict,
            ),
            candidate_options=(
                chat_service.ChatCandidateOption(
                    id="option-1",
                    label="Recommended",
                    title="Move the museum later",
                    body="Shift Art Institute of Chicago to 3:00 PM.",
                    tradeoff="Dinner has less buffer afterward.",
                    item_id=full_trip["art"].id,
                    patch={"start_hour": 15.0},
                ),
            ),
        )

    monkeypatch.setattr(chat_service, "respond_to_trip_chat", fake_respond_to_trip_chat)

    with _client(db) as client:
        response = client.post(
            f"/api/trips/{full_trip['trip'].id}/chat",
            headers=_account_headers(client, db, full_trip["me"], full_trip["trip"].id),
            json={"message": "周三排得太满了，能不能松一点"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["proposed_change"] == expected_proposed
    assert body["candidate_options"] == [
        {
            "id": "option-1",
            "label": "Recommended",
            "title": "Move the museum later",
            "body": "Shift Art Institute of Chicago to 3:00 PM.",
            "tradeoff": "Dinner has less buffer afterward.",
            "item_id": full_trip["art"].id,
            "patch": {"start_hour": 15.0},
        }
    ]
    serialized = str(body)
    assert "membership_id" not in serialized
    assert full_trip["me"].id not in serialized
    assert "No activities before 9:00 AM" not in serialized
