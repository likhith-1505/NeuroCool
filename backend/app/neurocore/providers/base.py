"""The LLMProvider contract.

Mirrors the same swappable-engine pattern used throughout this backend
(app.ai.base.DecisionEngine, app.forecasting.base.ForecastEngine,
app.optimization.base.OptimizationEngine): a single method, `generate()`,
is the entire seam. NeuroCoreService and every prompt-construction helper
in app.neurocore only ever depend on this Protocol — never on the
Anthropic or OpenAI SDKs/wire formats directly, so adding a third provider
later (or swapping the two existing ones) never touches app.neurocore.
service, app.neurocore.grounding, or the /api/ai/chat route.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, TypedDict


class LLMMessage(TypedDict):
    """One turn in the conversation sent to the provider. "system" is
    passed as its own argument to `generate()` (every major provider's API
    treats it specially), so only "user"/"assistant" turns appear here.
    """

    role: Literal["user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResponse:
    """A provider's raw output, normalized to one shape regardless of
    which vendor produced it. Token counts are optional — not every
    provider (or every error path) reports them.
    """

    text: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class ProviderError(Exception):
    """Raised by an LLMProvider when generate() fails for any reason — a
    network error, a non-2xx response, or an unexpected response shape.
    Adapters must never interpolate the API key or raw response headers
    into this message (see each adapter's generate() and the objective's
    observability/security requirements).
    """


class LLMProvider(Protocol):
    """Contract every provider adapter satisfies. `name` and `model` are
    plain attributes (not methods) so logging/observability can read them
    without an extra await.
    """

    name: str
    model: str

    async def generate(self, *, system: str, messages: list[LLMMessage], max_tokens: int) -> LLMResponse:
        """Produce a completion for `messages`, given `system` instructions.

        Raises ProviderError on any failure — callers (see
        app.neurocore.service.NeuroCoreService.answer) are expected to
        catch it and fall back to a clear, honest "AI reasoning is
        temporarily unavailable" response rather than let it propagate as
        a 500.
        """
        ...
