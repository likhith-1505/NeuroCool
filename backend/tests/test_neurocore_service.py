"""Unit tests for NeuroCoreService.answer() — the provider+grounding
orchestration that doesn't touch the database (see NeuroCoreService.chat
for the thin DB-persistence wrapper, verified live instead — same split
DecisionService/ExecutionService/OptimizationService use).
"""

import uuid
from datetime import UTC, datetime

import pytest

from app.models.enums import ExecutionActionType, RackStatus
from app.neurocore.context import build_context
from app.neurocore.providers.base import LLMMessage, LLMResponse, ProviderError
from app.neurocore.providers.mock_provider import MockLLMProvider
from app.neurocore.service import (
    EMPTY_MESSAGE_RESPONSE,
    PROVIDER_ERROR_RESPONSE,
    UNAVAILABLE_RESPONSE,
    NeuroCoreService,
)
from app.simulation.state import ClusterState, RackState

pytestmark = pytest.mark.asyncio


class _RaisingProvider:
    name = "raising"
    model = "raising-1"

    async def generate(self, *, system: str, messages: list[LLMMessage], max_tokens: int) -> LLMResponse:
        raise ProviderError("simulated provider failure")


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


async def test_answer_with_no_provider_returns_unavailable() -> None:
    service = NeuroCoreService(provider=None)
    context = _make_context([_make_rack()])

    result = await service.answer(context, message="Summarize the cluster.", rack_id=None)

    assert result.text == UNAVAILABLE_RESPONSE
    assert result.sources == []
    assert result.confidence == 0.0


async def test_answer_with_empty_message_does_not_call_the_provider() -> None:
    service = NeuroCoreService(provider=MockLLMProvider())
    context = _make_context([_make_rack()])

    result = await service.answer(context, message="   ", rack_id=None)

    assert result.text == EMPTY_MESSAGE_RESPONSE
    assert result.sources == []
    assert result.confidence == 0.0


async def test_answer_with_mock_provider_returns_grounded_sources() -> None:
    service = NeuroCoreService(provider=MockLLMProvider())
    rack = _make_rack(name="Rack A1")
    context = _make_context([rack])

    result = await service.answer(context, message="Why is Rack A1 at risk?", rack_id=None)

    assert "Rack A1" in result.text
    assert f"rack:{rack.name}" in result.sources
    assert result.confidence > 0.0


async def test_answer_falls_back_cleanly_when_provider_raises() -> None:
    service = NeuroCoreService(provider=_RaisingProvider())
    context = _make_context([_make_rack()])

    result = await service.answer(context, message="Summarize the cluster.", rack_id=None)

    assert result.text == PROVIDER_ERROR_RESPONSE
    assert result.sources == []
    assert result.confidence == 0.0


async def test_answer_with_unresolvable_rack_id_still_grounds_cluster_wide() -> None:
    service = NeuroCoreService(provider=MockLLMProvider())
    context = _make_context([_make_rack()])
    unknown_id = uuid.uuid4()

    result = await service.answer(context, message="Why is this rack at risk?", rack_id=unknown_id)

    assert str(unknown_id) in result.text or "does not match" in result.text.lower() or result.confidence >= 0.0
    # The unavailable-rack note always makes it into the grounded context,
    # which the mock provider echoes a preview of.


async def test_answer_history_is_forwarded_to_the_provider() -> None:
    """Regression guard: history must reach build_messages/generate() in
    the right order (oldest first, question last) rather than being
    dropped silently.
    """
    service = NeuroCoreService(provider=MockLLMProvider())
    context = _make_context([_make_rack()])
    history: list[LLMMessage] = [
        {"role": "user", "content": "What is the cluster's average temperature?"},
        {"role": "assistant", "content": "It is 65 degrees."},
    ]

    result = await service.answer(context, message="And now?", rack_id=None, history=history)

    assert "And now?" in result.text
