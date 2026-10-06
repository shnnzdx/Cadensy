from __future__ import annotations

import time
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import base, legacy_runtime
from app.db.models import ChangeProposal, DecisionRound, PlanChange, PlanItem, Vote
from app.domain.chat import service as chat_service
from app.domain.constraints.types import Path, Settledness


@pytest.fixture(autouse=True)
def _adapter_tool_surface_without_cross_transaction_fixture_reads(monkeypatch):
    """Keep Chat semantics tests at the adapter's public tool seam.

    The real adapter constructs scoped tools in its independent worker Session.
    These tests deliberately keep their fixtures inside pytest's uncommitted
    transaction, so they provide an empty read-only surface rather than using
    the request Session as a false concurrency proof. Tool/session ownership is
    exercised separately by the PR-01C lifecycle tests.
    """

    monkeypatch.setattr(
        legacy_runtime, "build_read_only_trip_tools", lambda *_args, **_kwargs: ()
    )


def test_clear_change_uses_agent_classification_result(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")
    calls = []

    def fake_call_agent(**kwargs):
        calls.append(kwargs)
        return base.AgentRunResult(
            content="I can prepare moving this to 3 PM.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 15.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert calls
    assert "Art Institute of Chicago" in calls[0]["user"]
    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch["start_hour"] == 15.0
    assert result.candidate_options == ()


def test_clear_named_change_without_selected_item_uses_agent_classification_result(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    calls = []

    def fake_call_agent(**kwargs):
        calls.append(kwargs)
        return base.AgentRunResult(
            content="I can prepare moving Art Institute to 3 PM.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 15.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="把周三的 Art Institute 改到下午 3 点",
    )

    assert calls
    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch["start_hour"] == 15.0


def test_fuzzy_change_uses_agent_branch(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")
    calls = []

    def fake_call_agent(**kwargs):
        calls.append(kwargs)
        return base.AgentRunResult(
            content="Wednesday can be loosened by moving the museum.",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="周三排得太满了，能不能松一点",
    )

    assert calls
    assert "Wednesday can be loosened" in result.reply
    assert result.proposed_change is None


def test_agent_reply_internal_reasoning_is_not_shown(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content=(
                "The user wants to replace the selected restaurant.\n\n"
                "Let me check whether a candidate exists.\n\n"
                "I should not invent a venue.\n\n"
                "I could not find a matching replacement venue. Try a broader search term."
            ),
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="replace with East Asia Hotel",
        item_id=full_trip["dinner"].id,
    )

    assert "Let me" not in result.reply
    assert "I should" not in result.reply
    assert "The user wants" not in result.reply
    assert result.reply == "I could not find a matching replacement venue. Try a broader search term."


def test_selected_item_replacement_search_phrases_stay_grounded_to_selection(
    db: Session, full_trip: dict
):
    selected = full_trip["dinner"]

    for message in (
        "search East Asia Hotel",
        "find East Asia Hotel",
        "look for East Asia Hotel",
    ):
        reference = chat_service._resolve_item_reference(
            message,
            [full_trip["art"], full_trip["dinner"]],
            selected,
            (),
        )

        assert reference.status == "matched"
        assert reference.source == "selected"
        assert reference.item == selected


def test_cross_day_question_without_selected_item_uses_agent_branch(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    calls = []

    def fake_call_agent(**kwargs):
        calls.append(kwargs)
        return base.AgentRunResult(
            content="The Art Institute can be checked against Thursday.",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="周三的 Art Institute 能不能挪到周四",
    )

    assert calls
    assert "Thursday" in result.reply


def test_agent_failure_degrades_without_raising(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")

    def fail_agent(**kwargs):
        raise RuntimeError("agent failed")

    monkeypatch.setattr(base, "call_agent", fail_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="周三排得太满了，能不能松一点",
    )

    assert result.proposed_change is None
    assert "shared itinerary" in result.reply.lower() or "specific block" in result.reply.lower()


def test_agent_timeout_degrades_without_waiting(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setattr(chat_service, "CHAT_AGENT_TIMEOUT_SECONDS", 0.01)

    def slow_agent(**kwargs):
        time.sleep(0.2)
        return base.AgentRunResult(
            content="too late",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=200.0,
        )

    monkeypatch.setattr(base, "call_agent", slow_agent)
    started = time.perf_counter()

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="周三排得太满了，能不能松一点",
    )

    assert (time.perf_counter() - started) < 0.15
    assert result.proposed_change is None
    assert "shared itinerary" in result.reply.lower() or "specific block" in result.reply.lower()


def test_history_is_passed_to_call_agent(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")
    calls = []

    def fake_call_agent(**kwargs):
        calls.append(kwargs)
        return base.AgentRunResult(
            content="I checked the plan.",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Can we move it?",
        item_id=full_trip["art"].id,
        history=(
            chat_service.chat_agent.HistoryTurn(role="user", text="I mean the museum."),
            chat_service.chat_agent.HistoryTurn(role="assistant", text="Which part?"),
        ),
    )

    assert calls[0]["history"] == (
        {"role": "user", "content": "I mean the museum."},
        {"role": "assistant", "content": "Which part?"},
    )


def test_selected_item_is_in_agent_user_message(monkeypatch, db: Session, full_trip: dict):
    monkeypatch.setenv("MOCK_AI", "1")
    calls = []

    def fake_call_agent(**kwargs):
        calls.append(kwargs)
        assert "Respond in plain text only." in kwargs["system"]
        return base.AgentRunResult(
            content="I checked the selected item.",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this later",
        item_id=full_trip["art"].id,
    )

    assert "Art Institute of Chicago" in calls[0]["user"]
    assert "Move this later" in calls[0]["user"]


def test_change_time_clarification_uses_plain_text_without_running_agent(
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

    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="change time",
        item_id=grand_park.id,
    )

    assert result.proposed_change is None
    assert result.candidate_options == ()
    assert result.reply == (
        "Gloria Molina Grand Park is currently scheduled for 10:00 AM on "
        "September 29. What time would you like to move it to?"
    )
    assert "**" not in result.reply
    assert "_" not in result.reply


def test_agent_reply_claiming_chinese_change_completed_is_replaced(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="已生效，我已经把行程改好了。",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 15.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="把这个改到下午 3 点",
        item_id=full_trip["art"].id,
    )

    assert "已生效" not in result.reply
    assert "改好了" not in result.reply
    assert "还没有改变" in result.reply
    assert "点击 Apply" in result.reply
    assert result.proposed_change is not None


def test_agent_reply_claiming_english_change_completed_is_replaced(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="All set — the change took effect.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 15.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3 PM",
        item_id=full_trip["art"].id,
    )

    assert "took effect" not in result.reply.lower()
    assert "all set" not in result.reply.lower()
    assert "has not changed" in result.reply
    assert "click Apply" in result.reply
    assert result.proposed_change is not None


def test_agent_multi_options_return_candidates_without_promoting_a_proposed_change(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="Here are options to loosen Wednesday.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "propose_options",
                    "arguments": {"day": "all"},
                    "output": {
                        "options": [
                            {
                                "id": "keep",
                                "label": "Keep current",
                                "title": "Keep current",
                                "body": "No change.",
                                "tradeoff": "Still busy.",
                                "item_id": full_trip["art"].id,
                                "patch": {},
                            },
                            {
                                "id": "shorten",
                                "label": "Shorten",
                                "title": "Shorten museum",
                                "body": "Cut the museum visit.",
                                "tradeoff": "Less museum time.",
                                "item_id": full_trip["art"].id,
                                "patch": {"duration_min": 90},
                            },
                            {
                                "id": "split",
                                "label": "Split up",
                                "title": "Split up",
                                "body": "Some rest, some go.",
                                "tradeoff": "Group separates.",
                                "item_id": full_trip["art"].id,
                                "patch": {"split": True},
                            },
                        ]
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Wednesday is too packed. What can we do?",
    )

    assert result.proposed_change is None
    assert len(result.candidate_options) == 3
    assert [option.id for option in result.candidate_options] == ["keep", "shorten", "split"]

def test_agent_explicit_change_takes_priority_over_followup_options(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="Moving the museum conflicts, and here are options.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"day_date": "2026-08-16"},
                    },
                    "guard_rejected": False,
                },
                {
                    "tool": "propose_options",
                    "arguments": {},
                    "output": {
                        "options": [
                            {
                                "id": "shorten-other",
                                "label": "Shorten",
                                "title": "Shorten dinner",
                                "body": "Shorten another item.",
                                "tradeoff": "Less time.",
                                "item_id": full_trip["dinner"].id,
                                "patch": {"duration_min": 60},
                            }
                        ]
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="周三的 Art Institute 能不能挪到周四",
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch == {"day_date": date(2026, 8, 16)}
    assert len(result.candidate_options) == 1

def test_authoritative_chat_revalidation_upgrades_overlap_to_round(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I can prepare moving this to 7 PM.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 19.0},
                        "classification": {"path": "notice"},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 7 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.verdict.path is Path.ROUND


def test_authoritative_chat_revalidation_keeps_settled_cross_day_as_reopen_round(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    full_trip["art"].settledness = Settledness.SETTLED.value
    db.flush()

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I can check moving this to tomorrow.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"day_date": "2026-08-16"},
                        "classification": {"path": "notice"},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to tomorrow",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.verdict.path is Path.REOPEN_ROUND


def test_authoritative_chat_revalidation_keeps_booked_change_as_confirm(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I can prepare moving dinner later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["dinner"].id},
                        "proposed_patch": {"start_hour": 20.0},
                        "classification": {"path": "round"},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move dinner to 8 PM",
        item_id=full_trip["dinner"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.verdict.path is Path.CONFIRM


def test_authoritative_chat_revalidation_keeps_required_constraint_as_confirm(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I can prepare moving this to 8 AM.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 8.0},
                        "classification": {"path": "notice"},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 8 AM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.verdict.path is Path.CONFIRM


def test_hallucinated_replacement_candidate_does_not_become_a_proposed_change(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I found another cafe.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "find_replacement_place",
                    "arguments": {"item_id": full_trip["art"].id, "keywords": ["cafe"]},
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
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {
                            "title": "Imaginary River North Cafe",
                            "place": "River North",
                            "price_per_person": 18.0,
                            "lat": 41.9001,
                            "lng": -87.6301,
                        },
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Replace this with another cafe",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None


def test_unknown_price_replacement_candidate_can_become_a_proposed_change(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="Echo Park Lake looks like a calmer replacement.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "find_replacement_place",
                    "arguments": {"item_id": full_trip["art"].id, "keywords": ["relaxing"]},
                    "output": {
                        "candidates": [
                            {
                                "candidate_id": "geoapify:echo-park-lake",
                                "title": "Echo Park Lake",
                                "place": "751 Echo Park Ave",
                                "price_per_person": None,
                                "opening_hours": "Mo-Su 05:00-22:30",
                                "lat": 34.0782,
                                "lng": -118.2606,
                                "tags": ["parks", "park", "relaxing"],
                            }
                        ]
                    },
                    "guard_rejected": False,
                },
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {
                            "title": "Echo Park Lake",
                            "place": "751 Echo Park Ave",
                            "lat": 34.0782,
                            "lng": -118.2606,
                        },
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Replace this with something more relaxing",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.patch == {
        "title": "Echo Park Lake",
        "place": "751 Echo Park Ave",
        "lat": 34.0782,
        "lng": -118.2606,
    }


def test_unknown_price_replacement_candidate_rejects_an_invented_price(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        return base.AgentRunResult(
            content="I found a calmer option.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "find_replacement_place",
                    "arguments": {"item_id": full_trip["art"].id, "keywords": ["relaxing"]},
                    "output": {
                        "candidates": [
                            {
                                "candidate_id": "geoapify:echo-park-lake",
                                "title": "Echo Park Lake",
                                "place": "751 Echo Park Ave",
                                "price_per_person": None,
                                "lat": 34.0782,
                                "lng": -118.2606,
                                "tags": ["parks", "park", "relaxing"],
                            }
                        ]
                    },
                    "guard_rejected": False,
                },
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {
                            "title": "Echo Park Lake",
                            "place": "751 Echo Park Ave",
                            "price_per_person": 25.0,
                            "lat": 34.0782,
                            "lng": -118.2606,
                        },
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Replace this with something more relaxing",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None

def _history_option(
    *,
    id: str,
    label: str,
    title: str,
    body: str,
    tradeoff: str,
    item_id: str,
    patch: dict,
):
    return chat_service.chat_agent.HistoryCandidateOption(
        id=id,
        label=label,
        title=title,
        body=body,
        tradeoff=tradeoff,
        item_id=item_id,
        patch=patch,
    )


def _history_with_options(*options):
    return (
        chat_service.chat_agent.HistoryTurn(role="user", text="Wednesday is too packed."),
        chat_service.chat_agent.HistoryTurn(
            role="assistant",
            text="Here are a few options.",
            candidate_options=tuple(options),
        ),
    )


def _unexpected_call_agent(**kwargs):
    raise AssertionError("call_agent should not run for follow-up option selection")


def test_followup_option_number_resolves_previous_candidate_without_calling_agent(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Option 2.",
        history=_history_with_options(
            _history_option(
                id="keep",
                label="Keep current",
                title="Keep current",
                body="No change.",
                tradeoff="Wednesday stays busy.",
                item_id=full_trip["art"].id,
                patch={},
            ),
            _history_option(
                id="move-later",
                label="Move later",
                title="Move the museum later",
                body="Shift Art Institute of Chicago to 3:00 PM.",
                tradeoff="Dinner has less buffer afterward.",
                item_id=full_trip["art"].id,
                patch={"start_hour": 15.0},
            ),
            _history_option(
                id="move-to-thursday",
                label="Move to Thursday",
                title="Move to Thursday",
                body="Shift Art Institute of Chicago to Thursday.",
                tradeoff="The visit moves to another day.",
                item_id=full_trip["art"].id,
                patch={"day_date": date(2026, 8, 20)},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch == {"start_hour": 15.0}
    assert result.proposed_change.verdict.path is Path.NOTICE
    assert result.candidate_options == ()
    assert "click Apply" in result.reply


def test_followup_bare_number_resolves_previous_candidate_without_calling_agent(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="2",
        history=_history_with_options(
            _history_option(
                id="keep",
                label="Keep current",
                title="Keep current",
                body="No change.",
                tradeoff="Wednesday stays busy.",
                item_id=full_trip["art"].id,
                patch={},
            ),
            _history_option(
                id="move-later",
                label="Move later",
                title="Move the museum later",
                body="Shift Art Institute of Chicago to 3:00 PM.",
                tradeoff="Dinner has less buffer afterward.",
                item_id=full_trip["art"].id,
                patch={"start_hour": 15.0},
            ),
            _history_option(
                id="move-to-thursday",
                label="Move to Thursday",
                title="Move to Thursday",
                body="Shift Art Institute of Chicago to Thursday.",
                tradeoff="The visit moves to another day.",
                item_id=full_trip["art"].id,
                patch={"day_date": date(2026, 8, 20)},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch == {"start_hour": 15.0}
    assert result.proposed_change.verdict.path is Path.NOTICE
    assert result.candidate_options == ()
    assert "click Apply" in result.reply


def test_followup_ordinal_resolves_same_previous_candidate(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="I'll take the second one.",
        history=_history_with_options(
            _history_option(
                id="keep",
                label="Keep current",
                title="Keep current",
                body="No change.",
                tradeoff="Wednesday stays busy.",
                item_id=full_trip["art"].id,
                patch={},
            ),
            _history_option(
                id="move-later",
                label="Move later",
                title="Move the museum later",
                body="Shift Art Institute of Chicago to 3:00 PM.",
                tradeoff="Dinner has less buffer afterward.",
                item_id=full_trip["art"].id,
                patch={"start_hour": 15.0},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.patch == {"start_hour": 15.0}
    assert result.proposed_change.verdict.path is Path.NOTICE


def test_followup_semantic_selection_resolves_unique_previous_candidate(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move it to Thursday.",
        history=_history_with_options(
            _history_option(
                id="move-later",
                label="Move later",
                title="Move the museum later",
                body="Shift Art Institute of Chicago to 3:00 PM.",
                tradeoff="Dinner has less buffer afterward.",
                item_id=full_trip["art"].id,
                patch={"start_hour": 15.0},
            ),
            _history_option(
                id="move-to-thursday",
                label="Move to Thursday",
                title="Move to Thursday",
                body="Shift Art Institute of Chicago to Thursday, August 20.",
                tradeoff="The visit moves to another day.",
                item_id=full_trip["art"].id,
                patch={"day_date": date(2026, 8, 20)},
            ),
            _history_option(
                id="shorten",
                label="Shorten",
                title="Shorten the museum visit",
                body="Cut the museum down to 90 minutes.",
                tradeoff="Less museum time.",
                item_id=full_trip["art"].id,
                patch={"duration_min": 90},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.patch == {"day_date": date(2026, 8, 20)}


def test_followup_full_option_text_resolves_previous_candidate(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message=(
            "2. Move the museum later - Shift Art Institute of Chicago to 3:00 PM."
        ),
        history=_history_with_options(
            _history_option(
                id="keep",
                label="Keep current",
                title="Keep current",
                body="No change.",
                tradeoff="Wednesday stays busy.",
                item_id=full_trip["art"].id,
                patch={},
            ),
            _history_option(
                id="move-later",
                label="Move later",
                title="Move the museum later",
                body="Shift Art Institute of Chicago to 3:00 PM.",
                tradeoff="Dinner has less buffer afterward.",
                item_id=full_trip["art"].id,
                patch={"start_hour": 15.0},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.patch == {"start_hour": 15.0}
    assert result.proposed_change.verdict.path is Path.NOTICE


def test_followup_ambiguous_acceptance_does_not_silently_choose(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="That sounds good.",
        history=_history_with_options(
            _history_option(
                id="move-later",
                label="Move later",
                title="Move the museum later",
                body="Shift Art Institute of Chicago to 3:00 PM.",
                tradeoff="Dinner has less buffer afterward.",
                item_id=full_trip["art"].id,
                patch={"start_hour": 15.0},
            ),
            _history_option(
                id="move-to-thursday",
                label="Move to Thursday",
                title="Move to Thursday",
                body="Shift Art Institute of Chicago to Thursday.",
                tradeoff="The visit moves to another day.",
                item_id=full_trip["art"].id,
                patch={"day_date": date(2026, 8, 20)},
            ),
        ),
    )

    assert result.proposed_change is None
    assert [option.id for option in result.candidate_options] == ["move-later", "move-to-thursday"]
    assert "choose the option number" in result.reply.lower()


def test_followup_selected_candidate_reclassifies_overlap_to_round(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Option 1.",
        history=_history_with_options(
            _history_option(
                id="move-late",
                label="Move later",
                title="Move the museum to 7 PM",
                body="Shift Art Institute of Chicago to 7:00 PM.",
                tradeoff="It overlaps dinner.",
                item_id=full_trip["art"].id,
                patch={"start_hour": 19.0},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.verdict.path is Path.ROUND


def test_followup_selected_candidate_reclassifies_settled_to_reopen_round(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)
    full_trip["art"].settledness = Settledness.SETTLED.value
    db.flush()

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Option 1.",
        history=_history_with_options(
            _history_option(
                id="move-to-thursday",
                label="Move to Thursday",
                title="Move to Thursday",
                body="Shift Art Institute of Chicago to Thursday.",
                tradeoff="The visit moves to another day.",
                item_id=full_trip["art"].id,
                patch={"day_date": date(2026, 8, 20)},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.verdict.path is Path.REOPEN_ROUND


def test_followup_selected_candidate_reclassifies_booked_to_confirm(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Option 1.",
        history=_history_with_options(
            _history_option(
                id="move-dinner-later",
                label="Move dinner later",
                title="Move dinner to 8 PM",
                body="Shift dinner to 8:00 PM.",
                tradeoff="It changes a booked item.",
                item_id=full_trip["dinner"].id,
                patch={"start_hour": 20.0},
            ),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.verdict.path is Path.CONFIRM


def test_selected_item_pronoun_reference_creates_a_proposed_change(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        assert 'selected on screen' in kwargs["user"]
        assert full_trip["art"].title in kwargs["user"]
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
                        "proposed_patch": {"start_hour": 15.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this one later",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch == {"start_hour": 15.0}


def test_selected_item_relative_time_request_without_pronoun_uses_selected_item(
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

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="move to 4pm",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch == {"start_hour": 16.0}


def test_explicit_item_reference_overrides_the_selected_item(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    cafe = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=2,
        day_date=date(2026, 8, 15),
        start_hour=11.0,
        duration_min=60,
        title="Blue Bottle Cafe",
        place="Wicker Park",
        settledness="loose",
    )
    db.add(cafe)
    db.flush()

    def fake_call_agent(**kwargs):
        assert 'selected on screen' not in kwargs["user"]
        assert full_trip["art"].title in kwargs["user"]
        assert cafe.title not in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving the contemporary art museum later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["art"].id},
                        "proposed_patch": {"start_hour": 15.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Actually move the museum later",
        item_id=cafe.id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["art"].id
    assert result.proposed_change.patch == {"start_hour": 15.0}


def test_unique_generic_item_reference_resolves_without_a_selected_item(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    cafe = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=2,
        day_date=date(2026, 8, 15),
        start_hour=11.0,
        duration_min=60,
        title="Blue Bottle Cafe",
        place="Wicker Park",
        settledness="loose",
    )
    db.add(cafe)
    db.flush()

    def fake_call_agent(**kwargs):
        assert cafe.title in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving the cafe later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": cafe.id},
                        "proposed_patch": {"start_hour": 12.5},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move the cafe later",
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == cafe.id
    assert result.proposed_change.patch == {"start_hour": 12.5}


def test_ambiguous_generic_item_reference_asks_for_clarification(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)
    db.add_all(
        [
            PlanItem(
                plan_id=full_trip["plan"].id,
                day_index=2,
                day_date=date(2026, 8, 15),
                start_hour=10.0,
                duration_min=60,
                title="Blue Bottle Cafe",
                place="Wicker Park",
                settledness="loose",
            ),
            PlanItem(
                plan_id=full_trip["plan"].id,
                day_index=2,
                day_date=date(2026, 8, 15),
                start_hour=12.0,
                duration_min=60,
                title="Sawada Coffee",
                place="Sawada Cafe",
                settledness="loose",
            ),
        ]
    )
    db.flush()

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move the cafe later",
    )

    assert result.proposed_change is None
    assert result.candidate_options == ()
    assert "which item" in result.reply.lower()


def test_day_qualified_followup_resolves_the_history_reference(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    wednesday_cafe = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=1,
        day_date=date(2026, 8, 19),
        start_hour=10.0,
        duration_min=60,
        title="Blue Bottle Cafe",
        place="Wicker Park",
        settledness="loose",
    )
    thursday_cafe = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=2,
        day_date=date(2026, 8, 20),
        start_hour=10.0,
        duration_min=60,
        title="Sawada Coffee",
        place="Sawada Cafe",
        settledness="loose",
    )
    db.add_all([wednesday_cafe, thursday_cafe])
    db.flush()

    def fake_call_agent(**kwargs):
        assert 'selected on screen' not in kwargs["user"]
        assert thursday_cafe.title in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving the Thursday cafe later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": thursday_cafe.id},
                        "proposed_patch": {"start_hour": 11.5},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="No, I meant the Thursday one.",
        history=(
            chat_service.chat_agent.HistoryTurn(role="user", text="Move the cafe later"),
            chat_service.chat_agent.HistoryTurn(role="assistant", text="Which cafe do you mean?"),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == thursday_cafe.id
    assert result.proposed_change.patch == {"start_hour": 11.5}


def test_latest_explicit_correction_wins_over_history_and_selection(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    cafe = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=2,
        day_date=date(2026, 8, 15),
        start_hour=11.0,
        duration_min=60,
        title="Blue Bottle Cafe",
        place="Wicker Park",
        settledness="loose",
    )
    museum = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=3,
        day_date=date(2026, 8, 16),
        start_hour=11.0,
        duration_min=90,
        title="Contemporary Art Museum",
        place="Streeterville",
        settledness="loose",
    )
    db.add_all([cafe, museum])
    db.flush()

    def fake_call_agent(**kwargs):
        assert museum.title in kwargs["user"]
        assert cafe.title not in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving the contemporary art museum later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": museum.id},
                        "proposed_patch": {"start_hour": 12.5},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="No, I meant the contemporary art museum.",
        item_id=cafe.id,
        history=(
            chat_service.chat_agent.HistoryTurn(role="user", text="Move the cafe later"),
            chat_service.chat_agent.HistoryTurn(role="assistant", text="Which cafe do you mean?"),
        ),
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == museum.id
    assert result.proposed_change.patch == {"start_hour": 12.5}


def test_missing_named_reference_does_not_hallucinate_an_item(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move the aquarium later",
    )

    assert result.proposed_change is None
    assert "current plan" in result.reply.lower()


def test_stale_selected_item_fails_safely_for_pronoun_reference(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setattr(base, "call_agent", _unexpected_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this one later",
        item_id="missing-item-id",
    )

    assert result.proposed_change is None
    assert "which item" in result.reply.lower()


def test_explicit_booked_item_reference_keeps_the_final_verdict_at_confirm(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")

    def fake_call_agent(**kwargs):
        assert full_trip["dinner"].title in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving dinner later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": full_trip["dinner"].id},
                        "proposed_patch": {"start_hour": 20.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move dinner to 8 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == full_trip["dinner"].id
    assert result.proposed_change.verdict.path is Path.CONFIRM


def test_explicit_settled_item_reference_keeps_the_final_verdict_at_reopen_round(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    museum = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=3,
        day_date=date(2026, 8, 16),
        start_hour=11.0,
        duration_min=90,
        title="Contemporary Art Museum",
        place="Streeterville",
        settledness=Settledness.SETTLED.value,
    )
    db.add(museum)
    db.flush()

    def fake_call_agent(**kwargs):
        assert museum.title in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving the contemporary art museum later.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": museum.id},
                        "proposed_patch": {"start_hour": 12.5},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move the contemporary art museum later",
        item_id=full_trip["dinner"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == museum.id
    assert result.proposed_change.verdict.path is Path.REOPEN_ROUND


def test_explicit_reference_reclassification_still_upgrades_overlap_to_round(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    museum = PlanItem(
        plan_id=full_trip["plan"].id,
        day_index=2,
        day_date=date(2026, 8, 15),
        start_hour=17.0,
        duration_min=90,
        title="Contemporary Art Museum",
        place="Streeterville",
        settledness="loose",
    )
    db.add(museum)
    db.flush()

    def fake_call_agent(**kwargs):
        assert museum.title in kwargs["user"]
        return base.AgentRunResult(
            content="I can prepare moving the contemporary art museum to 3 PM.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": {
                        "item": {"id": museum.id},
                        "proposed_patch": {"start_hour": 15.0},
                    },
                    "guard_rejected": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", fake_call_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move the contemporary art museum to 3 PM",
        item_id=full_trip["dinner"].id,
    )

    assert result.proposed_change is not None
    assert result.proposed_change.item_id == museum.id
    assert result.proposed_change.verdict.path is Path.ROUND

def _failure_write_counts(db: Session) -> dict[str, int]:
    return {
        "plan_items": len(db.scalars(select(PlanItem)).all()),
        "decision_rounds": len(db.scalars(select(DecisionRound)).all()),
        "change_proposals": len(db.scalars(select(ChangeProposal)).all()),
        "votes": len(db.scalars(select(Vote)).all()),
        "plan_changes": len(db.scalars(select(PlanChange)).all()),
    }


def _unexpected_answer_question(*args, **kwargs):
    raise AssertionError("answer_question should not run in deterministic failure fallback")


def test_agent_empty_response_uses_deterministic_failure_reply_for_selected_item(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setattr(chat_service.chat_agent, "answer_question", _unexpected_answer_question)

    def empty_agent(**kwargs):
        return base.AgentRunResult(
            content="",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
        )

    monkeypatch.setattr(base, "call_agent", empty_agent)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3:30 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None
    assert "could not check that reliably" in result.reply.lower()
    assert full_trip["art"].title.lower() in result.reply.lower()
    assert "current plan has not changed" in result.reply.lower()


def test_round_limit_failure_is_read_only_and_deterministic(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setattr(chat_service.chat_agent, "answer_question", _unexpected_answer_question)

    def round_limit_agent(**kwargs):
        return base.AgentRunResult(
            content="ERROR: Agent exceeded the 5-round tool loop limit.",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=0,
            total_elapsed_ms=1.0,
            stopped_reason="round_limit_exceeded",
        )

    monkeypatch.setattr(base, "call_agent", round_limit_agent)
    before = _failure_write_counts(db)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3:30 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None
    assert "could not check that reliably" in result.reply.lower()
    assert "current plan has not changed" in result.reply.lower()
    assert _failure_write_counts(db) == before


def test_token_limit_failure_is_read_only_and_deterministic(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setattr(chat_service.chat_agent, "answer_question", _unexpected_answer_question)

    def token_limit_agent(**kwargs):
        return base.AgentRunResult(
            content="ERROR: Agent stopped because the token cost limit was exceeded.",
            trace_id="trace",
            rounds=(),
            tool_results=(),
            total_tokens=99999,
            total_elapsed_ms=1.0,
            stopped_reason="token_limit_exceeded",
        )

    monkeypatch.setattr(base, "call_agent", token_limit_agent)
    before = _failure_write_counts(db)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3:30 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None
    assert "could not check that reliably" in result.reply.lower()
    assert "current plan has not changed" in result.reply.lower()
    assert _failure_write_counts(db) == before


def test_guard_rejection_limit_failure_is_read_only_and_deterministic(
    monkeypatch, db: Session, full_trip: dict
):
    monkeypatch.setenv("MOCK_AI", "1")
    monkeypatch.setattr(chat_service.chat_agent, "answer_question", _unexpected_answer_question)

    def guard_limit_agent(**kwargs):
        return base.AgentRunResult(
            content="ERROR: Agent stopped because the same tool was rejected by the guard too many times.",
            trace_id="trace",
            rounds=(),
            tool_results=(
                {
                    "tool": "classify_change",
                    "arguments": {},
                    "output": "Call get_current_plan first.",
                    "guard_rejected": True,
                    "guard_reason": "Call get_current_plan first.",
                    "guard_limit_exceeded": True,
                    "cached": False,
                },
            ),
            total_tokens=0,
            total_elapsed_ms=1.0,
            stopped_reason="guard_rejection_limit_exceeded",
        )

    monkeypatch.setattr(base, "call_agent", guard_limit_agent)
    before = _failure_write_counts(db)

    result = chat_service.respond_to_trip_chat(
        db,
        trip_id=full_trip["trip"].id,
        membership=full_trip["me"],
        message="Move this to 3:30 PM",
        item_id=full_trip["art"].id,
    )

    assert result.proposed_change is None
    assert "could not check that reliably" in result.reply.lower()
    assert "current plan has not changed" in result.reply.lower()
    assert _failure_write_counts(db) == before
