"""API representation of an OptimizationPlan — every candidate action the
planner considered, its scores, and which one won.

`candidates` on the ORM row is stored as JSONB shaped exactly like
list[OptimizationCandidateRead] (see app.models.optimization_plan's module
docstring), so it round-trips through Pydantic directly — no hand-written
dict-munging translation layer between the DB row and the API response.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ExecutionActionType, OptimizationPlanStatus


class CandidateScoreRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    temperature_reduction_c: float
    power_impact_kw: float
    cooling_improvement_pct: float
    execution_cost: float
    operational_disruption: float
    risk_reduction: float
    estimated_recovery_seconds: float
    confidence: float
    overall_score: float


class OptimizationCandidateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    action_type: ExecutionActionType
    description: str
    affected_racks: list[uuid.UUID]
    redistribute_racks: list[uuid.UUID]
    projected_temperature: float
    projected_cooling: float
    projected_power: float
    score: CandidateScoreRead
    rejection_reason: str | None = None


class OptimizationPlanRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cluster_id: uuid.UUID
    scenario_id: uuid.UUID | None
    trigger_rack_id: uuid.UUID | None
    trigger_key: str
    trigger_reason: str
    status: OptimizationPlanStatus
    error_message: str | None
    candidates: list[OptimizationCandidateRead]
    winner: OptimizationCandidateRead
    alternatives: list[OptimizationCandidateRead]
    created_at: datetime
    completed_at: datetime | None

    @classmethod
    def from_row(cls, row) -> "OptimizationPlanRead":
        """The ORM row's `candidates` JSONB list is already ranked
        best-first (see app.optimization.planner) — winner/alternatives
        are just a split of that same list, not separately stored.
        """
        candidates = [OptimizationCandidateRead.model_validate(c) for c in row.candidates]
        return cls(
            id=row.id,
            cluster_id=row.cluster_id,
            scenario_id=row.scenario_id,
            trigger_rack_id=row.trigger_rack_id,
            trigger_key=row.trigger_key,
            trigger_reason=row.trigger_reason,
            status=row.status,
            error_message=row.error_message,
            candidates=candidates,
            winner=candidates[0],
            alternatives=candidates[1:],
            created_at=row.created_at,
            completed_at=row.completed_at,
        )
