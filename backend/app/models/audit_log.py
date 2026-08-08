"""ActionAuditLog — an immutable record of every write action NeuroCore's
tool layer ever attempted (see app.neurocore.actions.PendingActionService).

One row per meaningful state transition of a PendingAction that a human
(or the confirm/cancel endpoints) triggered — proposal, confirmation
attempt, cancellation, execution outcome. Never stores secrets/API keys
(nothing in this schema could hold one); `conversation_id` stands in for
"user/session identifier" per the objective, since this phase has no
authentication system to draw a real user id from.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ActionAuditLog(Base):
    __tablename__ = "action_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pending_action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("pending_actions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # What happened: "proposed" / "confirmed" / "cancelled" / "executed" /
    # "execution_failed" / "rejected" (revalidation failed) — a short,
    # stable label, not a free-form sentence (see `result` for that).
    action: Mapped[str] = mapped_column(String(40), nullable=False)
    # Stand-in for "user/session identifier if available" — see module
    # docstring.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    plan_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    decision_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    result: Mapped[str] = mapped_column(String(1000), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ActionAuditLog(id={self.id!r}, action={self.action!r}, success={self.success!r})"
