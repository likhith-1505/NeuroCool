"""Event model — a discrete, timestamped occurrence in the system.

An event may be scoped to a cluster and/or a rack, and may optionally be
attributed to a scenario run. All scoping columns are nullable so an event
can represent anything from a cluster-wide notice to a single-rack alert.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import EventSeverity

if TYPE_CHECKING:
    from app.models.cluster import Cluster
    from app.models.rack import Rack
    from app.models.scenario import Scenario


class Event(Base):
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cluster_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    rack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("racks.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scenario_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scenarios.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    severity: Mapped[EventSeverity] = mapped_column(
        Enum(EventSeverity, name="event_severity"),
        default=EventSeverity.INFO,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    cluster: Mapped["Cluster | None"] = relationship(back_populates="events")
    rack: Mapped["Rack | None"] = relationship(back_populates="events")
    scenario: Mapped["Scenario | None"] = relationship(back_populates="events")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Event(id={self.id!r}, severity={self.severity!r}, title={self.title!r})"
