"""The optimization/planning engine: sits between forecasting and the
Decision Engine, evaluating multiple candidate remediations for a
triggered rack — each one simulated in an isolated physics context (see
app.optimization.simulator) and scored (see app.optimization.scoring) —
before any recommendation is generated (see app.optimization.planner for
the swappable OptimizationEngine contract, app.optimization.service for
persistence/lifecycle/event orchestration).

DecisionService consumes this package's *output* each tick (a plain
argument, keyed by trigger rack, the same way ClusterState/RackState and
ForecastService's predictions already are) — nothing in here imports
app.ai, keeping the two engines independent, the same principle
app.forecasting already follows.
"""
