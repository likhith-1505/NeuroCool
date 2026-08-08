"""The Tool contract every explicit, typed NeuroCore tool implements.

This is the *only* surface the LLM is ever given to affect anything —
"arbitrary code execution" or "arbitrary API calls" have no path to exist
here by construction: a tool is a fixed, named, strictly-typed Python
method the backend authored, never a string of code or a URL the model
supplies. See app.neurocore.tools.registry for the fixed set of tools and
app.neurocore.tools.executor for where every call is validated, permission
-checked, and dispatched.

Read tools execute immediately and return real data (never invented).
Write tools (see app.neurocore.tools.write_tools) never mutate anything
themselves — they only ever create a PendingAction (see
app.neurocore.actions) and return its confirmation summary; the actual
mutation happens later, only via PendingActionService.confirm, which goes
through the *existing* SimulationService/ExecutionService.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from pydantic import BaseModel

from app.neurocore.permissions import Permission, Principal

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.neurocore.actions import PendingActionService
    from app.neurocore.ports import SimulationPort


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool's run() is allowed to touch. Bundled into one
    object for the same reason DecisionContext/ForecastContext/
    OptimizationContext are — the Tool Protocol's signature stays stable
    even as what a tool needs grows.

    `pending_actions` is only used by write tools (see
    app.neurocore.tools.write_tools) — read tools never touch it.
    PendingActionService is stateless (every real fact it needs lives in
    the database), so constructing one per request is cheap.
    """

    db: "AsyncSession"
    simulation: "SimulationPort"
    principal: Principal
    conversation_id: uuid.UUID
    pending_actions: "PendingActionService"


class ToolExecutionError(Exception):
    """Raised by a tool's run() for an expected, explainable failure (rack
    not found, no plan/decision to read, etc.) — the executor turns this
    into a tool-result error message fed back to the model, never a raw
    500. Distinct from a Pydantic ValidationError (malformed arguments,
    rejected before run() is ever called) and from PermissionDenied
    (checked before run() is ever called).
    """


class Tool(Protocol):
    """Contract every concrete tool satisfies. `input_schema`/
    `output_schema` are Pydantic model *classes* (not instances) — the
    executor validates raw arguments against `input_schema` before run()
    is ever called, and every tool's return value is an instance of
    `output_schema`, so every tool's shape is machine-checkable end to end.

    `creates_pending_action` distinguishes a write tool from a read tool:
    True means this tool's output is a PendingActionProposal and the
    orchestration loop must stop and surface it for confirmation, rather
    than feeding the result back to the model for another turn.
    """

    name: str
    description: str
    required_permission: Permission
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    creates_pending_action: bool

    async def run(self, args: BaseModel, context: ToolContext) -> BaseModel:
        """Raises ToolExecutionError for an expected, explainable failure."""
        ...
