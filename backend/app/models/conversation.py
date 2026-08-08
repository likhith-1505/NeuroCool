"""Conversation / ConversationMessage — the minimum persistence needed for
NeuroCore chat history (see app.neurocore.service.NeuroCoreService.chat).

Conversation is deliberately bare (just an id and timestamps) — it exists
only to group messages; nothing about "what backend state was live when
this conversation started" is stored on it. ConversationMessage stores
exactly what the objective asks for (role, content, timestamp,
conversation_id) plus a small, bounded citation summary (`sources`,
`confidence`) for assistant turns — the compact list of which real backend
records grounded that answer, not the records themselves. It never stores
the full NeuroCoreContext (cluster/rack telemetry, forecasts, plans,
decisions, executions, events) that produced an answer — that would be
"unnecessarily persisting the entire backend context inside every
message", which the objective explicitly rules out; the live backend
state can always be re-read from its own tables when needed.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import ConversationRole


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    messages: Mapped[list["ConversationMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="ConversationMessage.created_at",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Conversation(id={self.id!r})"


class ConversationMessage(Base):
    __tablename__ = "conversation_messages"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[ConversationRole] = mapped_column(Enum(ConversationRole, name="conversation_role"), nullable=False)
    # Text, not a bounded String(n) like the rest of this codebase's
    # short operational fields (e.g. Decision.reasoning) — an LLM response
    # is genuinely unbounded free-form content, unlike anything else
    # persisted so far.
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Citation summary for an assistant turn — see module docstring.
    # Empty/None for user messages.
    sources: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"ConversationMessage(id={self.id!r}, role={self.role!r})"
