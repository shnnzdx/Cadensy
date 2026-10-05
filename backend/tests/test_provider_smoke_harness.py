"""Offline contracts for the PR-03R.1 provider smoke harness.

Every test in this module uses an in-process httpx2 MockTransport.  It must
never require a provider credential or permit a socket-backed transport.
"""

from __future__ import annotations

import asyncio
import json
import threading

import httpx2
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from evals.runner import (
    PRIVATE_FIXTURE_PHRASE,
    _independent_session_factory,
    _synthetic_fixture,
)

import app.agents.provider_smoke_harness as smoke_harness
from app.agents.provider_smoke_harness import (
    UnexpectedStreamingAttempt,
    GlobalUsageLedger,
    OfflineRecordingTransport,
    OutboundPayloadPrivacyGate,
    OutboundPayloadPrivacyViolation,
    ProviderUsageBudgetExceeded,
    ProviderUsageUnavailable,
    ProviderSmokeHarnessStopped,
    ProviderSmokeHarness,
    ProviderRequestBudgetExceeded,
    SmokeScenarioVerificationFailed,
    classify_smoke_exception,
    normalize_smoke_failure,
)
from app.agents.execution import (
    AgentProviderDeadlineExceeded,
    AgentRequestDeadlineExceeded,
    AgentToolDeadlineExceeded,
)
from app.agents.pydantic_poc import TripReadCapability
from app.db.models import ChangeProposal, DecisionRound, PlanItem, Trip, Vote


def test_http_boundary_blocks_the_fifth_provider_request_without_persisting_raw_payload():
    """The process-wide provider limit applies before a fifth mock request runs."""
    seen_by_mock: list[httpx2.Request] = []
    ledger = GlobalUsageLedger(max_requests=4, max_total_tokens=2_000)

    async def response(request: httpx2.Request) -> httpx2.Response:
        seen_by_mock.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4-flash",
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
            request=request,
        )

    transport = OfflineRecordingTransport(response, ledger=ledger)

    async def exercise() -> None:
        async with httpx2.AsyncClient(transport=transport) as client:
            for _ in range(4):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-v4-flash", "messages": []},
                    headers={"Authorization": "Bearer offline-placeholder"},
                )
            with pytest.raises(ProviderRequestBudgetExceeded):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-v4-flash", "messages": []},
                    headers={"Authorization": "Bearer offline-placeholder"},
                )

    asyncio.run(exercise())

    assert len(seen_by_mock) == 4
    assert ledger.requests_used == 4
    assert ledger.reported_cost_usd is None
    assert ledger.pricing_status == "offline_mock_unbilled"
    assert transport.captured_requests == (
        {
            "method": "POST",
            "path": "/chat/completions",
            "model": "deepseek-v4-flash",
            "request_keys": ("messages", "model"),
            "reasoning_effort": None,
            "max_tokens": None,
            "tool_choice": None,
            "strict_values": (),
            "has_native_json_schema_response_format": False,
            "unexpected_openai_parameters": (),
        },
    ) * 4
    assert "offline-placeholder" not in repr(transport.captured_requests)


def test_global_budget_is_shared_across_separate_agent_runs():
    """Four is a Harness-wide ceiling, never a per-Agent-run allowance."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            for _ in range(4):
                observation = await harness.run_r1_typed_clarification()
                assert observation.output_kind == "clarification"
            with pytest.raises(ProviderRequestBudgetExceeded):
                await harness.run_r1_typed_clarification()
            return harness.ledger, harness.captured_requests

    ledger, captured = asyncio.run(exercise())
    assert ledger.requests_used == 4
    assert len(captured) == 4


def test_exact_token_budget_reach_stops_every_later_request_before_http_dispatch():
    """Reaching 2,000 reported tokens is terminal even with request budget left."""
    handler_calls = 0
    ledger = GlobalUsageLedger(max_requests=4, max_total_tokens=2_000)

    async def response(request: httpx2.Request) -> httpx2.Response:
        nonlocal handler_calls
        handler_calls += 1
        return httpx2.Response(
            200,
            json={
                "id": "offline",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-v4-flash",
                "choices": [],
                "usage": {
                    "prompt_tokens": 1_900,
                    "completion_tokens": 100,
                    "total_tokens": 2_000,
                },
            },
            request=request,
        )

    transport = OfflineRecordingTransport(response, ledger=ledger)

    async def exercise() -> None:
        async with httpx2.AsyncClient(transport=transport) as client:
            await client.post(
                "https://api.deepseek.com/chat/completions",
                json={"model": "deepseek-v4-flash", "messages": []},
            )
            with pytest.raises(ProviderSmokeHarnessStopped, match="reported_token_budget_reached"):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={"model": "deepseek-v4-flash", "messages": []},
                )

    asyncio.run(exercise())

    assert handler_calls == 1
    assert ledger.requests_used == 1
    assert ledger.reported_total_tokens == 2_000
    assert ledger.stop_reason == "reported_token_budget_reached"


def test_r1_serializes_non_thinking_typed_clarification_without_native_schema_format():
    """R1 reaches a typed clarification through a fully mocked Chat Completion."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            observation = await harness.run_r1_typed_clarification()
            return observation, harness.captured_requests, harness.ledger

    observation, captured, ledger = asyncio.run(exercise())

    assert observation.output_kind == "clarification"
    assert observation.suggested_action is None
    assert observation.failure_classification is None
    assert ledger.requests_used == 1
    assert captured == (
        {
            "method": "POST",
            "path": "/chat/completions",
            "model": "deepseek-v4-flash",
            "request_keys": ("max_tokens", "messages", "model", "reasoning_effort", "stream", "tool_choice", "tools"),
            "reasoning_effort": "none",
            "max_tokens": 128,
            "tool_choice": "required",
            "strict_values": (),
            "has_native_json_schema_response_format": False,
            "unexpected_openai_parameters": (),
        },
    )


def test_privacy_gate_rejects_forbidden_payload_before_mock_handler_or_capture():
    """Sensitive values never cross the test-only outbound boundary."""
    handler_called = False

    async def response(request: httpx2.Request) -> httpx2.Response:
        nonlocal handler_called
        handler_called = True
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "unused"},
        )

    async def exercise():
        async with ProviderSmokeHarness(
            response,
            forbidden_payload_values=("member-internal-id", "private wording"),
        ) as harness:
            with pytest.raises(OutboundPayloadPrivacyViolation):
                await harness._http_client.post(  # test the network seam directly
                    "https://api.deepseek.com/chat/completions",
                    json={
                        "model": "deepseek-v4-flash",
                        "messages": [{"role": "user", "content": "private wording"}],
                    },
                    headers={"Authorization": "Bearer offline-provider-placeholder"},
                )
            return harness.captured_requests, harness.ledger

    captured, ledger = asyncio.run(exercise())

    assert handler_called is False
    assert captured == ()
    assert ledger.requests_used == 0


def test_scenario_tool_registry_rejects_an_unlisted_tool_before_dispatch():
    """The allow-list, not merely a write-prefix deny-list, constrains tools."""
    handler_called = False
    ledger = GlobalUsageLedger(max_requests=4, max_total_tokens=2_000)

    async def response(request: httpx2.Request) -> httpx2.Response:
        nonlocal handler_called
        handler_called = True
        raise AssertionError("unlisted tool must not reach the mock Provider")

    transport = OfflineRecordingTransport(
        response,
        ledger=ledger,
        privacy_gate=OutboundPayloadPrivacyGate(
            allowed_tool_names=frozenset({"clarification"})
        ),
    )

    async def exercise() -> None:
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(OutboundPayloadPrivacyViolation):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={
                        "model": "deepseek-v4-flash",
                        "tools": [{"function": {"name": "get_trip_facts"}}],
                    },
                )

    asyncio.run(exercise())
    assert handler_called is False
    assert ledger.stop_reason == "privacy_boundary_violation"


def test_success_without_usage_stops_before_a_model_result_can_be_consumed():
    """A 2xx response without metered usage is a budget stop, not free usage."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        payload = _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        ).json()
        payload.pop("usage")
        return httpx2.Response(200, json=payload, request=request)

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            with pytest.raises(ProviderUsageUnavailable):
                await harness.run_r1_typed_clarification()
            return harness.ledger, harness.captured_requests

    ledger, captured = asyncio.run(exercise())
    assert ledger.requests_used == 1
    assert ledger.reported_total_tokens == 0
    assert len(captured) == 1


def test_missing_usage_stops_later_cases_before_their_http_handler_runs():
    """A missing usage response is terminal even if a caller retries another case."""
    handler_calls = 0

    async def response(request: httpx2.Request) -> httpx2.Response:
        nonlocal handler_calls
        handler_calls += 1
        payload = _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        ).json()
        payload.pop("usage")
        return httpx2.Response(200, json=payload, request=request)

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            with pytest.raises(ProviderUsageUnavailable):
                await harness.run_r1_typed_clarification()
            with pytest.raises(ProviderSmokeHarnessStopped, match="provider_usage_missing"):
                await harness.run_r1_typed_clarification()
            return harness.ledger, harness.captured_requests

    ledger, captured = asyncio.run(exercise())
    assert handler_calls == 1
    assert ledger.requests_used == 1
    assert ledger.stop_reason == "provider_usage_missing"
    assert len(captured) == 1


def test_r3_preserves_explicit_required_tool_choice_with_thinking_disabled():
    """R3 proves the required-tool wire contract without a live Provider."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(
            request,
            name="compatibility_probe",
            arguments={},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            observation = await harness.run_r3_required_tool_choice()
            return observation, harness.captured_requests, harness.ledger, harness

    observation, captured, ledger, harness = asyncio.run(exercise())

    assert observation.output_kind == "required_tool_choice_verified"
    assert ledger.requests_used == 1
    assert captured[0]["reasoning_effort"] == "none"
    assert captured[0]["tool_choice"] == "required"
    assert captured[0]["max_tokens"] == 128
    assert captured[0]["has_native_json_schema_response_format"] is False
    assert captured[0]["unexpected_openai_parameters"] == ()
    # Pydantic AI 2.54.0 omits explicit false rather than serializing strict=false.
    assert captured[0]["strict_values"] == ()
    assert harness.scenario_tool_invocations == (("r3", "compatibility_probe"),)


def test_r3_marks_missing_required_probe_as_inconclusive_and_stops_the_harness():
    """UsageLimitExceeded alone is not R3 success evidence."""
    handler_calls = 0

    async def response(request: httpx2.Request) -> httpx2.Response:
        nonlocal handler_calls
        handler_calls += 1
        return _text_completion(request, content="unexpected direct text")

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            with pytest.raises(SmokeScenarioVerificationFailed, match="r3_required_probe"):
                await harness.run_r3_required_tool_choice()
            return harness.ledger, harness.captured_requests

    ledger, captured = asyncio.run(exercise())
    assert handler_calls == 1
    assert len(captured) == 1
    assert ledger.stop_reason == "r3_required_probe_inconclusive"


def test_r2_reads_a_synthetic_trip_through_independent_sessions_and_returns_typed_preview(
    db: Session,
):
    """R2 uses a scoped worker Session and performs no durable domain mutation."""
    base_factory = _independent_session_factory(db)
    before_counts = _durable_counts(db)
    worker_sessions = []
    worker_sessions_closed = []

    def worker_session_factory():
        worker_db = base_factory()
        original_close = worker_db.close

        def close():
            worker_sessions_closed.append(worker_db)
            original_close()

        worker_db.close = close
        worker_sessions.append(worker_db)
        return worker_db

    with _synthetic_fixture(db) as fixture:
        fixture_counts = _durable_counts(db)
        item_id = fixture["items"]["art"].id
        membership_id = fixture["memberships"]["organizer"].id
        trip_id = fixture["trip"].id
        capability = TripReadCapability(
            trip_id=trip_id,
            actor_membership_id=membership_id,
            session_factory=worker_session_factory,
        )
        calls = 0

        async def response(request: httpx2.Request) -> httpx2.Response:
            nonlocal calls
            calls += 1
            wire_text = request.content.decode("utf-8")
            assert "scoped_item" in wire_text
            assert item_id not in wire_text
            if calls == 1:
                return _tool_completion(
                    request,
                    name="get_scoped_plan_item",
                    arguments={"item_ref": "scoped_item"},
                )
            if calls == 2:
                return _tool_completion(
                    request,
                    name="change_preview",
                    arguments={
                        "output_kind": "change_preview",
                        "suggested_action": {
                            "kind": "move_time",
                            "item_ref": "scoped_item",
                            "new_start_hour": 15.5,
                        },
                    },
                )
            raise AssertionError("R2 must not retry or make a third Provider request")

        async def exercise():
            async with ProviderSmokeHarness(
                response,
                forbidden_payload_values=(
                    PRIVATE_FIXTURE_PHRASE,
                    membership_id,
                    item_id,
                ),
            ) as harness:
                observation = await harness.run_r2_scoped_change_preview(
                    capability,
                    item_id=item_id,
                )
                return observation, harness.captured_requests, harness.deadline_models[-1]

        observation, captured, deadline_model = asyncio.run(exercise())
        assert _durable_counts(db) == fixture_counts

    assert observation.output_kind == "change_preview"
    assert observation.suggested_action == {
        "kind": "move_time",
        "item_ref": "scoped_item",
        "new_start_hour": 15.5,
    }
    assert calls == 2
    assert len(captured) == 2
    assert all(request["max_tokens"] == 128 for request in captured)
    assert all(
        request["has_native_json_schema_response_format"] is False
        for request in captured
    )
    assert len(deadline_model.invocation_timeouts) == 2
    assert all(0 < timeout <= 8.0 for timeout in deadline_model.invocation_timeouts)
    assert len(worker_sessions) == 2  # capability preflight plus the real read-only tool
    assert len(worker_sessions_closed) == 2
    assert all(worker_db is not db for worker_db in worker_sessions)
    assert all(
        worker_db.connection() is not db.connection() for worker_db in worker_sessions
    )
    evidence_text = repr((captured, observation))
    assert item_id not in evidence_text
    assert membership_id not in evidence_text
    assert PRIVATE_FIXTURE_PHRASE not in evidence_text
    with base_factory() as verification_db:
        assert verification_db.get(Trip, trip_id) is None
    assert _durable_counts(db) == before_counts


@pytest.mark.parametrize(
    ("status_code", "expected_failure", "expected_normalized"),
    [
        (400, "provider_compatibility", "provider_compatibility"),
        (401, "provider_compatibility", "provider_compatibility"),
        (429, "provider_http_429", "provider_rate_limited"),
        (503, "provider_http_5xx", "provider_unavailable"),
    ],
)
def test_provider_http_errors_are_classified_once_without_sdk_retries(
    status_code: int, expected_failure: str, expected_normalized: str
):
    """The client has max_retries=0: a provider failure gets exactly one call."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            status_code,
            json={"error": {"message": "offline failure", "type": "synthetic"}},
            request=request,
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            with pytest.raises(Exception) as error:
                await harness.run_r1_typed_clarification()
            with pytest.raises(ProviderSmokeHarnessStopped, match=f"provider_http_{status_code}"):
                await harness.run_r1_typed_clarification()
            return error.value, harness.ledger, harness.captured_requests

    error, ledger, captured = asyncio.run(exercise())
    assert classify_smoke_exception(error) == expected_failure
    assert normalize_smoke_failure(error).normalized_evaluation_failure == expected_normalized
    assert ledger.requests_used == 1
    assert len(captured) == 1
    assert ledger.stop_reason == f"provider_http_{status_code}"


@pytest.mark.parametrize(
    ("error", "technical", "normalized"),
    [
        (
            AgentProviderDeadlineExceeded("offline provider deadline"),
            "provider_timeout",
            "provider_timeout",
        ),
        (
            AgentToolDeadlineExceeded("offline tool deadline"),
            "tool_timeout",
            "tool_timeout",
        ),
        (
            ProviderUsageUnavailable("usage absent"),
            "missing_usage",
            "usage_unavailable",
        ),
        (
            ProviderUsageBudgetExceeded("usage exceeded"),
            "usage_budget_exceeded",
            "usage_limit",
        ),
        (
            OutboundPayloadPrivacyViolation("private payload"),
            "privacy_boundary_violation",
            "privacy_boundary_violation",
        ),
        (
            asyncio.CancelledError(),
            "request_cancelled",
            "request_cancelled",
        ),
    ],
)
def test_technical_failure_taxonomy_maps_to_normalized_evaluation_failure(
    error: BaseException, technical: str, normalized: str
):
    """The runner can retain detail without changing the cross-runtime grader contract."""
    classification = normalize_smoke_failure(error)
    assert classification.technical_failure == technical
    assert classification.normalized_evaluation_failure == normalized


def test_reported_token_ceiling_refuses_a_response_before_agent_output_consumption():
    """Aggregate reported usage, rather than an estimate, enforces the ceiling."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(
            response,
            ledger=GlobalUsageLedger(max_requests=4, max_total_tokens=27),
        ) as harness:
            with pytest.raises(ProviderUsageBudgetExceeded):
                await harness.run_r1_typed_clarification()
            return harness.ledger, harness.captured_requests

    ledger, captured = asyncio.run(exercise())
    assert ledger.requests_used == 1
    # The rejected response actually reported 28 tokens; evidence is retained
    # instead of being made to look free just because it crossed the ceiling.
    assert ledger.reported_total_tokens == 28
    assert ledger.stop_reason == "reported_token_budget_exceeded"
    assert len(captured) == 1


def test_late_response_is_discarded_after_request_deadline():
    """Late remote completion is observable but cannot enter the Agent result."""
    handler_started = asyncio.Event()
    release_remote_work = asyncio.Event()
    response_finished = asyncio.Event()

    async def response(request: httpx2.Request) -> httpx2.Response:
        handler_started.set()
        # The local request will stop awaiting at its deadline, while this
        # accepted mock request keeps running until an explicit release.
        await release_remote_work.wait()
        response_finished.set()
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            asyncio.get_running_loop().call_later(0.15, release_remote_work.set)
            with pytest.raises(AgentRequestDeadlineExceeded):
                await harness.run_r1_typed_clarification(request_timeout_seconds=0.1)
            await handler_started.wait()
            await asyncio.wait_for(response_finished.wait(), timeout=0.4)
            await asyncio.sleep(0)
            return harness, harness.ledger, harness.captured_requests, harness.deadline_models[-1]

    harness, ledger, captured, model = asyncio.run(exercise())
    assert response_finished.is_set()
    assert ledger.requests_used == 1
    assert len(captured) == 1
    assert 0 < model.invocation_timeouts[0] <= 0.1
    assert model.late_provider_completions == 1
    assert model.active_late_provider_tasks == 0
    assert harness.unfinished_late_tasks_at_close == 0
    assert ledger.stop_reason == "request_timeout"


def test_provider_deadline_stops_later_cases_but_discards_the_late_response(
    monkeypatch: pytest.MonkeyPatch,
):
    """Provider budget expiry differs from the longer outer request deadline."""
    monkeypatch.setattr(smoke_harness, "PROVIDER_TIMEOUT_SECONDS", 0.01)
    handler_started = asyncio.Event()
    release_provider = asyncio.Event()
    response_finished = asyncio.Event()

    async def response(request: httpx2.Request) -> httpx2.Response:
        handler_started.set()
        await release_provider.wait()
        response_finished.set()
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            with pytest.raises(AgentProviderDeadlineExceeded):
                await harness.run_r1_typed_clarification(request_timeout_seconds=1.0)
            await handler_started.wait()
            release_provider.set()
            await asyncio.wait_for(response_finished.wait(), timeout=0.2)
            await asyncio.sleep(0)
            timed_out_model = harness.deadline_models[-1]
            with pytest.raises(ProviderSmokeHarnessStopped, match="provider_timeout"):
                await harness.run_r1_typed_clarification()
            return harness.ledger, harness.captured_requests, timed_out_model

    ledger, captured, model = asyncio.run(exercise())
    assert ledger.requests_used == 1
    assert ledger.stop_reason == "provider_timeout"
    assert len(captured) == 1
    assert model.late_provider_completions == 1
    assert model.active_late_provider_tasks == 0


def test_local_cancellation_discards_a_response_after_the_provider_returns():
    """Local cancellation is distinct from terminating the in-flight mock work."""
    entered_handler = asyncio.Event()
    release_handler = asyncio.Event()

    async def response(request: httpx2.Request) -> httpx2.Response:
        entered_handler.set()
        await release_handler.wait()
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            task = asyncio.create_task(harness.run_r1_typed_clarification())
            await entered_handler.wait()
            harness.deadline_models[-1].context.request_cancellation()
            release_handler.set()
            with pytest.raises(AgentRequestDeadlineExceeded):
                await task
            return harness.ledger, harness.captured_requests

    ledger, captured = asyncio.run(exercise())
    assert ledger.requests_used == 1
    assert len(captured) == 1
    assert ledger.stop_reason == "request_timeout"


def test_outer_task_cancel_tracks_and_discards_the_cancelled_provider_task():
    """Cancelling the caller never leaves its provider task untracked."""
    handler_started = asyncio.Event()
    provider_cancelled = asyncio.Event()
    release_handler = asyncio.Event()

    async def response(request: httpx2.Request) -> httpx2.Response:
        handler_started.set()
        try:
            await release_handler.wait()
        except asyncio.CancelledError:
            provider_cancelled.set()
            await release_handler.wait()
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            outer_task = asyncio.create_task(harness.run_r1_typed_clarification())
            await handler_started.wait()
            outer_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await outer_task
            await asyncio.wait_for(provider_cancelled.wait(), timeout=0.2)
            release_handler.set()
            await asyncio.sleep(0)
            return harness, harness.deadline_models[-1], harness.ledger

    harness, model, ledger = asyncio.run(exercise())
    assert model.context.cancellation_requested is True
    assert model.late_provider_completions == 1
    assert model.active_late_provider_tasks == 0
    assert harness.unfinished_late_tasks_at_close == 0
    assert ledger.stop_reason == "request_cancelled"


def test_streaming_attempt_is_refused_before_an_http_request_is_created():
    """The smoke harness has a non-streaming contract by construction."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            await harness.run_r1_typed_clarification()
            model = harness.deadline_models[-1]
            with pytest.raises(UnexpectedStreamingAttempt):
                async with model.request_stream(None, None, None):
                    pass
            return harness.ledger, harness.captured_requests

    ledger, captured = asyncio.run(exercise())
    assert ledger.requests_used == 1
    assert len(captured) == 1


def test_malformed_typed_output_is_not_retried_or_accepted():
    """Output validation has a zero retry budget in the smoke harness."""

    async def response(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(request, name="clarification", arguments={})

    async def exercise():
        async with ProviderSmokeHarness(response) as harness:
            with pytest.raises(Exception) as error:
                await harness.run_r1_typed_clarification()
            return error.value, harness.ledger, harness.captured_requests

    error, ledger, captured = asyncio.run(exercise())
    assert classify_smoke_exception(error) == "malformed_output"
    assert ledger.requests_used == 1
    assert len(captured) == 1


def test_invalid_read_tool_arguments_are_rejected_without_a_retrying_provider_call(
    db: Session,
):
    """Typed tool validation rejects a non-string PlanItem ID before tool logic."""
    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=_independent_session_factory(db),
        )

        async def response(request: httpx2.Request) -> httpx2.Response:
            return _tool_completion(
                request,
                name="get_scoped_plan_item",
                arguments={"item_ref": "foreign_item"},
            )

        async def exercise():
            async with ProviderSmokeHarness(response) as harness:
                with pytest.raises(Exception) as error:
                    await harness.run_r2_scoped_change_preview(
                        capability,
                        item_id=fixture["items"]["art"].id,
                    )
                return error.value, harness.ledger, harness.captured_requests

        error, ledger, captured = asyncio.run(exercise())

    assert classify_smoke_exception(error) == "tool_schema_failure"
    assert ledger.stop_reason == "tool_schema_failure"
    assert ledger.requests_used == 1
    assert len(captured) == 1


def test_cross_trip_capability_is_rejected_before_any_provider_request(db: Session):
    """A foreign membership fails closed before a model receives trip facts."""
    handler_called = False

    async def response(request: httpx2.Request) -> httpx2.Response:
        nonlocal handler_called
        handler_called = True
        raise AssertionError("cross-trip capability must fail before Provider dispatch")

    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["foreign_member"].id,
            session_factory=_independent_session_factory(db),
        )

        async def exercise():
            async with ProviderSmokeHarness(response) as harness:
                with pytest.raises(ValueError, match="Membership does not belong to this trip"):
                    await harness.run_r2_scoped_change_preview(
                        capability,
                        item_id=fixture["items"]["art"].id,
                    )
                return harness.ledger, harness.captured_requests

        ledger, captured = asyncio.run(exercise())

    assert handler_called is False
    assert ledger.requests_used == 0
    assert captured == ()
    assert ledger.stop_reason == "tool_schema_failure"


def test_synthetic_fixture_cleanup_runs_after_provider_failure(db: Session):
    """A failed R2 request cannot leave its committed visibility fixture behind."""
    base_factory = _independent_session_factory(db)
    before_counts = _durable_counts(db)
    fixture_trip_id = None

    async def response(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            503,
            json={"error": {"message": "offline provider unavailable"}},
            request=request,
        )

    with _synthetic_fixture(db) as fixture:
        fixture_trip_id = fixture["trip"].id
        capability = TripReadCapability(
            trip_id=fixture_trip_id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=base_factory,
        )

        async def exercise():
            async with ProviderSmokeHarness(response) as harness:
                with pytest.raises(Exception) as error:
                    await harness.run_r2_scoped_change_preview(
                        capability,
                        item_id=fixture["items"]["art"].id,
                    )
                return error.value, harness.ledger

        error, ledger = asyncio.run(exercise())
        assert classify_smoke_exception(error) == "provider_http_5xx"
        assert ledger.stop_reason == "provider_http_503"
        assert ledger.requests_used == 1

    assert fixture_trip_id is not None
    with base_factory() as verification_db:
        assert verification_db.get(Trip, fixture_trip_id) is None
    assert _durable_counts(db) == before_counts


def test_slow_read_tool_hits_its_own_deadline_before_a_second_provider_turn(
    db: Session,
):
    """A tool deadline is independent from both request and Provider limits."""
    base_factory = _independent_session_factory(db)
    factory_calls = 0
    tool_entered = threading.Event()
    release_tool = threading.Event()

    def delayed_worker_session_factory():
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 2:  # first is capability preflight; second is the tool
            tool_entered.set()
            assert release_tool.wait(timeout=0.5)
        return base_factory()

    with _synthetic_fixture(db) as fixture:
        capability = TripReadCapability(
            trip_id=fixture["trip"].id,
            actor_membership_id=fixture["memberships"]["organizer"].id,
            session_factory=delayed_worker_session_factory,
        )

        async def response(request: httpx2.Request) -> httpx2.Response:
            return _tool_completion(
                request,
                name="get_scoped_plan_item",
                arguments={"item_ref": "scoped_item"},
            )

        async def exercise():
            async with ProviderSmokeHarness(response) as harness:
                asyncio.get_running_loop().call_later(0.05, release_tool.set)
                with pytest.raises(AgentToolDeadlineExceeded):
                    await harness.run_r2_scoped_change_preview(
                        capability,
                        item_id=fixture["items"]["art"].id,
                        tool_timeout_seconds=0.01,
                    )
                return harness.ledger, harness.captured_requests

        ledger, captured = asyncio.run(exercise())

    assert tool_entered.is_set()
    assert ledger.requests_used == 1
    assert len(captured) == 1
    assert ledger.stop_reason == "tool_timeout"


def _tool_completion(
    request: httpx2.Request, *, name: str, arguments: dict[str, object]
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "offline-response",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "offline-tool-call",
                                "type": "function",
                                "function": {
                                    "name": name,
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28},
        },
        request=request,
    )


def _text_completion(request: httpx2.Request, *, content: str) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "offline-text-response",
            "object": "chat.completion",
            "created": 0,
            "model": "deepseek-v4-flash",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28},
        },
        request=request,
    )


def _durable_counts(db: Session) -> tuple[int, int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(PlanItem)) or 0,
        db.scalar(select(func.count()).select_from(ChangeProposal)) or 0,
        db.scalar(select(func.count()).select_from(DecisionRound)) or 0,
        db.scalar(select(func.count()).select_from(Vote)) or 0,
    )
