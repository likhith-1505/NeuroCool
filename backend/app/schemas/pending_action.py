"""API representation of a PendingAction — see app.neurocore.actions."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import PendingActionStatus, PendingActionType


class PendingActionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    conversation_id: uuid.UUID
    plan_id: uuid.UUID | None
    decision_id: uuid.UUID | None
    action_type: PendingActionType
    target: str
    status: PendingActionStatus
    summary: str
    error_message: str | None
    execution_id: uuid.UUID | None
    created_at: datetime
    expires_at: datetime
    confirmed_at: datetime | None
    completed_at: datetime | None
