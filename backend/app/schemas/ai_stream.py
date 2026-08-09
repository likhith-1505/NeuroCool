"""Typed stream events for POST /api/ai/chat/stream (see
app.neurocore.service.NeuroCoreService.answer_stream/chat_stream).

Every event the frontend can ever receive over that SSE connection is one
of these seven Pydantic models — never an arbitrary/unstructured string —
so the wire contract is machine-checkable end to end, the same way every
other response this backend produces is a Pydantic schema, not a bare
dict.

  - `thinking`      — high-level operational narration only (e.g.
                       "Checking forecast..."). NEVER the model's private
                       chain-of-thought — see ThinkingEvent's docstring.
  - `tool_started` / `tool_completed` — bracket one real tool call (see
                       app.neurocore.tools.executor).
  - `text_delta`    — one fragment of the assistant's actual answer, in
                       order; concatenating every text_delta in a turn
                       reconstructs the full response text.
  - `action_confirmation_required` — a write tool proposed a PendingAction
                       this turn (see app.neurocore.actions); nothing has
                       executed — POST /api/ai/actions/{id}/confirm is
                       still the only way anything happens.
  - `completed`     — the turn is done and persisted; carries the
                       conversation/message ids the frontend needs to keep
                       chatting.
  - `error`         — the turn failed; always the last event on the
                       stream when present. `message` is a short, safe,
                       operator-facing explanation — never a raw exception,
                       stack trace, or provider/DB internal (see
                       NeuroCoreService.chat_stream).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field


class ThinkingEvent(BaseModel):
    """High-level operational narration only — e.g. "Analyzing cluster
    state...", "Reading Rack A1 telemetry...". Never the model's actual
    (private) chain-of-thought/reasoning tokens; every ThinkingEvent this
    backend ever emits comes from a small, fixed, backend-authored set of
    phrases describing *what step is happening*, not step-by-step model
    reasoning (see app.neurocore.service for where these are produced).
    """

    type: Literal["thinking"] = "thinking"
    message: str


class ToolStartedEvent(BaseModel):
    type: Literal["tool_started"] = "tool_started"
    tool: str


class ToolCompletedEvent(BaseModel):
    type: Literal["tool_completed"] = "tool_completed"
    tool: str
    ok: bool


class TextDeltaEvent(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ActionConfirmationRequiredEvent(BaseModel):
    type: Literal["action_confirmation_required"] = "action_confirmation_required"
    action_id: uuid.UUID
    action_type: str
    summary: str
    expires_at: datetime


class CompletedEvent(BaseModel):
    type: Literal["completed"] = "completed"
    conversation_id: uuid.UUID
    message_id: uuid.UUID


class ErrorEvent(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


StreamEvent = Annotated[
    Union[
        ThinkingEvent,
        ToolStartedEvent,
        ToolCompletedEvent,
        TextDeltaEvent,
        ActionConfirmationRequiredEvent,
        CompletedEvent,
        ErrorEvent,
    ],
    Field(discriminator="type"),
]


def encode_sse(event: BaseModel) -> str:
    """Frames one stream event as standard SSE text: `event: <type>` plus
    a `data: <json>` line, terminated by a blank line — see
    https://developer.mozilla.org/docs/Web/API/Server-sent_events. Every
    payload is one of the models above, so `type` is always present.
    """
    payload = event.model_dump(mode="json")
    event_type = payload.get("type", "message")
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n"
