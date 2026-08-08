"""Write tools — the only tools the LLM can call that lead to a real
mutation, and even they never mutate anything themselves. Calling one
only ever creates a PendingAction (see app.neurocore.actions) and returns
its confirmation summary; the actual mutation happens later, only inside
PendingActionService.confirm, only via the *existing*
SimulationService.execute_decision/.replay_scenario.

This is what makes "the LLM must never silently execute a destructive or
operational action" true even though these tools are technically callable
by the model without any human already having agreed to anything — the
tool's own effect is bounded to "propose, and wait".
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PendingActionStatus, PendingActionType
from app.neurocore.permissions import Permission
from app.neurocore.tools.base import ToolContext, ToolExecutionError


class ExecuteDecisionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision_id: uuid.UUID = Field(..., description="The decision to execute, as seen via read_decision.")


class ReplaySimulationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PendingActionProposal(BaseModel):
    """The result of calling a write tool — never the result of the
    mutation itself, which hasn't happened yet.
    """

    pending_action_id: uuid.UUID
    action_type: PendingActionType
    target: str
    status: PendingActionStatus
    summary: str


class ExecuteDecisionTool:
    name = "execute_decision"
    description = (
        "Propose executing a decision's recommended action (e.g. a workload migration or cooling adjustment). "
        "This does NOT execute anything by itself — it creates a pending action that a human operator must "
        "separately confirm via POST /api/ai/actions/{id}/confirm before anything actually happens."
    )
    required_permission = Permission.OPERATE
    input_schema = ExecuteDecisionInput
    output_schema = PendingActionProposal
    creates_pending_action = True

    async def run(self, args: ExecuteDecisionInput, context: ToolContext) -> PendingActionProposal:
        try:
            action = await context.pending_actions.create_for_decision(
                context.db,
                conversation_id=context.conversation_id,
                decision_id=args.decision_id,
                simulation=context.simulation,
            )
        except LookupError as exc:
            raise ToolExecutionError(str(exc)) from exc
        except ValueError as exc:
            raise ToolExecutionError(str(exc)) from exc

        return PendingActionProposal(
            pending_action_id=action.id, action_type=action.action_type,
            target=action.target, status=action.status, summary=action.summary,
        )


class ReplaySimulationTool:
    name = "replay_simulation"
    description = (
        "Propose replaying the most recently completed scenario from the beginning. This does NOT execute "
        "anything by itself — it creates a pending action a human operator must separately confirm."
    )
    required_permission = Permission.OPERATE
    input_schema = ReplaySimulationInput
    output_schema = PendingActionProposal
    creates_pending_action = True

    async def run(self, args: ReplaySimulationInput, context: ToolContext) -> PendingActionProposal:
        action = await context.pending_actions.create_for_replay(
            context.db, conversation_id=context.conversation_id, simulation=context.simulation,
        )
        return PendingActionProposal(
            pending_action_id=action.id, action_type=action.action_type,
            target=action.target, status=action.status, summary=action.summary,
        )
