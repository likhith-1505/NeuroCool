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

Streaming: `answer_stream()`/`chat_stream()` are the streaming counterparts
to `answer()`/`chat()` (see POST /api/ai/chat/stream in app.api.ai) — same
split, same tool loop, same PendingAction confirmation boundary, just
surfaced as an async generator of typed StreamEvents (see
app.schemas.ai_stream) instead of one returned result. They are additive:
`answer()`/`chat()` are untouched, so the existing non-streaming REST
endpoint keeps working exactly as before. `answer_stream()` never persists
anything itself (same division of labor as `answer()`); `chat_stream()`
accumulates the streamed text/tool-call/failure information as it forwards
each event, and persists exactly one assistant ConversationMessage when
the turn ends — win, lose, or client-disconnect — never one row per token.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
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
from app.neurocore.providers.base import LLMMessage, LLMProvider, LLMStreamChunk, ProviderError, ToolCall
from app.neurocore.tools.base import ToolContext
from app.neurocore.tools.display import tool_display_name
from app.neurocore.tools.executor import execute_tool_call
from app.neurocore.tools.registry import tool_specs
from app.schemas.ai_stream import (
    ActionConfirmationRequiredEvent,
    CompletedEvent,
    ErrorEvent,
    StreamEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolCompletedEvent,
    ToolStartedEvent,
)
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

# --- streaming defaults (overridable via app.config.settings; see
# NeuroCoreService.__init__ and app.main's construction call) -------------
DEFAULT_LLM_TIMEOUT_SECONDS = 30.0  # per-chunk "is the provider still alive" stall guard
DEFAULT_TOOL_TIMEOUT_SECONDS = 15.0  # one tool call, during a streamed turn
DEFAULT_STREAM_TIMEOUT_SECONDS = 60.0  # the whole streamed turn, end to end

# High-level operational narration shown just before a given tool actually
# runs (see the objective's example flow: "Reading Rack A1 telemetry..." ->
# "Checking forecast..." -> ...). Deliberately a small, fixed, backend-
# authored set of phrases, not anything derived from the model's own
# output — see ThinkingEvent's docstring on why hidden model reasoning is
# never streamed. A tool with no entry here still gets a generic phrase
# (see _thinking_message_for_tool).
_TOOL_THINKING_MESSAGES: dict[str, str] = {
    "read_cluster_state": "Analyzing cluster state...",
    "read_rack": "Reading rack telemetry...",
    "read_forecast": "Checking forecast...",
    "read_optimization_plan": "Evaluating optimization plan...",
    "read_decision": "Reviewing decision...",
    "read_recent_events": "Checking recent events...",
    "read_execution_history": "Reviewing execution history...",
    "execute_decision": "Preparing to execute the recommended action...",
    "replay_simulation": "Preparing scenario replay...",
}


def _thinking_message_for_tool(tool_name: str) -> str:
    return _TOOL_THINKING_MESSAGES.get(tool_name, f"Running {tool_display_name(tool_name)}...")


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
        llm_timeout_seconds: float = DEFAULT_LLM_TIMEOUT_SECONDS,
        tool_timeout_seconds: float = DEFAULT_TOOL_TIMEOUT_SECONDS,
        stream_timeout_seconds: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
    ) -> None:
        self._provider = provider
        self._max_response_tokens = max_response_tokens
        # Stateless (see PendingActionService's own docstring) — safe to
        # default-construct; only overridden in tests that want to spy on it.
        self._pending_actions = pending_actions or PendingActionService()
        # Streaming-only timeouts (see answer_stream/chat_stream below) —
        # configurable, never hardcoded at the call site (see app.config
        # .settings.AI_TOOL_TIMEOUT_SECONDS/AI_STREAM_TIMEOUT_SECONDS and
        # app.main's construction of this service).
        self._llm_timeout_seconds = llm_timeout_seconds
        self._tool_timeout_seconds = tool_timeout_seconds
        self._stream_timeout_seconds = stream_timeout_seconds

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

    # --- streaming reasoning: same tool loop as answer(), as StreamEvents ----

    async def answer_stream(
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
    ) -> AsyncIterator[StreamEvent]:
        """The streaming counterpart to answer() — same grounding, same
        bounded tool-use loop, same write-tool-never-executes-itself
        boundary, yielded incrementally instead of returned as one result.

        Graceful, expected outcomes (no message, no provider configured,
        the tool loop running out of turns) are streamed as ordinary
        TextDeltaEvents — exactly the same fallback text answer() returns
        for them — since they are the service successfully declining to
        answer, not a failure. Only genuine failures (a provider error, a
        provider/tool timeout, an unexpected exception) are streamed as an
        ErrorEvent, per the objective's error-streaming requirements; an
        ErrorEvent is always the last event this method ever yields.
        """
        if not message or not message.strip():
            yield TextDeltaEvent(text=EMPTY_MESSAGE_RESPONSE)
            return

        grounding = build_grounding(context, message=message, rack_id=rack_id)

        if self._provider is None:
            yield TextDeltaEvent(text=UNAVAILABLE_RESPONSE)
            return

        system_prompt = build_system_prompt(grounding, generated_at=context.generated_at.isoformat())
        conversation: list[LLMMessage] = build_messages(message, history or [])

        tools_enabled = db is not None and simulation is not None and conversation_id is not None
        tool_context: ToolContext | None = None
        if tools_enabled:
            tool_context = ToolContext(
                db=db, simulation=simulation, principal=principal or DEFAULT_PRINCIPAL,
                conversation_id=conversation_id, pending_actions=self._pending_actions,
            )

        specs = tool_specs() if tools_enabled else None
        iterations = MAX_TOOL_ITERATIONS if tools_enabled else 1

        # A monotonic-clock deadline, not a single asyncio.wait_for around
        # this whole generator — the "overall stream timeout" has to keep
        # working across an arbitrary number of provider calls and tool
        # calls, each with their own timeout, not just one blocking await.
        deadline = time.monotonic() + self._stream_timeout_seconds

        yield ThinkingEvent(message="Analyzing cluster state...")

        for iteration in range(iterations):
            if time.monotonic() > deadline:
                yield ErrorEvent(code="stream_timeout", message="The response took too long to generate.")
                return

            if iteration > 0:
                yield ThinkingEvent(message="Generating explanation...")

            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []
            try:
                async for chunk in self._stream_one_turn(system_prompt, conversation, specs, deadline):
                    if chunk.text_delta:
                        text_parts.append(chunk.text_delta)
                        yield TextDeltaEvent(text=chunk.text_delta)
                    if chunk.tool_calls:
                        tool_calls.extend(chunk.tool_calls)
            except ProviderError:
                yield ErrorEvent(code="provider_error", message=PROVIDER_ERROR_RESPONSE)
                return
            except TimeoutError:
                yield ErrorEvent(code="provider_timeout", message="The AI provider did not respond in time.")
                return

            if not tool_calls:
                return  # a plain text answer — every fragment already streamed above

            if tool_context is None:
                # Defensive, mirrors answer(): the model called a tool
                # despite none being offered. Should be unreachable (no
                # tool specs were ever sent), but never silently drop it.
                yield ErrorEvent(code="unexpected_tool_call", message="The AI attempted to use a tool that isn't available.")
                return

            conversation.append(LLMMessage(role="assistant", content="".join(text_parts), tool_calls=tool_calls))

            for call in tool_calls:
                if time.monotonic() > deadline:
                    yield ErrorEvent(code="stream_timeout", message="The response took too long to generate.")
                    return

                display = tool_display_name(call["name"])
                yield ThinkingEvent(message=_thinking_message_for_tool(call["name"]))
                yield ToolStartedEvent(tool=display)

                try:
                    outcome = await asyncio.wait_for(execute_tool_call(call, tool_context), timeout=self._tool_timeout_seconds)
                except TimeoutError:
                    yield ToolCompletedEvent(tool=display, ok=False)
                    yield ErrorEvent(code="tool_timeout", message=f"'{display}' timed out and was aborted.")
                    return

                yield ToolCompletedEvent(tool=display, ok=outcome.ok)

                if outcome.creates_pending_action:
                    pending_id = uuid.UUID(outcome.pending_action_id) if outcome.pending_action_id else None
                    action = await tool_context.pending_actions.get(tool_context.db, pending_id) if pending_id else None
                    if action is not None:
                        yield ActionConfirmationRequiredEvent(
                            action_id=action.id, action_type=action.action_type.value,
                            summary=action.summary, expires_at=action.expires_at,
                        )
                    else:  # pragma: no cover - defensive, the tool call above just created this row
                        yield TextDeltaEvent(text=outcome.confirmation_text or "")
                    return

                conversation.append(LLMMessage(role="tool", tool_call_id=outcome.tool_call_id, content=outcome.result_json))

        yield TextDeltaEvent(text=TOOL_LOOP_EXHAUSTED_RESPONSE)

    async def _stream_one_turn(
        self, system_prompt: str, messages: list[LLMMessage], tools, deadline: float
    ) -> AsyncIterator[LLMStreamChunk]:
        """One provider round trip, streamed. Falls back to a single
        ordinary generate() call — emitted as one whole-text chunk — when
        the provider doesn't implement real streaming (see
        LLMProvider.supports_streaming); the rest of answer_stream never
        needs to know which happened. Raises ProviderError (a genuine
        provider failure) or TimeoutError (no data within
        `_llm_timeout_seconds` of asking) — both handled by the caller.
        """
        assert self._provider is not None
        remaining = max(deadline - time.monotonic(), 0.01)
        per_call_timeout = min(remaining, self._llm_timeout_seconds)

        if not getattr(self._provider, "supports_streaming", False):
            result = await asyncio.wait_for(
                self._provider.generate(system=system_prompt, messages=messages, max_tokens=self._max_response_tokens, tools=tools),
                timeout=per_call_timeout,
            )
            yield LLMStreamChunk(
                text_delta=result.text, tool_calls=result.tool_calls, finished=True,
                model=result.model, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            )
            return

        stream = self._provider.generate_stream(
            system=system_prompt, messages=messages, max_tokens=self._max_response_tokens, tools=tools
        )
        try:
            while True:
                # Each individual `anext` is bounded by the LLM request
                # timeout (a stall guard — "no data for N seconds"), not
                # the whole per-turn call, so a slow-but-steady stream
                # isn't punished for taking longer than one request's
                # worth of time overall (the outer deadline in
                # answer_stream still bounds the whole turn).
                chunk = await asyncio.wait_for(stream.__anext__(), timeout=self._llm_timeout_seconds)
                yield chunk
        except StopAsyncIteration:
            return
        finally:
            await stream.aclose()

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

    async def chat_stream(
        self,
        *,
        db: "AsyncSession",
        simulation: "SimulationPort",
        message: str,
        rack_id: uuid.UUID | None,
        conversation_id: uuid.UUID | None,
    ) -> AsyncIterator[StreamEvent]:
        """The streaming counterpart to chat() — same conversation
        get-or-create, same history load, same context built exactly once
        per request (never rebuilt per streamed token), same single
        assistant message persisted at the end. Raises LookupError, same as
        chat(), if `conversation_id` is given but doesn't exist — checked
        eagerly, before any event is yielded, so app.api.ai can translate
        it into an `error` SSE event before anything else has streamed
        (see the objective: every stream-level error is an `error` event,
        never an HTTP status, since the response has already committed to
        200 by the time a later failure could happen).

        Persists exactly one assistant ConversationMessage no matter how
        the turn ends — a clean answer, a graceful decline (e.g. "please
        ask a question"), a genuine provider/tool failure, or an early
        client disconnect (see the `finally` below, which is exactly as
        reachable on GeneratorExit as on normal/exception completion).
        Never yields a `completed` event after an `error` event or after a
        disconnect — see the trailing `if not failed` below.
        """
        request_id = uuid.uuid4().hex
        conversation = await self._get_or_create_conversation(db, conversation_id)
        history = await self._load_history(db, conversation.id)

        await self._append_message(db, conversation.id, ConversationRole.USER, message, sources=[], confidence=None)

        # Built exactly once for this request — every tool call inside the
        # loop below still does its own narrow, targeted DB read (see e.g.
        # ReadRecentEventsTool), which is unrelated to (and doesn't need)
        # rebuilding this whole snapshot.
        context = await load_context(db, simulation)

        started_at = time.monotonic()
        first_token_at: float | None = None
        text_parts: list[str] = []
        tool_call_count = 0
        failed = False
        message_row: ConversationMessage | None = None

        inner = self.answer_stream(
            context, message=message, rack_id=rack_id, history=history,
            db=db, simulation=simulation, principal=DEFAULT_PRINCIPAL, conversation_id=conversation.id,
        )
        try:
            async for event in inner:
                if isinstance(event, TextDeltaEvent):
                    if first_token_at is None:
                        first_token_at = time.monotonic()
                    text_parts.append(event.text)
                elif isinstance(event, ToolStartedEvent):
                    tool_call_count += 1
                elif isinstance(event, ErrorEvent):
                    failed = True
                yield event
        except Exception:
            # A bug somewhere in the tool loop/provider adapter that
            # escaped as a raw exception rather than a clean ErrorEvent —
            # still must never crash the stream or leak internals (see
            # module docstring's error-streaming requirement).
            logger.exception("NeuroCore chat_stream: unhandled error (request_id=%s)", request_id)
            failed = True
            yield ErrorEvent(code="internal_error", message="An unexpected error occurred while generating a response.")
        finally:
            # Reached on normal completion, on the except above, AND on an
            # early client disconnect (Starlette closes the SSE response's
            # generator chain via .aclose(), which raises GeneratorExit
            # here) — this is what makes "always persist exactly one
            # assistant message, never leave a turn unrecorded" true in
            # every case, not just the happy path.
            await inner.aclose()
            final_text = "".join(text_parts).strip()
            if not final_text:
                final_text = PROVIDER_ERROR_RESPONSE if failed else EMPTY_MESSAGE_RESPONSE
            message_row = await self._append_message(
                db, conversation.id, ConversationRole.ASSISTANT, final_text, sources=[], confidence=None,
            )
            total_latency_ms = (time.monotonic() - started_at) * 1000
            ttft_ms = (first_token_at - started_at) * 1000 if first_token_at is not None else None
            provider_name = self._provider.name if self._provider is not None else None
            provider_model = self._provider.model if self._provider is not None else None
            logger.info(
                "NeuroCore stream finished: request_id=%s conversation_id=%s provider=%s model=%s "
                "ttft_ms=%s total_latency_ms=%.0f tool_calls=%d success=%s",
                request_id, conversation.id, provider_name, provider_model,
                f"{ttft_ms:.0f}" if ttft_ms is not None else "n/a", total_latency_ms, tool_call_count, not failed,
            )

        if not failed and message_row is not None:
            yield CompletedEvent(conversation_id=conversation.id, message_id=message_row.id)

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
    ) -> ConversationMessage:
        row = ConversationMessage(
            conversation_id=conversation_id, role=role, content=content, sources=sources, confidence=confidence
        )
        db.add(row)
        # Keep Conversation.updated_at current without a second round trip.
        conversation = await db.get(Conversation, conversation_id)
        if conversation is not None:
            conversation.updated_at = utcnow()
        await db.commit()
        await db.refresh(row)
        return row
