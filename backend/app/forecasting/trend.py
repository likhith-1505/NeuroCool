"""TrendForecastEngine — deterministic linear extrapolation from recent
telemetry history. The initial ForecastEngine implementation; see
app.forecasting.base.ForecastEngine for the contract a future ARIMA/
Prophet/LSTM/Transformer-based engine would need to satisfy instead.

"Extrapolate from current telemetry, recent history, current scenario,
simulation trends" (per the objective) is implemented as: fit a
least-squares line to each metric over a short recent window (not the
full 10-minute retention — a fresher trend is more informative than one
diluted by old, possibly-unrelated history), then project it forward to
each horizon. The current scenario and simulation trends are already
reflected in *how* the telemetry has been moving — the fit doesn't need
to know *why* it's trending, only that it is, which is what keeps this
free of scenario-specific branches (the same principle RuleBasedDecision
Engine follows in app.ai.rules).
"""

from __future__ import annotations

from datetime import timedelta

from app.forecasting.base import ForecastContext, RackPrediction
from app.forecasting.risk import compute_risk
from app.simulation.physics import clamp, compute_health_score

TREND_WINDOW = timedelta(seconds=90)
MIN_SAMPLES_FOR_TREND = 3
MIN_SAMPLES_FOR_FULL_CONFIDENCE = 30

BASE_CONFIDENCE = 95.0
MIN_CONFIDENCE = 5.0
HORIZON_DECAY_SPAN_SECONDS = 600.0
HORIZON_DECAY_FLOOR = 0.15


def _linear_fit(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Least-squares y = slope*x + intercept over (x, y) pairs. Returns
    (slope, intercept, r_squared). x is expected to be small (seconds
    since the window's first point), which keeps this numerically fine
    without needing a real numeric library — deterministic, not random,
    per the objective.
    """
    n = len(points)
    if n == 0:
        return 0.0, 0.0, 0.0
    if n == 1:
        return 0.0, points[0][1], 0.0

    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    ss_xx = sum((x - mean_x) ** 2 for x, _ in points)

    if ss_xx == 0:
        return 0.0, mean_y, 0.0

    slope = ss_xy / ss_xx
    intercept = mean_y - slope * mean_x

    ss_tot = sum((y - mean_y) ** 2 for _, y in points)
    if ss_tot == 0:
        return slope, intercept, 1.0  # perfectly flat and perfectly fit
    ss_res = sum((y - (slope * x + intercept)) ** 2 for x, y in points)
    r_squared = clamp(1.0 - ss_res / ss_tot, 0.0, 1.0)
    return slope, intercept, r_squared


class TrendForecastEngine:
    """Linear-extrapolation ForecastEngine. Conforms structurally to
    app.forecasting.base.ForecastEngine.
    """

    def forecast(self, context: ForecastContext, horizons: tuple[int, ...]) -> list[RackPrediction]:
        window = [p for p in context.history if p.timestamp >= context.now - TREND_WINDOW] or context.history

        if len(window) < MIN_SAMPLES_FOR_TREND:
            return [self._flat_prediction(context, horizon) for horizon in horizons]

        origin = window[0].timestamp
        temp_slope, temp_intercept, temp_fit = _linear_fit(
            [((p.timestamp - origin).total_seconds(), p.temperature) for p in window]
        )
        gpu_slope, gpu_intercept, gpu_fit = _linear_fit(
            [((p.timestamp - origin).total_seconds(), p.gpu_utilization) for p in window]
        )
        power_slope, power_intercept, power_fit = _linear_fit(
            [((p.timestamp - origin).total_seconds(), p.power_draw) for p in window]
        )
        cooling_slope, cooling_intercept, cooling_fit = _linear_fit(
            [((p.timestamp - origin).total_seconds(), p.cooling_efficiency) for p in window]
        )
        fit_quality = (temp_fit + gpu_fit + power_fit + cooling_fit) / 4.0
        elapsed_now = (context.now - origin).total_seconds()

        predictions: list[RackPrediction] = []
        for horizon in horizons:
            elapsed_target = elapsed_now + horizon

            predicted_temperature = clamp(temp_slope * elapsed_target + temp_intercept, 35.0, 99.0)
            predicted_gpu = clamp(gpu_slope * elapsed_target + gpu_intercept, 0.0, 100.0)
            predicted_power = max(0.0, power_slope * elapsed_target + power_intercept)
            predicted_cooling = clamp(cooling_slope * elapsed_target + cooling_intercept, 30.0, 99.0)
            predicted_health = compute_health_score(predicted_temperature, predicted_cooling, predicted_gpu)

            predicted_risk = compute_risk(
                predicted_temperature=predicted_temperature,
                temperature_slope_per_sec=temp_slope,
                gpu_slope_per_sec=gpu_slope,
                power_slope_per_sec=power_slope,
                predicted_cooling=predicted_cooling,
                scenario_active=context.scenario_active,
                neighbor_trend_hint=context.neighbor_trend_hint,
            )

            confidence = self._confidence_for(horizon, len(window), fit_quality)

            predictions.append(
                RackPrediction(
                    horizon_seconds=horizon,
                    timestamp=context.now + timedelta(seconds=horizon),
                    predicted_temperature=round(predicted_temperature, 2),
                    predicted_gpu_utilization=round(predicted_gpu, 2),
                    predicted_power=round(predicted_power, 2),
                    predicted_health=round(predicted_health, 2),
                    predicted_cooling=round(predicted_cooling, 2),
                    predicted_risk=predicted_risk,
                    confidence=confidence,
                )
            )
        return predictions

    @staticmethod
    def _flat_prediction(context: ForecastContext, horizon: int) -> RackPrediction:
        """Not enough history yet to fit a trend — predict "no change" at
        low confidence rather than guessing. Still uses the real risk
        model (with zero trend contribution), not a placeholder value.
        """
        rack = context.rack
        risk = compute_risk(
            predicted_temperature=rack.temperature,
            temperature_slope_per_sec=0.0,
            gpu_slope_per_sec=0.0,
            power_slope_per_sec=0.0,
            predicted_cooling=rack.cooling_efficiency,
            scenario_active=context.scenario_active,
            neighbor_trend_hint=context.neighbor_trend_hint,
        )
        return RackPrediction(
            horizon_seconds=horizon,
            timestamp=context.now + timedelta(seconds=horizon),
            predicted_temperature=rack.temperature,
            predicted_gpu_utilization=rack.gpu_utilization,
            predicted_power=rack.power_draw,
            predicted_health=rack.health_score,
            predicted_cooling=rack.cooling_efficiency,
            predicted_risk=risk,
            confidence=MIN_CONFIDENCE,
        )

    @staticmethod
    def _confidence_for(horizon_seconds: int, sample_count: int, fit_quality: float) -> float:
        """Confidence decreases as horizon grows (required by the
        objective), and also with insufficient history or a noisy/poorly
        fitting recent trend — all derived from the fit itself, not a
        fixed number per horizon.
        """
        horizon_decay = clamp(1.0 - horizon_seconds / HORIZON_DECAY_SPAN_SECONDS, HORIZON_DECAY_FLOOR, 1.0)
        data_sufficiency = clamp(sample_count / MIN_SAMPLES_FOR_FULL_CONFIDENCE, 0.2, 1.0)
        fit_factor = clamp(0.5 + fit_quality * 0.5, 0.5, 1.0)
        confidence = BASE_CONFIDENCE * horizon_decay * data_sufficiency * fit_factor
        return round(clamp(confidence, MIN_CONFIDENCE, BASE_CONFIDENCE), 1)
