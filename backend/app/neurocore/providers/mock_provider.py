"""MockLLMProvider — a deterministic, no-network provider.

Used by the test suite (per the objective: "Do not require a real LLM API
key for the test suite. Use a deterministic mock provider.") and available
as a real, configurable option (AI_PROVIDER=mock) for local development
without any API key. Never makes a network call, so its output is a
reproducible pure function of its input — the same context always produces
the same response shape, which is what makes it usable in assertions.
"""

from __future__ import annotations

from app.neurocore.providers.base import LLMMessage, LLMResponse

DEFAULT_MOCK_MODEL = "mock-echo-1"


class MockLLMProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "mock"

    def __init__(self, model: str = DEFAULT_MOCK_MODEL) -> None:
        self.model = model

    async def generate(self, *, system: str, messages: list[LLMMessage], max_tokens: int) -> LLMResponse:
        last_user = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
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
            input_tokens=len(system.split()) + sum(len(m["content"].split()) for m in messages),
            output_tokens=len(text.split()),
        )
