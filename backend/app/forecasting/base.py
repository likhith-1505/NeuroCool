"""The ForecastEngine contract.

Mirrors app.ai.base's DecisionEngine design exactly, for the same reason:
this is the seam that lets the forecasting *strategy* be swapped later
(linear trend extrapolation today, ARIMA/Prophet/LSTM/Transformer later)
without touching ForecastService, the REST routes, or the WebSocket
payload. Every concrete engine implements one method:
`forecast(context, horizons) -> list[RackPrediction]`.

Deliberately excluded from ForecastContext: database ids, WebSocket
connections, anything persistence- or transport-related — same principle
as DecisionContext. ForecastService (see app.forecasting.service) owns
history and turning predictions into REST/WebSocket/event output; a
forecasting engine only ever sees telemetry.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.simulation.state import RackState

# The four horizons every forecast is generated for, per the objective.
FORECAST_HORIZONS_SECONDS: tuple[int, ...] = (30, 60, 120, 300)


@dataclass(frozen=True)
class HistoryPoint:
    """One historical reading for a rack — the forecasting engine's raw material."""

    timestamp: datetime
    temperature: float
    gpu_utilization: float
    power_draw: float
    cooling_efficiency: float


@dataclass(frozen=True)
class ForecastContext:
    """Everything a forecasting engine is allowed to look at, for one rack."""

    rack: RackState
    history: list[HistoryPoint]  # oldest first
    scenario_key: str
    scenario_active: bool  # scenario_key != "normal"
    neighbor_trend_hint: float  # 0.0-1.0: how much ring-neighbors are also trending toward trouble
    now: datetime


@dataclass(frozen=True)
class RackPrediction:
    """One forecast point at a specific horizon — exactly the fields the
    objective specifies a forecast should contain, plus horizon_seconds so
    a list of these can be told apart.
    """

    horizon_seconds: int
    timestamp: datetime
    predicted_temperature: float
    predicted_gpu_utilization: float
    predicted_power: float
    predicted_health: float
    predicted_cooling: float
    predicted_risk: float
    confidence: float


class ForecastEngine(Protocol):
    """Contract every forecasting strategy must satisfy.

    Stateless from the *caller's* perspective — ForecastService owns
    history and lifecycle. An engine only maps "recent history for one
    rack" to "predictions at these horizons"; it is free to keep private
    state internally if a future implementation needs it, as long as
    `forecast` keeps this exact signature.
    """

    def forecast(self, context: ForecastContext, horizons: tuple[int, ...]) -> list[RackPrediction]:
        """Return one RackPrediction per horizon, ordered ascending."""
        ...
