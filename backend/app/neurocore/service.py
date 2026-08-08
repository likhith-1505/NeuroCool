"""NeuroCoreService — the FastAPI-independent orchestrator for NeuroCore's
reasoning *and* action-orchestration layers.

Deliberately split in two:
  - `answer()` takes an already-built NeuroCoreContext and does the
    interesting work (grounding, prompt construction, provider call incl.
    the tool-use loop, honest fallbacks). Tool use is optional: when
    `db`/`simulation` aren't supplied, it behaves exactly like the
    read-only reasoning phase — a single generate() call, no tools — which
    is what keeps every one of that phase's existing tests passing
    unchanged. When they are supplied (as `chat()` always does), tool
    calls the model makes are validated, permission-checked, and
    dispatched via app.neurocore.tools.executor in a bounded loop.
  - `chat()` is the thin, database-touching wrapper: loads/creates the
    Conversation, loads prior history, calls `load_context` + `answer()`,
    and persists both turns. This mirrors DecisionService/ExecutionService/
    OptimizationService's own split — their DB-touching orchestration
    methods aren't unit tested either, only the pure logic underneath is;
    `chat()` is verified live instead (see the podman verification in this
    phase's commit).

A *write* tool call (see app.neurocore.tools.write_tools) never runs to
completion inside this loop — it only ever creates a PendingAction (via
the PendingActionService this class holds) and `answer()` immediately
returns its confirmation summary as the response text, short-circuiting
any further model turns. The actual mutation only ever happens later,
inside PendingActionService.confirm, triggered by a separate, explicit
POST /api/ai/actions/{id}/confirm call — never from inside a chat turn.

Independent of FastAPI on purpose (per the objective) — app.api.ai is a
thin route wrapper around this class, the same relationship every other
engine in this backend has with its REST routes.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models.conversation import Conversation, ConversationMessage
from app.models.enums import ConversationRole, PendingActionStatus
from app.neurocore.actions import PendingActionService
from app.neurocore.context import NeuroCoreContext, load_context
from app.neurocore.grounding import build_grounding
from app.neurocore.permissions import DEFAULT_PRINCIPAL, Principal
from app.neurocore.prompts import build_messages, build_system_prompt
from app.neurocore.providers.base import LLMMessage, LLMProvider, ProviderError
from app.neurocore.tools.base import ToolContext
from app.neurocore.tools.executor import execute_tool_call
from app.neurocore.tools.registry import tool_specs
from app.utils.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.pending_action import PendingAction
    from app.neurocore.ports import SimulationPort

logger = logging.getLogger(__name__)

# How many prior turns (user+assistant messages, not conversations) get
# fed back to the provider as history — bounds latency/cost on a long
# conversation without losing recent context.
MAX_HISTORY_MESSAGES = 10

DEFAULT_MAX_RESPONSE_TOKENS = 800

# A tool-use turn (model calls a read tool, gets a real result, reasons
# over it) followed by a final text answer is 2 provider round trips;
# this allows a couple of read tools in sequence before giving up, never
# an unbounded loop.
MAX_TOOL_ITERATIONS = 4

UNAVAILABLE_RESPONSE = (
    "AI reasoning is currently unavailable: no LLM provider is configured (or the configured "
    "provider has no API key set). The deterministic backend — simulation, forecasting, "
    "optimization, decisions, and execution — is unaffected and continues to operate normally. "
    "Configure AI_PROVIDER and the matching API key to enable this endpoint."
)
PROVIDER_ERROR_RESPONSE = (
    "AI reasoning is temporarily unavailable due to a provider error. The deterministic backend "
    "is unaffected. Please try again shortly."
)
EMPTY_MESSAGE_RESPONSE = "Please ask a question about the cluster, a rack, a forecast, a plan, a decision, or an execution."
TOOL_LOOP_EXHAUSTED_RESPONSE = (
    "I wasn't able to finish reasoning about that within the allotted number of steps. "
    "Please try a more specific question."
)


@dataclass(frozen=True)
class AnswerResult:
    text: str
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.0
    # Set only when a write tool call created a PendingAction this turn —
    # see module docstring. The chat response surfaces the full row (see
    # ChatResult.pending_action) so the frontend never has to guess.
    pending_action_id: uuid.UUID | None = None


@dataclass(frozen=True)
class ChatResult:
    conversation_id: uuid.UUID
    response: str
    confidence: float
    sources: list[str]
    pending_action: "PendingAction | None" = None


class NeuroCoreService:
    def __init__(
        self,
        provider: LLMProvider | None,
        *,
        max_response_tokens: int = DEFAULT_MAX_RESPONSE_TOKENS,
        pending_actions: PendingActionService | None = None,
    ) -> None:
        self._provider = provider
        self._max_response_tokens = max_response_tokens
        # Stateless (see PendingActionService's own docstring) — safe to
        # default-construct; only overridden in tests that want to spy on it.
        self._pending_actions = pending_actions or PendingActionService()

    @property
    def provider_available(self) -> bool:
        return self._provider is not None

    # --- reasoning: context + grounding + provider (+ optional tools) ------

    async def answer(
        self,
        context: NeuroCoreContext,
        *,
        message: str,
        rack_id: uuid.UUID | None,
        history: list[LLMMessage] | None = None,
        db: "AsyncSession | None" = None,
        simulation: "SimulationPort | None" = None,
        principal: Principal | None = None,
        conversation_id: uuid.UUID | None = None,
    ) -> AnswerResult:
        if not message or not message.strip():
            return AnswerResult(text=EMPTY_MESSAGE_RESPONSE)

        grounding = build_grounding(context, message=message, rack_id=rack_id)

        if self._provider is None:
            return AnswerResult(text=UNAVAILABLE_RESPONSE)

        system_prompt = build_system_prompt(grounding, generated_at=context.generated_at.isoformat())
        conversation: list[LLMMessage] = build_messages(message, history or [])

        # Tool use requires a database session, live simulation access, and
        # a conversation to attach any PendingAction to. All three are
        # optional purely so `answer()` keeps working exactly as it did in
        # the read-only reasoning phase when called without them (see
        # every existing test in tests/test_neurocore_service.py).
        tools_enabled = db is not None and simulation is not None and conversation_id is not None
        tool_context: ToolContext | None = None
        if tools_enabled:
            tool_context = ToolContext(
                db=db, simulation=simulation, principal=principal or DEFAULT_PRINCIPAL,
                conversation_id=conversation_id, pending_actions=self._pending_actions,
            )

        specs = tool_specs() if tools_enabled else None
        iterations = MAX_TOOL_ITERATIONS if tools_enabled else 1

        for _ in range(iterations):
            result = await self._generate(system_prompt, conversation, specs)
            if result is None:
                return AnswerResult(text=PROVIDER_ERROR_RESPONSE)

            if not result.tool_calls:
                return AnswerResult(text=result.text, sources=grounding.sources, confidence=grounding.confidence)

            if tool_context is None:
                # Defensive: the model called a tool despite none being
                # offered. Never silently drop it — surface what text we
                # do have rather than pretend the turn succeeded cleanly.
                return AnswerResult(
                    text=result.text or PROVIDER_ERROR_RESPONSE, sources=grounding.sources, confidence=grounding.confidence
                )

            conversation.append(LLMMessage(role="assistant", content=result.text or "", tool_calls=result.tool_calls))

            for call in result.tool_calls:
                outcome = await execute_tool_call(call, tool_context)
                if outcome.creates_pending_action:
                    pending_id = uuid.UUID(outcome.pending_action_id) if outcome.pending_action_id else None
                    return AnswerResult(
                        text=outcome.confirmation_text or "", sources=grounding.sources,
                        confidence=grounding.confidence, pending_action_id=pending_id,
                    )
                conversation.append(LLMMessage(role="tool", tool_call_id=outcome.tool_call_id, content=outcome.result_json))

        return AnswerResult(text=TOOL_LOOP_EXHAUSTED_RESPONSE, sources=grounding.sources, confidence=grounding.confidence)

    async def _generate(self, system_prompt: str, messages: list[LLMMessage], tools):
        assert self._provider is not None
        started_at = time.monotonic()
        try:
            result = await self._provider.generate(
                system=system_prompt, messages=messages, max_tokens=self._max_response_tokens, tools=tools
            )
        except ProviderError as exc:
            latency_ms = (time.monotonic() - started_at) * 1000
            logger.warning(
                "NeuroCore provider call failed: provider=%s model=%s latency_ms=%.0f error_type=%s",
                self._provider.name, self._provider.model, latency_ms, type(exc).__name__,
            )
            return None

        latency_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "NeuroCore provider call succeeded: provider=%s model=%s latency_ms=%.0f tool_calls=%d "
            "input_tokens=%s output_tokens=%s",
            self._provider.name, result.model, latency_ms, len(result.tool_calls),
            result.input_tokens, result.output_tokens,
        )
        return result

    # --- DB-touching orchestration -------------------------------------------

    async def chat(
        self,
        *,
        db: "AsyncSession",
        simulation: "SimulationPort",
        message: str,
        rack_id: uuid.UUID | None,
        conversation_id: uuid.UUID | None,
    ) -> ChatResult:
        """Raises LookupError (-> 404 at the API layer) if `conversation_id`
        is given but doesn't exist.
        """
        conversation = await self._get_or_create_conversation(db, conversation_id)
        history = await self._load_history(db, conversation.id)

        await self._append_message(db, conversation.id, ConversationRole.USER, message, sources=[], confidence=None)

        context = await load_context(db, simulation)
        result = await self.answer(
            context, message=message, rack_id=rack_id, history=history,
            db=db, simulation=simulation, principal=DEFAULT_PRINCIPAL, conversation_id=conversation.id,
        )

        await self._append_message(
            db, conversation.id, ConversationRole.ASSISTANT, result.text,
            sources=result.sources, confidence=result.confidence,
        )

        pending_action = None
        if result.pending_action_id is not None:
            pending_action = await self._pending_actions.get(db, result.pending_action_id)

        return ChatResult(
            conversation_id=conversation.id, response=result.text,
            confidence=result.confidence, sources=result.sources, pending_action=pending_action,
        )

    # --- pending-action pass-throughs (see app.neurocore.actions) ----------

    async def confirm_action(self, *, db: "AsyncSession", simulation: "SimulationPort", action_id: uuid.UUID) -> "PendingAction":
        return await self._pending_actions.confirm(db, action_id=action_id, simulation=simulation)

    async def cancel_action(self, *, db: "AsyncSession", action_id: uuid.UUID) -> "PendingAction":
        return await self._pending_actions.cancel(db, action_id=action_id)

    async def get_action(self, *, db: "AsyncSession", action_id: uuid.UUID) -> "PendingAction | None":
        return await self._pending_actions.get(db, action_id)

    async def list_actions(
        self,
        *,
        db: "AsyncSession",
        conversation_id: uuid.UUID | None = None,
        status: PendingActionStatus | None = None,
        limit: int = 50,
    ) -> list["PendingAction"]:
        return await self._pending_actions.list(db, conversation_id=conversation_id, status=status, limit=limit)

    # --- internals -----------------------------------------------------------

    @staticmethod
    async def _get_or_create_conversation(db: "AsyncSession", conversation_id: uuid.UUID | None) -> Conversation:
        if conversation_id is not None:
            row = await db.get(Conversation, conversation_id)
            if row is None:
                raise LookupError(f"Conversation '{conversation_id}' not found.")
            return row

        row = Conversation()
        db.add(row)
        await db.commit()
        await db.refresh(row)
        return row

    @staticmethod
    async def _load_history(db: "AsyncSession", conversation_id: uuid.UUID) -> list[LLMMessage]:
        result = await db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at.desc())
            .limit(MAX_HISTORY_MESSAGES)
        )
        rows = list(reversed(result.scalars().all()))
        return [LLMMessage(role=row.role.value, content=row.content) for row in rows]

    @staticmethod
    async def _append_message(
        db: "AsyncSession",
        conversation_id: uuid.UUID,
        role: ConversationRole,
        content: str,
        *,
        sources: list[str],
        confidence: float | None,
    ) -> None:
        row = ConversationMessage(
            conversation_id=conversation_id, role=role, content=content, sources=sources, confidence=confidence
        )
        db.add(row)
        # Keep Conversation.updated_at current without a second round trip.
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.updated_at = utcnow()
        await db.commit()
