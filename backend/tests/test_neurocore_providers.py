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


# --- tool calling: Anthropic -----------------------------------------------


async def test_anthropic_provider_parses_a_tool_use_response() -> None:
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-5")
    fake_response = _FakeHttpResponse(
        {
            "content": [
                {"type": "text", "text": "Let me check."},
                {"type": "tool_use", "id": "toolu_1", "name": "read_rack", "input": {"rack_id": "abc"}},
            ],
            "model": "claude-sonnet-5", "stop_reason": "tool_use", "usage": {"input_tokens": 10, "output_tokens": 5},
        }
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await provider.generate(
            system="sys", messages=[{"role": "user", "content": "Check rack A1"}], max_tokens=100,
            tools=[{"name": "read_rack", "description": "Read a rack", "input_schema": {"type": "object"}}],
        )

    assert result.text == "Let me check."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "read_rack"
    assert result.tool_calls[0]["arguments"] == {"rack_id": "abc"}


async def test_anthropic_provider_sends_tools_and_tool_result_messages() -> None:
    provider = AnthropicProvider(api_key="sk-test", model="claude-sonnet-5")
    fake_response = _FakeHttpResponse({"content": [{"type": "text", "text": "done"}], "model": "claude-sonnet-5", "usage": {}})
    captured = {}

    async def _capture_post(self, url, *, json, headers):
        captured["json"] = json
        return fake_response

    with patch("httpx.AsyncClient.post", new=_capture_post):
        await provider.generate(
            system="sys",
            messages=[
                {"role": "user", "content": "Check rack A1"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "toolu_1", "name": "read_rack", "arguments": {"rack_id": "abc"}}]},
                {"role": "tool", "tool_call_id": "toolu_1", "content": '{"temperature": 70.0}'},
            ],
            max_tokens=100,
            tools=[{"name": "read_rack", "description": "Read a rack", "input_schema": {"type": "object"}}],
        )

    assert captured["json"]["tools"][0]["name"] == "read_rack"
    sent_messages = captured["json"]["messages"]
    assert sent_messages[1]["content"][0]["type"] == "tool_use"
    assert sent_messages[2]["role"] == "user"  # Anthropic represents tool results as a user turn
    assert sent_messages[2]["content"][0]["type"] == "tool_result"
    assert sent_messages[2]["content"][0]["tool_use_id"] == "toolu_1"


# --- tool calling: OpenAI ---------------------------------------------------


async def test_openai_provider_parses_a_tool_calls_response() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")
    fake_response = _FakeHttpResponse(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant", "content": None,
                        "tool_calls": [
                            {"id": "call_1", "type": "function", "function": {"name": "read_rack", "arguments": '{"rack_id": "abc"}'}}
                        ],
                    }
                }
            ],
            "model": "gpt-4o-mini", "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
    )
    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=fake_response)):
        result = await provider.generate(
            system="sys", messages=[{"role": "user", "content": "Check rack A1"}], max_tokens=100,
            tools=[{"name": "read_rack", "description": "Read a rack", "input_schema": {"type": "object"}}],
        )

    assert result.text == ""
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "read_rack"
    assert result.tool_calls[0]["arguments"] == {"rack_id": "abc"}


async def test_openai_provider_sends_tools_and_tool_result_messages() -> None:
    provider = OpenAIProvider(api_key="sk-test", model="gpt-4o-mini")
    fake_response = _FakeHttpResponse({"choices": [{"message": {"content": "done"}}], "model": "gpt-4o-mini", "usage": {}})
    captured = {}

    async def _capture_post(self, url, *, json, headers):
        captured["json"] = json
        return fake_response

    with patch("httpx.AsyncClient.post", new=_capture_post):
        await provider.generate(
            system="sys",
            messages=[
                {"role": "user", "content": "Check rack A1"},
                {"role": "assistant", "content": "", "tool_calls": [{"id": "call_1", "name": "read_rack", "arguments": {"rack_id": "abc"}}]},
                {"role": "tool", "tool_call_id": "call_1", "content": '{"temperature": 70.0}'},
            ],
            max_tokens=100,
            tools=[{"name": "read_rack", "description": "Read a rack", "input_schema": {"type": "object"}}],
        )

    assert captured["json"]["tools"][0]["function"]["name"] == "read_rack"
    sent_messages = captured["json"]["messages"]
    assert sent_messages[0]["role"] == "system"
    assistant_message = next(m for m in sent_messages if m.get("role") == "assistant" and m.get("tool_calls"))
    assert assistant_message["tool_calls"][0]["function"]["arguments"] == '{"rack_id": "abc"}'
    tool_message = next(m for m in sent_messages if m.get("role") == "tool")
    assert tool_message["tool_call_id"] == "call_1"


# --- mock provider scripted tool-calling -----------------------------------


async def test_mock_provider_scripted_responses_are_returned_in_order() -> None:
    from app.neurocore.providers.base import LLMResponse, ToolCall

    provider = MockLLMProvider(
        responses=[
            LLMResponse(text="", model="mock-1", tool_calls=[ToolCall(id="c1", name="read_rack", arguments={"rack_id": "abc"})]),
            LLMResponse(text="Final answer.", model="mock-1"),
        ]
    )

    first = await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    second = await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert len(first.tool_calls) == 1
    assert first.tool_calls[0]["name"] == "read_rack"
    assert second.text == "Final answer."
    assert provider.call_count == 2
