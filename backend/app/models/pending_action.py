"""PendingAction — a write action NeuroCore has proposed but not yet
carried out (see app.neurocore.actions.PendingActionService).

Created the instant a write tool (ExecuteDecision, ReplaySimulation) is
called — never when the underlying mutation actually happens. The mutation
itself only ever occurs inside PendingActionService.confirm, which
re-validates everything (the plan/decision/rack/scenario may have moved on
since this row was created) and then calls the *existing*
SimulationService.execute_decision/.replay_scenario — this model never
grows its own execution logic.

`scenario_key` is captured at creation time specifically so confirm() can
detect "the active scenario has changed since this action was proposed"
— one of the objective's required re-validation checks — without needing
a second source of truth for "what scenario was active back then".
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import PendingActionStatus, PendingActionType

if TYPE_CHECKING:
    from app.models.conversation import Conversation
    from app.models.decision import Decision
    from app.models.execution import Execution
    from app.models.optimization_plan import OptimizationPlan


class PendingAction(Base):
    __tablename__ = "pending_actions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Present for EXECUTE_DECISION, null for REPLAY_SIMULATION (which has
    # no single plan/decision behind it).
    plan_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("optimization_plans.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action_type: Mapped[PendingActionType] = mapped_column(
        Enum(PendingActionType, name="pending_action_type"), nullable=False
    )
    # Human-readable target for display — a rack name, or "cluster" for a
    # cluster-wide action like ReplaySimulation.
    target: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[PendingActionStatus] = mapped_column(
        Enum(PendingActionStatus, name="pending_action_status"),
        default=PendingActionStatus.PENDING,
        nullable=False,
        index=True,
    )
    # The exact confirmation prompt shown to the operator ("I can execute
    # ... Proceed?") — deterministically built from real plan/decision
    # fields, never LLM-generated, so it can be trusted and re-displayed.
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    # The scenario active when this action was proposed — see module
    # docstring.
    scenario_key: Mapped[str] = mapped_column(String(80), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("executions.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["Conversation"] = relationship()
    plan: Mapped["OptimizationPlan | None"] = relationship()
    decision: Mapped["Decision | None"] = relationship()
    execution: Mapped["Execution | None"] = relationship()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"PendingAction(id={self.id!r}, action_type={self.action_type!r}, status={self.status!r})"
