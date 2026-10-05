"""Offline contracts for the separately constructed PR-03R.2 live adapter.

No test in this module may construct a socket-backed transport.  The injected
network seam is an in-memory transport that observes only redacted contract
facts after the adapter's controls have accepted the request.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field

import httpx2
import pytest

from app.agents.provider_smoke_harness import (
    GlobalUsageLedger,
    LiveDeepSeekSmokeAdapter,
    LiveProviderHostRejected,
    OutboundPayloadPrivacyViolation,
    ProviderSmokeHarnessStopped,
)
from app.agents.execution import AgentProviderDeadlineExceeded
import app.agents.provider_smoke_harness as smoke_harness


@dataclass
class InMemoryNetworkTransport(httpx2.AsyncBaseTransport):
    """Public injected network seam; never opens a socket."""

    responder: object
    requests: list[httpx2.Request] = field(default_factory=list)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        response = self.responder(request)
        if asyncio.iscoroutine(response):
            return await response
        return response


def test_live_adapter_applies_the_same_redacted_counter_and_usage_contract_offline():
    """Its public execution seam is safe before a real credential is supplied."""

    async def responder(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    network = InMemoryNetworkTransport(responder)
    adapter = LiveDeepSeekSmokeAdapter(
        api_key="test-only-key",
        network_transport_factory=lambda: network,
    )

    async def exercise():
        async with adapter.create_harness() as harness:
            observation = await harness.run_r1_typed_clarification()
            return observation, harness.ledger, harness.captured_requests

    observation, ledger, captured = asyncio.run(exercise())
    assert observation.output_kind == "clarification"
    assert len(network.requests) == 1
    assert ledger.requests_used == 1
    assert ledger.reported_total_tokens == 28
    assert ledger.reported_usage == (
        {"prompt_tokens": 21, "completion_tokens": 7, "total_tokens": 28},
    )
    assert captured[0]["model"] == "deepseek-v4-flash"
    assert "test-only-key" not in repr(captured)


def test_live_adapter_rejects_a_non_allowlisted_https_host_before_network_dispatch():
    """The live transport allows only the exact DeepSeek HTTPS authority."""
    network = InMemoryNetworkTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("must not dispatch"))
    )
    adapter = LiveDeepSeekSmokeAdapter(
        api_key="test-only-key",
        network_transport_factory=lambda: network,
    )

    async def exercise():
        transport = adapter.create_transport()
        async with httpx2.AsyncClient(transport=transport, follow_redirects=False) as client:
            with pytest.raises(LiveProviderHostRejected):
                await client.post(
                    "https://unexpected.example/chat/completions",
                    json={"model": "deepseek-v4-flash", "messages": []},
                )
        return adapter.ledger

    ledger = asyncio.run(exercise())
    assert network.requests == []
    assert ledger.requests_used == 0
    assert ledger.stop_reason == "provider_host_not_allowed"


def test_live_adapter_runs_privacy_gate_before_the_injected_network_boundary():
    """Live transport has the same pre-dispatch privacy boundary as Mock mode."""
    network = InMemoryNetworkTransport(
        lambda request: (_ for _ in ()).throw(AssertionError("must not dispatch"))
    )
    adapter = LiveDeepSeekSmokeAdapter(
        api_key="test-only-key",
        forbidden_payload_values=("private synthetic wording",),
        network_transport_factory=lambda: network,
    )

    async def exercise():
        transport = adapter.create_transport()
        async with httpx2.AsyncClient(transport=transport, follow_redirects=False) as client:
            with pytest.raises(OutboundPayloadPrivacyViolation):
                await client.post(
                    "https://api.deepseek.com/chat/completions",
                    json={
                        "model": "deepseek-v4-flash",
                        "messages": [{"role": "user", "content": "private synthetic wording"}],
                    },
                )
        return adapter.ledger

    ledger = asyncio.run(exercise())
    assert network.requests == []
    assert ledger.requests_used == 0
    assert ledger.stop_reason == "privacy_boundary_violation"


def test_live_adapter_keeps_the_shared_provider_deadline_and_terminal_stop(
    monkeypatch: pytest.MonkeyPatch,
):
    """The injected live seam cannot bypass the framework-neutral deadline wrapper."""
    monkeypatch.setattr(smoke_harness, "PROVIDER_TIMEOUT_SECONDS", 0.01)
    started = asyncio.Event()
    release = asyncio.Event()

    async def responder(request: httpx2.Request) -> httpx2.Response:
        started.set()
        await release.wait()
        return _tool_completion(
            request,
            name="clarification",
            arguments={"output_kind": "clarification", "question": "Which time?"},
        )

    network = InMemoryNetworkTransport(responder)
    adapter = LiveDeepSeekSmokeAdapter(
        api_key="test-only-key",
        network_transport_factory=lambda: network,
    )

    async def exercise():
        async with adapter.create_harness() as harness:
            with pytest.raises(AgentProviderDeadlineExceeded):
                await harness.run_r1_typed_clarification(request_timeout_seconds=1.0)
            await started.wait()
            release.set()
            await asyncio.sleep(0)
            with pytest.raises(ProviderSmokeHarnessStopped, match="provider_timeout"):
                await harness.run_r1_typed_clarification()
            return harness.ledger

    ledger = asyncio.run(exercise())
    assert len(network.requests) == 1
    assert ledger.stop_reason == "provider_timeout"


def test_live_adapter_does_not_follow_a_redirect_and_stops_future_admission():
    """A redirect is a terminal non-2xx response, never a second network hop."""

    async def responder(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            302,
            headers={"Location": "https://unexpected.example/redirect"},
            request=request,
        )

    network = InMemoryNetworkTransport(responder)
    adapter = LiveDeepSeekSmokeAdapter(
        api_key="test-only-key",
        network_transport_factory=lambda: network,
    )

    async def exercise():
        async with adapter.create_harness() as harness:
            with pytest.raises(Exception):
                await harness.run_r1_typed_clarification()
            with pytest.raises(ProviderSmokeHarnessStopped, match="provider_http_302"):
                await harness.run_r1_typed_clarification()
            return harness.ledger

    ledger = asyncio.run(exercise())
    assert len(network.requests) == 1
    assert ledger.requests_used == 1
    assert ledger.stop_reason == "provider_http_302"


def test_r3_accepts_an_incremental_500_token_budget_and_blocks_its_second_turn():
    """R3 proves only its first required-tool turn under the new one-request budget."""

    async def responder(request: httpx2.Request) -> httpx2.Response:
        return _tool_completion(
            request,
            name="compatibility_probe",
            arguments={},
        )

    network = InMemoryNetworkTransport(responder)
    ledger = GlobalUsageLedger(max_requests=1, max_total_tokens=500)
    adapter = LiveDeepSeekSmokeAdapter(
        api_key="test-only-key",
        ledger=ledger,
        network_transport_factory=lambda: network,
    )

    async def exercise():
        async with adapter.create_harness() as harness:
            observation = await harness.run_r3_required_tool_choice(
                total_tokens_limit=500
            )
            return observation, harness.captured_requests, harness.scenario_tool_invocations

    observation, captured, invocations = asyncio.run(exercise())
    assert observation.output_kind == "required_tool_choice_verified"
    assert len(network.requests) == 1
    assert ledger.requests_used == 1
    assert ledger.reported_total_tokens == 28
    assert captured[0]["reasoning_effort"] == "none"
    assert captured[0]["tool_choice"] == "required"
    assert captured[0]["max_tokens"] == 128
    assert invocations == (("r3", "compatibility_probe"),)


def _tool_completion(
    request: httpx2.Request, *, name: str, arguments: dict[str, object]
) -> httpx2.Response:
    return httpx2.Response(
        200,
        json={
            "id": "offline-live-adapter-response",
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
