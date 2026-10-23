"""Application-composition selection for the Chat Agent Runtime.

This module is deliberately safe to import in the Legacy-only dependency
environment.  Pydantic-specific construction is injected only when explicitly
selected, rather than imported at module load time or silently falling back.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from . import base
from .legacy_runtime import LegacyChatAgentRuntime
from .runtime_contract import ChatAgentRuntime


class ChatRuntimeConfigurationError(RuntimeError):
    """The configured runtime cannot be composed safely."""


PydanticRuntimeFactory = Callable[[], ChatAgentRuntime]

_PYDANTIC_CONFIGURATION_ERROR = "Pydantic Chat Runtime configuration is unavailable"
_PRODUCTION_ENVIRONMENTS = frozenset({"production", "prod"})


def chat_runtime_configuration_ready(*, system_prompt: str) -> bool:
    """Return configuration readiness without invoking a model provider.

    Composition is the same fail-closed selector used by execution. Constructing
    a Runtime may validate its local SDK/configuration, but never runs it.
    """
    fake_e2e = os.getenv("CADENSY_E2E_FAKE_RUNTIME") == "1"
    environment = os.getenv("APP_ENV", "development").strip().lower()
    if fake_e2e:
        # The browser harness is a capability-complete local adapter. Admit it
        # only in an explicitly mocked test process; it never needs Provider
        # configuration and remains unavailable in development/production.
        return environment == "test" and base.is_mocked()
    if base.is_mocked():
        # The real Legacy adapter uses base.call_agent's existing mock seam;
        # admit it only for non-production Legacy selection. Pydantic remains
        # explicitly unavailable in mock mode and never falls back to Legacy.
        selector = os.getenv("CHAT_AGENT_RUNTIME", "").strip()
        return (
            environment not in _PRODUCTION_ENVIRONMENTS
            and selector in {"", "legacy"}
        )
    try:
        if not base.deepseek_runtime_configuration_ready():
            return False
        build_chat_runtime(system_prompt=system_prompt)
    except Exception:
        return False
    return True


def _build_production_pydantic_runtime(*, system_prompt: str) -> ChatAgentRuntime:
    """Load production Pydantic composition only after explicit selection."""

    from .pydantic_composition import build_pydantic_chat_runtime

    return build_pydantic_chat_runtime(system_prompt=system_prompt)


def build_chat_runtime(
    *,
    system_prompt: str,
    pydantic_runtime_factory: PydanticRuntimeFactory | None = None,
) -> ChatAgentRuntime:
    """Build exactly one selected runtime; default directly to Legacy.

    Tests may inject an already composed Pydantic Runtime.  Production
    composition is loaded only when Pydantic is explicitly selected without
    that test seam; any failure is normalized and never falls back to Legacy.
    """

    # This exists solely for the disposable local browser harness.  It is a
    # real contract implementation, not an HTTP/API mock, and cannot be
    # enabled in a production process.
    if os.getenv("CADENSY_E2E_FAKE_RUNTIME") == "1":
        if os.getenv("APP_ENV", "development").strip().lower() != "test":
            raise ChatRuntimeConfigurationError("local E2E runtime is available only in APP_ENV=test")
        from .e2e_runtime import LocalE2ERuntime

        return LocalE2ERuntime()

    selector = os.getenv("CHAT_AGENT_RUNTIME", "").strip()
    if not selector or selector == "legacy":
        return LegacyChatAgentRuntime(system_prompt=system_prompt)
    if selector == "pydantic":
        if pydantic_runtime_factory is not None:
            return pydantic_runtime_factory()
        try:
            return _build_production_pydantic_runtime(system_prompt=system_prompt)
        except Exception as error:
            raise ChatRuntimeConfigurationError(_PYDANTIC_CONFIGURATION_ERROR) from error
    raise ChatRuntimeConfigurationError(
        "CHAT_AGENT_RUNTIME must be one of: legacy, pydantic"
    )
