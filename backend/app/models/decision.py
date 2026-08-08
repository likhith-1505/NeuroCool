"""Decision model — a single AI-generated operational recommendation.

Represents a persisted Decision produced by the DecisionEngine (see
app.ai). A decision can span multiple racks, so affected racks are stored
as a plain array of rack ids rather than a single FK — unlike Event.rack_id,
which is always exactly one rack or none. affected_jobs has no backing Job
model yet, so it's stored as opaque JSON — an explicit placeholder, per the
objective, until a real Job entity exists.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import DecisionStatus, EventSeverity

if TYPE_CHECKING:
    from app.models.cluster import Cluster
    from app.models.scenario import Scenario


class Decision(Base):
    __tablename__ = "decisions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # The OptimizationPlan this decision was derived from, if any (see
    # app.optimization) — nullable because the reactive rules in app.ai.rules
    # still reason directly from telemetry, not every rule's threshold, not
    # every decision goes through planning. Nulled rather than cascade-
    # deleted if the plan is ever pruned: the decision's own recommended_
    # action/confidence/reasoning stand on their own, plan_id is only a
    # pointer to the fuller candidate/alternatives detail.
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Stable identifier for *what kind* of recommendation this is (e.g.
    # "workload_migration:<rack_id>"), used to deduplicate repeated firings
    # of the same rule into a single updated row instead of a new one.
    rule_key: Mapped[str] = mapped_column(String(160), nullable=False, index=True)

    severity: Mapped[EventSeverity] = mapped_column(Enum(EventSeverity, name="event_severity"), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    reasoning: Mapped[str] = mapped_column(String(2000), nullable=False)
    recommended_action: Mapped[str] = mapped_column(String(500), nullable=False)
    expected_temperature_reduction: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_power_saving: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    affected_racks: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    affected_jobs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Compact snapshot of the runner-up candidates from the OptimizationPlan
    # this decision came from (empty list for decisions that didn't go
    # through planning) — {action_type, description, overall_score,
    # rejection_reason} per alternative, at most 2 ("Alternative 1",
    # "Alternative 2" per the objective). The plan itself (see plan_id)
    # remains the canonical, full-detail record — this is a denormalized
    # summary so a single GET /api/decisions/{id} already answers "what
    # else was considered and why not" without a second round trip, the
    # same way expected_temperature_reduction is already a derived summary
    # rather than a pointer back into the rule that computed it.
    alternative_actions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    status: Mapped[DecisionStatus] = mapped_column(
        Enum(DecisionStatus, name="decision_status"), default=DecisionStatus.PENDING, nullable=False
    )

    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    cluster: Mapped["Cluster"] = relationship()
    scenario: Mapped["Scenario | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Decision(id={self.id!r}, title={self.title!r}, status={self.status!r})"
