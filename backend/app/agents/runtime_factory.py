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


def build_chat_runtime(
    *,
    system_prompt: str,
    pydantic_runtime_factory: PydanticRuntimeFactory | None = None,
) -> ChatAgentRuntime:
    """Build exactly one selected runtime; default directly to Legacy.

    A Pydantic runtime has no reviewed production Provider composition in
    PR-04C. Callers selecting it must inject an already composed Pydantic
    Runtime (tests do so with local fake models), otherwise configuration fails
    closed before Agent execution.
    """

    selector = os.getenv("CHAT_AGENT_RUNTIME", "").strip()
    if not selector or selector == "legacy":
        return LegacyChatAgentRuntime(system_prompt=system_prompt)
    if selector == "pydantic":
        if pydantic_runtime_factory is None:
            raise ChatRuntimeConfigurationError(
                "CHAT_AGENT_RUNTIME=pydantic requires explicit Pydantic runtime composition"
            )
        return pydantic_runtime_factory()
    raise ChatRuntimeConfigurationError(
        "CHAT_AGENT_RUNTIME must be one of: legacy, pydantic"
    )
