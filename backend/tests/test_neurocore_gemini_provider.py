"""Tests for GeminiProvider — the third LLMProvider adapter (see
app.neurocore.providers.gemini_provider). No real Gemini API key or network
call is used anywhere here: every test patches the real `google.genai`
SDK's async client methods directly on a real (but keyless-safe)
`genai.Client` instance — the same "patch the one seam that talks to the
network" approach tests/test_neurocore_providers.py already uses for
Anthropic/OpenAI's httpx calls, just aimed at the SDK method instead of
httpx.

Numbered per the objective's testing checklist:
  1. Provider initialization
  2. Missing API key
  3. Configuration
  4. Basic generation
  5. Streaming
  6. Tool-call normalization
  7. Malformed provider response
  8. Rate-limit handling
  9. Timeout handling
  10. Provider unavailable
  11. NeuroCore provider switching
  12. Existing Anthropic behavior remains intact
  13. Existing OpenAI behavior remains intact
  14. Existing AI action confirmation remains intact
  15. Existing execution safety remains intact
"""

from __future__ import annotations

import asyncio
import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from google.genai import errors, types

from app.models.enums import PendingActionStatus, PendingActionType, RackStatus
from app.models.pending_action import PendingAction
from app.neurocore.context import build_context
from app.neurocore.providers.anthropic_provider import AnthropicProvider
from app.neurocore.providers.base import ProviderError
from app.neurocore.providers.factory import KNOWN_PROVIDER_NAMES, build_provider, build_provider_from_settings, provider_status
from app.neurocore.providers.gemini_provider import GeminiProvider, _to_gemini_contents
from app.neurocore.providers.openai_provider import OpenAIProvider
from app.neurocore.service import NeuroCoreService, UNAVAILABLE_RESPONSE
from app.simulation.state import ClusterState, RackState

# No blanket `pytestmark = pytest.mark.asyncio` — this file mixes sync
# (factory/config) and async (provider generate()) tests, same convention
# as tests/test_neurocore_providers.py.


class _Settings:
    """A minimal stand-in for app.config.Settings — just the attributes
    build_provider_from_settings/provider_status actually read, so these
    tests never depend on real env vars or app.config's global singleton.
    """

    def __init__(
        self,
        *,
        ai_provider: str = "gemini",
        anthropic_api_key: str | None = None,
        anthropic_model: str = "claude-sonnet-5",
        openai_api_key: str | None = None,
        openai_model: str = "gpt-4o-mini",
        gemini_api_key: str | None = "sk-gemini-test",
        gemini_model: str = "gemini-flash-lite-latest",
        timeout_seconds: float = 1.0,
    ) -> None:
        self.AI_PROVIDER = ai_provider
        self.ANTHROPIC_API_KEY = anthropic_api_key
        self.ANTHROPIC_MODEL = anthropic_model
        self.OPENAI_API_KEY = openai_api_key
        self.OPENAI_MODEL = openai_model
        self.GEMINI_API_KEY = gemini_api_key
        self.GEMINI_MODEL = gemini_model
        self.AI_REQUEST_TIMEOUT_SECONDS = timeout_seconds


def _text_response(text: str, *, prompt_tokens: int | None = None, candidates_tokens: int | None = None) -> types.GenerateContentResponse:
    usage = None
    if prompt_tokens is not None or candidates_tokens is not None:
        usage = types.GenerateContentResponseUsageMetadata(prompt_token_count=prompt_tokens, candidates_token_count=candidates_tokens)
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part.from_text(text=text)]))],
        usage_metadata=usage,
    )


def _tool_call_response(name: str, args: dict, *, text: str = "") -> types.GenerateContentResponse:
    parts = []
    if text:
        parts.append(types.Part.from_text(text=text))
    parts.append(types.Part.from_function_call(name=name, args=args))
    return types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(role="model", parts=parts))])


# --- (1) provider initialization -------------------------------------------


def test_gemini_provider_initializes_with_name_model_and_streaming_flag() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")
    assert provider.name == "gemini"
    assert provider.model == "gemini-flash-lite-latest"
    assert provider.supports_streaming is True


# --- (2) missing API key -----------------------------------------------------


def test_build_provider_returns_none_for_gemini_without_key() -> None:
    provider = build_provider(
        provider_name="gemini", anthropic_api_key=None, anthropic_model="m",
        openai_api_key=None, openai_model="m", gemini_api_key=None, gemini_model="gemini-flash-lite-latest",
        timeout_seconds=1.0,
    )
    assert provider is None


def test_build_provider_returns_gemini_with_key() -> None:
    provider = build_provider(
        provider_name="gemini", anthropic_api_key=None, anthropic_model="m",
        openai_api_key=None, openai_model="m", gemini_api_key="sk-gemini-test", gemini_model="gemini-flash-lite-latest",
        timeout_seconds=1.0,
    )
    assert isinstance(provider, GeminiProvider)
    assert provider.name == "gemini"
    assert provider.model == "gemini-flash-lite-latest"


# --- (3) configuration -------------------------------------------------------


def test_build_provider_from_settings_selects_gemini() -> None:
    settings = _Settings(ai_provider="gemini", gemini_api_key="sk-gemini-test", gemini_model="gemini-flash-lite-latest")
    provider = build_provider_from_settings(settings)
    assert isinstance(provider, GeminiProvider)
    assert provider.model == "gemini-flash-lite-latest"


def test_settings_default_gemini_model_is_a_current_non_deprecated_flash_lite_model() -> None:
    from app.config import Settings

    # A bare Settings() (no env vars) must never require a key to exist,
    # and its default GEMINI_MODEL must be set (not blank/hardcoded to a
    # deprecated model) — see .env.example for the documented default.
    settings = Settings(_env_file=None)
    assert settings.GEMINI_API_KEY is None
    assert settings.GEMINI_MODEL == "gemini-flash-lite-latest"


def test_provider_status_never_includes_the_api_key_value() -> None:
    settings = _Settings(gemini_api_key="sk-gemini-super-secret")
    statuses = provider_status(settings)
    assert "sk-gemini-super-secret" not in str(statuses)


def test_provider_status_reports_gemini_configured_and_available_with_a_key() -> None:
    settings = _Settings(gemini_api_key="sk-gemini-test")
    statuses = {s["name"]: s for s in provider_status(settings)}
    assert set(KNOWN_PROVIDER_NAMES) <= statuses.keys()
    assert statuses["gemini"] == {"name": "gemini", "configured": True, "available": True}


def test_provider_status_reports_gemini_unconfigured_without_a_key() -> None:
    settings = _Settings(gemini_api_key=None)
    statuses = {s["name"]: s for s in provider_status(settings)}
    assert statuses["gemini"] == {"name": "gemini", "configured": False, "available": False}


# --- (4) basic generation ----------------------------------------------------


async def test_gemini_provider_generates_a_text_response() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")
    fake = _text_response("Hello operator", prompt_tokens=42, candidates_tokens=7)
    with patch.object(provider._client.aio.models, "generate_content", new=AsyncMock(return_value=fake)):
        result = await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert result.text == "Hello operator"
    assert result.model == "gemini-flash-lite-latest"
    assert result.input_tokens == 42
    assert result.output_tokens == 7
    assert result.tool_calls == []


async def test_gemini_provider_error_never_leaks_the_api_key() -> None:
    provider = GeminiProvider(api_key="sk-gemini-super-secret", model="gemini-flash-lite-latest")
    empty = types.GenerateContentResponse(candidates=[])
    with patch.object(provider._client.aio.models, "generate_content", new=AsyncMock(return_value=empty)):
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
    assert "sk-gemini-super-secret" not in str(exc_info.value)


# --- (5) streaming -----------------------------------------------------------


async def test_gemini_provider_streams_multiple_text_chunks() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")

    async def fake_stream():
        yield _text_response("Hel")
        yield _text_response("lo, ")
        yield _text_response("world!", prompt_tokens=5, candidates_tokens=3)

    with patch.object(provider._client.aio.models, "generate_content_stream", new=AsyncMock(return_value=fake_stream())):
        chunks = [
            c async for c in provider.generate_stream(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
        ]

    text_chunks = [c for c in chunks if c.text_delta]
    assert len(text_chunks) == 3
    assert "".join(c.text_delta for c in chunks) == "Hello, world!"
    assert chunks[-1].finished is True
    assert chunks[-1].model == "gemini-flash-lite-latest"
    assert chunks[-1].input_tokens == 5
    assert chunks[-1].output_tokens == 3


async def test_gemini_provider_stream_maps_a_mid_stream_api_error() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")

    async def fake_stream():
        yield _text_response("partial")
        raise errors.ServerError(503, {"error": {"message": "overloaded"}})

    with patch.object(provider._client.aio.models, "generate_content_stream", new=AsyncMock(return_value=fake_stream())):
        with pytest.raises(ProviderError):
            _ = [
                c
                async for c in provider.generate_stream(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
            ]


# --- (6) tool-call normalization ---------------------------------------------


async def test_gemini_provider_parses_a_function_call_response() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")
    fake = _tool_call_response("read_rack", {"rack_id": "abc"}, text="Let me check.")
    with patch.object(provider._client.aio.models, "generate_content", new=AsyncMock(return_value=fake)):
        result = await provider.generate(
            system="sys", messages=[{"role": "user", "content": "Check rack A1"}], max_tokens=100,
            tools=[{"name": "read_rack", "description": "Read a rack", "input_schema": {"type": "object"}}],
        )

    assert result.text == "Let me check."
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "read_rack"
    assert result.tool_calls[0]["arguments"] == {"rack_id": "abc"}
    assert result.tool_calls[0]["id"]  # never empty — always a usable correlation id


async def test_gemini_provider_streams_a_tool_call_as_one_complete_chunk() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")

    async def fake_stream():
        yield _tool_call_response("read_rack", {"rack_id": "abc"})

    with patch.object(provider._client.aio.models, "generate_content_stream", new=AsyncMock(return_value=fake_stream())):
        chunks = [
            c
            async for c in provider.generate_stream(
                system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100,
                tools=[{"name": "read_rack", "description": "d", "input_schema": {"type": "object"}}],
            )
        ]

    tool_chunks = [c for c in chunks if c.tool_calls]
    assert len(tool_chunks) == 1
    assert tool_chunks[0].tool_calls[0]["name"] == "read_rack"
    assert tool_chunks[0].tool_calls[0]["arguments"] == {"rack_id": "abc"}


def test_gemini_provider_sends_declared_tools_and_correlates_results_by_name() -> None:
    """Gemini's FunctionResponse Part is correlated by function *name*, not
    the opaque call id Anthropic/OpenAI use (see the module's own
    docstring) — this proves _to_gemini_contents rebuilds that correlation
    correctly from a 'tool' role message that only carries the id.
    """
    messages = [
        {"role": "user", "content": "Check rack A1"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call-xyz", "name": "read_rack", "arguments": {"rack_id": "abc"}}]},
        {"role": "tool", "tool_call_id": "call-xyz", "content": '{"temperature": 70.0}'},
    ]
    contents = _to_gemini_contents(messages)

    assert contents[0].role == "user"
    assert contents[1].role == "model"
    assert contents[1].parts[-1].function_call.name == "read_rack"
    assert contents[2].role == "tool"
    assert contents[2].parts[0].function_response.name == "read_rack"
    assert contents[2].parts[0].function_response.response == {"result": '{"temperature": 70.0}'}


# --- (7) malformed provider response -----------------------------------------


async def test_gemini_provider_raises_provider_error_on_empty_candidates() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")
    empty = types.GenerateContentResponse(candidates=[])
    with patch.object(provider._client.aio.models, "generate_content", new=AsyncMock(return_value=empty)):
        with pytest.raises(ProviderError):
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)


async def test_gemini_provider_stream_skips_a_malformed_chunk_without_crashing() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")

    async def fake_stream():
        yield types.GenerateContentResponse(candidates=[])  # malformed/empty — must be skipped, not raised
        yield _text_response("still works")

    with patch.object(provider._client.aio.models, "generate_content_stream", new=AsyncMock(return_value=fake_stream())):
        chunks = [
            c async for c in provider.generate_stream(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
        ]
    assert "".join(c.text_delta for c in chunks) == "still works"


# --- (8) rate-limit handling --------------------------------------------------


async def test_gemini_provider_maps_429_to_a_clear_rate_limit_error_without_retrying() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")
    call_count = {"n": 0}

    async def rate_limited(*_args, **_kwargs):
        call_count["n"] += 1
        raise errors.ClientError(429, {"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}})

    with patch.object(provider._client.aio.models, "generate_content", new=rate_limited):
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    message = str(exc_info.value).lower()
    assert "rate limit" in message or "quota" in message
    # Never burn additional free-tier quota retrying a 429 — see the
    # module docstring's rate-limit/free-tier policy.
    assert call_count["n"] == 1


async def test_gemini_provider_retries_a_transient_server_error_once_then_succeeds() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest", timeout_seconds=2.0)
    call_count = {"n": 0}

    async def flaky(*_args, **_kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise errors.ServerError(503, {"error": {"message": "temporarily overloaded"}})
        return _text_response("recovered")

    with patch.object(provider._client.aio.models, "generate_content", new=flaky):
        result = await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert result.text == "recovered"
    assert call_count["n"] == 2  # exactly one bounded retry, not an unbounded loop


async def test_gemini_provider_server_error_retry_is_bounded_not_infinite() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest", timeout_seconds=2.0)
    call_count = {"n": 0}

    async def always_503(*_args, **_kwargs):
        call_count["n"] += 1
        raise errors.ServerError(503, {"error": {"message": "still overloaded"}})

    with patch.object(provider._client.aio.models, "generate_content", new=always_503):
        with pytest.raises(ProviderError):
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)

    assert call_count["n"] == 2  # one initial attempt + exactly one retry, then it gives up


# --- (9) timeout handling ------------------------------------------------------


async def test_gemini_provider_generate_times_out_cleanly() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest", timeout_seconds=0.05)

    async def hangs(*_args, **_kwargs):
        await asyncio.sleep(5)

    with patch.object(provider._client.aio.models, "generate_content", new=hangs):
        started = time.monotonic()
        with pytest.raises(ProviderError) as exc_info:
            await provider.generate(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
        elapsed = time.monotonic() - started

    assert elapsed < 2.0  # bounded by timeout_seconds, not the 5s hang
    assert "time" in str(exc_info.value).lower()


async def test_gemini_provider_stream_open_times_out_cleanly() -> None:
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest", timeout_seconds=0.05)

    async def hangs(*_args, **_kwargs):
        await asyncio.sleep(5)

    with patch.object(provider._client.aio.models, "generate_content_stream", new=hangs):
        with pytest.raises(ProviderError):
            _ = [
                c
                async for c in provider.generate_stream(system="sys", messages=[{"role": "user", "content": "hi"}], max_tokens=100)
            ]


# --- (10) provider unavailable -------------------------------------------------


async def test_neurocore_reports_unavailable_when_gemini_has_no_key() -> None:
    settings = _Settings(ai_provider="gemini", gemini_api_key=None)
    provider = build_provider_from_settings(settings)
    assert provider is None

    service = NeuroCoreService(provider=provider)
    context = _make_context([_make_rack()])
    result = await service.answer(context, message="Summarize the cluster.", rack_id=None)

    assert result.text == UNAVAILABLE_RESPONSE
    assert service.provider_available is False


# --- (11) NeuroCore provider switching -----------------------------------------


def test_build_provider_switches_cleanly_between_anthropic_openai_gemini_and_mock() -> None:
    common = dict(
        anthropic_api_key="sk-a", anthropic_model="claude-sonnet-5",
        openai_api_key="sk-o", openai_model="gpt-4o-mini",
        gemini_api_key="sk-g", gemini_model="gemini-flash-lite-latest",
        timeout_seconds=1.0,
    )
    anthropic = build_provider(provider_name="anthropic", **common)
    openai = build_provider(provider_name="openai", **common)
    gemini = build_provider(provider_name="gemini", **common)
    mock = build_provider(provider_name="mock", **common)

    assert isinstance(anthropic, AnthropicProvider)
    assert isinstance(openai, OpenAIProvider)
    assert isinstance(gemini, GeminiProvider)
    assert mock.name == "mock"
    # Each is an independent instance targeting the right vendor — nothing
    # about "switch AI_PROVIDER" requires touching NeuroCoreService,
    # app.neurocore.tools, or any route (see module docstring).
    assert {anthropic.name, openai.name, gemini.name} == {"anthropic", "openai", "gemini"}


# --- (12) / (13) existing Anthropic/OpenAI behavior remains intact -------------


def test_adding_gemini_does_not_change_anthropic_or_openai_selection() -> None:
    """Regression guard specifically for this change: build_provider grew a
    new gemini_api_key/gemini_model parameter — confirm the anthropic/openai
    branches still behave identically (see
    tests/test_neurocore_providers.py for the original, still-unmodified
    coverage of these two adapters' own request/response handling).
    """
    provider = build_provider(
        provider_name="anthropic", anthropic_api_key="sk-a", anthropic_model="claude-sonnet-5",
        openai_api_key=None, openai_model="m", gemini_api_key="sk-g", gemini_model="gemini-flash-lite-latest",
        timeout_seconds=1.0,
    )
    assert isinstance(provider, AnthropicProvider)
    assert provider.model == "claude-sonnet-5"

    provider2 = build_provider(
        provider_name="openai", anthropic_api_key=None, anthropic_model="m",
        openai_api_key="sk-o", openai_model="gpt-4o-mini", gemini_api_key="sk-g", gemini_model="gemini-flash-lite-latest",
        timeout_seconds=1.0,
    )
    assert isinstance(provider2, OpenAIProvider)
    assert provider2.model == "gpt-4o-mini"


# --- (14) / (15) AI action confirmation + execution safety remain intact -----
# Mirrors tests/test_neurocore_service.py's own write-tool test, but driven
# by a real GeminiProvider round trip instead of MockLLMProvider's scripted
# responses — proving Gemini's tool calls flow through the exact same
# PendingAction short-circuit (see app.neurocore.tools.write_tools /
# app.neurocore.service.NeuroCoreService.answer) as every other provider,
# never executing anything itself.


class _FakeSimulationPort:
    def __init__(self, racks: list[RackState]) -> None:
        self._racks = {r.id: r for r in racks}

    @property
    def cluster_state(self) -> ClusterState:
        return ClusterState(
            id=uuid.uuid4(), name="Test Cluster", overall_health=90.0, average_temperature=65.0,
            total_power=sum(r.power_draw for r in self._racks.values()), cooling_efficiency=60.0,
            energy_savings=15.0, prediction_confidence=90.0,
        )

    def rack_state(self, rack_id):
        return self._racks.get(rack_id)

    @property
    def scenario_status(self):
        from datetime import UTC, datetime

        from app.schemas.scenario import ScenarioStatus

        return ScenarioStatus(key="normal", name="Normal", transition_state="steady", target_rack_id=None, activated_at=datetime.now(UTC))

    def get_decision(self, decision_id):
        return None

    def get_plan(self, plan_id):
        return None

    @property
    def all_executions(self):
        return []


class _StubPendingActions:
    def __init__(self) -> None:
        self.create_for_decision_calls: list[uuid.UUID] = []

    async def create_for_decision(self, db, *, conversation_id, decision_id, simulation, now=None):
        self.create_for_decision_calls.append(decision_id)
        from datetime import UTC, datetime

        return PendingAction(
            id=uuid.uuid4(), conversation_id=conversation_id, plan_id=None, decision_id=decision_id,
            action_type=PendingActionType.EXECUTE_DECISION, target="Rack A1", status=PendingActionStatus.PENDING,
            summary="I can execute the recommended migration for Rack A1. Proceed?", scenario_key="normal",
            execution_id=None, expires_at=datetime.now(UTC),
        )

    async def create_for_replay(self, db, *, conversation_id, simulation, now=None):
        raise AssertionError("not used in this test")


def _make_rack(**overrides: object) -> RackState:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(), name="Rack A1", temperature=65.0, gpu_utilization=55.0,
        cpu_utilization=40.0, power_draw=9.0, cooling_efficiency=60.0, fan_speed=40.0,
        health_score=90.0, prediction_state="stable", running_jobs=10, status=RackStatus.HEALTHY,
    )
    defaults.update(overrides)
    return RackState(**defaults)  # type: ignore[arg-type]


def _make_context(racks: list[RackState], **overrides: object):
    defaults: dict[str, object] = dict(
        cluster=ClusterState(
            id=uuid.uuid4(), name="Test Cluster", overall_health=90.0, average_temperature=65.0,
            total_power=sum(r.power_draw for r in racks), cooling_efficiency=60.0,
            energy_savings=15.0, prediction_confidence=90.0,
        ),
        racks=racks, scenario_key="normal", forecasts={}, cluster_forecast=[],
        active_plans=[], all_plans=[], active_decisions=[], all_decisions=[],
        all_executions=[], recent_events=[],
    )
    defaults.update(overrides)
    return build_context(**defaults)  # type: ignore[arg-type]


async def test_gemini_write_tool_call_only_creates_a_pending_action_never_executes() -> None:
    rack = _make_rack(name="Rack A1")
    decision_id = uuid.uuid4()
    provider = GeminiProvider(api_key="sk-gemini-test", model="gemini-flash-lite-latest")
    fake_response = _tool_call_response("execute_decision", {"decision_id": str(decision_id)})

    stub_actions = _StubPendingActions()
    service = NeuroCoreService(provider=provider, pending_actions=stub_actions)
    context = _make_context([rack])

    with patch.object(provider._client.aio.models, "generate_content", new=AsyncMock(return_value=fake_response)):
        result = await service.answer(
            context, message="Move the workload off Rack A1.", rack_id=None,
            db=object(), simulation=_FakeSimulationPort([rack]), conversation_id=uuid.uuid4(),
        )

    # The write tool never runs anything itself — it only ever proposes a
    # PendingAction; confirmation (POST /api/ai/actions/{id}/confirm) and
    # ExecutionService are the only real execution path (see
    # app.neurocore.tools.write_tools / app.neurocore.actions).
    assert result.pending_action_id is not None
    assert "proceed" in result.text.lower()
    assert stub_actions.create_for_decision_calls == [decision_id]
    # The loop stopped at the tool call — Gemini was never asked (and never
    # got the chance) to narrate a "success" that hasn't actually happened.
    assert provider.model == "gemini-flash-lite-latest"  # sanity: this really was the Gemini adapter
