"""NeuroCoreService — the FastAPI-independent orchestrator for the
read-only AI reasoning layer.

Deliberately split in two:
  - `answer()` takes an already-built NeuroCoreContext and does the
    interesting work (grounding, prompt construction, provider call,
    honest fallbacks) — no database access, so it's fully unit-testable
    with a hand-built context and a MockLLMProvider (see tests/
    test_neurocore_service.py).
  - `chat()` is the thin, database-touching wrapper: loads/creates the
    Conversation, loads prior history, calls `load_context` +  `answer()`,
    and persists both turns. This mirrors DecisionService/ExecutionService/
    OptimizationService's own split — their DB-touching orchestration
    methods aren't unit tested either, only the pure logic underneath is;
    `chat()` is verified live instead (see the podman verification in this
    phase's commit).

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
from app.models.enums import ConversationRole
from app.neurocore.context import NeuroCoreContext, load_context
from app.neurocore.grounding import build_grounding
from app.neurocore.prompts import build_messages, build_system_prompt
from app.neurocore.providers.base import LLMMessage, LLMProvider, ProviderError
from app.utils.time import utcnow

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.simulation.engine import SimulationService

logger = logging.getLogger(__name__)

# How many prior turns (user+assistant messages, not conversations) get
# fed back to the provider as history — bounds latency/cost on a long
# conversation without losing recent context.
MAX_HISTORY_MESSAGES = 10

DEFAULT_MAX_RESPONSE_TOKENS = 800

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


@dataclass(frozen=True)
class AnswerResult:
    text: str
    sources: list[str] = field(default_factory=list)
    confidence: float = 0.0


@dataclass(frozen=True)
class ChatResult:
    conversation_id: uuid.UUID
    response: str
    confidence: float
    sources: list[str]


class NeuroCoreService:
    def __init__(self, provider: LLMProvider | None, *, max_response_tokens: int = DEFAULT_MAX_RESPONSE_TOKENS) -> None:
        self._provider = provider
        self._max_response_tokens = max_response_tokens

    @property
    def provider_available(self) -> bool:
        return self._provider is not None

    # --- pure-ish reasoning: context + grounding + provider, no DB ---------

    async def answer(
        self,
        context: NeuroCoreContext,
        *,
        message: str,
        rack_id: uuid.UUID | None,
        history: list[LLMMessage] | None = None,
    ) -> AnswerResult:
        if not message or not message.strip():
            return AnswerResult(text=EMPTY_MESSAGE_RESPONSE)

        grounding = build_grounding(context, message=message, rack_id=rack_id)

        if self._provider is None:
            return AnswerResult(text=UNAVAILABLE_RESPONSE)

        system_prompt = build_system_prompt(grounding, generated_at=context.generated_at.isoformat())
        messages = build_messages(message, history or [])

        started_at = time.monotonic()
        try:
            result = await self._provider.generate(
                system=system_prompt, messages=messages, max_tokens=self._max_response_tokens
            )
        except ProviderError as exc:
            latency_ms = (time.monotonic() - started_at) * 1000
            logger.warning(
                "NeuroCore provider call failed: provider=%s model=%s latency_ms=%.0f error_type=%s",
                self._provider.name, self._provider.model, latency_ms, type(exc).__name__,
            )
            return AnswerResult(text=PROVIDER_ERROR_RESPONSE)

        latency_ms = (time.monotonic() - started_at) * 1000
        logger.info(
            "NeuroCore provider call succeeded: provider=%s model=%s latency_ms=%.0f input_tokens=%s output_tokens=%s",
            self._provider.name, result.model, latency_ms, result.input_tokens, result.output_tokens,
        )
        return AnswerResult(text=result.text, sources=grounding.sources, confidence=grounding.confidence)

    # --- DB-touching orchestration -------------------------------------------

    async def chat(
        self,
        *,
        db: "AsyncSession",
        simulation: "SimulationService",
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
        result = await self.answer(context, message=message, rack_id=rack_id, history=history)

        await self._append_message(
            db, conversation.id, ConversationRole.ASSISTANT, result.text,
            sources=result.sources, confidence=result.confidence,
        )

        return ChatResult(
            conversation_id=conversation.id, response=result.text,
            confidence=result.confidence, sources=result.sources,
        )

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
