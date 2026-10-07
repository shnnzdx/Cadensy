"""Application-composition selection for the Chat Agent Runtime.

This module is deliberately safe to import in the Legacy-only dependency
environment.  Pydantic-specific construction is injected only when explicitly
selected, rather than imported at module load time or silently falling back.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from .legacy_runtime import LegacyChatAgentRuntime
from .runtime_contract import ChatAgentRuntime


class ChatRuntimeConfigurationError(RuntimeError):
    """The configured runtime cannot be composed safely."""


PydanticRuntimeFactory = Callable[[], ChatAgentRuntime]

_PYDANTIC_CONFIGURATION_ERROR = "Pydantic Chat Runtime configuration is unavailable"


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
