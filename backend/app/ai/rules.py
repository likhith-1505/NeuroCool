"""RuleBasedDecisionEngine — deterministic reasoning over live telemetry.

Every rule reasons purely from ClusterState/RackState (and, for two rules,
each rack's short-term trend) — never from which scenario is active. A
scenario like "cooling_failure" or "power_surge" is just a way of *driving*
the telemetry; the engine only ever sees the numbers it produces, the same
way it would see numbers produced by any other cause. This is what "reason
from live telemetry, don't hardcode recommendations per scenario" means in
practice, and it's also what makes an LLM-based replacement plausible later
— nothing here depends on backend-specific scenario machinery.

Trend tracking (previous reading per rack) lives entirely inside this
class, not in DecisionContext — it's an implementation detail of *this*
reasoning strategy. An LLM engine might infer trends from a longer history
window instead; the shared contract (app.ai.base) doesn't need to know
either way.
"""

from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field

from app.ai.base import DecisionContext, DecisionDraft
from app.models.enums import EventSeverity
from app.services.event_service import POWER_SPIKE_DELTA_KW
from app.simulation.physics import COMFORTABLE_TEMPERATURE_C, clamp
from app.simulation.state import ClusterState, RackState

# --- Rule thresholds -------------------------------------------------------

MIGRATION_TEMPERATURE_C = 82.0
MIGRATION_GPU_UTILIZATION_PCT = 90.0

# Cooling/temperature trend is measured over a short rolling window, not a
# single tick: during a real degradation the cooling-drop rate peaks early
# and the temperature-rise rate peaks later (thermal mass lags), so a
# single-tick delta can miss the "both worsening" moment entirely even
# though the sustained trend is obvious over a few seconds. See
# _rule_cooling_intervention.
COOLING_TREND_WINDOW_TICKS = 5
COOLING_DROP_THRESHOLD_PCT = 1.5  # drop in cooling_efficiency over the window
TEMPERATURE_RISE_THRESHOLD_C = 2.0  # rise in temperature over the window

REBALANCE_TEMPERATURE_THRESHOLD_C = 78.0
REBALANCE_MIN_RACKS = 2

DELAY_JOBS_POWER_DELTA_KW = POWER_SPIKE_DELTA_KW  # reuse event_service's definition of "a spike"


@dataclass
class _RackTrend:
    """Per-rack bookkeeping between evaluations.

    power_draw is compared tick-to-tick (a surge is meant to be sudden —
    POWER_EASE_RATE is the fastest-reacting value in the physics engine,
    so a single-tick delta is already a strong signal). temperature/cooling
    use a short rolling window instead, for the reason above.
    """

    power_draw: float
    window: deque[tuple[float, float]] = field(
        default_factory=lambda: deque(maxlen=COOLING_TREND_WINDOW_TICKS)
    )


# However close the seeded initial telemetry is to true equilibrium,
# there's still some settling on boot — trend-based rules stay quiet until
# this many evaluations have happened, so that settling is never mistaken
# for genuine degradation. Threshold-based rules (workload migration,
# cluster rebalance) are unaffected: they only ever look at the current
# reading, not a trend, so they have nothing to warm up.
WARMUP_EVALUATIONS = 20


class RuleBasedDecisionEngine:
    """Deterministic, explainable reasoning — the initial DecisionEngine
    implementation. Conforms structurally to app.ai.base.DecisionEngine.
    """

    def __init__(self) -> None:
        self._trends: dict[uuid.UUID, _RackTrend] = {}
        self._evaluations_seen = 0

    def evaluate(self, context: DecisionContext) -> list[DecisionDraft]:
        self._evaluations_seen += 1
        past_warmup = self._evaluations_seen > WARMUP_EVALUATIONS
        drafts: list[DecisionDraft] = []

        for rack in context.racks:
            trend = self._trends.get(rack.id)

            migration = self._rule_workload_migration(rack)
            if migration is not None:
                drafts.append(migration)

            if trend is not None and past_warmup:
                if len(trend.window) >= COOLING_TREND_WINDOW_TICKS:
                    baseline_temperature, baseline_cooling = trend.window[0]
                    cooling = self._rule_cooling_intervention(rack, baseline_temperature, baseline_cooling)
                    if cooling is not None:
                        drafts.append(cooling)

                delay = self._rule_delay_new_jobs(rack, trend.power_draw)
                if delay is not None:
                    drafts.append(delay)

            window = trend.window if trend is not None else deque(maxlen=COOLING_TREND_WINDOW_TICKS)
            window.append((rack.temperature, rack.cooling_efficiency))
            self._trends[rack.id] = _RackTrend(power_draw=rack.power_draw, window=window)

        rebalance = self._rule_cluster_rebalance(context.cluster, context.racks)
        if rebalance is not None:
            drafts.append(rebalance)

        return drafts

    # --- rules -------------------------------------------------------------

    @staticmethod
    def _rule_workload_migration(rack: RackState) -> DecisionDraft | None:
        """IF temperature > 82 AND gpu_utilization > 90 THEN recommend
        workload migration.
        """
        if rack.temperature <= MIGRATION_TEMPERATURE_C or rack.gpu_utilization <= MIGRATION_GPU_UTILIZATION_PCT:
            return None

        return DecisionDraft(
            rule_key=f"workload_migration:{rack.id}",
            severity=EventSeverity.CRITICAL if rack.temperature >= 90.0 else EventSeverity.WARNING,
            title=f"Migrate workload off {rack.name}",
            reasoning=(
                f"{rack.name} is at {rack.temperature:.1f}°C with GPU utilization at "
                f"{rack.gpu_utilization:.0f}%, both above the sustained-load thresholds "
                f"({MIGRATION_TEMPERATURE_C:.0f}°C / {MIGRATION_GPU_UTILIZATION_PCT:.0f}%). Continuing "
                f"to schedule work here risks further thermal degradation."
            ),
            recommended_action=f"Migrate a portion of {rack.name}'s workload to a cooler rack.",
            confidence=_confidence_from_margin(rack.temperature, MIGRATION_TEMPERATURE_C, 15.0),
            affected_racks=[rack.id],
            expected_temperature_reduction=_estimate_temperature_relief(rack),
        )

    @staticmethod
    def _rule_cooling_intervention(
        rack: RackState, baseline_temperature: float, baseline_cooling: float
    ) -> DecisionDraft | None:
        """IF cooling efficiency drops AND temperature continues increasing
        (sustained over the last few ticks) THEN recommend cooling
        intervention.
        """
        cooling_dropping = rack.cooling_efficiency <= baseline_cooling - COOLING_DROP_THRESHOLD_PCT
        temperature_rising = rack.temperature >= baseline_temperature + TEMPERATURE_RISE_THRESHOLD_C
        if not (cooling_dropping and temperature_rising):
            return None

        return DecisionDraft(
            rule_key=f"cooling_intervention:{rack.id}",
            severity=EventSeverity.WARNING,
            title=f"Cooling intervention needed on {rack.name}",
            reasoning=(
                f"{rack.name}'s cooling efficiency fell from {baseline_cooling:.1f}% to "
                f"{rack.cooling_efficiency:.1f}% while temperature kept climbing "
                f"({baseline_temperature:.1f}°C → {rack.temperature:.1f}°C) over the last "
                f"{COOLING_TREND_WINDOW_TICKS} readings. Cooling is losing ground against heat generation."
            ),
            recommended_action=f"Inspect {rack.name}'s cooling path and consider a manual airflow assist.",
            confidence=_confidence_from_margin(
                rack.temperature - baseline_temperature, TEMPERATURE_RISE_THRESHOLD_C, 3.0
            ),
            affected_racks=[rack.id],
            expected_temperature_reduction=_estimate_temperature_relief(rack),
        )

    @staticmethod
    def _rule_cluster_rebalance(cluster: ClusterState, racks: list[RackState]) -> DecisionDraft | None:
        """IF multiple racks exceed thresholds THEN recommend cluster rebalance.

        "Neighbouring" is approximated as "simultaneously" here: with a
        small, densely-interconnected cluster, several racks running hot at
        once is itself the signal that the imbalance is cluster-wide rather
        than an isolated single-rack issue.
        """
        hot_racks = [r for r in racks if r.temperature > REBALANCE_TEMPERATURE_THRESHOLD_C]
        if len(hot_racks) < REBALANCE_MIN_RACKS:
            return None

        names = ", ".join(r.name for r in hot_racks)
        return DecisionDraft(
            rule_key="cluster_rebalance",
            severity=EventSeverity.WARNING,
            title="Rebalance cluster workload",
            reasoning=(
                f"{len(hot_racks)} racks are simultaneously running above "
                f"{REBALANCE_TEMPERATURE_THRESHOLD_C:.0f}°C ({names}), suggesting the imbalance is "
                f"cluster-wide rather than isolated to a single rack."
            ),
            recommended_action="Redistribute scheduled jobs more evenly across all racks.",
            confidence=_confidence_from_margin(float(len(hot_racks)), float(REBALANCE_MIN_RACKS), 2.0),
            affected_racks=[r.id for r in hot_racks],
            expected_power_saving=_estimate_power_saving(hot_racks),
        )

    @staticmethod
    def _rule_delay_new_jobs(rack: RackState, previous_power_draw: float) -> DecisionDraft | None:
        """IF a power surge is detected (power draw jumps sharply) THEN
        recommend delaying new jobs on that rack.
        """
        delta = rack.power_draw - previous_power_draw
        if delta < DELAY_JOBS_POWER_DELTA_KW:
            return None

        return DecisionDraft(
            rule_key=f"delay_new_jobs:{rack.id}",
            severity=EventSeverity.WARNING,
            title=f"Delay new jobs on {rack.name}",
            reasoning=(
                f"{rack.name}'s power draw jumped by {delta:.1f} kW in one reading (to "
                f"{rack.power_draw:.1f} kW) — consistent with a power surge. Scheduling more work onto "
                f"it right now would compound the spike."
            ),
            recommended_action=f"Hold new job scheduling on {rack.name} until power draw stabilizes.",
            confidence=_confidence_from_margin(delta, DELAY_JOBS_POWER_DELTA_KW, 3.0),
            affected_racks=[rack.id],
            expected_power_saving=round(delta, 1),
            ttl_seconds=60.0,  # a surge-driven recommendation should go stale quickly if not re-affirmed
        )


def _confidence_from_margin(value: float, threshold: float, span: float) -> float:
    """Confidence scales with how far past the threshold a value is — a
    reading just over the line is a modest signal, one far past it is much
    stronger. This is what makes confidence genuinely derived from
    telemetry each time a rule is evaluated, rather than a fixed number.
    """
    margin = max(0.0, value - threshold)
    return round(clamp(55.0 + (margin / span) * 40.0, 55.0, 98.0), 1)


def _estimate_temperature_relief(rack: RackState) -> float:
    """An honest, derived estimate rather than a fabricated number:
    intervention typically claws back roughly a third of the excess above
    the comfortable baseline the physics engine itself targets.
    """
    excess = max(0.0, rack.temperature - COMFORTABLE_TEMPERATURE_C)
    return round(excess * 0.35, 1)


def _estimate_power_saving(racks: list[RackState]) -> float:
    """Rough, honest estimate: rebalancing recovers a fraction of the
    affected racks' combined power draw.
    """
    return round(sum(r.power_draw for r in racks) * 0.08, 1)
