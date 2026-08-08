"""The LLMProvider contract.

Mirrors the same swappable-engine pattern used throughout this backend
(app.ai.base.DecisionEngine, app.forecasting.base.ForecastEngine,
app.optimization.base.OptimizationEngine): a single method, `generate()`,
is the entire seam. NeuroCoreService and every prompt-construction helper
in app.neurocore only ever depend on this Protocol — never on the
Anthropic or OpenAI SDKs/wire formats directly, so adding a third provider
later (or swapping the two existing ones) never touches app.neurocore.
service, app.neurocore.grounding, or the /api/ai/chat route.

Extended in the action-orchestration phase to carry structured tool/
function calling: `generate()` optionally takes `tools` (see ToolSpec) and
may return `tool_calls` in its LLMResponse instead of (or alongside) text.
This is provider-agnostic on purpose — each adapter translates ToolSpec/
ToolCall to and from its own vendor wire format internally (Anthropic's
`tool_use`/`tool_result` content blocks vs. OpenAI's `tool_calls`/
`role: "tool"` messages); nothing outside app.neurocore.providers needs to
know the difference. The actual *decision* of which tool to call always
comes from the provider's own structured tool-calling output — never from
regex/keyword-matching the operator's raw text (see app.neurocore.tools
and app.neurocore.service for where a returned ToolCall is validated and
dispatched).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypedDict


class ToolSpec(TypedDict):
    """One tool's definition, as sent to the provider on every request
    that allows tool use — see app.neurocore.tools.registry.tool_specs.
    `input_schema` is a JSON Schema dict (from a Pydantic model's
    `.model_json_schema()`), not a Python type, since that's the one
    format both providers' wire formats accept directly.
    """

    name: str
    description: str
    input_schema: dict[str, Any]


class ToolCall(TypedDict):
    """One tool invocation the model asked for. `arguments` is the raw,
    not-yet-validated dict the model produced — app.neurocore.tools.
    executor is what validates it against the tool's real Pydantic input
    schema and rejects anything that doesn't conform.
    """

    id: str
    name: str
    arguments: dict[str, Any]


class LLMMessage(TypedDict, total=False):
    """One turn in the conversation sent to the provider. "system" is
    passed as its own argument to `generate()` (every major provider's API
    treats it specially), so only "user"/"assistant"/"tool" turns appear
    here.

    Not every key is present on every message — `total=False` lets a
    plain text turn omit tool_calls/tool_call_id entirely (the common
    case, and the only shape the read-only reasoning phase ever produced,
    so its existing call sites keep working unchanged):
      - {"role": "user"|"assistant", "content": "..."} — plain text turn.
      - {"role": "assistant", "tool_calls": [...], "content": "..."} — the
        model asked to call one or more tools (content may be empty).
      - {"role": "tool", "tool_call_id": "...", "content": "..."} — the
        (already-executed, validated) result of one tool call, fed back
        so the model can use it in its next turn.
    """

    role: Literal["user", "assistant", "tool"]
    content: str
    tool_calls: list[ToolCall]
    tool_call_id: str


@dataclass(frozen=True)
class LLMResponse:
    """A provider's raw output, normalized to one shape regardless of
    which vendor produced it. Token counts are optional — not every
    provider (or every error path) reports them. `tool_calls` is empty for
    an ordinary text completion; when non-empty, `text` may be empty (the
    model chose to call a tool instead of answering directly).
    """

    text: str
    model: str
    tool_calls: list[ToolCall] = field(default_factory=list)
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

    async def generate(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        """Produce a completion for `messages`, given `system` instructions
        and, optionally, a set of tools the model may call instead of (or
        before) answering in text.

        Raises ProviderError on any failure — callers (see
        app.neurocore.service.NeuroCoreService.answer) are expected to
        catch it and fall back to a clear, honest "AI reasoning is
        temporarily unavailable" response rather than let it propagate as
        a 500.
        """
        ...
