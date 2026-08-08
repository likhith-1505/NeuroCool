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
"""

from __future__ import annotations

from app.neurocore.providers.base import LLMMessage, LLMResponse, ToolSpec

DEFAULT_MOCK_MODEL = "mock-echo-1"


class MockLLMProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "mock"

    def __init__(self, model: str = DEFAULT_MOCK_MODEL, responses: list[LLMResponse] | None = None) -> None:
        self.model = model
        self._responses = list(responses) if responses is not None else None
        self.call_count = 0
        # Every messages/tools payload this provider was ever called with —
        # lets a test assert on what actually reached the "model", the same
        # way a real provider's request would be inspectable via a network
        # mock.
        self.calls: list[dict] = []

    async def generate(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
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
