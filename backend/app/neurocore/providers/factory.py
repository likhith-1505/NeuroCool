"""Configuration-driven provider selection — the only place that decides
*which* LLMProvider gets constructed. Kept as a pure function (settings
values in, a provider or None out) so it's trivially unit-testable without
touching app.config.settings or the network.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.neurocore.providers.anthropic_provider import AnthropicProvider
from app.neurocore.providers.base import LLMProvider
from app.neurocore.providers.mock_provider import MockLLMProvider
from app.neurocore.providers.openai_provider import OpenAIProvider

if TYPE_CHECKING:
    from app.config import Settings


def build_provider(
    *,
    provider_name: str,
    anthropic_api_key: str | None,
    anthropic_model: str,
    openai_api_key: str | None,
    openai_model: str,
    timeout_seconds: float,
) -> LLMProvider | None:
    """Returns None (never raises) when the configured provider has no
    matching API key, or the provider name isn't recognized — this is what
    makes "no API key configured -> AI endpoints return a clear
    unavailable response, everything else still starts" possible: the
    backend never fails to boot over a missing/invalid AI_PROVIDER value.
    """
    name = (provider_name or "").strip().lower()

    if name == "anthropic":
        if not anthropic_api_key:
            return None
        return AnthropicProvider(api_key=anthropic_api_key, model=anthropic_model, timeout_seconds=timeout_seconds)

    if name == "openai":
        if not openai_api_key:
            return None
        return OpenAIProvider(api_key=openai_api_key, model=openai_model, timeout_seconds=timeout_seconds)

    if name == "mock":
        return MockLLMProvider()

    return None


def build_provider_from_settings(settings: "Settings") -> LLMProvider | None:
    return build_provider(
        provider_name=settings.AI_PROVIDER,
        anthropic_api_key=settings.ANTHROPIC_API_KEY,
        anthropic_model=settings.ANTHROPIC_MODEL,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_model=settings.OPENAI_MODEL,
        timeout_seconds=settings.AI_REQUEST_TIMEOUT_SECONDS,
    )
