"""The single place a provider's ToolCall becomes a real, validated,
permission-checked tool invocation — see app.neurocore.tools.base for the
Tool contract and app.neurocore.tools.registry for the fixed tool set.

Every one of the objective's tool-safety requirements is enforced right
here, in this order:
  1. Unknown tool name -> rejected (never dispatched to anything).
  2. Arguments validated against the tool's real Pydantic input_schema
     (extra="forbid" on every schema — unknown arguments rejected;
     invalid enum values rejected by Pydantic's own enum validation).
  3. Permission check (see app.neurocore.permissions).
  4. tool.run() — the only step that can still fail *expectedly*
     (ToolExecutionError, e.g. "no rack with that id") — every other
     failure above short-circuits before run() is ever called.

None of these failure modes raise up through this function — each becomes
a ToolOutcome the orchestration loop feeds back to the model as the tool's
result, so a malformed or rejected call is a recoverable conversational
turn, not a crash (see app.neurocore.service.NeuroCoreService.answer).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from pydantic import ValidationError

from app.neurocore.permissions import PermissionDenied, require
from app.neurocore.providers.base import ToolCall
from app.neurocore.tools.base import ToolContext, ToolExecutionError
from app.neurocore.tools.registry import get_tool
from app.neurocore.tools.write_tools import PendingActionProposal

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 4000  # bounds how much of a tool's result gets fed back into the prompt


@dataclass(frozen=True)
class ToolOutcome:
    tool_call_id: str
    tool_name: str
    ok: bool
    result_json: str
    creates_pending_action: bool = False
    pending_action_id: str | None = None
    confirmation_text: str | None = None


async def execute_tool_call(call: ToolCall, context: ToolContext) -> ToolOutcome:
    tool = get_tool(call["name"])
    if tool is None:
        logger.warning("NeuroCore tool call rejected: unknown tool %r", call["name"])
        return _error_outcome(call, f"Unknown tool '{call['name']}'.")

    try:
        args = tool.input_schema.model_validate(call.get("arguments") or {})
    except ValidationError as exc:
        logger.warning("NeuroCore tool call rejected: invalid arguments for %s", tool.name)
        return _error_outcome(call, f"Invalid arguments for '{tool.name}': {_summarize_validation_error(exc)}")

    try:
        require(context.principal, tool.required_permission)
    except PermissionDenied as exc:
        logger.warning("NeuroCore tool call denied: %s", tool.name)
        return _error_outcome(call, str(exc))

    try:
        result = await tool.run(args, context)
    except ToolExecutionError as exc:
        return _error_outcome(call, str(exc))

    result_json = result.model_dump_json()[:MAX_RESULT_CHARS]

    if tool.creates_pending_action:
        assert isinstance(result, PendingActionProposal)
        return ToolOutcome(
            tool_call_id=call["id"], tool_name=tool.name, ok=True, result_json=result_json,
            creates_pending_action=True, pending_action_id=str(result.pending_action_id),
            confirmation_text=result.summary,
        )

    return ToolOutcome(tool_call_id=call["id"], tool_name=tool.name, ok=True, result_json=result_json)


def _error_outcome(call: ToolCall, message: str) -> ToolOutcome:
    return ToolOutcome(
        tool_call_id=call.get("id", ""), tool_name=call.get("name", "unknown"),
        ok=False, result_json=json.dumps({"error": message}),
    )


def _summarize_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error["loc"]) or "(root)"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts) or str(exc)
