"""The forecasting engine: continuously predicts future cluster/rack state
from rolling telemetry history, independently of the Decision Engine (see
app.forecasting.base for the swappable ForecastEngine contract,
app.forecasting.trend for the current linear-extrapolation implementation,
and app.forecasting.service for history/aggregation/event orchestration).

DecisionService consumes this package's *output* each tick (a plain
argument, like ClusterState/RackState already are) — nothing in here
imports app.ai, keeping the two engines independent per the objective.
"""
