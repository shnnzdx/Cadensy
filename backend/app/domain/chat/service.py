from __future__ import annotations

import re
from uuid import uuid4
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ...agents import chat as chat_agent
from ...agents.execution import AgentExecutionConfig
from ...agents.legacy_runtime import LegacyChatAgentRuntime, LegacyReadTripCapability
from ...agents.runtime_contract import (
    AgentCandidateOption,
    AgentSuggestedChange,
    RuntimeHistoryTurn,
    RuntimeLimits,
    RuntimeRequest,
    RuntimeResult,
)
from ...agents.runtime_factory import build_chat_runtime
from ...db.session import SessionLocal
from ...db.models import Plan, PlanItem, Trip, TripMembership
from ..constraints.types import Classification
from ..decisions import orchestrator as orch

CHAT_AGENT_TIMEOUT_SECONDS = 30.0
CHAT_AGENT_PROVIDER_TIMEOUT_SECONDS = 20.0
CHAT_AGENT_TOOL_TIMEOUT_SECONDS = 5.0
CHAT_AGENT_MAX_ROUNDS = 8
# Counted across rounds, and every round re-sends the whole conversation, so this
# grows quadratically with trip size. A four-round exchange on a one-week trip
# measured ~32k. The real ceiling on cost is CHAT_AGENT_MAX_ROUNDS; this is only a
# backstop against a runaway loop.
CHAT_AGENT_MAX_TOTAL_TOKENS = 120000


class ChatAccessDenied(Exception):
    pass


class ChatTripNotFound(Exception):
    pass


class ChatItemNotFound(Exception):
    pass


@dataclass(frozen=True)
class ProposedChatChange:
    item_id: str
    item_title: str
    patch: dict[str, Any]
    verdict: Classification


@dataclass(frozen=True)
class ChatCandidateOption:
    id: str
    label: str
    title: str
    body: str
    tradeoff: str
    item_id: str
    patch: dict[str, Any]


@dataclass(frozen=True)
class ChatResult:
    reply: str
    proposed_change: ProposedChatChange | None
    candidate_options: tuple[ChatCandidateOption, ...] = ()


@dataclass(frozen=True)
class _HistoryOptionSelection:
    status: str
    option: ChatCandidateOption | None = None


def respond_to_trip_chat(
    db: Session,
    *,
    trip_id: str,
    membership: TripMembership,
    message: str,
    item_id: str | None = None,
    history: tuple[chat_agent.HistoryTurn, ...] = (),
) -> ChatResult:
    if membership.trip_id != trip_id:
        raise ChatAccessDenied("This membership does not belong to this trip")

    trip = db.get(Trip, trip_id)
    if trip is None:
        raise ChatTripNotFound("Trip not found")

    items = _trip_items(db, trip_id)
    selected = _selected_item(items, item_id)

    return _respond_with_agent_branch(
        db=db,
        trip_id=trip_id,
        membership=membership,
        message=message,
        items=items,
        selected=selected,
        history=history,
    )


def _respond_with_agent_branch(
    *,
    db: Session,
    trip_id: str,
    membership: TripMembership,
    message: str,
    items: list[PlanItem],
    selected: PlanItem | None = None,
    history: tuple[chat_agent.HistoryTurn, ...] = (),
) -> ChatResult:
    followup = _followup_option_selection_result(
        db=db,
        membership=membership,
        message=message,
        items=items,
        history=history,
    )
    if followup is not None:
        return followup

    reference = _resolve_item_reference(message, items, selected, history)
    if reference.status == "ambiguous":
        return ChatResult(
            reply=_ambiguous_item_reference_reply(message),
            proposed_change=None,
        )
    if reference.status == "missing":
        return ChatResult(
            reply=_missing_item_reference_reply(message),
            proposed_change=None,
        )

    target = reference.item or selected
    expected_item_id = reference.item.id if reference.item is not None else None
    selected_on_screen = reference.item is None or reference.source == "selected"
    if target is not None and _is_item_explanation_request(message):
        return ChatResult(
            reply=_structured_item_explanation(target),
            proposed_change=None,
        )
    clarification = _plain_text_clarification_reply(message, target)
    if clarification is not None:
        return ChatResult(reply=clarification, proposed_change=None)

    try:
        request = RuntimeRequest(
            message=_agent_user_message(
                message,
                target,
                selected_on_screen=selected_on_screen,
            ),
            history=tuple(
                RuntimeHistoryTurn(role=turn.role, text=turn.text) for turn in history
            ),
            selected_item_ref=target.id if target is not None else None,
            request_id=uuid4().hex,
            limits=RuntimeLimits(
                max_rounds=CHAT_AGENT_MAX_ROUNDS,
                max_total_tokens=CHAT_AGENT_MAX_TOTAL_TOKENS,
            ),
        )
        # This public capability surface is framework-neutral despite its
        # Legacy name. PR-04B proves the Pydantic adapter consumes it without a
        # request Session or an ORM/query capability.
        capability = LegacyReadTripCapability(
            trip_id=trip_id,
            actor_membership_id=membership.id,
            session_factory=SessionLocal,
        )
        runtime = build_chat_runtime(system_prompt=_agent_system_prompt())
        result = runtime.run(
            request,
            capability,
            execution=_chat_agent_execution_config(),
        )
    except Exception:
        return _degraded_reply(message, items, target)

    if result.observation.failure is not None:
        return _degraded_reply(message, items, target)
    reply = result.reply.strip()
    if not reply:
        return _degraded_reply(message, items, target)
    reply = _strip_internal_reasoning(reply)
    if chat_agent._claims_change_completed(reply):
        reply = _safe_pending_agent_reply(message)

    candidates = _candidate_options_from_runtime(result)
    if isinstance(runtime, LegacyChatAgentRuntime):
        # Legacy replacements retain their additional raw tool provenance
        # contract. The common RuntimeResult intentionally does not expose it.
        explicit = _proposed_change_from_agent_classification(
            db=db,
            items=items,
            membership=membership,
            tool_results=capability._application_legacy_tool_results,
            expected_item_id=expected_item_id,
        )
    else:
        explicit = _proposed_change_from_runtime_suggestion(
            db=db,
            items=items,
            membership=membership,
            outcome=result.outcome,
            expected_item_id=expected_item_id,
        )
    if explicit is not None:
        return ChatResult(
            reply=reply,
            proposed_change=explicit,
            candidate_options=candidates,
        )
    return ChatResult(reply=reply, proposed_change=None, candidate_options=candidates)


def _is_item_explanation_request(message: str) -> bool:
    normalized = message.casefold().strip()
    return bool(re.search(
        r"\b(?:explain|details?|describe|tell me about|what is)\b|解释|介绍|详情",
        normalized,
    ))


def _strip_internal_reasoning(reply: str) -> str:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", reply.strip()) if part.strip()]
    if len(paragraphs) <= 1:
        return reply
    internal_patterns = (
        r"^the user wants\b",
        r"^the traveler wants\b",
        r"^let me\b",
        r"^i should\b",
        r"^i need to\b",
        r"^i'll\b",
    )
    visible = [
        paragraph
        for paragraph in paragraphs
        if not any(re.search(pattern, paragraph, re.IGNORECASE) for pattern in internal_patterns)
    ]
    return "\n\n".join(visible or paragraphs[-1:])


def _format_item_hour(hour: float | None) -> str | None:
    if hour is None:
        return None
    numeric = float(hour)
    whole = int(numeric)
    minutes = round((numeric - whole) * 60)
    suffix = "PM" if whole >= 12 else "AM"
    return f"{whole % 12 or 12}:{minutes:02d} {suffix}"


def _structured_item_explanation(item: PlanItem) -> str:
    lines: list[str] = []
    if item.day_date or item.start_hour is not None or item.duration_min:
        schedule: list[str] = []
        if item.day_date:
            schedule.append(item.day_date.strftime("%A, %B %-d, %Y"))
        start = _format_item_hour(item.start_hour)
        if start:
            end = (
                _format_item_hour(item.start_hour + item.duration_min / 60)
                if item.duration_min
                else None
            )
            schedule.append(f"{start}–{end}" if end else start)
        if schedule:
            lines.append("Schedule\n" + " · ".join(schedule))

    details: list[str] = []
    if item.tags:
        details.append("Type: " + ", ".join(tag.replace("_", " ").title() for tag in item.tags))
    if item.place:
        details.append("Address: " + item.place)
    if item.duration_min:
        details.append(f"Duration: {item.duration_min} min")
    if details:
        lines.append("Details\n" + "\n".join(details))

    status = {
        "booked": "Booked",
        "settled": "Settled",
        "touched": "Updated",
        "loose": "Not booked",
    }.get(item.settledness)
    if status:
        lines.append("Status\n" + status)
    return "\n\n".join(lines)


@dataclass(frozen=True)
class _ResolvedItemReference:
    status: str
    item: PlanItem | None = None
    candidates: tuple[PlanItem, ...] = ()
    source: str = ""


_REFERENCE_GENERIC_TERMS = frozenset(
    {
        "breakfast",
        "brunch",
        "cafe",
        "coffee",
        "dinner",
        "lunch",
        "meal",
        "museum",
        "restaurant",
        "walk",
    }
)


def _resolve_item_reference(
    message: str,
    items: list[PlanItem],
    selected: PlanItem | None,
    history: tuple[chat_agent.HistoryTurn, ...],
) -> _ResolvedItemReference:
    explicit_candidates = _explicit_item_candidates(message, items)
    day_filter = _reference_day_filter(message)
    if explicit_candidates:
        narrowed = _narrow_items_by_day(explicit_candidates, day_filter)
        if len(narrowed) == 1:
            return _ResolvedItemReference(status="matched", item=narrowed[0], source="explicit")
        if day_filter and explicit_candidates and not narrowed:
            return _ResolvedItemReference(status="missing", candidates=explicit_candidates)
        return _ResolvedItemReference(
            status="ambiguous",
            candidates=narrowed or explicit_candidates,
            source="explicit",
        )

    if day_filter and _mentions_generic_day_qualified_reference(message):
        history_candidates = _history_reference_candidates(history, items)
        if history_candidates:
            narrowed = _narrow_items_by_day(history_candidates, day_filter)
            if len(narrowed) == 1:
                return _ResolvedItemReference(status="matched", item=narrowed[0], source="history")
            if len(narrowed) > 1:
                return _ResolvedItemReference(status="ambiguous", candidates=narrowed, source="history")
            return _ResolvedItemReference(status="missing", candidates=history_candidates, source="history")
        if selected is not None:
            related = _related_items_for_selected(selected, items)
            narrowed = _narrow_items_by_day(related, day_filter)
            if len(narrowed) == 1:
                return _ResolvedItemReference(status="matched", item=narrowed[0], source="selected")
            if len(narrowed) > 1:
                return _ResolvedItemReference(status="ambiguous", candidates=narrowed, source="selected")
            return _ResolvedItemReference(status="missing", candidates=related, source="selected")
        return _ResolvedItemReference(status="ambiguous")

    if _contains_pronoun_reference(message):
        if selected is not None:
            return _ResolvedItemReference(status="matched", item=selected, source="selected")
        history_candidates = _history_reference_candidates(history, items)
        if len(history_candidates) == 1:
            return _ResolvedItemReference(status="matched", item=history_candidates[0], source="history")
        if len(history_candidates) > 1:
            return _ResolvedItemReference(status="ambiguous", candidates=history_candidates, source="history")
        return _ResolvedItemReference(status="ambiguous")

    if selected is not None and (
        _looks_like_selected_item_relative_request(message)
        or _looks_like_selected_replacement_search_request(message)
    ):
        return _ResolvedItemReference(status="matched", item=selected, source="selected")

    if _looks_like_named_reference(message):
        return _ResolvedItemReference(status="missing")

    return _ResolvedItemReference(status="none")


def _explicit_item_candidates(message: str, items: list[PlanItem]) -> tuple[PlanItem, ...]:
    normalized_message = _normalize_selection_text(message)
    if not normalized_message:
        return ()
    scored_matches: list[tuple[int, PlanItem]] = []
    best_score = 0
    for item in items:
        score = max(
            (
                len(label.split())
                for label in _reference_labels(item)
                if _message_mentions_reference(normalized_message, label)
            ),
            default=0,
        )
        if score <= 0:
            continue
        best_score = max(best_score, score)
        scored_matches.append((score, item))
    if best_score <= 0:
        return ()
    return tuple(item for score, item in scored_matches if score == best_score)


def _reference_labels(item: PlanItem) -> frozenset[str]:
    title = _normalize_selection_text(item.title)
    place = _normalize_selection_text(item.place)
    labels: set[str] = set()
    if title:
        labels.add(title)
        words = [word for word in title.split() if word not in {"the", "of"}]
        if len(words) >= 2:
            labels.add(" ".join(words[:2]))
        if len(words) >= 3:
            labels.add(" ".join(words[:3]))
    lowered_title = title
    lowered_place = place
    if "art institute" in lowered_title or "museum" in lowered_title:
        labels.add("museum")
        labels.add("art institute")
    if "cafe" in lowered_title or "coffee" in lowered_title or "cafe" in lowered_place:
        labels.update({"cafe", "coffee"})
    if item.is_meal or any(term in lowered_title for term in ("dinner", "lunch", "breakfast", "brunch")):
        labels.update({"meal", "restaurant"})
    for term in ("dinner", "lunch", "breakfast", "brunch", "walk"):
        if term in lowered_title:
            labels.add(term)
    return frozenset(label for label in labels if label)


def _message_mentions_reference(normalized_message: str, label: str) -> bool:
    return bool(re.search(rf"\b{re.escape(label)}\b", normalized_message))


def _reference_day_filter(message: str) -> date | str | None:
    iso_match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", message)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError:
            pass
    lowered = message.lower()
    for weekday in (
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
        "friday",
        "saturday",
        "sunday",
    ):
        if re.search(rf"\b{weekday}\b", lowered):
            return weekday
    return None


def _narrow_items_by_day(
    items: tuple[PlanItem, ...], target: date | str | None
) -> tuple[PlanItem, ...]:
    if target is None:
        return items
    narrowed: list[PlanItem] = []
    for item in items:
        if isinstance(target, date):
            if item.day_date == target:
                narrowed.append(item)
            continue
        if item.day_date.strftime("%A").lower() == target:
            narrowed.append(item)
    return tuple(narrowed)


def _mentions_generic_day_qualified_reference(message: str) -> bool:
    normalized = _normalize_selection_text(message)
    return bool(re.search(r"\bone\b", normalized)) or _looks_like_named_reference(message)


def _contains_pronoun_reference(message: str) -> bool:
    normalized = _normalize_selection_text(message)
    if not normalized:
        return False
    if re.search(r"\bthis one\b", normalized):
        return True
    if re.search(r"\bthat one\b", normalized):
        return True
    if re.search(r"\bit\b", normalized):
        return True
    return bool(re.search(r"\bthis\b", normalized))


def _history_reference_candidates(
    history: tuple[chat_agent.HistoryTurn, ...],
    items: list[PlanItem],
) -> tuple[PlanItem, ...]:
    for turn in reversed(history):
        candidates = _explicit_item_candidates(turn.text, items)
        if candidates:
            return candidates
    return ()


def _related_items_for_selected(
    selected: PlanItem,
    items: list[PlanItem],
) -> tuple[PlanItem, ...]:
    selected_labels = _reference_labels(selected)
    generic_labels = selected_labels & _REFERENCE_GENERIC_TERMS
    if not generic_labels:
        return (selected,)
    related = [item for item in items if _reference_labels(item) & generic_labels]
    return tuple(related or [selected])


def _looks_like_named_reference(message: str) -> bool:
    normalized = _normalize_selection_text(message)
    patterns = (
        r"\bwhat about(?: the)? [a-z0-9]+(?: [a-z0-9]+){0,3}\b",
        r"\bi meant(?: the)? [a-z0-9]+(?: [a-z0-9]+){0,3}\b",
        r"\bmove(?: the)? [a-z0-9]+(?: [a-z0-9]+){0,3}\b",
        r"\bcheck(?: the)? [a-z0-9]+(?: [a-z0-9]+){0,3}\b",
        r"\bthat [a-z0-9]+(?: [a-z0-9]+){0,3}\b",
        r"\bthe [a-z0-9]+(?: [a-z0-9]+){0,3}\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _looks_like_selected_item_relative_request(message: str) -> bool:
    normalized = _normalize_selection_text(message)
    if not normalized:
        return False
    patterns = (
        r"^(move|shift|push|delay|reschedule)\s+(to|later|earlier|tomorrow|next)\b",
        r"^(start)\s+(at|later|earlier)\b",
        r"^(make)\s+[a-z0-9]+(?:\s+[a-z0-9]+){0,4}\s+(shorter|longer)\b",
        r"^(shorten|lengthen|extend|replace|remove|delete)\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _looks_like_selected_replacement_search_request(message: str) -> bool:
    normalized = _normalize_selection_text(message)
    if not normalized:
        return False
    patterns = (
        r"^(search|find|look for|look up)\s+[a-z0-9]+(?:\s+[a-z0-9]+){0,6}\b",
        r"^(suggest|recommend)\s+(a|an|some|another|other)?\s*[a-z0-9]+(?:\s+[a-z0-9]+){0,6}\b",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


def _plain_text_clarification_reply(message: str, target: PlanItem | None) -> str | None:
    """Return deterministic plain-text follow-ups when the request is missing a slot.

    Plain text is the product contract for ordinary chat bubbles in the current UI.
    Keep these follow-ups out of the model path so the frontend never has to render
    Markdown for basic clarification turns.
    """
    if target is None:
        return None
    if not _needs_time_clarification(message):
        return None
    return _time_clarification_reply(message, target)


def _needs_time_clarification(message: str) -> bool:
    if chat_agent._hour_from_text(message.lower()) is not None:
        return False
    normalized = _normalize_selection_text(message)
    return normalized in {
        "change time",
        "change the time",
        "edit time",
        "edit the time",
    }


def _time_clarification_reply(message: str, item: PlanItem) -> str:
    if _contains_chinese(message):
        return (
            f"{item.title} 现在安排在 {_format_chat_time(item.start_hour)}，日期是 "
            f"{_format_chat_day(item.day_date)}。你想改到几点？"
        )
    return (
        f"{item.title} is currently scheduled for {_format_chat_time(item.start_hour)} "
        f"on {_format_chat_day(item.day_date)}. What time would you like to move it to?"
    )


def _ambiguous_item_reference_reply(message: str) -> str:
    if _contains_chinese(message):
        return "请说清楚你指的是哪一项行程。"
    return "Which item do you mean?"


def _missing_item_reference_reply(message: str) -> str:
    if _contains_chinese(message):
        return "请说清楚你指的是哪一项行程。"
    return "I don't see that item in the Current Plan yet. Which item do you mean?"


_SELECTION_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "do",
        "for",
        "good",
        "i",
        "ill",
        "it",
        "its",
        "let",
        "lets",
        "me",
        "move",
        "my",
        "of",
        "one",
        "option",
        "please",
        "sounds",
        "take",
        "that",
        "the",
        "this",
        "to",
        "use",
        "version",
        "we",
    }
)


_ORDINAL_WORDS = {
    "first": 0,
    "1st": 0,
    "second": 1,
    "2nd": 1,
    "third": 2,
    "3rd": 2,
    "fourth": 3,
    "4th": 3,
    "fifth": 4,
    "5th": 4,
}


_AMBIGUOUS_SELECTION_PHRASES = (
    "that sounds good",
    "this sounds good",
    "that one sounds good",
    "this one sounds good",
    "that one",
    "this one",
    "do that",
    "use that",
    "lets do that",
    "ill take that",
    "that works",
    "sounds good",
)


def _followup_option_selection_result(
    *,
    db: Session,
    membership: TripMembership,
    message: str,
    items: list[PlanItem],
    history: tuple[chat_agent.HistoryTurn, ...],
) -> ChatResult | None:
    candidates = _latest_history_candidate_options(history)
    if not candidates:
        return None

    selection = _resolve_history_option_selection(message, candidates, items)
    if selection.status == "none":
        return None
    if selection.status != "matched" or selection.option is None:
        return ChatResult(
            reply=_ambiguous_option_selection_reply(message),
            proposed_change=None,
            candidate_options=candidates,
        )

    option = selection.option
    if not option.patch:
        return ChatResult(
            reply=_no_change_option_selection_reply(message),
            proposed_change=None,
            candidate_options=candidates,
        )

    item_by_id = {item.id: item for item in items}
    target = item_by_id.get(option.item_id)
    patch = _normalize_patch(option.patch)
    if target is None or not patch:
        return ChatResult(
            reply=_stale_option_selection_reply(message),
            proposed_change=None,
            candidate_options=candidates,
        )

    verdict = orch.classify_change(db, target, patch, membership.id)
    proposed = ProposedChatChange(
        item_id=target.id,
        item_title=target.title,
        patch=patch,
        verdict=verdict,
    )
    reply = chat_agent._safe_pending_explanation(
        chat_agent.ReplyInput(
            message=message,
            item=_item_context(target),
            patch=patch,
            verdict=verdict,
        )
    )
    return ChatResult(reply=reply, proposed_change=proposed)


def _latest_history_candidate_options(
    history: tuple[chat_agent.HistoryTurn, ...]
) -> tuple[ChatCandidateOption, ...]:
    for turn in reversed(history):
        if turn.role != "assistant" or not turn.candidate_options:
            continue
        options: list[ChatCandidateOption] = []
        for option in turn.candidate_options:
            patch = dict(option.patch) if isinstance(option.patch, dict) else {}
            options.append(
                ChatCandidateOption(
                    id=option.id,
                    label=option.label,
                    title=option.title,
                    body=option.body,
                    tradeoff=option.tradeoff,
                    item_id=option.item_id,
                    patch=patch,
                )
            )
        return tuple(options)
    return ()


def _resolve_history_option_selection(
    message: str,
    candidates: tuple[ChatCandidateOption, ...],
    items: list[PlanItem],
) -> _HistoryOptionSelection:
    normalized = _normalize_selection_text(message)
    if not normalized:
        return _HistoryOptionSelection(status="none")

    id_matches = [
        option for option in candidates if _option_id_is_explicitly_selected(option, normalized)
    ]
    if len(id_matches) == 1:
        return _HistoryOptionSelection(status="matched", option=id_matches[0])
    if len(id_matches) > 1:
        return _HistoryOptionSelection(status="ambiguous")

    index, explicit_index = _selected_option_index(normalized)
    if explicit_index:
        if index is not None and 0 <= index < len(candidates):
            return _HistoryOptionSelection(status="matched", option=candidates[index])
        return _HistoryOptionSelection(status="ambiguous")

    semantic_matches = _semantic_option_matches(normalized, candidates, items)
    if len(semantic_matches) == 1:
        return _HistoryOptionSelection(status="matched", option=semantic_matches[0])
    if len(semantic_matches) > 1:
        return _HistoryOptionSelection(status="ambiguous")

    if _looks_like_ambiguous_option_acceptance(normalized):
        if len(candidates) == 1:
            return _HistoryOptionSelection(status="matched", option=candidates[0])
        return _HistoryOptionSelection(status="ambiguous")

    return _HistoryOptionSelection(status="none")


def _option_id_is_explicitly_selected(option: ChatCandidateOption, normalized_message: str) -> bool:
    option_id = _normalize_selection_text(option.id)
    if not option_id:
        return False
    return re.search(rf"\b{re.escape(option_id)}\b", normalized_message) is not None


def _selected_option_index(normalized_message: str) -> tuple[int | None, bool]:
    bare_number_match = re.fullmatch(r"(\d+)", normalized_message)
    if bare_number_match:
        return int(bare_number_match.group(1)) - 1, True

    number_match = re.search(r"\boption\s+(\d+)\b", normalized_message)
    if number_match:
        return int(number_match.group(1)) - 1, True

    letter_match = re.search(r"\boption\s+([a-z])\b", normalized_message)
    if letter_match:
        return ord(letter_match.group(1)) - ord("a"), True

    for token, index in _ORDINAL_WORDS.items():
        if re.search(rf"\b{re.escape(token)}\b", normalized_message):
            return index, True
    return None, False


def _semantic_option_matches(
    normalized_message: str,
    candidates: tuple[ChatCandidateOption, ...],
    items: list[PlanItem],
) -> tuple[ChatCandidateOption, ...]:
    message_tokens = {
        token
        for token in normalized_message.split()
        if len(token) > 2 and token not in _SELECTION_STOPWORDS
    }
    if not message_tokens:
        return ()

    item_by_id = {item.id: item for item in items}
    scores: list[tuple[int, ChatCandidateOption]] = []
    for option in candidates:
        option_tokens = _history_option_tokens(option, item_by_id.get(option.item_id))
        score = len(message_tokens & option_tokens)
        if score:
            scores.append((score, option))
    if not scores:
        return ()

    top_score = max(score for score, _ in scores)
    return tuple(option for score, option in scores if score == top_score)


def _history_option_tokens(
    option: ChatCandidateOption, current_item: PlanItem | None
) -> frozenset[str]:
    parts = [option.id, option.label, option.title, option.body, option.tradeoff]
    normalized_patch = _normalize_patch(option.patch)
    day_value = normalized_patch.get("day_date")
    if isinstance(day_value, date):
        parts.append(day_value.isoformat())
        parts.append(day_value.strftime("%A"))
    duration_value = normalized_patch.get("duration_min")
    if (
        current_item is not None
        and isinstance(duration_value, (int, float))
        and duration_value < current_item.duration_min
    ):
        parts.extend(["short", "shorten", "shorter", "lighter"])
    if not option.patch:
        parts.extend(["keep", "current", "unchanged", "stay"])
    normalized = _normalize_selection_text(" ".join(str(part) for part in parts if part))
    return frozenset(token for token in normalized.split() if token)


def _normalize_selection_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()


def _looks_like_ambiguous_option_acceptance(normalized_message: str) -> bool:
    return any(phrase in normalized_message for phrase in _AMBIGUOUS_SELECTION_PHRASES)


def _ambiguous_option_selection_reply(message: str) -> str:
    if _contains_chinese(message):
        return "我可以准备上一轮给你的方案，但我还不能确定你选的是哪一个。请直接说 option 第几个，或者重新说具体想改成什么。"
    return (
        "I can prepare one of those options, but I can't tell which one you mean. "
        "Please choose the option number or name the specific change."
    )


def _no_change_option_selection_reply(message: str) -> str:
    if _contains_chinese(message):
        return "你选中的这个方案是保持当前行程不变，所以现在没有可以准备的变更。"
    return "That option keeps the Current Plan as-is, so there is no change for me to prepare."


def _stale_option_selection_reply(message: str) -> str:
    if _contains_chinese(message):
        return "那个之前的方案现在已经不再适用了。请让我根据当前行程重新查看或提供新的方案。"
    return (
        "That earlier option is no longer something I can prepare safely from the current plan. "
        "Please ask me to re-check it or generate fresh options."
    )


def _agent_user_message(
    message: str,
    selected: PlanItem | None,
    *,
    selected_on_screen: bool = True,
) -> str:
    """Carry the best-known item context into the agent turn.

    UI selection can ground pronouns such as "this one". When the backend has
    already resolved a unique item from the latest message/history, that item
    should become the focus instead.
    """
    if selected is None:
        return message
    if selected_on_screen:
        return (
            f'[The traveler has "{selected.title}" selected on screen. Resolve "this" '
            f'or "it" to that item unless they name a different one. If they say '
            f'"search X", "find X", "look for X", "replace with X", or "swap this '
            f'with X", treat X as a replacement venue query for the selected item, '
            f'not as the Current Plan item to identify.]\n\n{message}'
        )
    return (
        f'[The traveler is referring to "{selected.title}" in the Current Plan. '
        f'Use that item unless they clearly switch to a different one.]\n\n{message}'
    )


def _degraded_reply(
    message: str, items: list[PlanItem], selected: PlanItem | None
) -> ChatResult:
    """Return a deterministic safe fallback after agent-branch failure.

    Failure handling must not depend on a second model call. Use only facts already
    in hand from the Current Plan and keep the result read-only.
    """
    reply = chat_agent.failure_reply(
        chat_agent.QuestionInput(
            message=message,
            item=_item_context(selected) if selected else None,
            itinerary=tuple(_item_context(item) for item in items),
        )
    )
    return ChatResult(reply=reply, proposed_change=None)


def _safe_pending_agent_reply(message: str) -> str:
    """Replace unsafe agent claims that imply a change already happened.

    Agent-branch replies do not always have a verdict yet, so this safety
    fallback states only the product invariant: the assistant prepared/checks a
    possible change, but nothing is submitted or applied until the traveler
    clicks Apply in the UI.
    """
    if _contains_chinese(message):
        return (
            "我可以帮你准备这个改动，但当前行程还没有改变。需要你点击 Apply "
            "后才会提交，之后才会进入对应的小组流程。"
        )
    return (
        "I can help prepare this change, but the Current Plan has not changed. "
        "It is not submitted until you click Apply in the product UI."
    )


def _contains_chinese(value: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in value)


def _chat_agent_execution_config() -> AgentExecutionConfig:
    """Retain the PR-01C request/provider/tool deadline partition."""

    return AgentExecutionConfig(
        request_timeout_seconds=CHAT_AGENT_TIMEOUT_SECONDS,
        provider_timeout_seconds=CHAT_AGENT_PROVIDER_TIMEOUT_SECONDS,
        tool_timeout_seconds=CHAT_AGENT_TOOL_TIMEOUT_SECONDS,
    )


def _agent_system_prompt() -> str:
    """The one definition of the agent's system prompt.

    The manual trace scripts under app/agents/agent-server/ import this rather
    than keeping their own copy. They used to carry a separate wording, and the
    two drifted apart in both directions, so a scripted validation run was no
    longer evidence about production behavior.
    """
    return (
        "You are Cadensy's read-only trip assistant.\n"
        "\n"
        "Rules:\n"
        "1. Answer in English only.\n"
        "2. Respond in plain text only. Do not use Markdown, emphasis markers, "
        "headings, bullets, or list syntax in the final user-visible reply.\n"
        "3. Before answering any itinerary question, verify facts with tools. "
        "Do not guess.\n"
        "4. Use only facts returned by tools. Do not invent items, times, dates, "
        "places, member details, membership ids, or private preference wording.\n"
        "5. If suggesting a move to another day, inspect that day before judging "
        "whether it has room.\n"
        "6. For fuzzy requests to make a day easier or to offer compromises, call "
        "propose_options after checking the Current Plan.\n"
        "7. For a specific proposed change, call classify_change and follow its "
        "result. Do not choose a decision path yourself.\n"
        "8. If the traveler has not given a required target time, date, or other "
        "missing slot yet, ask only the concise follow-up question needed to continue.\n"
        "9. Do not say the Current Plan has changed. Changes require user action "
        "in the product UI.\n"
    )


def _format_chat_time(value: float | int | None) -> str:
    if value is None:
        return "an unknown time"
    numeric = float(value)
    whole = int(numeric)
    minutes = round((numeric - whole) * 60)
    suffix = "PM" if whole >= 12 else "AM"
    return f"{whole % 12 or 12}:{minutes:02d} {suffix}"


def _format_chat_day(value: date) -> str:
    return value.strftime("%B %d").replace(" 0", " ")


def _candidate_options_from_agent(
    tool_results: tuple[dict[str, Any], ...]
) -> tuple[ChatCandidateOption, ...]:
    options: list[ChatCandidateOption] = []
    for result in tool_results:
        if result.get("tool") != "propose_options" or result.get("guard_rejected"):
            continue
        output = result.get("output")
        if not isinstance(output, dict):
            continue
        for option in output.get("options") or ():
            if not isinstance(option, dict):
                continue
            patch = option.get("patch") if isinstance(option.get("patch"), dict) else {}
            options.append(
                ChatCandidateOption(
                    id=str(option.get("id") or ""),
                    label=str(option.get("label") or ""),
                    title=str(option.get("title") or ""),
                    body=str(option.get("body") or ""),
                    tradeoff=str(option.get("tradeoff") or ""),
                    item_id=str(option.get("item_id") or ""),
                    patch=_normalize_patch(patch),
                )
            )
    return tuple(options)


def _candidate_options_from_runtime(result: RuntimeResult) -> tuple[ChatCandidateOption, ...]:
    """Translate contract candidates without making them a primary outcome."""

    return tuple(
        ChatCandidateOption(
            id=option.id,
            label=option.label,
            title=option.title,
            body=option.body,
            tradeoff=option.tradeoff,
            item_id=option.item_ref,
            patch=_normalize_patch(dict(option.safe_patch)),
        )
        for option in result.candidate_options
    )


_REPLACEMENT_CORE_FIELDS = (
    "title",
    "place",
    "lat",
    "lng",
)
_REPLACEMENT_PRICE_FIELD = "price_per_person"


def _replacement_signature(payload: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(payload.get(field) for field in _REPLACEMENT_CORE_FIELDS)


def _replacement_candidates_from_agent(
    tool_results: tuple[dict[str, Any], ...]
) -> tuple[dict[str, Any], ...]:
    candidates: list[dict[str, Any]] = []
    for result in tool_results:
        if result.get("tool") != "find_replacement_place" or result.get("guard_rejected"):
            continue
        output = result.get("output")
        if not isinstance(output, dict):
            continue
        for candidate in output.get("candidates") or ():
            if not isinstance(candidate, dict):
                continue
            if any(candidate.get(field) is None for field in _REPLACEMENT_CORE_FIELDS):
                continue
            candidates.append(candidate)
    return tuple(candidates)


def _replacement_patch_is_supported(
    patch: dict[str, Any], tool_results: tuple[dict[str, Any], ...]
) -> bool:
    replacement_fields = (*_REPLACEMENT_CORE_FIELDS, _REPLACEMENT_PRICE_FIELD)
    if not any(field in patch for field in replacement_fields):
        return True
    matching = [
        candidate
        for candidate in _replacement_candidates_from_agent(tool_results)
        if _replacement_signature(candidate) == _replacement_signature(patch)
    ]
    if not matching:
        return False
    if _REPLACEMENT_PRICE_FIELD in patch:
        return any(
            candidate.get(_REPLACEMENT_PRICE_FIELD) == patch.get(_REPLACEMENT_PRICE_FIELD)
            for candidate in matching
        )
    return any(candidate.get(_REPLACEMENT_PRICE_FIELD) is None for candidate in matching)


def _proposed_change_from_agent_classification(
    *,
    db: Session,
    items: list[PlanItem],
    membership: TripMembership,
    tool_results: tuple[dict[str, Any], ...],
    expected_item_id: str | None = None,
) -> ProposedChatChange | None:
    item_by_id = {item.id: item for item in items}
    for result in reversed(tool_results):
        if result.get("tool") != "classify_change" or result.get("guard_rejected"):
            continue
        output = result.get("output")
        if not isinstance(output, dict):
            continue
        item_payload = output.get("item")
        patch_payload = output.get("proposed_patch")
        if not isinstance(item_payload, dict) or not isinstance(patch_payload, dict):
            continue
        target = item_by_id.get(str(item_payload.get("id") or ""))
        patch = _normalize_patch(patch_payload)
        if target is None or not patch:
            continue
        if expected_item_id is not None and target.id != expected_item_id:
            continue
        if not _replacement_patch_is_supported(patch, tool_results):
            continue
        verdict = orch.classify_change(db, target, patch, membership.id)
        return ProposedChatChange(
            item_id=target.id,
            item_title=target.title,
            patch=patch,
            verdict=verdict,
        )
    return None


def _proposed_change_from_runtime_suggestion(
    *,
    db: Session,
    items: list[PlanItem],
    membership: TripMembership,
    outcome: object,
    expected_item_id: str | None = None,
) -> ProposedChatChange | None:
    """Validate common Runtime syntax before deterministic Domain preview.

    Runtime output has neither authority nor a fresh-state/concurrency claim.
    In PR-04C only Pydantic reaches this branch; replacement fields fail closed
    because they require Legacy-only candidate provenance.
    """

    if not isinstance(outcome, AgentSuggestedChange):
        return None
    target = next((item for item in items if item.id == outcome.item_ref), None)
    patch = _normalize_patch(dict(outcome.safe_patch))
    if target is None or not patch:
        return None
    if expected_item_id is not None and target.id != expected_item_id:
        return None
    if not _replacement_patch_is_supported(patch, ()):
        return None
    verdict = orch.classify_change(db, target, patch, membership.id)
    return ProposedChatChange(
        item_id=target.id,
        item_title=target.title,
        patch=patch,
        verdict=verdict,
    )


def _trip_items(db: Session, trip_id: str) -> list[PlanItem]:
    plan = db.scalar(select(Plan).where(Plan.trip_id == trip_id))
    if plan is None:
        return []
    return list(
        db.scalars(
            select(PlanItem)
            .where(PlanItem.plan_id == plan.id, PlanItem.settledness != "removed")
            .order_by(PlanItem.day_index, PlanItem.start_hour)
        )
    )


def _selected_item(items: list[PlanItem], item_id: str | None) -> PlanItem | None:
    if item_id is None:
        return None
    for item in items:
        if item.id == item_id:
            return item
    return None


def _item_context(item: PlanItem) -> chat_agent.ItemContext:
    return chat_agent.ItemContext(
        id=item.id,
        title=item.title,
        place=item.place,
        day_date=item.day_date,
        start_hour=item.start_hour,
        duration_min=item.duration_min,
    )


def _normalize_patch(patch: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "title",
        "place",
        "start_hour",
        "day_date",
        "duration_min",
        "price_per_person",
        "lat",
        "lng",
    }
    normalized: dict[str, Any] = {}
    for key, value in patch.items():
        if key not in allowed or value in (None, ""):
            continue
        if key == "day_date" and isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                continue
        normalized[key] = value
    return normalized
