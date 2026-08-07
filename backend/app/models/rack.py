"""Rack model — a single physical/simulated rack within a cluster."""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Enum, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.enums import RackStatus
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.cluster import Cluster
    from app.models.event import Event
    from app.models.telemetry import TelemetryRecord


class Rack(Base, TimestampMixin):
    __tablename__ = "racks"
    __table_args__ = (UniqueConstraint("cluster_id", "name", name="uq_rack_cluster_name"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    cluster_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("clusters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[RackStatus] = mapped_column(
        Enum(RackStatus, name="rack_status"),
        default=RackStatus.HEALTHY,
        nullable=False,
    )

    cluster: Mapped["Cluster"] = relationship(back_populates="racks")
    telemetry_records: Mapped[list["TelemetryRecord"]] = relationship(
        back_populates="rack",
        cascade="all, delete-orphan",
    )
    events: Mapped[list["Event"]] = relationship(
        back_populates="rack",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Rack(id={self.id!r}, name={self.name!r}, status={self.status!r})"
