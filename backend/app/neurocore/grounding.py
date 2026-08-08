"""Deterministic retrieval over NeuroCoreContext — plain Python, not an
LLM call. This module decides *which* real backend facts are relevant to
a question (which rack, which plan, which decision, which execution,
which events) and renders them into a plain-text block; the LLM's only
job afterward is phrasing an answer from that block (see app.neurocore.
service.NeuroCoreService.answer and app.neurocore.prompts). This is what
keeps "NeuroCore must never calculate thermal physics / replace
forecasting / replace optimization / replace decision scoring" true by
construction: nothing here computes a new number, it only selects and
formats numbers the deterministic backend already produced.

Every `render_*` function is a straight f-string over real attributes —
no invented values. Where a real record doesn't exist (no forecast, no
plan, no decision for the rack in question), the block says so explicitly
instead of silently omitting it, so the LLM has an honest fact to relay
rather than a gap it might paper over.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.neurocore.context import NeuroCoreContext

if TYPE_CHECKING:
    from app.forecasting.base import RackPrediction
    from app.models.decision import Decision
    from app.models.event import Event
    from app.models.execution import Execution
    from app.models.optimization_plan import OptimizationPlan
    from app.simulation.state import RackState

MAX_EVENTS_IN_CONTEXT = 8
MAX_CLUSTER_WIDE_PLANS = 3
MAX_CLUSTER_WIDE_DECISIONS = 3

# Grounding confidence is a proxy for "how much real backend evidence
# backs this answer", not a model-reported number — see
# _grounding_confidence. Weights are additive and capped at 100.
_CONFIDENCE_BASE = 30.0  # the cluster/rack overview below is always available
_CONFIDENCE_RACK_RESOLVED = 15.0
_CONFIDENCE_FORECAST_FOUND = 20.0
_CONFIDENCE_PLAN_FOUND = 20.0
_CONFIDENCE_DECISION_FOUND = 10.0
_CONFIDENCE_EXECUTION_FOUND = 5.0


@dataclass(frozen=True)
class Grounding:
    """The retrieval result for one question: the formatted facts the LLM
    is allowed to use, which real records they came from (returned to the
    caller as `sources`), and a confidence score derived from how much was
    actually found.
    """

    context_block: str
    sources: list[str]
    confidence: float


def build_grounding(context: NeuroCoreContext, *, message: str, rack_id: uuid.UUID | None) -> Grounding:
    sections: list[str] = [render_cluster_summary(context), render_rack_overview(context)]
    sources: list[str] = []

    found_rack = found_forecast = found_plan = found_decision = found_execution = False

    if rack_id is not None and not any(r.id == rack_id for r in context.racks):
        sections.append(f"The requested rack_id {rack_id} does not match any known rack in this cluster.")

    target_rack = resolve_rack(context, message=message, rack_id=rack_id)

    if target_rack is not None:
        found_rack = True
        sources.append(f"rack:{target_rack.name}")

        forecast = context.forecasts.get(target_rack.id, [])
        if forecast:
            found_forecast = True
            sections.append(render_forecast(target_rack, forecast))
            sources.append(f"forecast:{target_rack.name}")
        else:
            sections.append(f"No forecast is currently available for {target_rack.name}.")

        plan = find_latest_plan_for_rack(context, target_rack.id)
        if plan is not None:
            found_plan = True
            sections.append(render_plan(plan))
            sources.append(f"plan:{plan.id}")
        else:
            sections.append(f"No optimization plan is currently active for {target_rack.name}.")

        decision = find_latest_decision_for_rack(context, target_rack.id)
        if decision is not None:
            found_decision = True
            sections.append(render_decision(decision))
            sources.append(f"decision:{decision.id}")

            execution = find_latest_execution_for_rack(context, target_rack.id, decision_id=decision.id)
            if execution is not None:
                found_execution = True
                sections.append(render_execution(execution))
                sources.append(f"execution:{execution.id}")
        else:
            sections.append(f"No decision has been recorded for {target_rack.name}.")

        rack_events = [e for e in context.recent_events if e.rack_id == target_rack.id][:MAX_EVENTS_IN_CONTEXT]
        if rack_events:
            sections.append(render_events(rack_events, heading=f"Recent events for {target_rack.name}:"))
            sources.extend(f"event:{event.id}" for event in rack_events)
    else:
        cluster_events = context.recent_events[:MAX_EVENTS_IN_CONTEXT]
        if cluster_events:
            sections.append(render_events(cluster_events, heading="Recent cluster-wide events:"))
            sources.extend(f"event:{event.id}" for event in cluster_events)

        for plan in context.active_plans[:MAX_CLUSTER_WIDE_PLANS]:
            found_plan = True
            sections.append(render_plan(plan))
            sources.append(f"plan:{plan.id}")

        for decision in context.active_decisions[:MAX_CLUSTER_WIDE_DECISIONS]:
            found_decision = True
            sections.append(render_decision(decision))
            sources.append(f"decision:{decision.id}")

    confidence = _grounding_confidence(
        found_rack=found_rack,
        found_forecast=found_forecast,
        found_plan=found_plan,
        found_decision=found_decision,
        found_execution=found_execution,
    )
    return Grounding(context_block="\n\n".join(sections), sources=sources, confidence=confidence)


# --- rack/record resolution --------------------------------------------


def resolve_rack(context: NeuroCoreContext, *, message: str, rack_id: uuid.UUID | None) -> "RackState | None":
    """Explicit rack_id wins. Otherwise, look for a rack name mentioned in
    the message text (longest names checked first, so "Rack A1" isn't
    shadowed by a shorter name that happens to be a substring of it).
    Never guesses beyond a literal name match — an unresolved rack means
    the answer stays cluster-wide, not a fabricated guess at which rack
    was meant.
    """
    if rack_id is not None:
        return next((rack for rack in context.racks if rack.id == rack_id), None)

    lowered = message.lower()
    for rack in sorted(context.racks, key=lambda r: -len(r.name)):
        if rack.name.lower() in lowered:
            return rack
    return None


def find_latest_plan_for_rack(context: NeuroCoreContext, rack_id: uuid.UUID) -> "OptimizationPlan | None":
    return next((plan for plan in context.all_plans if plan.trigger_rack_id == rack_id), None)


def find_latest_decision_for_rack(context: NeuroCoreContext, rack_id: uuid.UUID) -> "Decision | None":
    return next((decision for decision in context.all_decisions if rack_id in decision.affected_racks), None)


def find_latest_execution_for_rack(
    context: NeuroCoreContext, rack_id: uuid.UUID, *, decision_id: uuid.UUID | None = None
) -> "Execution | None":
    candidates = [execution for execution in context.all_executions if rack_id in execution.affected_racks]
    if decision_id is not None:
        for execution in candidates:
            if execution.decision_id == decision_id:
                return execution
    return candidates[0] if candidates else None


# --- rendering -----------------------------------------------------------


def render_cluster_summary(context: NeuroCoreContext) -> str:
    cluster = context.cluster
    scenario_note = (
        f"non-normal scenario '{context.scenario_key}' is currently driving telemetry"
        if context.scenario_active
        else "operating at baseline (no active incident scenario)"
    )
    return (
        f"Cluster '{cluster.name}': overall health {cluster.overall_health:.0f}/100, average temperature "
        f"{cluster.average_temperature:.1f}°C, total power {cluster.total_power:.1f} kW, cooling efficiency "
        f"{cluster.cooling_efficiency:.0f}%, prediction confidence {cluster.prediction_confidence:.0f}%. "
        f"The cluster is {scenario_note}."
    )


def render_rack_overview(context: NeuroCoreContext) -> str:
    lines = ["Per-rack current status and forecast risk (nearest -> farthest horizon):"]
    for rack in context.racks:
        forecast = context.forecasts.get(rack.id, [])
        if forecast:
            nearest, farthest = forecast[0], forecast[-1]
            risk_text = (
                f"risk now {nearest.predicted_risk:.0f}/100, in {farthest.horizon_seconds // 60} min "
                f"{farthest.predicted_risk:.0f}/100 ({farthest.confidence:.0f}% confidence)"
            )
        else:
            risk_text = "no forecast available"
        lines.append(
            f"- {rack.name}: {rack.temperature:.1f}°C, {rack.gpu_utilization:.0f}% GPU, "
            f"health {rack.health_score:.0f}/100, status {rack.status.value}; {risk_text}."
        )
    return "\n".join(lines)


def render_forecast(rack: "RackState", forecast: list["RackPrediction"]) -> str:
    lines = [f"Forecast for {rack.name} (currently {rack.temperature:.1f}°C):"]
    for point in forecast:
        minutes = point.horizon_seconds / 60
        lines.append(
            f"- in {minutes:g} min: {point.predicted_temperature:.1f}°C, GPU {point.predicted_gpu_utilization:.0f}%, "
            f"cooling {point.predicted_cooling:.0f}%, thermal risk {point.predicted_risk:.0f}/100, "
            f"confidence {point.confidence:.0f}%."
        )
    return "\n".join(lines)


def render_plan(plan: "OptimizationPlan") -> str:
    candidates = plan.candidates or []
    lines = [f"Optimization plan {plan.id} — trigger: {plan.trigger_reason}"]

    winner = candidates[0] if candidates else None
    if winner is not None:
        score = winner["score"]
        lines.append(
            f"Recommended action: {winner['description']} (overall score {score['overall_score']:.0f}/100, "
            f"confidence {score['confidence']:.0f}%, projected temperature {winner['projected_temperature']:.1f}°C, "
            f"expected temperature reduction {score['temperature_reduction_c']:.1f}°C, "
            f"estimated recovery {score['estimated_recovery_seconds']:.0f}s)."
        )

    for alternative in candidates[1:]:
        score = alternative["score"]
        reason = alternative.get("rejection_reason") or "lower overall score"
        lines.append(
            f"Alternative considered: {alternative['description']} (score {score['overall_score']:.0f}/100) "
            f"— not chosen: {reason}"
        )

    no_action = next((c for c in candidates if c["action_type"] == "no_action"), None)
    if no_action is not None:
        lines.append(
            f"If no action is taken: projected temperature {no_action['projected_temperature']:.1f}°C, "
            f"risk change {no_action['score']['risk_reduction']:+.1f} (negative means risk would increase)."
        )

    return "\n".join(lines)


def render_decision(decision: "Decision") -> str:
    lines = [
        f"Decision '{decision.title}' (id {decision.id}, status {decision.status.value}, "
        f"confidence {decision.confidence:.0f}%):",
        decision.reasoning,
        f"Recommended action: {decision.recommended_action}",
    ]
    if decision.expected_temperature_reduction is not None:
        lines.append(f"Expected temperature reduction: {decision.expected_temperature_reduction:.1f}°C.")
    if decision.expected_power_saving is not None:
        lines.append(f"Expected power saving: {decision.expected_power_saving:.1f} kW.")
    for alternative in decision.alternative_actions or []:
        reason = alternative.get("rejection_reason") or "lower overall score"
        lines.append(f"Alternative: {alternative['description']} — not chosen: {reason}")
    return "\n".join(lines)


def render_execution(execution: "Execution") -> str:
    action = execution.action_type.value if execution.action_type is not None else "unknown"
    lines = [
        f"Execution {execution.id}: action={action}, status={execution.status.value}.",
        execution.summary,
    ]
    if execution.error_message:
        lines.append(f"Error: {execution.error_message}")
    return "\n".join(lines)


def render_events(events: list["Event"], *, heading: str) -> str:
    lines = [heading]
    for event in events:
        message = f": {event.message}" if event.message else ""
        lines.append(f"- [{event.severity.value}] {event.occurred_at.isoformat()} — {event.title}{message}")
    return "\n".join(lines)


def _grounding_confidence(
    *, found_rack: bool, found_forecast: bool, found_plan: bool, found_decision: bool, found_execution: bool
) -> float:
    score = _CONFIDENCE_BASE
    if found_rack:
        score += _CONFIDENCE_RACK_RESOLVED
    if found_forecast:
        score += _CONFIDENCE_FORECAST_FOUND
    if found_plan:
        score += _CONFIDENCE_PLAN_FOUND
    if found_decision:
        score += _CONFIDENCE_DECISION_FOUND
    if found_execution:
        score += _CONFIDENCE_EXECUTION_FOUND
    return round(min(100.0, score), 1)
