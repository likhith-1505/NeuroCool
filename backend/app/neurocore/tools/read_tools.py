"""Read tools — execute immediately, never mutate anything, always return
real backend data (or raise ToolExecutionError when there's genuinely
nothing to return, never a fabricated value).

Every output_schema is either reused directly from an existing
app.schemas.* model (so a tool's answer is exactly what the matching REST
endpoint would return — no parallel representation to keep in sync) or a
small wrapper around one when the tool needs to expose a rack/plan lookup
the REST layer doesn't offer standalone.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.models.event import Event
from app.neurocore.permissions import Permission
from app.neurocore.tools.base import ToolContext, ToolExecutionError
from app.schemas.decision import DecisionRead
from app.schemas.event import EventRead
from app.schemas.execution import ExecutionRead
from app.schemas.forecast import ForecastPoint, RackForecastRead
from app.schemas.optimization import OptimizationPlanRead
from app.schemas.rack import RackTelemetry

DEFAULT_EVENT_LIMIT = 10
MAX_EVENT_LIMIT = 50
DEFAULT_EXECUTION_LIMIT = 10
MAX_EXECUTION_LIMIT = 50


# --- ReadClusterState --------------------------------------------------


class ReadClusterStateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadClusterStateOutput(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    overall_health: float
    average_temperature: float
    total_power: float
    cooling_efficiency: float
    energy_savings: float
    prediction_confidence: float
    scenario_key: str
    scenario_active: bool


class ReadClusterStateTool:
    name = "read_cluster_state"
    description = "Read the cluster's current live telemetry summary and which scenario (if any) is active."
    required_permission = Permission.READ_ONLY
    input_schema = ReadClusterStateInput
    output_schema = ReadClusterStateOutput
    creates_pending_action = False

    async def run(self, args: ReadClusterStateInput, context: ToolContext) -> ReadClusterStateOutput:
        cluster = context.simulation.cluster_state
        scenario = context.simulation.scenario_status
        return ReadClusterStateOutput(
            id=cluster.id,
            name=cluster.name,
            overall_health=cluster.overall_health,
            average_temperature=cluster.average_temperature,
            total_power=cluster.total_power,
            cooling_efficiency=cluster.cooling_efficiency,
            energy_savings=cluster.energy_savings,
            prediction_confidence=cluster.prediction_confidence,
            scenario_key=scenario.key,
            scenario_active=scenario.key != "normal",
        )


# --- ReadRack ------------------------------------------------------------


class ReadRackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rack_id: uuid.UUID = Field(..., description="The rack's id, as seen in the cluster overview.")


class ReadRackTool:
    name = "read_rack"
    description = "Read a single rack's current live telemetry (temperature, GPU utilization, health, status, etc.)."
    required_permission = Permission.READ_ONLY
    input_schema = ReadRackInput
    output_schema = RackTelemetry
    creates_pending_action = False

    async def run(self, args: ReadRackInput, context: ToolContext) -> RackTelemetry:
        rack = context.simulation.rack_state(args.rack_id)
        if rack is None:
            raise ToolExecutionError(f"No rack found with id {args.rack_id}.")
        return RackTelemetry.model_validate(rack)


# --- ReadForecast ----------------------------------------------------------


class ReadForecastInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rack_id: uuid.UUID = Field(..., description="The rack to read the forecast for.")


class ReadForecastTool:
    name = "read_forecast"
    description = "Read a rack's forecast — predicted temperature/GPU/cooling/risk at each horizon (30s/60s/120s/300s)."
    required_permission = Permission.READ_ONLY
    input_schema = ReadForecastInput
    output_schema = RackForecastRead
    creates_pending_action = False

    async def run(self, args: ReadForecastInput, context: ToolContext) -> RackForecastRead:
        rack = context.simulation.rack_state(args.rack_id)
        if rack is None:
            raise ToolExecutionError(f"No rack found with id {args.rack_id}.")
        predictions = context.simulation.rack_forecast(rack.id)
        return RackForecastRead(
            rack_id=rack.id, rack_name=rack.name,
            predictions=[ForecastPoint.model_validate(point) for point in predictions],
        )


# --- ReadOptimizationPlan ---------------------------------------------------


class ReadOptimizationPlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: uuid.UUID | None = Field(default=None, description="Read this specific plan.")
    rack_id: uuid.UUID | None = Field(
        default=None, description="Read the most recent plan triggered for this rack, if plan_id is not given."
    )


class ReadOptimizationPlanTool:
    name = "read_optimization_plan"
    description = (
        "Read an optimization plan — every candidate action considered, their scores, the winner, and the "
        "rejected alternatives with their reasons. Provide plan_id, or rack_id to find the latest plan for a rack."
    )
    required_permission = Permission.READ_ONLY
    input_schema = ReadOptimizationPlanInput
    output_schema = OptimizationPlanRead
    creates_pending_action = False

    async def run(self, args: ReadOptimizationPlanInput, context: ToolContext) -> OptimizationPlanRead:
        if args.plan_id is not None:
            plan = context.simulation.get_plan(args.plan_id)
            if plan is None:
                raise ToolExecutionError(f"No optimization plan found with id {args.plan_id}.")
            return OptimizationPlanRead.from_row(plan)

        if args.rack_id is not None:
            plan = next(
                (p for p in context.simulation.all_plans if p.trigger_rack_id == args.rack_id), None
            )
            if plan is None:
                raise ToolExecutionError(f"No optimization plan has been triggered for rack {args.rack_id}.")
            return OptimizationPlanRead.from_row(plan)

        raise ToolExecutionError("Provide either plan_id or rack_id.")


# --- ReadDecision ----------------------------------------------------------


class ReadDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: uuid.UUID | None = Field(default=None, description="Read this specific decision.")
    rack_id: uuid.UUID | None = Field(
        default=None, description="Read the most recent decision for this rack, if decision_id is not given."
    )


class ReadDecisionTool:
    name = "read_decision"
    description = (
        "Read a decision — the recommended action, reasoning, confidence, and alternatives considered. "
        "Provide decision_id, or rack_id to find the latest decision for a rack."
    )
    required_permission = Permission.READ_ONLY
    input_schema = ReadDecisionInput
    output_schema = DecisionRead
    creates_pending_action = False

    async def run(self, args: ReadDecisionInput, context: ToolContext) -> DecisionRead:
        if args.decision_id is not None:
            decision = context.simulation.get_decision(args.decision_id)
            if decision is None:
                raise ToolExecutionError(f"No decision found with id {args.decision_id}.")
            return DecisionRead.model_validate(decision)

        if args.rack_id is not None:
            decision = next(
                (d for d in context.simulation.all_decisions if args.rack_id in d.affected_racks), None
            )
            if decision is None:
                raise ToolExecutionError(f"No decision has been recorded for rack {args.rack_id}.")
            return DecisionRead.model_validate(decision)

        raise ToolExecutionError("Provide either decision_id or rack_id.")


# --- ReadRecentEvents --------------------------------------------------


class ReadRecentEventsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rack_id: uuid.UUID | None = Field(default=None, description="Limit to events for this rack; omit for cluster-wide.")
    limit: int = Field(default=DEFAULT_EVENT_LIMIT, ge=1, le=MAX_EVENT_LIMIT)


class ReadRecentEventsOutput(BaseModel):
    events: list[EventRead]


class ReadRecentEventsTool:
    name = "read_recent_events"
    description = "Read the most recent events (incidents, threshold crossings, scenario/decision/execution lifecycle notices), newest first."
    required_permission = Permission.READ_ONLY
    input_schema = ReadRecentEventsInput
    output_schema = ReadRecentEventsOutput
    creates_pending_action = False

    async def run(self, args: ReadRecentEventsInput, context: ToolContext) -> ReadRecentEventsOutput:
        query = select(Event).order_by(Event.occurred_at.desc()).limit(args.limit)
        if args.rack_id is not None:
            query = query.where(Event.rack_id == args.rack_id)
        result = await context.db.execute(query)
        events = list(result.scalars().all())
        return ReadRecentEventsOutput(events=[EventRead.model_validate(event) for event in events])


# --- ReadExecutionHistory ------------------------------------------------


class ReadExecutionHistoryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rack_id: uuid.UUID | None = Field(default=None, description="Limit to executions affecting this rack.")
    decision_id: uuid.UUID | None = Field(default=None, description="Limit to executions for this decision.")
    limit: int = Field(default=DEFAULT_EXECUTION_LIMIT, ge=1, le=MAX_EXECUTION_LIMIT)


class ReadExecutionHistoryOutput(BaseModel):
    executions: list[ExecutionRead]


class ReadExecutionHistoryTool:
    name = "read_execution_history"
    description = "Read past remediation executions (action taken, status, summary, error if failed), newest first."
    required_permission = Permission.READ_ONLY
    input_schema = ReadExecutionHistoryInput
    output_schema = ReadExecutionHistoryOutput
    creates_pending_action = False

    async def run(self, args: ReadExecutionHistoryInput, context: ToolContext) -> ReadExecutionHistoryOutput:
        executions = context.simulation.all_executions
        if args.rack_id is not None:
            executions = [e for e in executions if args.rack_id in e.affected_racks]
        if args.decision_id is not None:
            executions = [e for e in executions if e.decision_id == args.decision_id]
        executions = executions[: args.limit]
        return ReadExecutionHistoryOutput(executions=[ExecutionRead.model_validate(e) for e in executions])
