"""OptimizationPlan model — the durable record of one planning cycle.

Produced by app.optimization.service.OptimizationService whenever a rack's
telemetry/forecast crosses a planning trigger (see app.optimization.planner)
— every candidate action it evaluated, each one's scores, and which one won,
are all captured here, so "what else did the system consider, and why did
it reject it" (per the objective) is durably queryable via GET /api/plans,
not just implied by the single recommendation that made it into a Decision.

`candidates` is stored as JSONB rather than a child table for the same
reason Decision.affected_jobs is JSONB: it's a small, self-contained list
that's always read/written as a whole with its parent plan, never queried
independently — a normalized child table would add a join for no benefit.
Each element's shape matches app.schemas.optimization.OptimizationCandidate
Read exactly, so it round-trips through Pydantic without translation code.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import ExecutionActionType, OptimizationPlanStatus

if TYPE_CHECKING:
    from app.models.cluster import Cluster
    from app.models.scenario import Scenario


class OptimizationPlan(Base):
    __tablename__ = "optimization_plans"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The rack whose telemetry/forecast triggered this planning cycle. Every
    # plan is anchored to exactly one rack even when its winning candidate
    # (e.g. cluster_rebalance) ends up spanning several — see
    # app.optimization.planner.
    trigger_rack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("racks.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Dedup key, mirroring Decision.rule_key: "rack_plan:<trigger_rack_id>".
    trigger_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    trigger_reason: Mapped[str] = mapped_column(String(500), nullable=False)

    status: Mapped[OptimizationPlanStatus] = mapped_column(
        Enum(OptimizationPlanStatus, name="optimization_plan_status"),
        default=OptimizationPlanStatus.COMPLETED,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Every evaluated candidate, ranked best-first (candidates[0] is the
    # winner) — see the module docstring for why this is JSONB.
    candidates: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # Denormalized from candidates[0] so the winner is queryable/sortable
    # without unpacking JSONB — the same "summary columns + full detail in
    # JSON" split Decision already uses for expected_temperature_reduction
    # alongside affected_jobs.
    winner_action_type: Mapped[ExecutionActionType | None] = mapped_column(
        Enum(ExecutionActionType, name="execution_action_type"), nullable=True
    )
    winner_overall_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    winner_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cluster: Mapped["Cluster"] = relationship()
    scenario: Mapped["Scenario | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"OptimizationPlan(id={self.id!r}, winner={self.winner_action_type!r}, status={self.status!r})"
