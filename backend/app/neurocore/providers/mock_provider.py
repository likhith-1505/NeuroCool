"""MockLLMProvider — a deterministic, no-network provider.

Used by the test suite (per the objective: "Do not require a real LLM API
key for the test suite. Use a deterministic mock provider.") and available
as a real, configurable option (AI_PROVIDER=mock) for local development
without any API key. Never makes a network call, so its output is a
reproducible pure function of its input.

Two modes:
  - Default (no `responses` given): echoes a snippet of the grounded
    system context back, exactly as the read-only reasoning phase's tests
    already rely on — unchanged behavior, never calls a tool.
  - Scripted (`responses=[...]`): returns each LLMResponse in order, one
    per call — how tests deterministically simulate a multi-turn tool-use
    loop (e.g. first call returns a ToolCall, second call — after the
    tool's real result is fed back — returns the final text answer)
    without needing a real model to actually decide anything.

`generate_stream()` reuses the exact same "which response is next" logic
as `generate()` (see `_next_response`) so a test can script tool-use turns
identically whether it drives the non-streaming or the streaming path —
it only differs in *how* that one LLMResponse is delivered: chopped into
several small text_delta chunks (so tests can assert on "multiple chunks
arrived", per the streaming objective's testing checklist) instead of
returned whole. A tool-calling turn still streams as a single chunk
carrying the complete tool call(s) — see LLMStreamChunk's docstring for
why partial tool-call JSON is never something a provider adapter exposes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.neurocore.providers.base import LLMMessage, LLMResponse, LLMStreamChunk, ToolSpec

DEFAULT_MOCK_MODEL = "mock-echo-1"


class MockLLMProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "mock"
    supports_streaming = True

    def __init__(
        self, model: str = DEFAULT_MOCK_MODEL, responses: list[LLMResponse] | None = None, *, chunk_words: int = 3
    ) -> None:
        self.model = model
        self._responses = list(responses) if responses is not None else None
        self.call_count = 0
        # Every messages/tools payload this provider was ever called with —
        # lets a test assert on what actually reached the "model", the same
        # way a real provider's request would be inspectable via a network
        # mock.
        self.calls: list[dict] = []
        # How many words land in each streamed text_delta chunk — small on
        # purpose so a short test message still produces several chunks.
        self._chunk_words = max(1, chunk_words)

    def _next_response(self, system: str, messages: list[LLMMessage], tools: list[ToolSpec] | None) -> LLMResponse:
        self.call_count += 1
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})

        if self._responses is not None:
            index = min(self.call_count - 1, len(self._responses) - 1)
            return self._responses[index]

        last_user = next((m.get("content", "") for m in reversed(messages) if m["role"] == "user"), "")
        # Echoes a snippet of the grounded system context back — lets a
        # test assert that grounded facts actually reached the provider
        # call, without needing a real model to "understand" anything.
        context_preview = system[:160].replace("\n", " ")
        text = (
            f"[mock:{self.model}] Grounded answer to: {last_user!r}. "
            f"Context seen (first 160 chars): {context_preview!r}"
        )
        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=len(system.split()) + sum(len(m.get("content", "").split()) for m in messages),
            output_tokens=len(text.split()),
        )

    async def generate(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        return self._next_response(system, messages, tools)

    async def generate_stream(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[LLMStreamChunk]:
        response = self._next_response(system, messages, tools)

        if response.tool_calls:
            yield LLMStreamChunk(
                text_delta=response.text,
                tool_calls=response.tool_calls,
                finished=True,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
            )
            return

        words = response.text.split(" ") if response.text else []
        if not words:
            yield LLMStreamChunk(
                finished=True, model=response.model, input_tokens=response.input_tokens, output_tokens=response.output_tokens
            )
            return

        for start in range(0, len(words), self._chunk_words):
            piece = " ".join(words[start : start + self._chunk_words])
            # A leading space (on every chunk after the first) keeps words
            # correctly separated when a consumer simply concatenates every
            # text_delta in arrival order.
            delta = piece if start == 0 else " " + piece
            is_last = start + self._chunk_words >= len(words)
            yield LLMStreamChunk(
                text_delta=delta,
                finished=is_last,
                model=response.model if is_last else None,
                input_tokens=response.input_tokens if is_last else None,
                output_tokens=response.output_tokens if is_last else None,
            )


class NonStreamingMockProvider:
    """A minimal LLMProvider that only implements generate() — no
    generate_stream() at all, exactly like a real minimal adapter with no
    streaming API of its own might. Used to prove
    NeuroCoreService.answer_stream falls back to a single whole-text
    text_delta chunk when a provider doesn't support streaming (see the
    objective's "the application must still function" requirement).
    Deliberately doesn't subclass MockLLMProvider so it structurally lacks
    generate_stream rather than merely disabling it.
    """

    name = "mock-nonstreaming"
    supports_streaming = False

    def __init__(self, model: str = "mock-nonstreaming-1", response: LLMResponse | None = None) -> None:
        self.model = model
        self._response = response
        self.call_count = 0
        self.calls: list[dict] = []

    async def generate(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        self.call_count += 1
        self.calls.append({"system": system, "messages": list(messages), "tools": tools})
        if self._response is not None:
            return self._response
        return LLMResponse(text=f"[{self.model}] non-streaming fallback response.", model=self.model)
