"""Unit tests for the LLM provider abstraction — no real network calls or
API keys required (per the objective: "Do not require a real LLM API key
for the test suite").
"""

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.neurocore.providers.anthropic_provider import AnthropicProvider
from app.neurocore.providers.base import ProviderError
from app.neurocore.providers.factory import build_provider
from app.neurocore.providers.mock_provider import MockLLMProvider
from app.neurocore.providers.openai_provider import OpenAIProvider

# No blanket `pytestmark = pytest.mark.asyncio` here — this file mixes sync
# (factory) and async (provider generate()) tests, and pytest.ini's
# asyncio_mode=auto already detects async def tests on its own; marking
# every test (including the sync ones) would just produce a PytestWarning.


class _FakeHttpResponse:
    """A minimal stand-in for httpx.Response, just enough for the
    adapters' generate() to exercise its real parsing/error-handling
    logic without a real network call.
    """

    def __init__(self, json_data: dict, status_code: int = 200) -> None:
        self._json_data = json_data
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("error", request=request, response=response)

    def json(self) -> dict:
        return self._json_data


# --- factory / provider selection ---------------------------------------


def test_build_provider_returns_none_for_anthropic_without_key() -> None:
    provider = build_provider(
        provider_name="anthropic", anthropic_api_key=None, anthropic_model="m",
        openai_api_key=None, openai_model="m", timeout_seconds=1.0,
    )
    assert provider is None


def test_build_provider_returns_anthropic_with_key() -> None:
    provider = build_provider(
        provider_name="anthropic", anthropic_api_key="sk-test", anthropic_model="claude-sonnet-5",
        openai_api_key=None, openai_model="m", timeout_seconds=1.0,
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.name == "anthropic"
    assert provider.model == "claude-sonnet-5"


def test_build_provider_returns_none_for_openai_without_key() -> None:
    provider = build_provider(
        provider_name="openai", anthropic_api_key=None, anthropic_model="m",
        openai_api_key=None, openai_model="gpt-4o-mini", timeout_seconds=1.0,
    )
    assert provider is None


def test_build_provider_returns_openai_with_key() -> None:
    provider = build_provider(
        provider_name="openai", anthropic_api_key=None, anthropic_model="m",
        openai_api_key="sk-test", openai_model="gpt-4o-mini", timeout_seconds=1.0,
    )
    assert isinstance(provider, OpenAIProvider)
    assert provider.name == "openai"
    assert provider.model == "gpt-4o-mini"


def test_build_provider_mock_is_always_available_without_any_key() -> None:
    provider = build_provider(
        provider_name="mock", anthropic_api_key=None, anthropic_model="m",
        openai_api_key=None, openai_model="m", timeout_seconds=1.0,
    )
    assert isinstance(provider, MockLLMProvider)


def test_build_provider_unknown_name_returns_none() -> None:
    provider = build_provider(
        provider_name="not-a-real-provider", anthropic_api_key="sk-test", anthropic_model="m",
        openai_api_key="sk-test", openai_model="m", timeout_seconds=1.0,
    )
    assert provider is None


def test_build_provider_is_case_and_whitespace_insensitive() -> None:
    provider = build_provider(
        provider_name="  Mock  ", anthropic_api_key=None, anthropic_model="m",
        openai_api_key=None, openai_model="m", timeout_seconds=1.0,
    )
    assert isinstance(provider, MockLLMProvider)


# --- mock provider ---------------------------------------------------------


async def test_mock_provider_is_deterministic_given_the_same_input() -> None:
    provider = MockLLMProvider()
    messages = [{"role": "user", "content": "Why is Rack A1 at risk?"}]
    first = await provider.generate(system="sys context", messages=messages, max_tokens=100)
    second = await provider.generate(system="sys context", messages=messages, max_tokens=100)
    assert first.text == second.text


async def test_mock_provider_echoes_the_last_user_message() -> None:
    provider = MockLLMProvider()
    messages = [{"role": "user", "content": "What changed recently?"}]
    result = await provider.generate(system="sys", messages=messages, max_tokens=100)
    assert "What changed recently?" in result.text


# --- anthropic adapter -----------------------------------------------------


async def test_anthropic_provider_parses_a_successful_response() -> None:
    provider = AnthropicProvider(api_key="sk-super-secret", model="claude-sonnet-5")
    fake_response = _FakeHttpResponse(
        {"content": [{"type": "text", "text": "Hello operator"}], "model": "claude-sonnet-5",
         "usage": {"input_tokens": 42, "output_tokens": 7}}
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert result.text == "Hello operator"
    assert result.input_tokens == 42
    assert result.output_tokens == 7


async def test_anthropic_provider_raises_provider_error_on_http_failure() -> None:
    provider = AnthropicProvider(api_key="sk-super-secret", model="claude-sonnet-5")
    fake_response = _FakeHttpResponse({}, status_code=401)
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        with pytest.raises(ProviderError):
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)


async def test_anthropic_provider_error_never_leaks_the_api_key() -> None:
    provider = AnthropicProvider(api_key="sk-super-secret", model="claude-sonnet-5")
    fake_response = _FakeHttpResponse({"unexpected": "shape"})
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    assert "sk-super-secret" not in str(exc_info.value)


# --- openai adapter ---------------------------------------------------------


async def test_openai_provider_parses_a_successful_response() -> None:
    provider = OpenAIProvider(api_key="sk-super-secret", model="gpt-4o-mini")
    fake_response = _FakeHttpResponse(
        {"choices": [{"message": {"role": "assistant", "content": "Hello operator"}}], "model": "gpt-4o-mini",
         "usage": {"prompt_tokens": 42, "completion_tokens": 7}}
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert result.text == "Hello operator"
    assert result.input_tokens == 42
    assert result.output_tokens == 7


async def test_openai_provider_raises_provider_error_on_http_failure() -> None:
    provider = OpenAIProvider(api_key="sk-super-secret", model="gpt-4o-mini")
    fake_response = _FakeHttpResponse({}, status_code=500)
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        with pytest.raises(ProviderError):
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)


async def test_openai_provider_error_never_leaks_the_api_key() -> None:
    provider = OpenAIProvider(api_key="sk-super-secret", model="gpt-4o-mini")
    fake_response = _FakeHttpResponse({"unexpected": "shape"})
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    assert "sk-super-secret" not in str(exc_info.value)
