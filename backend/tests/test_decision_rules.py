"""Unit tests for RuleBasedDecisionEngine — no database/Redis required.

These test the reasoning in isolation (DecisionContext in, DecisionDraft
list out), the same way test_physics.py and test_scenario_manager.py test
the rest of the simulation's pure logic.
"""

import uuid
from datetime import UTC, datetime

from app.ai.base import DecisionContext
from app.ai.rules import MIGRATION_TEMPERATURE_C, WARMUP_EVALUATIONS, RuleBasedDecisionEngine
from app.models.enums import ExecutionActionType, RackStatus
from app.optimization.base import CandidateScore, OptimizationCandidate, OptimizationPlan
from app.simulation.state import ClusterState, RackState


def _make_rack(**overrides: object) -> RackState:
    defaults: dict[str, object] = dict(
        id=uuid.uuid4(),
        name="Rack Test",
        temperature=60.0,
        gpu_utilization=50.0,
        cpu_utilization=40.0,
        power_draw=8.0,
        cooling_efficiency=65.0,
        fan_speed=40.0,
        health_score=90.0,
        prediction_state="stable",
        running_jobs=10,
        status=RackStatus.HEALTHY,
    )
    defaults.update(overrides)
    return RackState(**defaults)  # type: ignore[arg-type]


def _make_cluster(racks: list[RackState]) -> ClusterState:
    return ClusterState(
        id=uuid.uuid4(),
        name="Test Cluster",
        overall_health=90.0,
        average_temperature=60.0,
        total_power=sum(r.power_draw for r in racks),
        cooling_efficiency=65.0,
        energy_savings=15.0,
        prediction_confidence=90.0,
    )


def _make_context(
    racks: list[RackState],
    *,
    scenario_key: str = "normal",
    scenario_target_rack_id: uuid.UUID | None = None,
    plans: dict[uuid.UUID, OptimizationPlan] | None = None,
) -> DecisionContext:
    return DecisionContext(
        cluster=_make_cluster(racks),
        racks=racks,
        scenario_key=scenario_key,
        scenario_target_rack_id=scenario_target_rack_id,
        recent_events=[],
        now=datetime.now(UTC),
        plans=plans or {},
    )


def _make_score(overall_score: float, *, confidence: float = 70.0, temperature_reduction_c: float = 5.0) -> CandidateScore:
    return CandidateScore(
        temperature_reduction_c=temperature_reduction_c,
        power_impact_kw=-1.0,
        cooling_improvement_pct=5.0,
        execution_cost=20.0,
        operational_disruption=15.0,
        risk_reduction=10.0,
        estimated_recovery_seconds=20.0,
        confidence=confidence,
        overall_score=overall_score,
    )


def _make_candidate(
    action_type: ExecutionActionType,
    rack: RackState,
    *,
    overall_score: float = 60.0,
    confidence: float = 70.0,
    temperature_reduction_c: float = 5.0,
    rejection_reason: str | None = None,
) -> OptimizationCandidate:
    return OptimizationCandidate(
        action_type=action_type,
        description=f"{action_type.value.replace('_', ' ').title()} on {rack.name}.",
        affected_racks=[rack.id],
        redistribute_racks=[],
        projected_temperature=rack.temperature - temperature_reduction_c,
        projected_cooling=rack.cooling_efficiency + 5.0,
        projected_power=rack.power_draw - 1.0,
        score=_make_score(overall_score, confidence=confidence, temperature_reduction_c=temperature_reduction_c),
        rejection_reason=rejection_reason,
    )


def _make_plan(rack: RackState, candidates: list[OptimizationCandidate], *, reason: str = "Trigger reason.") -> OptimizationPlan:
    return OptimizationPlan(
        trigger_key=f"rack_plan:{rack.id}",
        trigger_rack_id=rack.id,
        trigger_reason=reason,
        candidates=candidates,
        now=datetime.now(UTC),
    )


def _warm_up(engine: RuleBasedDecisionEngine, rack_id: uuid.UUID, *, temperature: float, cooling_efficiency: float, power_draw: float = 8.0) -> None:
    """Feed enough stable readings to clear WARMUP_EVALUATIONS, so a test
    exercises the rule's own logic rather than the warm-up gate.
    """
    for _ in range(WARMUP_EVALUATIONS + 1):
        rack = _make_rack(
            id=rack_id, temperature=temperature, cooling_efficiency=cooling_efficiency, power_draw=power_draw
        )
        engine.evaluate(_make_context([rack]))


# --- workload migration ------------------------------------------------


def test_workload_migration_fires_above_both_thresholds() -> None:
    engine = RuleBasedDecisionEngine()
    hot_rack = _make_rack(temperature=85.0, gpu_utilization=95.0)
    drafts = engine.evaluate(_make_context([hot_rack]))

    migration = [d for d in drafts if d.rule_key == f"workload_migration:{hot_rack.id}"]
    assert len(migration) == 1
    assert migration[0].affected_racks == [hot_rack.id]
    assert "migrate" in migration[0].recommended_action.lower()


def test_workload_migration_does_not_fire_below_thresholds() -> None:
    engine = RuleBasedDecisionEngine()
    ok_rack = _make_rack(temperature=75.0, gpu_utilization=95.0)  # gpu high but temp not
    drafts = engine.evaluate(_make_context([ok_rack]))
    assert not any(d.rule_key.startswith("workload_migration") for d in drafts)


def test_workload_migration_requires_both_conditions() -> None:
    engine = RuleBasedDecisionEngine()
    hot_but_idle = _make_rack(temperature=90.0, gpu_utilization=50.0)
    drafts = engine.evaluate(_make_context([hot_but_idle]))
    assert not any(d.rule_key.startswith("workload_migration") for d in drafts)


# --- cooling intervention (trend-based) ---------------------------------


def test_cooling_intervention_does_not_fire_on_first_sighting() -> None:
    """No previous reading yet -> no trend -> rule can't evaluate."""
    engine = RuleBasedDecisionEngine()
    rack = _make_rack(temperature=70.0, cooling_efficiency=40.0)
    drafts = engine.evaluate(_make_context([rack]))
    assert not any(d.rule_key.startswith("cooling_intervention") for d in drafts)


def test_cooling_intervention_fires_after_a_sustained_drop_over_the_window() -> None:
    """The trend is measured over a short rolling window (see
    COOLING_TREND_WINDOW_TICKS), not a single tick — so this feeds several
    consecutive worsening readings before checking. Warms up first so the
    warm-up gate (a separate concern — see test_decision_rules for it
    specifically) doesn't mask what's being tested here.
    """
    engine = RuleBasedDecisionEngine()
    rack_id = uuid.uuid4()
    _warm_up(engine, rack_id, temperature=65.0, cooling_efficiency=60.0)

    temperatures = [65.0, 66.0, 67.2, 68.6, 70.2, 72.0]
    coolings = [60.0, 57.0, 53.5, 49.5, 45.0, 40.0]

    drafts: list = []
    for temp, cooling in zip(temperatures, coolings):
        rack = _make_rack(id=rack_id, temperature=temp, cooling_efficiency=cooling)
        drafts = engine.evaluate(_make_context([rack]))

    matches = [d for d in drafts if d.rule_key == f"cooling_intervention:{rack_id}"]
    assert len(matches) == 1


def test_cooling_intervention_does_not_fire_before_the_window_fills() -> None:
    """Even a large apparent change shouldn't fire until enough readings
    have accumulated to call it a sustained trend rather than a blip. Warms
    up the engine using a *different* rack first (warm-up is a global,
    engine-wide counter) so this specifically isolates the per-rack window
    gate from the warm-up gate tested separately above.
    """
    engine = RuleBasedDecisionEngine()
    _warm_up(engine, uuid.uuid4(), temperature=65.0, cooling_efficiency=60.0)

    rack_id = uuid.uuid4()  # fresh rack — its window starts empty even though the engine is warmed up
    readings = [(65.0, 60.0), (70.0, 45.0), (75.0, 30.0)]  # only 3 — window needs 5
    drafts: list = []
    for temp, cooling in readings:
        rack = _make_rack(id=rack_id, temperature=temp, cooling_efficiency=cooling)
        drafts = engine.evaluate(_make_context([rack]))

    assert not any(d.rule_key.startswith("cooling_intervention") for d in drafts)


def test_cooling_intervention_does_not_fire_when_stable() -> None:
    engine = RuleBasedDecisionEngine()
    rack_id = uuid.uuid4()
    _warm_up(engine, rack_id, temperature=65.0, cooling_efficiency=60.0)

    drafts: list = []
    for _ in range(6):
        rack = _make_rack(id=rack_id, temperature=65.02, cooling_efficiency=59.98)  # basically unchanged
        drafts = engine.evaluate(_make_context([rack]))

    assert not any(d.rule_key.startswith("cooling_intervention") for d in drafts)


def test_cooling_intervention_stays_quiet_during_warmup_even_with_a_sharp_change() -> None:
    """The warm-up gate is a distinct safeguard from the window gate: a
    fresh engine (e.g. a just-booted simulation settling toward its true
    equilibrium) shouldn't fire trend rules at all for the first
    WARMUP_EVALUATIONS ticks, no matter how sharp the apparent trend looks.
    """
    engine = RuleBasedDecisionEngine()
    rack_id = uuid.uuid4()

    temperatures = [60.0, 63.0, 66.0, 69.0, 72.0, 75.0]
    coolings = [65.0, 58.0, 51.0, 44.0, 37.0, 30.0]

    drafts: list = []
    for temp, cooling in zip(temperatures, coolings):
        rack = _make_rack(id=rack_id, temperature=temp, cooling_efficiency=cooling)
        drafts = engine.evaluate(_make_context([rack]))

    assert not any(d.rule_key.startswith("cooling_intervention") for d in drafts)


# --- cluster rebalance ---------------------------------------------------


def test_cluster_rebalance_fires_with_multiple_hot_racks() -> None:
    engine = RuleBasedDecisionEngine()
    racks = [_make_rack(temperature=80.0), _make_rack(temperature=82.0), _make_rack(temperature=60.0)]
    drafts = engine.evaluate(_make_context(racks))

    rebalance = [d for d in drafts if d.rule_key == "cluster_rebalance"]
    assert len(rebalance) == 1
    assert len(rebalance[0].affected_racks) == 2


def test_cluster_rebalance_does_not_fire_with_a_single_hot_rack() -> None:
    engine = RuleBasedDecisionEngine()
    racks = [_make_rack(temperature=85.0), _make_rack(temperature=60.0), _make_rack(temperature=55.0)]
    drafts = engine.evaluate(_make_context(racks))
    assert not any(d.rule_key == "cluster_rebalance" for d in drafts)


# --- delay new jobs (power surge) ---------------------------------------


def test_delay_new_jobs_fires_on_power_spike() -> None:
    engine = RuleBasedDecisionEngine()
    rack_id = uuid.uuid4()
    _warm_up(engine, rack_id, temperature=65.0, cooling_efficiency=60.0, power_draw=8.0)

    tick2 = _make_rack(id=rack_id, power_draw=14.0)  # +6kW, well above the spike threshold
    drafts = engine.evaluate(_make_context([tick2]))

    matches = [d for d in drafts if d.rule_key == f"delay_new_jobs:{rack_id}"]
    assert len(matches) == 1
    assert "delay" in matches[0].title.lower()
    assert "hold" in matches[0].recommended_action.lower()


def test_delay_new_jobs_does_not_fire_on_gradual_power_increase() -> None:
    engine = RuleBasedDecisionEngine()
    rack_id = uuid.uuid4()
    _warm_up(engine, rack_id, temperature=65.0, cooling_efficiency=60.0, power_draw=8.0)

    tick2 = _make_rack(id=rack_id, power_draw=8.5)  # tiny increase
    drafts = engine.evaluate(_make_context([tick2]))

    assert not any(d.rule_key.startswith("delay_new_jobs") for d in drafts)


# --- confidence is derived from telemetry, not fixed ---------------------


def test_confidence_increases_with_margin_past_threshold() -> None:
    engine = RuleBasedDecisionEngine()
    barely_over = _make_rack(temperature=83.0, gpu_utilization=95.0)
    way_over = _make_rack(temperature=98.0, gpu_utilization=95.0)

    barely_drafts = engine.evaluate(_make_context([barely_over]))
    way_drafts = RuleBasedDecisionEngine().evaluate(_make_context([way_over]))

    barely_confidence = next(d.confidence for d in barely_drafts if d.rule_key.startswith("workload_migration"))
    way_confidence = next(d.confidence for d in way_drafts if d.rule_key.startswith("workload_migration"))
    assert way_confidence > barely_confidence
    assert 0.0 <= barely_confidence <= 100.0
    assert 0.0 <= way_confidence <= 100.0


# --- rule_key stability (what dedup relies on) ---------------------------


def test_rule_key_is_stable_across_evaluations_for_the_same_rack() -> None:
    engine = RuleBasedDecisionEngine()
    rack_id = uuid.uuid4()
    rack = _make_rack(id=rack_id, temperature=85.0, gpu_utilization=95.0)

    drafts1 = engine.evaluate(_make_context([rack]))
    drafts2 = engine.evaluate(_make_context([rack]))

    key1 = next(d.rule_key for d in drafts1 if d.rule_key.startswith("workload_migration"))
    key2 = next(d.rule_key for d in drafts2 if d.rule_key.startswith("workload_migration"))
    assert key1 == key2


# --- must reason from telemetry, never from the active scenario ----------


def test_recommendations_are_identical_regardless_of_scenario_key() -> None:
    """The same telemetry must produce the same recommendations no matter
    which scenario (if any) is reported as active — the engine is only
    allowed to look at the numbers.
    """
    hot_rack = _make_rack(temperature=88.0, gpu_utilization=95.0)

    drafts_normal = RuleBasedDecisionEngine().evaluate(_make_context([hot_rack], scenario_key="normal"))
    drafts_other = RuleBasedDecisionEngine().evaluate(_make_context([hot_rack], scenario_key="power_surge"))

    keys_normal = sorted(d.rule_key for d in drafts_normal)
    keys_other = sorted(d.rule_key for d in drafts_other)
    assert keys_normal == keys_other


# --- plan-driven recommendations (Optimization Engine-driven) ------------


def _pass_warmup(engine: RuleBasedDecisionEngine) -> None:
    """This rule shares the trend rules' warm-up gate (see app.ai.rules —
    a plan can carry the same kind of post-boot settling noise a live
    trend can), so exercising it needs the engine warmed up first, same as
    test_cooling_intervention_* above.
    """
    for _ in range(WARMUP_EVALUATIONS + 1):
        engine.evaluate(_make_context([_make_rack(id=uuid.uuid4())]))


def test_plan_driven_rule_fires_from_the_plans_winning_candidate() -> None:
    """The recommendation, confidence, and expected relief all come
    straight from the plan's winning candidate — nothing re-derived here.
    """
    engine = RuleBasedDecisionEngine()
    _pass_warmup(engine)
    rack = _make_rack(temperature=74.0, gpu_utilization=70.0)
    winner = _make_candidate(
        ExecutionActionType.WORKLOAD_MIGRATION, rack, overall_score=80.0, confidence=70.0, temperature_reduction_c=8.0
    )
    runner_up = _make_candidate(
        ExecutionActionType.FAN_OVERRIDE, rack, overall_score=40.0, rejection_reason="Less temperature relief."
    )
    plan = _make_plan(rack, [winner, runner_up])
    drafts = engine.evaluate(_make_context([rack], plans={rack.id: plan}))

    matches = [d for d in drafts if d.rule_key == f"optimized_workload_migration:{rack.id}"]
    assert len(matches) == 1
    match = matches[0]
    assert match.affected_racks == [rack.id]
    assert match.confidence == 70.0
    assert match.expected_temperature_reduction == 8.0
    assert match.plan_id == plan.id
    assert len(match.alternative_actions) == 1
    assert match.alternative_actions[0].rejection_reason == "Less temperature relief."


def test_plan_driven_rule_does_not_fire_when_winner_is_no_action() -> None:
    engine = RuleBasedDecisionEngine()
    _pass_warmup(engine)
    rack = _make_rack(temperature=74.0)
    winner = _make_candidate(ExecutionActionType.NO_ACTION, rack, overall_score=50.0)
    plan = _make_plan(rack, [winner])
    drafts = engine.evaluate(_make_context([rack], plans={rack.id: plan}))
    assert not any(d.rule_key.startswith("optimized_") for d in drafts)


def test_plan_driven_rule_does_not_fire_when_winner_is_cluster_rebalance() -> None:
    """cluster_rebalance stays the reactive rule's territory — see
    RuleBasedDecisionEngine._rule_from_optimization_plan's docstring.
    """
    engine = RuleBasedDecisionEngine()
    _pass_warmup(engine)
    rack = _make_rack(temperature=74.0)
    winner = _make_candidate(ExecutionActionType.CLUSTER_REBALANCE, rack, overall_score=70.0)
    plan = _make_plan(rack, [winner])
    drafts = engine.evaluate(_make_context([rack], plans={rack.id: plan}))
    assert not any(d.rule_key.startswith("optimized_") for d in drafts)


def test_plan_driven_rule_does_not_fire_without_a_plan() -> None:
    engine = RuleBasedDecisionEngine()
    _pass_warmup(engine)
    rack = _make_rack(temperature=74.0)
    drafts = engine.evaluate(_make_context([rack]))
    assert not any(d.rule_key.startswith("optimized_") for d in drafts)


def test_plan_driven_rule_does_not_fire_once_already_in_reactive_territory() -> None:
    """Once the rack is already past MIGRATION_TEMPERATURE_C, the reactive
    workload_migration rule owns the recommendation — no duplicate from
    the plan-driven rule.
    """
    engine = RuleBasedDecisionEngine()
    _pass_warmup(engine)
    rack = _make_rack(temperature=MIGRATION_TEMPERATURE_C + 1.0, gpu_utilization=95.0)
    winner = _make_candidate(ExecutionActionType.WORKLOAD_MIGRATION, rack, overall_score=80.0)
    plan = _make_plan(rack, [winner])
    drafts = engine.evaluate(_make_context([rack], plans={rack.id: plan}))
    assert not any(d.rule_key.startswith("optimized_") for d in drafts)
    assert any(d.rule_key.startswith("workload_migration") for d in drafts)


def test_plan_driven_rule_stays_quiet_during_warmup_even_with_a_confident_plan() -> None:
    """Mirrors test_cooling_intervention_stays_quiet_during_warmup_even_
    with_a_sharp_change: a freshly booted engine shouldn't act on a plan
    either, no matter how confident it claims to be, until the warm-up
    window has passed.
    """
    engine = RuleBasedDecisionEngine()
    rack = _make_rack(temperature=74.0)
    winner = _make_candidate(ExecutionActionType.WORKLOAD_MIGRATION, rack, overall_score=90.0, confidence=90.0)
    plan = _make_plan(rack, [winner])
    drafts = engine.evaluate(_make_context([rack], plans={rack.id: plan}))
    assert not any(d.rule_key.startswith("optimized_") for d in drafts)


def test_plan_driven_rule_alternatives_are_capped_at_two() -> None:
    engine = RuleBasedDecisionEngine()
    _pass_warmup(engine)
    rack = _make_rack(temperature=74.0)
    winner = _make_candidate(ExecutionActionType.WORKLOAD_MIGRATION, rack, overall_score=80.0)
    alt1 = _make_candidate(ExecutionActionType.FAN_OVERRIDE, rack, overall_score=60.0, rejection_reason="a")
    alt2 = _make_candidate(ExecutionActionType.JOB_DELAY, rack, overall_score=50.0, rejection_reason="b")
    alt3 = _make_candidate(ExecutionActionType.COOLING_ADJUSTMENT, rack, overall_score=40.0, rejection_reason="c")
    plan = _make_plan(rack, [winner, alt1, alt2, alt3])
    drafts = engine.evaluate(_make_context([rack], plans={rack.id: plan}))

    match = next(d for d in drafts if d.rule_key.startswith("optimized_"))
    assert len(match.alternative_actions) == 2
