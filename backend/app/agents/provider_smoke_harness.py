"""Test-only, offline boundary primitives for the PR-03R smoke harness.

This module is intentionally not imported by Cadensy's HTTP routes or Legacy
Agent Runtime.  The offline transport is constructed around an httpx2 mock
handler, so it has no socket-backed transport and cannot make a real provider
request by itself.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx2
from openai import AsyncOpenAI
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry, RunContext, ToolOutput, UsageLimits, models
from pydantic_ai.exceptions import ModelHTTPError, UnexpectedModelBehavior, UsageLimitExceeded
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.profiles.openai import OpenAIModelProfile

from .execution import (
    AgentExecutionConfig,
    AgentExecutionContext,
    AgentProviderDeadlineExceeded,
    AgentRequestDeadlineExceeded,
    AgentToolDeadlineExceeded,
)
from .pydantic_poc import (
    ModelClarification,
    TripReadCapability,
)
from .tools import build_read_only_trip_tools


DEEPSEEK_MODEL_ID = "deepseek-v4-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_LIVE_HOSTS = frozenset({"api.deepseek.com"})
MAX_PROVIDER_REQUESTS = 4
MAX_REPORTED_TOTAL_TOKENS = 2_000
PROVIDER_TIMEOUT_SECONDS = 8.0
PER_REQUEST_OUTPUT_CAP = 128
OFFLINE_API_KEY_PLACEHOLDER = "offline-provider-placeholder"
SCENARIO_ALLOWED_TOOL_NAMES: dict[str, frozenset[str]] = {
    "r1": frozenset({"clarification"}),
    "r2": frozenset(
        {"get_scoped_plan_item", "change_preview", "clarification"}
    ),
    "r3": frozenset({"compatibility_probe"}),
}


class ProviderSmokeHarnessStopped(RuntimeError):
    """A terminal smoke-run condition refuses all later outbound work."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class ProviderRequestBudgetExceeded(ProviderSmokeHarnessStopped):
    """The process-wide outbound provider-request budget is exhausted."""


class ProviderUsageUnavailable(RuntimeError):
    """A successful provider response did not contain reportable usage."""


class ProviderUsageBudgetExceeded(RuntimeError):
    """A provider response exceeded the process-wide reported-token budget."""


class UnexpectedStreamingAttempt(RuntimeError):
    """The smoke harness is intentionally non-streaming."""


class OutboundPayloadPrivacyViolation(RuntimeError):
    """The test-only outbound boundary refused a sensitive or write payload."""


class LiveProviderHostRejected(RuntimeError):
    """The live test boundary permits only the documented DeepSeek authority."""


class SmokeScenarioVerificationFailed(RuntimeError):
    """A scenario did not meet its declared, non-negotiable success criteria."""


@dataclass(frozen=True)
class SmokeFailureClassification:
    """Trace-safe bridge from a technical fault to the PR-02 evaluation label."""

    technical_failure: str
    normalized_evaluation_failure: str


@dataclass(frozen=True)
class OutboundPayloadPrivacyGate:
    """Reject payloads that would violate the smoke-run privacy contract.

    This gate runs before request admission, capture, or mock-handler dispatch.
    It inspects the transient JSON object only; it intentionally records none
    of the values it rejects.
    """

    forbidden_values: tuple[str, ...] = ()
    allowed_tool_names: frozenset[str] | None = None

    def validate(self, payload: dict[str, Any]) -> None:
        self._reject_credential_fields(payload)
        payload_text = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        if any(value and value in payload_text for value in self.forbidden_values):
            raise OutboundPayloadPrivacyViolation(
                "Outbound payload contains a forbidden private value"
            )
        for tool in payload.get("tools") or []:
            name = (
                tool.get("function", {}).get("name")
                if isinstance(tool, dict) and isinstance(tool.get("function"), dict)
                else None
            )
            if self.allowed_tool_names is not None and name not in self.allowed_tool_names:
                raise OutboundPayloadPrivacyViolation(
                    "Outbound payload exposes a tool outside the scenario registry"
                )
            if isinstance(name, str) and name.startswith(
                ("apply_", "create_", "delete_", "mutate_", "submit_", "update_", "vote_", "write_")
            ):
                raise OutboundPayloadPrivacyViolation(
                    "Outbound payload exposes a write-capable Domain tool"
                )

    @staticmethod
    def _reject_credential_fields(value: object) -> None:
        if isinstance(value, dict):
            for key, nested in value.items():
                if str(key).casefold().replace("-", "_") in {
                    "api_key",
                    "authorization",
                    "x_api_key",
                }:
                    raise OutboundPayloadPrivacyViolation(
                        "Outbound payload contains a credential field"
                    )
                OutboundPayloadPrivacyGate._reject_credential_fields(nested)
        elif isinstance(value, list):
            for nested in value:
                OutboundPayloadPrivacyGate._reject_credential_fields(nested)


@dataclass
class GlobalUsageLedger:
    """One process-wide budget shared by all future R1/R2/R3 Agent runs."""

    max_requests: int
    max_total_tokens: int
    _requests_used: int = 0
    _reported_total_tokens: int = 0
    _reported_usage: list[dict[str, int | None]] = field(default_factory=list)
    _stop_reason: str | None = None

    @property
    def requests_used(self) -> int:
        return self._requests_used

    @property
    def reported_total_tokens(self) -> int:
        return self._reported_total_tokens

    @property
    def reported_usage(self) -> tuple[dict[str, int | None], ...]:
        """Provider counters only; never request/response text or headers."""
        return tuple(self._reported_usage)

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    @property
    def reported_cost_usd(self) -> None:
        """Offline fixtures cannot truthfully report or estimate Provider billing."""
        return None

    @property
    def pricing_status(self) -> str:
        return "offline_mock_unbilled"

    def reserve_provider_request(self) -> None:
        """Reserve exactly one request before the mock handler is invoked."""
        self._raise_if_stopped()
        if self._requests_used >= self.max_requests:
            self.stop("provider_request_budget_exhausted")
            raise ProviderRequestBudgetExceeded(
                "provider_request_budget_exhausted"
            )
        self._requests_used += 1

    def record_reported_usage(self, usage: object) -> None:
        """Record numeric provider usage or stop before the response is consumed."""
        if not isinstance(usage, dict):
            self.stop("provider_usage_missing")
            raise ProviderUsageUnavailable("Provider response omitted usage metadata")
        total_tokens = usage.get("total_tokens")
        if not isinstance(total_tokens, int) or isinstance(total_tokens, bool):
            self.stop("provider_usage_invalid")
            raise ProviderUsageUnavailable("Provider response omitted numeric total_tokens")
        if total_tokens < 0:
            self.stop("provider_usage_invalid")
            raise ProviderUsageUnavailable("Provider response reported negative total_tokens")
        prompt_tokens = _optional_usage_counter(usage.get("prompt_tokens"))
        completion_tokens = _optional_usage_counter(usage.get("completion_tokens"))
        self._reported_usage.append(
            {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
            }
        )
        updated_total = self._reported_total_tokens + total_tokens
        # Keep observed usage evidence even when the current response crosses
        # the operational ceiling; a rejected response was not free.
        self._reported_total_tokens = updated_total
        if updated_total > self.max_total_tokens:
            self.stop("reported_token_budget_exceeded")
            raise ProviderUsageBudgetExceeded(
                f"Reported token budget of {self.max_total_tokens} would be exceeded"
            )
        if updated_total == self.max_total_tokens:
            self.stop("reported_token_budget_reached")

    def stop(self, reason: str) -> None:
        """Record the first terminal condition; it is intentionally immutable."""
        if self._stop_reason is None:
            self._stop_reason = reason

    def _raise_if_stopped(self) -> None:
        if self._stop_reason == "provider_request_budget_exhausted":
            raise ProviderRequestBudgetExceeded(self._stop_reason)
        if self._stop_reason is not None:
            raise ProviderSmokeHarnessStopped(self._stop_reason)


def _optional_usage_counter(value: object) -> int | None:
    """Accept an optional non-negative provider counter without inventing one."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return None


ResponseHandler = Callable[
    [httpx2.Request], httpx2.Response | Awaitable[httpx2.Response]
]


@dataclass
class OfflineRecordingTransport(httpx2.AsyncBaseTransport):
    """Mock-only HTTP boundary that counts requests before dispatching them.

    Captured evidence deliberately excludes request bodies, headers, and
    response bodies. The handler may inspect its in-memory request to emulate
    a provider, but no sensitive payload is retained by this transport.
    """

    handler: ResponseHandler
    ledger: GlobalUsageLedger
    privacy_gate: OutboundPayloadPrivacyGate = field(
        default_factory=OutboundPayloadPrivacyGate
    )
    _captured_requests: list[dict[str, object]] = field(default_factory=list)

    @property
    def captured_requests(self) -> tuple[dict[str, object], ...]:
        return tuple(self._captured_requests)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        await request.aread()
        payload = json.loads(request.content.decode("utf-8"))
        try:
            self.privacy_gate.validate(payload)
        except OutboundPayloadPrivacyViolation:
            self.ledger.stop("privacy_boundary_violation")
            raise
        self.ledger.reserve_provider_request()
        self._captured_requests.append(
            _redacted_wire_contract(request, payload)
        )
        response = self.handler(request)
        if inspect.isawaitable(response):
            response = await response
        if 200 <= response.status_code <= 299:
            self.ledger.record_reported_usage(response.json().get("usage"))
        else:
            self.ledger.stop(f"provider_http_{response.status_code}")
        return response


@dataclass
class LiveProviderTransport(httpx2.AsyncBaseTransport):
    """Controlled live-only HTTP boundary for a separately authorized run.

    It deliberately owns real-network admission rather than reusing the Mock
    transport with a different credential. It stores only redacted request
    contract facts and no response, header, or credential material.
    """

    network_transport: httpx2.AsyncBaseTransport
    ledger: GlobalUsageLedger
    privacy_gate: OutboundPayloadPrivacyGate = field(
        default_factory=OutboundPayloadPrivacyGate
    )
    _captured_requests: list[dict[str, object]] = field(default_factory=list)

    @property
    def captured_requests(self) -> tuple[dict[str, object], ...]:
        return tuple(self._captured_requests)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        host = (request.url.host or "").casefold()
        if (
            request.url.scheme != "https"
            or host not in DEEPSEEK_LIVE_HOSTS
            or request.url.port not in {None, 443}
        ):
            self.ledger.stop("provider_host_not_allowed")
            raise LiveProviderHostRejected("Live Provider host is not allowlisted")

        await request.aread()
        payload = json.loads(request.content.decode("utf-8"))
        try:
            self.privacy_gate.validate(payload)
        except OutboundPayloadPrivacyViolation:
            self.ledger.stop("privacy_boundary_violation")
            raise

        self.ledger.reserve_provider_request()
        self._captured_requests.append(_redacted_wire_contract(request, payload))
        response = await self.network_transport.handle_async_request(request)
        await response.aread()
        if 200 <= response.status_code <= 299:
            self.ledger.record_reported_usage(response.json().get("usage"))
        else:
            self.ledger.stop(f"provider_http_{response.status_code}")
        return response

    async def aclose(self) -> None:
        await self.network_transport.aclose()


@dataclass(frozen=True)
class SmokeObservation:
    """Redacted, framework-neutral result for one offline smoke scenario."""

    output_kind: str
    suggested_action: dict[str, object] | None
    failure_classification: str | None


class ScopedPlanItemOutput(BaseModel):
    """Model-safe fact projection with no database identifier."""

    item_ref: Literal["scoped_item"]
    title: str
    start_hour: float
    settledness: str


class ScopedSuggestedAction(BaseModel):
    """A test-only suggestion targeting the sole model-visible item reference."""

    kind: Literal["move_time"]
    item_ref: Literal["scoped_item"]
    new_start_hour: float


class ScopedModelChangePreview(BaseModel):
    """Typed R2 output that cannot name a raw PlanItem identifier."""

    output_kind: Literal["change_preview"]
    suggested_action: ScopedSuggestedAction


@dataclass
class _SmokeRunState:
    """Ephemeral, trace-safe proof that a model preview followed a real read."""

    tool_trajectory: list[str] = field(default_factory=list)
    read_item_refs: set[str] = field(default_factory=set)


class DeadlineBoundModel(WrapperModel):
    """Test-only model wrapper that recomputes timeout for every request."""

    def __init__(self, wrapped: OpenAIChatModel, *, context: AgentExecutionContext):
        super().__init__(wrapped)
        self.context = context
        self.invocation_timeouts: list[float] = []
        self._late_provider_tasks: set[asyncio.Task[object]] = set()
        self.late_provider_completions = 0

    @property
    def active_late_provider_tasks(self) -> int:
        return len(self._late_provider_tasks)

    @property
    def late_provider_tasks(self) -> tuple[asyncio.Task[object], ...]:
        """Pending result-discard tasks, exposed only for test-only cleanup."""
        return tuple(self._late_provider_tasks)

    async def request(self, messages, model_settings, model_request_parameters):  # type: ignore[no-untyped-def]
        request_remaining = self.context.request_remaining_seconds()
        timeout = min(PROVIDER_TIMEOUT_SECONDS, request_remaining)
        self.context.ensure_request_active()
        if timeout <= 0:
            self.context.ensure_request_active()
        settings = dict(model_settings or {})
        settings["timeout"] = timeout
        self.invocation_timeouts.append(timeout)
        provider_task = asyncio.create_task(
            self.wrapped.request(
                messages,
                settings,
                model_request_parameters,
            )
        )
        try:
            completed, pending = await asyncio.wait({provider_task}, timeout=timeout)
        except asyncio.CancelledError:
            # This requests cancellation of the local SDK coroutine only.  It
            # cannot prove that an already accepted remote Provider request
            # has been terminated, so retain the task and discard any late
            # terminal result instead of letting it become unobserved work.
            self.context.request_cancellation()
            if not provider_task.done():
                provider_task.cancel()
            self._track_late_completion(provider_task)
            raise
        if pending:
            self.context.request_cancellation()
            self._track_late_completion(provider_task)
            if request_remaining <= PROVIDER_TIMEOUT_SECONDS:
                raise AgentRequestDeadlineExceeded(
                    "Agent request deadline elapsed during provider invocation"
                )
            raise AgentProviderDeadlineExceeded(
                "Provider invocation exceeded its local deadline"
            )
        response = provider_task.result()
        # Do not hand a late response to the Pydantic agent graph, whose next
        # step could otherwise process a tool or output result.
        self.context.after_provider()
        return response

    def _track_late_completion(self, task: asyncio.Task[object]) -> None:
        """Discard an unawaited provider result once it eventually completes.

        Calling ``Task.cancel`` cannot stop an already accepted remote request,
        and `wait_for` may itself wait for a cancellation-resistant awaitable.
        We therefore stop awaiting at the local deadline, retain only a bounded
        task reference until completion, and consume no result or exception.
        """
        if task in self._late_provider_tasks:
            return
        self._late_provider_tasks.add(task)

        def discard_completed_task(completed: asyncio.Task[object]) -> None:
            self._late_provider_tasks.discard(completed)
            self.late_provider_completions += 1
            try:
                completed.result()
            except BaseException:
                # The event is counted but no payload, response, or trace is
                # persisted. The deadline response has already been returned.
                pass

        task.add_done_callback(discard_completed_task)

    @asynccontextmanager
    async def request_stream(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        del args, kwargs
        raise UnexpectedStreamingAttempt("Provider smoke harness forbids streaming")
        yield  # pragma: no cover


class ProviderSmokeHarness:
    """Offline-only Pydantic AI smoke harness.

    It owns a mock HTTP transport, uses a non-secret placeholder as the SDK
    credential, and briefly enables Pydantic model requests only inside the
    in-process mock boundary. No production route imports this class.
    """

    def __init__(
        self,
        response_handler: ResponseHandler,
        *,
        ledger: GlobalUsageLedger | None = None,
        forbidden_payload_values: tuple[str, ...] = (),
    ) -> None:
        self.ledger = ledger or GlobalUsageLedger(
            max_requests=MAX_PROVIDER_REQUESTS,
            max_total_tokens=MAX_REPORTED_TOTAL_TOKENS,
        )
        self._forbidden_payload_values = forbidden_payload_values
        self._transport = OfflineRecordingTransport(
            response_handler,
            ledger=self.ledger,
            privacy_gate=OutboundPayloadPrivacyGate(
                forbidden_payload_values
            ),
        )
        self._http_client = httpx2.AsyncClient(
            transport=self._transport,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            trust_env=False,
        )
        self._sdk_client = AsyncOpenAI(
            api_key=OFFLINE_API_KEY_PLACEHOLDER,
            base_url=DEEPSEEK_BASE_URL,
            max_retries=0,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            http_client=self._http_client,
        )
        self._base_model = OpenAIChatModel(
            DEEPSEEK_MODEL_ID,
            provider=DeepSeekProvider(openai_client=self._sdk_client),
            profile=OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=False
            ),
            settings=OpenAIChatModelSettings(thinking=False),
        )
        self.deadline_models: list[DeadlineBoundModel] = []
        self.unfinished_late_tasks_at_close = 0
        self._scenario_tool_invocations: list[tuple[str, str]] = []

    @classmethod
    def _from_provider_components(
        cls,
        *,
        ledger: GlobalUsageLedger,
        forbidden_payload_values: tuple[str, ...],
        transport: OfflineRecordingTransport | LiveProviderTransport,
        http_client: httpx2.AsyncClient,
        sdk_client: AsyncOpenAI,
    ) -> "ProviderSmokeHarness":
        """Create the shared scenario runner around an already-bound transport."""
        harness = cls.__new__(cls)
        harness.ledger = ledger
        harness._forbidden_payload_values = forbidden_payload_values
        harness._transport = transport
        harness._http_client = http_client
        harness._sdk_client = sdk_client
        harness._base_model = OpenAIChatModel(
            DEEPSEEK_MODEL_ID,
            provider=DeepSeekProvider(openai_client=sdk_client),
            profile=OpenAIModelProfile(
                openai_chat_supports_max_completion_tokens=False
            ),
            settings=OpenAIChatModelSettings(thinking=False),
        )
        harness.deadline_models = []
        harness.unfinished_late_tasks_at_close = 0
        harness._scenario_tool_invocations = []
        return harness

    @property
    def captured_requests(self) -> tuple[dict[str, object], ...]:
        return self._transport.captured_requests

    @property
    def scenario_tool_invocations(self) -> tuple[tuple[str, str], ...]:
        """Trace-safe scenario/tool-name evidence, with no tool arguments."""
        return tuple(self._scenario_tool_invocations)

    async def __aenter__(self) -> "ProviderSmokeHarness":
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:  # type: ignore[no-untyped-def]
        del exc_type, exc_value, traceback
        pending = tuple(
            task
            for model in self.deadline_models
            for task in model.late_provider_tasks
        )
        if pending:
            # This is a CLI/test-harness cleanup window, not request-path
            # waiting. It permits a bounded late completion to settle so its
            # result is discarded and client resources can close cleanly.
            _, still_pending = await asyncio.wait(
                pending, timeout=PROVIDER_TIMEOUT_SECONDS
            )
            self.unfinished_late_tasks_at_close = len(still_pending)
        await self._sdk_client.close()

    async def run_r1_typed_clarification(
        self, *, request_timeout_seconds: float = 10.0
    ) -> SmokeObservation:
        """Run R1: one non-thinking typed clarification with no domain tool."""
        self._configure_scenario_tools("r1")
        context = AgentExecutionContext(
            execution_id="offline-r1",
            config=AgentExecutionConfig(
                request_timeout_seconds=request_timeout_seconds,
                provider_timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
                tool_timeout_seconds=2.0,
            ),
        )
        model = DeadlineBoundModel(self._base_model, context=context)
        self.deadline_models.append(model)
        agent = Agent(
            model,
            output_type=ToolOutput(
                ModelClarification,
                name="clarification",
                strict=False,
                max_retries=0,
            ),
            instructions="Return a clarification when the requested time is unknown.",
            retries=0,
            tool_timeout=2.0,
        )
        try:
            with models.override_allow_model_requests(True):
                result = await agent.run(
                    "The requested target time is unknown.",
                    model_settings={"max_tokens": PER_REQUEST_OUTPUT_CAP},
                    usage_limits=UsageLimits(
                        request_limit=1,
                        tool_calls_limit=1,
                        total_tokens_limit=MAX_REPORTED_TOTAL_TOKENS,
                    ),
                )
        except BaseException as exc:
            self._stop_after_failure(exc)
            raise
        output = result.output
        if not isinstance(output, ModelClarification):  # pragma: no cover - typed contract
            error = RuntimeError("R1 returned an unexpected output type")
            self._stop_after_failure(error)
            raise error
        return SmokeObservation(
            output_kind=output.output_kind,
            suggested_action=None,
            failure_classification=None,
        )

    async def run_r2_scoped_change_preview(
        self,
        capability: TripReadCapability,
        *,
        item_id: str,
        request_timeout_seconds: float = 10.0,
        tool_timeout_seconds: float = 2.0,
    ) -> SmokeObservation:
        """Run R2 with an independent, scoped reader and typed preview only.

        The capability is checked using its own short-lived Session before the
        model can receive a request.  Each later tool invocation opens and
        closes a second independent Session; no request Session is accepted by
        this test-only interface.
        """
        self._configure_scenario_tools("r2")
        try:
            with capability.session_factory() as scope_db:
                build_read_only_trip_tools(
                    scope_db,
                    trip_id=capability.trip_id,
                    actor_membership_id=capability.actor_membership_id,
                )
        except BaseException as exc:
            self._stop_after_failure(exc)
            raise

        state = _SmokeRunState()
        context = AgentExecutionContext(
            execution_id="offline-r2",
            config=AgentExecutionConfig(
                request_timeout_seconds=request_timeout_seconds,
                provider_timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
                tool_timeout_seconds=tool_timeout_seconds,
            ),
        )
        model = DeadlineBoundModel(self._base_model, context=context)
        self.deadline_models.append(model)
        agent = Agent(
            model,
            deps_type=TripReadCapability,
            output_type=[
                ToolOutput(
                    ScopedModelChangePreview,
                    name="change_preview",
                    strict=False,
                    max_retries=0,
                ),
                ToolOutput(
                    ModelClarification,
                    name="clarification",
                    strict=False,
                    max_retries=0,
                ),
            ],
            instructions=(
                "The only available item reference is scoped_item. "
                "Call get_scoped_plan_item(item_ref='scoped_item') before returning "
                "a typed change preview for scoped_item. "
                "Return only a typed preview or clarification; never mutate data."
            ),
            retries=0,
            tool_timeout=tool_timeout_seconds,
        )

        @agent.tool(retries=0, strict=False, timeout=tool_timeout_seconds)
        def get_scoped_plan_item(
            ctx: RunContext[TripReadCapability],
            item_ref: Literal["scoped_item"],
        ) -> ScopedPlanItemOutput:
            """Read the one model-visible scoped item through a fresh Session."""
            started_at = context.before_tool()
            try:
                with ctx.deps.session_factory() as worker_db:
                    scoped_tools = build_read_only_trip_tools(
                        worker_db,
                        trip_id=ctx.deps.trip_id,
                        actor_membership_id=ctx.deps.actor_membership_id,
                    )
                    plan = next(
                        tool for tool in scoped_tools if tool.name == "get_current_plan"
                    ).handler(day="all")
            finally:
                context.after_tool(started_at)
            for day in plan["days"]:
                for plan_item in day["items"]:
                    if plan_item["id"] == item_id:
                        state.tool_trajectory.append("get_scoped_plan_item")
                        state.read_item_refs.add(item_ref)
                        return ScopedPlanItemOutput(
                            item_ref="scoped_item",
                            title=plan_item["title"],
                            start_hour=plan_item["start_hour"],
                            settledness=plan_item["settledness"],
                        )
            raise ModelRetry("The scoped synthetic item is not in the Current Plan.")

        @agent.output_validator
        def validate_output(
            _ctx: RunContext[TripReadCapability],
            output: ScopedModelChangePreview | ModelClarification,
        ) -> ScopedModelChangePreview | ModelClarification:
            if isinstance(output, ScopedModelChangePreview):
                action = output.suggested_action
                if action.item_ref not in state.read_item_refs:
                    raise ModelRetry("Read scoped_item through get_scoped_plan_item first.")
                if not 0.0 <= action.new_start_hour < 24.0:
                    raise ModelRetry("new_start_hour must be within one calendar day.")
            return output

        try:
            with models.override_allow_model_requests(True):
                result = await agent.run(
                    "Move the inspected item to 3:30 PM.",
                    deps=capability,
                    model_settings={"max_tokens": PER_REQUEST_OUTPUT_CAP},
                    usage_limits=UsageLimits(
                        request_limit=2,
                        tool_calls_limit=1,
                        total_tokens_limit=MAX_REPORTED_TOTAL_TOKENS,
                    ),
                )
        except UnexpectedModelBehavior as exc:
            # Pydantic's per-tool timeout reaches the no-retry guard as an
            # UnexpectedModelBehavior. Translate only that known cause into
            # the framework-neutral deadline taxonomy; do not mislabel typed
            # argument/output validation failures.
            if isinstance(exc.__cause__, ModelRetry) and str(exc.__cause__).startswith(
                "Timed out after "
            ):
                context.request_cancellation()
                translated = AgentToolDeadlineExceeded(
                    "Read-only Agent tool exceeded its deadline"
                )
                self._stop_after_failure(translated)
                raise translated from exc
            self._stop_after_failure(exc)
            raise
        except BaseException as exc:
            self._stop_after_failure(exc)
            raise
        output = result.output
        if isinstance(output, ScopedModelChangePreview):
            return SmokeObservation(
                output_kind=output.output_kind,
                # Use a stable symbolic item key in persisted observations;
                # the raw PlanItem ID remains in memory only.
                suggested_action={
                    "kind": output.suggested_action.kind,
                    "item_ref": output.suggested_action.item_ref,
                    "new_start_hour": output.suggested_action.new_start_hour,
                },
                failure_classification=None,
            )
        if isinstance(output, ModelClarification):
            return SmokeObservation(
                output_kind=output.output_kind,
                suggested_action=None,
                failure_classification=None,
            )
        error = RuntimeError("R2 returned an unexpected scoped output")
        self._stop_after_failure(error)
        raise error

    async def run_r3_required_tool_choice(
        self,
        *,
        total_tokens_limit: int = MAX_REPORTED_TOTAL_TOKENS,
    ) -> SmokeObservation:
        """Run R3 with thinking disabled and an explicit required tool choice.

        A no-op compatibility probe makes ``required`` legal for Pydantic AI;
        it has no database handle or write capability.  R3 stops after that
        mandatory function-tool turn, before a second provider request could
        occur, so it verifies configuration rather than an Agent completion.
        """
        self._configure_scenario_tools("r3")
        context = AgentExecutionContext(
            execution_id="offline-r3",
            config=AgentExecutionConfig(
                request_timeout_seconds=10.0,
                provider_timeout_seconds=PROVIDER_TIMEOUT_SECONDS,
                tool_timeout_seconds=2.0,
            ),
        )
        model = DeadlineBoundModel(self._base_model, context=context)
        self.deadline_models.append(model)
        agent = Agent(
            model,
            output_type=str,
            instructions="Call only the test compatibility probe.",
            retries=0,
            tool_timeout=2.0,
        )

        @agent.tool(retries=0, strict=False, timeout=2.0)
        def compatibility_probe(_ctx: RunContext[object]) -> str:
            """A test-only no-op proving required tool choice serialization."""
            self._scenario_tool_invocations.append(("r3", "compatibility_probe"))
            return "offline probe"

        def required_first_turn_settings(
            _ctx: RunContext[object],
        ) -> dict[str, object]:
            # Pydantic AI rejects a static `required` setting because it would
            # force every later Agent turn too. This run-time setting applies
            # only to the one request that R3 permits and verifies.
            return {
                "max_tokens": PER_REQUEST_OUTPUT_CAP,
                "tool_choice": "required",
            }

        captured_before = len(self.captured_requests)
        requests_before = self.ledger.requests_used
        invocations_before = len(self._scenario_tool_invocations)
        expected_usage_limit = False
        with models.override_allow_model_requests(True):
            try:
                await agent.run(
                    "Call the compatibility probe through a required tool call.",
                    model_settings=required_first_turn_settings,
                    usage_limits=UsageLimits(
                        request_limit=1,
                        tool_calls_limit=1,
                        total_tokens_limit=total_tokens_limit,
                    ),
                )
            except UsageLimitExceeded:
                # The model executed the required no-op tool.  Pydantic AI
                # then wanted a second request for a final response, which is
                # intentionally refused by the one-request R3 budget.
                expected_usage_limit = True
            except BaseException as exc:
                self._stop_after_failure(exc)
                raise
        captured = self.captured_requests[captured_before:]
        invocations = self._scenario_tool_invocations[invocations_before:]
        if not (
            expected_usage_limit
            and self.ledger.requests_used == requests_before + 1
            and len(captured) == 1
            and captured[0]["reasoning_effort"] == "none"
            and captured[0]["tool_choice"] == "required"
            and invocations == [("r3", "compatibility_probe")]
        ):
            self.ledger.stop("r3_required_probe_inconclusive")
            raise SmokeScenarioVerificationFailed("r3_required_probe_inconclusive")
        return SmokeObservation(
            output_kind="required_tool_choice_verified",
            suggested_action=None,
            failure_classification=None,
        )

    def _configure_scenario_tools(self, scenario: str) -> None:
        self._transport.privacy_gate = OutboundPayloadPrivacyGate(
            forbidden_values=self._forbidden_payload_values,
            allowed_tool_names=SCENARIO_ALLOWED_TOOL_NAMES[scenario],
        )

    def _stop_after_failure(self, error: BaseException) -> None:
        """Turn every unsuccessful scenario attempt into an immutable local stop.

        The first terminal condition is retained by ``GlobalUsageLedger``.  A
        later caller cannot reopen the Harness by ignoring an exception and
        starting another scenario.
        """
        self.ledger.stop(classify_smoke_exception(error))


class LiveDeepSeekSmokeAdapter:
    """Separately constructed, test-only gateway for a bounded live smoke run.

    The adapter is not imported by the application route or Legacy Runtime.
    A test may inject an in-memory transport; execution gets one fresh HTTPS
    transport with no retries and no proxy environment.
    """

    def __init__(
        self,
        *,
        api_key: str,
        ledger: GlobalUsageLedger | None = None,
        forbidden_payload_values: tuple[str, ...] = (),
        network_transport_factory: Callable[[], httpx2.AsyncBaseTransport] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("A non-empty process-scoped DeepSeek credential is required")
        self._api_key = api_key
        self.ledger = ledger or GlobalUsageLedger(
            max_requests=MAX_PROVIDER_REQUESTS,
            max_total_tokens=MAX_REPORTED_TOTAL_TOKENS,
        )
        self._forbidden_payload_values = forbidden_payload_values
        self._network_transport_factory = (
            network_transport_factory
            or (lambda: httpx2.AsyncHTTPTransport(retries=0))
        )

    def create_transport(self) -> LiveProviderTransport:
        """Build a fresh allowlisted boundary for one Harness execution."""
        return LiveProviderTransport(
            network_transport=self._network_transport_factory(),
            ledger=self.ledger,
            privacy_gate=OutboundPayloadPrivacyGate(
                forbidden_values=self._forbidden_payload_values
            ),
        )

    def create_harness(self) -> ProviderSmokeHarness:
        """Bind Pydantic AI to the controlled live boundary, not Mock mode."""
        transport = self.create_transport()
        http_client = httpx2.AsyncClient(
            transport=transport,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            trust_env=False,
            follow_redirects=False,
        )
        sdk_client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=DEEPSEEK_BASE_URL,
            max_retries=0,
            timeout=PROVIDER_TIMEOUT_SECONDS,
            http_client=http_client,
        )
        return ProviderSmokeHarness._from_provider_components(
            ledger=self.ledger,
            forbidden_payload_values=self._forbidden_payload_values,
            transport=transport,
            http_client=http_client,
            sdk_client=sdk_client,
        )


def _redacted_wire_contract(
    request: httpx2.Request, payload: dict[str, Any]
) -> dict[str, object]:
    """Extract assertions without retaining raw request data or headers."""
    tools = payload.get("tools") or []
    strict_values = tuple(
        tool["function"]["strict"]
        for tool in tools
        if isinstance(tool, dict)
        and isinstance(tool.get("function"), dict)
        and "strict" in tool["function"]
    )
    disallowed_openai_parameters = {
        "user",
        "store",
        "service_tier",
        "logprobs",
        "top_logprobs",
        "parallel_tool_calls",
        "openai_user",
        "openai_store",
    }
    return {
        "method": request.method,
        "path": request.url.path,
        "model": payload.get("model"),
        "request_keys": tuple(sorted(payload)),
        "reasoning_effort": payload.get("reasoning_effort"),
        "max_tokens": payload.get("max_tokens"),
        "tool_choice": payload.get("tool_choice"),
        "strict_values": strict_values,
        "has_native_json_schema_response_format": (
            isinstance(payload.get("response_format"), dict)
            and payload["response_format"].get("type") == "json_schema"
        ),
        "unexpected_openai_parameters": tuple(
            sorted(disallowed_openai_parameters.intersection(payload))
        ),
    }


def classify_smoke_exception(error: BaseException) -> str:
    """Return the technical failure label for a smoke-boundary exception."""
    if isinstance(error, ProviderRequestBudgetExceeded):
        return "provider_request_budget_exhausted"
    if isinstance(error, ProviderUsageUnavailable):
        return "missing_usage"
    if isinstance(error, ProviderUsageBudgetExceeded):
        return "usage_budget_exceeded"
    if isinstance(error, ProviderSmokeHarnessStopped):
        return "smoke_harness_stopped"
    if isinstance(error, OutboundPayloadPrivacyViolation):
        return "privacy_boundary_violation"
    if isinstance(error, LiveProviderHostRejected):
        return "provider_host_not_allowed"
    if isinstance(error, AgentProviderDeadlineExceeded):
        return "provider_timeout"
    if isinstance(error, AgentRequestDeadlineExceeded):
        return "request_timeout"
    if isinstance(error, AgentToolDeadlineExceeded):
        return "tool_timeout"
    if isinstance(error, asyncio.CancelledError):
        return "request_cancelled"
    if isinstance(error, UsageLimitExceeded):
        return "unexpected_retry"
    if isinstance(error, ModelHTTPError):
        if error.status_code == 429:
            return "provider_http_429"
        if 500 <= error.status_code <= 599:
            return "provider_http_5xx"
        return "provider_compatibility"
    if isinstance(error, UnexpectedModelBehavior) and (
        "Tool '" in str(error) and "exceeded max retries" in str(error)
    ):
        return "tool_schema_failure"
    if isinstance(error, UnexpectedModelBehavior):
        return "malformed_output"
    if isinstance(error, (ModelRetry, ValueError)):
        return "tool_schema_failure"
    return "pydantic_ai_unclassified_failure"


def normalize_smoke_failure(error: BaseException) -> SmokeFailureClassification:
    """Map a technical smoke error to the frozen, framework-neutral grader label."""
    technical_failure = classify_smoke_exception(error)
    normalized = {
        "provider_request_budget_exhausted": "usage_limit",
        "missing_usage": "usage_unavailable",
        "usage_budget_exceeded": "usage_limit",
        "privacy_boundary_violation": "privacy_boundary_violation",
        "provider_host_not_allowed": "provider_compatibility",
        "provider_timeout": "provider_timeout",
        "request_timeout": "request_timeout",
        "tool_timeout": "tool_timeout",
        "request_cancelled": "request_cancelled",
        "unexpected_retry": "retry_policy_violation",
        "provider_compatibility": "provider_compatibility",
        "provider_http_429": "provider_rate_limited",
        "provider_http_5xx": "provider_unavailable",
        "tool_schema_failure": "tool_schema_failure",
        "malformed_output": "malformed_output",
        "smoke_harness_stopped": "harness_stopped",
    }.get(technical_failure, "unclassified_failure")
    return SmokeFailureClassification(
        technical_failure=technical_failure,
        normalized_evaluation_failure=normalized,
    )
