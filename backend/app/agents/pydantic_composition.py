"""Fail-closed production composition for the opt-in Pydantic Chat Runtime.

This module only validates local configuration and constructs objects.  It does
not execute an Agent, make a Provider request, register tools, or access a
database.  Pydantic-specific imports stay inside the explicit composition
function so importing the Legacy selector remains dependency-safe.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from . import base
from .runtime_contract import ChatAgentRuntime


class PydanticRuntimeCompositionError(RuntimeError):
    """The opt-in production Pydantic Runtime cannot be composed safely."""


def _uses_official_deepseek_endpoint(base_url: str | None) -> bool:
    """Accept only the direct provider's official endpoint, with an optional slash."""

    if not base_url:
        return False
    try:
        parsed = urlsplit(base_url.strip())
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.deepseek.com"
        and port in {None, 443}
        and parsed.path in {"", "/"}
        and not parsed.query
        and not parsed.fragment
        and parsed.username is None
        and parsed.password is None
    )


def build_pydantic_chat_runtime(*, system_prompt: str) -> ChatAgentRuntime:
    """Construct the reviewed Pydantic Runtime without executing it.

    The direct ``DeepSeekProvider`` implementation is intentionally restricted
    to its official endpoint.  All detailed failures remain internal; the
    public factory converts them into its stable configuration error.
    """

    if base.is_mocked():
        raise PydanticRuntimeCompositionError(
            "Pydantic runtime production composition requires MOCK_AI=0"
        )

    config = base.provider_catalog()[base.DEEPSEEK_PROVIDER]
    if not config.api_key:
        raise PydanticRuntimeCompositionError(
            "Pydantic runtime production composition requires a DeepSeek API key"
        )
    if not _uses_official_deepseek_endpoint(config.base_url):
        raise PydanticRuntimeCompositionError(
            "Pydantic runtime production composition requires the official DeepSeek endpoint"
        )
    if not config.model:
        raise PydanticRuntimeCompositionError(
            "Pydantic runtime production composition requires a DeepSeek model"
        )

    try:
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
        from pydantic_ai.providers.deepseek import DeepSeekProvider

        from .pydantic_runtime import PydanticChatAgentRuntime
    except ImportError as error:
        raise PydanticRuntimeCompositionError(
            "Pydantic runtime production dependency is unavailable"
        ) from error

    try:
        model = OpenAIChatModel(
            config.model,
            provider=DeepSeekProvider(api_key=config.api_key),
            settings=OpenAIChatModelSettings(thinking=False),
        )
        return PydanticChatAgentRuntime(model=model, system_prompt=system_prompt)
    except Exception as error:
        raise PydanticRuntimeCompositionError(
            "Pydantic runtime production composition could not be constructed"
        ) from error
