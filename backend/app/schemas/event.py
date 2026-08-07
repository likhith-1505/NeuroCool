"""API representation of a persisted Event row."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import EventSeverity


class EventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cluster_id: uuid.UUID | None
    rack_id: uuid.UUID | None
    scenario_id: uuid.UUID | None
    severity: EventSeverity
    title: str
    message: str | None
    occurred_at: datetime
