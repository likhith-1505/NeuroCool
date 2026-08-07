"""Execution model — the durable record of one attempt to carry out a
Decision's recommended_action.

Always created when POST /api/decisions/{id}/execute is called: on success
status=RUNNING (then later COMPLETED once its effect has fully run its
course — see app.execution.manager), on failure (e.g. no healthy rack
available to redistribute onto) status=FAILED with error_message set. Either
way this is the durable "execution history" the objective asks for — a
failed attempt is still history worth keeping, not silently dropped.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import ExecutionActionType, ExecutionStatus

if TYPE_CHECKING:
    from app.models.cluster import Cluster
    from app.models.decision import Decision
    from app.models.scenario import Scenario


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clusters.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id", ondelete="SET NULL"), nullable=True, index=True
    )

    # Nullable to accommodate a decision whose rule_key has no known
    # remediation mapping yet (defensive — not reachable with the current
    # rule set, but a future rule could outpace the execution mapping).
    # Such an attempt still gets a durable, FAILED Execution row.
    action_type: Mapped[ExecutionActionType | None] = mapped_column(
        Enum(ExecutionActionType, name="execution_action_type"), nullable=True
    )
    status: Mapped[ExecutionStatus] = mapped_column(
        Enum(ExecutionStatus, name="execution_status"), default=ExecutionStatus.RUNNING, nullable=False
    )
    affected_racks: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    summary: Mapped[str] = mapped_column(String(500), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    decision: Mapped["Decision"] = relationship()
    cluster: Mapped["Cluster"] = relationship()
    scenario: Mapped["Scenario | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Execution(id={self.id!r}, action_type={self.action_type!r}, status={self.status!r})"
