"""TelemetryRecord model — a single timestamped sensor reading for a rack.

Append-only by design: no updated_at column, since a recorded reading is
never edited after the fact.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base

if TYPE_CHECKING:
    from app.models.rack import Rack


class TelemetryRecord(Base):
    __tablename__ = "telemetry_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    rack_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("racks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    temperature_celsius: Mapped[float] = mapped_column(Float, nullable=False)
    gpu_utilization_pct: Mapped[float] = mapped_column(Float, nullable=False)
    power_draw_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    rack: Mapped["Rack"] = relationship(back_populates="telemetry_records")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"TelemetryRecord(id={self.id!r}, rack_id={self.rack_id!r}, recorded_at={self.recorded_at!r})"
