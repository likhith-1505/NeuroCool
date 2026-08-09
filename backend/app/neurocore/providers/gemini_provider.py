"""GeminiProvider — calls Google's Gemini API via the official `google-genai`
SDK (see requirements.txt). Unlike AnthropicProvider/OpenAIProvider (which
hand-roll their vendor's REST wire format directly over httpx to avoid an
extra dependency), Gemini's tool-calling/streaming response shapes are
non-trivial protobuf-derived objects that aren't worth reimplementing by
hand — the objective for this provider explicitly calls for the current
official SDK instead. The async client (`client.aio.models.*`) is used
throughout so a Gemini call never blocks the event loop, matching how the
other two adapters use httpx's async client.

Tool calling is always MANUAL: every tool this adapter offers Gemini is a
`types.FunctionDeclaration` built straight from our own ToolSpec (the exact
same JSON Schema Anthropic/OpenAI receive — Gemini's `parameters_json_schema`
field accepts it verbatim), and a returned `function_call` part is only ever
turned into a ToolCall for app.neurocore.tools.executor to validate and
dispatch — the SDK's *automatic* function-calling mode (passing raw Python
callables that it invokes itself) is never used. This is what keeps
NeuroCore's tool layer the sole source of truth (see the objective's safety
requirements): Gemini can request a tool call, never execute one.

Gemini correlates a function result by the function's *name*, not an opaque
call id the way Anthropic's tool_use_id/OpenAI's tool_call_id do. Since a
"tool" role LLMMessage only carries `tool_call_id` (see
app.neurocore.providers.base and NeuroCoreService.answer), `_to_gemini_
contents` rebuilds an id->name lookup from this same conversation's own
assistant tool_calls before translating — see its docstring.

Rate limits / free tier (per the objective): no automatic retry loop.
`_call_with_timeout_and_retry` retries at most once, and only for a
transient 5xx (`errors.ServerError`) — a 429 (`errors.ClientError`, rate
limit or quota exhaustion) is never retried, since doing so would only risk
burning more of a limited free-tier quota; it becomes a clear ProviderError
immediately instead. Every call is bounded by `timeout_seconds` via
`asyncio.wait_for`, independent of whatever timeout behavior the SDK itself
does or doesn't enforce.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TypeVar

from google import genai
from google.genai import errors, types

from app.neurocore.providers.base import LLMMessage, LLMResponse, LLMStreamChunk, ProviderError, ToolCall, ToolSpec

_MAX_ATTEMPTS = 2  # one initial attempt + at most one retry
_RETRY_BACKOFF_SECONDS = 0.75

T = TypeVar("T")


class GeminiProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "gemini"
    supports_streaming = True

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 30.0) -> None:
        self.model = model
        self._timeout_seconds = timeout_seconds
        # Constructed once and reused for every call, matching the SDK's
        # own documented usage pattern — it never gets the raw api_key
        # logged or included in any exception message raised below (see
        # ProviderError's docstring).
        self._client = genai.Client(api_key=api_key)

    async def generate(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        config = _build_config(system, max_tokens, tools)
        contents = _to_gemini_contents(messages)

        async def _issue() -> types.GenerateContentResponse:
            return await self._client.aio.models.generate_content(model=self.model, contents=contents, config=config)

        response = await self._call_with_timeout_and_retry(_issue)

        try:
            candidate = response.candidates[0]
            parts = candidate.content.parts if candidate.content else []
        except (AttributeError, IndexError, TypeError) as exc:
            raise ProviderError("Gemini API returned an unexpected response shape") from exc

        text, tool_calls = _parse_gemini_parts(parts)

        usage = getattr(response, "usage_metadata", None)
        input_tokens = getattr(usage, "prompt_token_count", None) if usage else None
        output_tokens = getattr(usage, "candidates_token_count", None) if usage else None

        return LLMResponse(
            text=text, model=self.model, tool_calls=tool_calls, input_tokens=input_tokens, output_tokens=output_tokens
        )

    async def generate_stream(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMStreamChunk]:
        config = _build_config(system, max_tokens, tools)
        contents = _to_gemini_contents(messages)

        async def _open() -> AsyncIterator[types.GenerateContentResponse]:
            return await self._client.aio.models.generate_content_stream(model=self.model, contents=contents, config=config)

        stream = await self._call_with_timeout_and_retry(_open)

        model_name = self.model
        input_tokens: int | None = None
        output_tokens: int | None = None

        try:
            async for chunk in stream:
                try:
                    candidate = chunk.candidates[0] if chunk.candidates else None
                    parts = candidate.content.parts if candidate is not None and candidate.content else []
                except (AttributeError, IndexError, TypeError):
                    continue  # a malformed/empty chunk is skipped, never crashes the stream

                text, tool_calls = _parse_gemini_parts(parts)
                if text:
                    yield LLMStreamChunk(text_delta=text)
                # Gemini's SDK exposes a function call's `args` already
                # parsed (not an incrementally-streamed JSON fragment the
                # way Anthropic/OpenAI's raw wire formats do) — see the
                # module docstring — so each one is already complete and
                # safe to emit as its own chunk immediately.
                for call in tool_calls:
                    yield LLMStreamChunk(tool_calls=[call])

                usage = getattr(chunk, "usage_metadata", None)
                if usage is not None:
                    input_tokens = getattr(usage, "prompt_token_count", input_tokens)
                    output_tokens = getattr(usage, "candidates_token_count", output_tokens)
        except errors.APIError as exc:
            raise _map_api_error(exc) from exc
        except TimeoutError as exc:
            raise ProviderError("Gemini API stream timed out") from exc
        except ProviderError:
            raise
        except Exception as exc:
            raise ProviderError(f"Gemini API stream failed: {type(exc).__name__}") from exc

        yield LLMStreamChunk(finished=True, model=model_name, input_tokens=input_tokens, output_tokens=output_tokens)

    async def _call_with_timeout_and_retry(self, factory: Callable[[], Awaitable[T]]) -> T:
        attempt = 0
        while True:
            attempt += 1
            try:
                return await asyncio.wait_for(factory(), timeout=self._timeout_seconds)
            except TimeoutError as exc:
                raise ProviderError("Gemini API request timed out") from exc
            except errors.ServerError as exc:
                if attempt >= _MAX_ATTEMPTS:
                    raise _map_api_error(exc) from exc
                # Bounded, backed-off retry — only for a transient server
                # error, never for a 4xx/rate-limit (see module docstring).
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS * attempt)
                continue
            except errors.APIError as exc:
                raise _map_api_error(exc) from exc
            except Exception as exc:
                raise ProviderError(f"Gemini API request failed: {type(exc).__name__}") from exc


def _build_config(system: str, max_tokens: int, tools: list[ToolSpec] | None) -> types.GenerateContentConfig:
    config_kwargs: dict[str, object] = {"system_instruction": system, "max_output_tokens": max_tokens}
    if tools:
        config_kwargs["tools"] = [
            types.Tool(
                function_declarations=[
                    types.FunctionDeclaration(
                        name=tool["name"], description=tool["description"], parameters_json_schema=tool["input_schema"]
                    )
                    for tool in tools
                ]
            )
        ]
    return types.GenerateContentConfig(**config_kwargs)


def _map_api_error(exc: errors.APIError) -> ProviderError:
    code = getattr(exc, "code", None)
    if code == 429:
        return ProviderError("Gemini API rate limit or quota exceeded")
    if code in (401, 403):
        return ProviderError(f"Gemini API request was not authorized (HTTP {code})")
    if code:
        return ProviderError(f"Gemini API returned HTTP {code}")
    return ProviderError(f"Gemini API error: {type(exc).__name__}")


def _to_gemini_contents(messages: list[LLMMessage]) -> list[types.Content]:
    """Gemini's FunctionResponse Part is correlated by function *name*, not
    an opaque call id the way Anthropic's tool_use_id / OpenAI's
    tool_call_id are — build a quick id->name lookup from this same
    conversation's own assistant tool_calls first, since a "tool" role
    LLMMessage only ever carries the id (see app.neurocore.service's
    conversation-building, shared across every provider).
    """
    name_by_call_id: dict[str, str] = {}
    for message in messages:
        for call in message.get("tool_calls") or []:
            name_by_call_id[call["id"]] = call["name"]

    converted: list[types.Content] = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            call_id = message.get("tool_call_id", "")
            name = name_by_call_id.get(call_id, call_id)
            converted.append(
                types.Content(
                    role="tool",
                    parts=[types.Part.from_function_response(name=name, response={"result": message.get("content", "")})],
                )
            )
        elif role == "assistant" and message.get("tool_calls"):
            parts: list[types.Part] = []
            if message.get("content"):
                parts.append(types.Part.from_text(text=message["content"]))
            for call in message["tool_calls"]:
                parts.append(types.Part.from_function_call(name=call["name"], args=call["arguments"]))
            converted.append(types.Content(role="model", parts=parts))
        else:
            # Gemini uses "user"/"model" turns; our own "assistant" role
            # (a plain text turn with no tool_calls) maps to "model".
            gemini_role = "user" if role == "user" else "model"
            converted.append(types.Content(role=gemini_role, parts=[types.Part.from_text(text=message.get("content", ""))]))
    return converted


def _parse_gemini_parts(parts: object) -> tuple[str, list[ToolCall]]:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for index, part in enumerate(parts or []):
        text = getattr(part, "text", None)
        if text:
            text_parts.append(text)
        function_call = getattr(part, "function_call", None)
        if function_call is not None:
            # Gemini's FunctionCall carries its own `id` for parallel-call
            # disambiguation, but it isn't always populated — fall back to
            # a synthesized, stable-within-this-turn id (see the module
            # docstring on how a "tool" role message is correlated back by
            # *name*, not this id, regardless of which one is used).
            call_id = getattr(function_call, "id", None) or f"{function_call.name}:{index}"
            tool_calls.append(
                ToolCall(id=call_id, name=function_call.name or "", arguments=dict(function_call.args or {}))
            )
    return "".join(text_parts), tool_calls
