"""Scenario model — a named simulation profile (e.g. "thermal-spike").

Definitions only: the engine that actually drives scenario behavior is out
of scope for the backend foundation.
"""

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base
from app.models.mixins import TimestampMixin

if TYPE_CHECKING:
    from app.models.event import Event


class Scenario(Base, TimestampMixin):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String(60), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    events: Mapped[list["Event"]] = relationship(back_populates="scenario")

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"Scenario(id={self.id!r}, key={self.key!r})"
