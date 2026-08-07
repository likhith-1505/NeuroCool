"""Thermal risk scoring — shared by any ForecastEngine implementation
(trend-based today, a learned model later), since scoring risk from
already-predicted values is a separate concern from *how* those values
were predicted.

A high score here is also, conceptually, "probability of throttling" — one
factor viewed from two angles, not two separate numbers to maintain.
"""

from __future__ import annotations

from app.simulation.physics import clamp

# --- Tunable weights ----------------------------------------------------
# Kept in one place, same convention as app.simulation.physics — each
# factor from the objective's risk model gets its own clearly-named
# contribution, summed and clamped to 0-100 at the end.

THERMAL_BASE_C = 70.0
THERMAL_WEIGHT = 2.2
THERMAL_CAP = 55.0

TEMPERATURE_TREND_WEIGHT = 40.0  # per °C/sec
GPU_TREND_WEIGHT = 4.0  # per %/sec
POWER_TREND_WEIGHT = 3.0  # per kW/sec
TREND_FLOOR = -20.0
TREND_CAP = 25.0

COOLING_BASE_PCT = 60.0
COOLING_WEIGHT = 0.3
COOLING_CAP = 15.0

SCENARIO_ACTIVE_RISK = 8.0
NEIGHBOR_INFLUENCE_WEIGHT = 10.0


def compute_risk(
    predicted_temperature: float,
    temperature_slope_per_sec: float,
    gpu_slope_per_sec: float,
    power_slope_per_sec: float,
    predicted_cooling: float,
    scenario_active: bool,
    neighbor_trend_hint: float,
) -> float:
    """0-100 thermal risk score blending six factors: temperature trend,
    GPU trend, power trend, cooling efficiency, current scenario, and
    neighbour influence — exactly the risk model the objective specifies.
    """
    thermal_component = clamp((predicted_temperature - THERMAL_BASE_C) * THERMAL_WEIGHT, 0.0, THERMAL_CAP)

    # Trending toward trouble (positive slopes) adds risk; trending toward
    # safety (negative slopes, e.g. an active remediation) subtracts.
    trend_component = clamp(
        temperature_slope_per_sec * TEMPERATURE_TREND_WEIGHT
        + gpu_slope_per_sec * GPU_TREND_WEIGHT
        + power_slope_per_sec * POWER_TREND_WEIGHT,
        TREND_FLOOR,
        TREND_CAP,
    )

    # Weak cooling amplifies risk independent of current temperature.
    cooling_component = clamp((COOLING_BASE_PCT - predicted_cooling) * COOLING_WEIGHT, 0.0, COOLING_CAP)

    scenario_component = SCENARIO_ACTIVE_RISK if scenario_active else 0.0
    neighbor_component = clamp(neighbor_trend_hint, 0.0, 1.0) * NEIGHBOR_INFLUENCE_WEIGHT

    total = thermal_component + trend_component + cooling_component + scenario_component + neighbor_component
    return round(clamp(total, 0.0, 100.0), 1)
