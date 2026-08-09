"""ForecastService — maintains rolling telemetry history, drives a
ForecastEngine every tick, aggregates a cluster-level forecast by reusing
the exact same aggregation the physics engine already uses for live
ClusterState, detects forecast-based events, and exposes read access for
the REST API and WebSocket broadcast.

Mirrors DecisionService/ExecutionService's role: SimulationService and the
REST API only ever talk to this service, never to a concrete engine
directly. Swapping TrendForecastEngine for an ARIMA/Prophet/LSTM/
Transformer-based engine later means constructing ForecastService with a
different `engine=` argument (dependency injection) — nothing else changes
(see app.forecasting.base.ForecastEngine).

Deliberately independent of the Decision Engine (per the objective): this
module never imports app.ai. DecisionService instead receives this
service's *output* (the latest predictions) as a plain argument each tick,
the same way it already receives ClusterState/RackState — see
SimulationService._tick.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from statistics import fmean

from app.db.session import AsyncSessionLocal
from app.forecasting.base import FORECAST_HORIZONS_SECONDS, ForecastContext, ForecastEngine, RackPrediction
from app.forecasting.history import TelemetryHistory
from app.models.enums import EventSeverity
from app.models.event import Event
from app.services.event_service import EventDraft, persist_events
from app.simulation.physics import compute_cluster_state
from app.simulation.state import RackState, ring_neighbors

logger = logging.getLogger(__name__)

# Forecast-driven events are edge-detected (only on the transition, not
# every tick the condition still holds) at this one horizon — medium-term
# and still actionable, rather than checking all 4 horizons x 4 event
# types every tick.
EVENT_HORIZON_SECONDS = 120
THERMAL_HOTSPOT_THRESHOLD_C = 85.0
COOLING_DEGRADATION_THRESHOLD_PCT = 40.0
POWER_SPIKE_DELTA_KW = 4.0
RISK_RECOVERY_THRESHOLD = 40.0
CONFIDENCE_CHANGE_THRESHOLD = 15.0

# A threshold-crossing prediction is only worth surfacing as an event once
# it clears this confidence floor. Without it, the handful of noisy
# readings present right after boot (see TrendForecastEngine's own
# MIN_SAMPLES_FOR_TREND) can extrapolate a wild, low-confidence slope that
# clamps straight to a physical bound and fires a "hotspot predicted"
# event nobody should trust. Matches app.ai.rules.PROACTIVE_MIN_CONFIDENCE
# so a forecast only ever *acts* (as an event or a decision) above the
# same bar.
EVENT_MIN_CONFIDENCE = 40.0

# However good the seeded initial telemetry is, the system is still
# settling toward its true equilibrium for the first several ticks after
# boot — with only a handful of samples, even TrendForecastEngine's own
# R²-based fit_quality can look deceptively strong (few points fit a line
# well almost by construction), so a settling-noise trend can still clear
# EVENT_MIN_CONFIDENCE and extrapolate to a physical clamp before the
# telemetry has any real signal to go on. No forecast-driven event fires
# until this many ticks have been seen — the same lesson already learned
# for RuleBasedDecisionEngine's WARMUP_EVALUATIONS in app.ai.rules, which
# gates the proactive rule that consumes this service's output the same
# way.
EVENT_WARMUP_TICKS = 30


class ForecastService:
    """Owns rolling history and the current forecast snapshot. Takes a
    ForecastEngine via constructor injection.
    """

    def __init__(self, engine: ForecastEngine) -> None:
        self._engine = engine
        self._history = TelemetryHistory()
        self._rack_forecasts: dict[uuid.UUID, list[RackPrediction]] = {}
        self._previous_rack_forecasts: dict[uuid.UUID, list[RackPrediction]] = {}
        self._cluster_forecast: list[RackPrediction] = []
        self._previous_cluster_risk: float | None = None
        self._ticks_seen = 0

    # --- read access -------------------------------------------------------

    @property
    def rack_forecasts(self) -> dict[uuid.UUID, list[RackPrediction]]:
        """rack_id -> predictions, one per horizon, ascending."""
        return self._rack_forecasts

    @property
    def cluster_forecast(self) -> list[RackPrediction]:
        return self._cluster_forecast

    def rack_forecast(self, rack_id: uuid.UUID) -> list[RackPrediction]:
        return self._rack_forecasts.get(rack_id, [])

    def reset(self) -> None:
        """Drops rolling history and every cached prediction — called from
        app.simulation.engine.SimulationService.reset so a fresh simulation
        run doesn't extrapolate a trend from telemetry that no longer
        exists once the baseline has been restored. No database rows exist
        here to begin with (forecasts are never persisted, see the module
        docstring), so this is purely in-memory and instant.
        """
        self._history = TelemetryHistory()
        self._rack_forecasts = {}
        self._previous_rack_forecasts = {}
        self._cluster_forecast = []
        self._previous_cluster_risk = None
        self._ticks_seen = 0

    # --- per-tick ---------------------------------------------------------

    async def tick(
        self,
        racks: list[RackState],
        scenario_key: str,
        cluster_id: uuid.UUID,
        scenario_db_id: uuid.UUID | None,
        now: datetime,
    ) -> list[Event]:
        """Record this tick's readings, recompute every rack's forecast and
        the cluster aggregate, and return any forecast-driven lifecycle
        events for the caller to fold into its own broadcast.
        """
        self._ticks_seen += 1
        self._history.append(now, racks)
        scenario_active = scenario_key != "normal"

        next_forecasts: dict[uuid.UUID, list[RackPrediction]] = {}
        for rack in racks:
            history = self._history.for_rack(rack.id).recent()
            neighbor_ids = ring_neighbors(rack.id, racks)
            context = ForecastContext(
                rack=rack,
                history=history,
                scenario_key=scenario_key,
                scenario_active=scenario_active,
                neighbor_trend_hint=self._neighbor_trend_hint(neighbor_ids),
                now=now,
            )
            next_forecasts[rack.id] = self._engine.forecast(context, FORECAST_HORIZONS_SECONDS)

        events = await self._detect_and_persist_events(
            racks, next_forecasts, cluster_id, scenario_db_id, now
        )

        self._previous_rack_forecasts = self._rack_forecasts
        self._rack_forecasts = next_forecasts
        self._cluster_forecast = self._aggregate_cluster_forecast(cluster_id, racks)
        return events

    # --- internals -----------------------------------------------------------

    def _neighbor_trend_hint(self, neighbor_ids: frozenset[uuid.UUID]) -> float:
        """Fraction (0.0-1.0) of ring-neighbors whose own forecast shows a
        rising near-term temperature trend — a simple, honest proxy for
        "is trouble spreading nearby". Uses last tick's already-computed
        forecasts (this tick's aren't all computed yet while iterating
        rack by rack, and using stale-by-one-tick data avoids an arbitrary
        dependency on rack iteration order).
        """
        if not neighbor_ids:
            return 0.0
        trending_up = 0
        counted = 0
        for neighbor_id in neighbor_ids:
            predictions = self._previous_rack_forecasts.get(neighbor_id)
            if not predictions or len(predictions) < 2:
                continue
            counted += 1
            nearest = min(predictions, key=lambda p: p.horizon_seconds)
            farthest = max(predictions, key=lambda p: p.horizon_seconds)
            if farthest.predicted_temperature > nearest.predicted_temperature + 1.0:
                trending_up += 1
        return trending_up / counted if counted else 0.0

    def _aggregate_cluster_forecast(
        self,
        cluster_id: uuid.UUID,
        racks: list[RackState],
        forecasts: dict[uuid.UUID, list[RackPrediction]] | None = None,
    ) -> list[RackPrediction]:
        """Reuses compute_cluster_state — the exact same aggregation the
        live ClusterState uses — by building synthetic RackStates out of
        each rack's predicted values at each horizon, instead of a second,
        parallel cluster-aggregation formula.

        `forecasts` defaults to the current live snapshot (self._rack_
        forecasts), but event detection needs to aggregate the *freshly
        computed* forecasts before they've been swapped in — passed
        explicitly there instead.
        """
        forecasts = self._rack_forecasts if forecasts is None else forecasts
        if not racks or not forecasts:
            return []

        cluster_points: list[RackPrediction] = []
        for horizon in FORECAST_HORIZONS_SECONDS:
            synthetic_racks: list[RackState] = []
            risks: list[float] = []
            confidences: list[float] = []
            timestamp = None

            for rack in racks:
                point = self._forecast_at(forecasts.get(rack.id), horizon)
                if point is None:
                    continue
                timestamp = point.timestamp
                risks.append(point.predicted_risk)
                confidences.append(point.confidence)
                synthetic_racks.append(
                    RackState(
                        id=rack.id,
                        name=rack.name,
                        temperature=point.predicted_temperature,
                        gpu_utilization=point.predicted_gpu_utilization,
                        cpu_utilization=rack.cpu_utilization,  # not forecast per-metric; carry current
                        power_draw=point.predicted_power,
                        cooling_efficiency=point.predicted_cooling,
                        fan_speed=rack.fan_speed,  # not forecast per-metric; carry current
                        health_score=point.predicted_health,
                        prediction_state=rack.prediction_state,
                        running_jobs=rack.running_jobs,
                        status=rack.status,
                    )
                )

            if not synthetic_racks or timestamp is None:
                continue

            predicted_cluster = compute_cluster_state(cluster_id, "forecast", synthetic_racks)
            cluster_points.append(
                RackPrediction(
                    horizon_seconds=horizon,
                    timestamp=timestamp,
                    predicted_temperature=predicted_cluster.average_temperature,
                    predicted_gpu_utilization=round(fmean(r.gpu_utilization for r in synthetic_racks), 2),
                    predicted_power=predicted_cluster.total_power,
                    predicted_health=predicted_cluster.overall_health,
                    predicted_cooling=predicted_cluster.cooling_efficiency,
                    predicted_risk=round(max(risks), 1),  # a cluster is only as safe as its riskiest rack
                    confidence=round(fmean(confidences), 1),
                )
            )
        return cluster_points

    async def _detect_and_persist_events(
        self,
        racks: list[RackState],
        next_forecasts: dict[uuid.UUID, list[RackPrediction]],
        cluster_id: uuid.UUID,
        scenario_db_id: uuid.UUID | None,
        now: datetime,
    ) -> list[Event]:
        drafts: list[EventDraft] = []
        past_warmup = self._ticks_seen > EVENT_WARMUP_TICKS

        for rack in racks:
            current = self._forecast_at(next_forecasts.get(rack.id), EVENT_HORIZON_SECONDS)
            previous = self._forecast_at(self._rack_forecasts.get(rack.id), EVENT_HORIZON_SECONDS)
            if current is None:
                continue

            confident_enough = past_warmup and current.confidence >= EVENT_MIN_CONFIDENCE

            was_safe = previous is None or previous.predicted_temperature <= THERMAL_HOTSPOT_THRESHOLD_C
            if confident_enough and was_safe and current.predicted_temperature > THERMAL_HOTSPOT_THRESHOLD_C:
                drafts.append(
                    self._draft(
                        cluster_id, rack.id, scenario_db_id, EventSeverity.WARNING, "Thermal hotspot predicted",
                        f"{rack.name} is forecast to reach {current.predicted_temperature:.1f}°C within "
                        f"{EVENT_HORIZON_SECONDS // 60} minute(s) ({current.confidence:.0f}% confidence).",
                    )
                )

            was_ok = previous is None or previous.predicted_cooling >= COOLING_DEGRADATION_THRESHOLD_PCT
            if confident_enough and was_ok and current.predicted_cooling < COOLING_DEGRADATION_THRESHOLD_PCT:
                drafts.append(
                    self._draft(
                        cluster_id, rack.id, scenario_db_id, EventSeverity.WARNING, "Cooling degradation predicted",
                        f"{rack.name}'s cooling efficiency is forecast to fall to "
                        f"{current.predicted_cooling:.0f}% within {EVENT_HORIZON_SECONDS // 60} minute(s).",
                    )
                )

            currently_spiking = current.predicted_power - rack.power_draw >= POWER_SPIKE_DELTA_KW
            previously_spiking = previous is not None and (previous.predicted_power - rack.power_draw) >= POWER_SPIKE_DELTA_KW
            if confident_enough and currently_spiking and not previously_spiking:
                drafts.append(
                    self._draft(
                        cluster_id, rack.id, scenario_db_id, EventSeverity.WARNING, "Power spike predicted",
                        f"{rack.name}'s power draw is forecast to reach {current.predicted_power:.1f} kW "
                        f"(+{current.predicted_power - rack.power_draw:.1f} kW) within "
                        f"{EVENT_HORIZON_SECONDS // 60} minute(s).",
                    )
                )

            if past_warmup and previous is not None:
                if abs(current.confidence - previous.confidence) >= CONFIDENCE_CHANGE_THRESHOLD:
                    drafts.append(
                        self._draft(
                            cluster_id, rack.id, scenario_db_id, EventSeverity.INFO, "Prediction confidence changed",
                            f"{rack.name}'s {EVENT_HORIZON_SECONDS // 60}-minute forecast confidence moved from "
                            f"{previous.confidence:.0f}% to {current.confidence:.0f}%.",
                        )
                    )

        # Cluster-level recovery: aggregate risk drops back below the safe
        # threshold after having been at or above it. Uses next_forecasts
        # explicitly — self._rack_forecasts still holds the *previous*
        # tick's values at this point in tick().
        fresh_cluster_forecast = self._aggregate_cluster_forecast(cluster_id, racks, next_forecasts)
        cluster_point = self._forecast_at(fresh_cluster_forecast, EVENT_HORIZON_SECONDS)
        if cluster_point is not None and past_warmup and cluster_point.confidence >= EVENT_MIN_CONFIDENCE:
            if (
                self._previous_cluster_risk is not None
                and self._previous_cluster_risk >= RISK_RECOVERY_THRESHOLD
                and cluster_point.predicted_risk < RISK_RECOVERY_THRESHOLD
            ):
                drafts.append(
                    self._draft(
                        cluster_id, None, scenario_db_id, EventSeverity.INFO, "Cluster recovery predicted",
                        f"Cluster-wide forecast risk has dropped to {cluster_point.predicted_risk:.0f}/100, "
                        f"below the {RISK_RECOVERY_THRESHOLD:.0f} recovery threshold.",
                    )
                )
            self._previous_cluster_risk = cluster_point.predicted_risk

        if not drafts:
            return []
        async with AsyncSessionLocal() as db:
            persisted = await persist_events(db, drafts)
        for event in persisted:
            logger.info("Forecast event: [%s] %s", event.severity.value, event.title)
        return persisted

    @staticmethod
    def _forecast_at(predictions: list[RackPrediction] | None, horizon: int) -> RackPrediction | None:
        if not predictions:
            return None
        return next((p for p in predictions if p.horizon_seconds == horizon), None)

    @staticmethod
    def _draft(
        cluster_id: uuid.UUID,
        rack_id: uuid.UUID | None,
        scenario_id: uuid.UUID | None,
        severity: EventSeverity,
        title: str,
        message: str,
    ) -> EventDraft:
        return EventDraft(
            cluster_id=cluster_id, rack_id=rack_id, scenario_id=scenario_id,
            severity=severity, title=title, message=message,
        )
