"""API representation of an Execution — the durable record of one attempt
to carry out a Decision's recommended_action.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import ExecutionActionType, ExecutionStatus


class ExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    decision_id: uuid.UUID
    action_type: ExecutionActionType | None
    status: ExecutionStatus
    affected_racks: list[uuid.UUID]
    summary: str
    error_message: str | None
    started_at: datetime
    completed_at: datetime | None
