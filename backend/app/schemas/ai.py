"""API representation of the NeuroCore chat request/response — see
app.neurocore.service.NeuroCoreService.chat.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.schemas.pending_action import PendingActionRead


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="The operator's question.")
    rack_id: uuid.UUID | None = Field(default=None, description="Optional rack to scope the question to.")
    conversation_id: uuid.UUID | None = Field(
        default=None, description="Continue an existing conversation; omit to start a new one."
    )


class ChatResponse(BaseModel):
    conversation_id: uuid.UUID
    response: str
    confidence: float
    sources: list[str] = Field(
        default_factory=list,
        description="Real backend records the answer is grounded in, e.g. 'forecast:Rack A1', 'plan:<uuid>'.",
    )
    pending_action: PendingActionRead | None = Field(
        default=None,
        description=(
            "Set when this turn proposed a write action (e.g. execute_decision). Nothing has been "
            "executed yet — POST /api/ai/actions/{id}/confirm or .../cancel to resolve it."
        ),
    )
