"""A tiny shared Server-Sent-Events line parser for the two real provider
adapters (see AnthropicProvider.generate_stream / OpenAIProvider.
generate_stream). Both vendors' streaming APIs speak plain SSE framing
(`event: ...` / `data: ...` lines, one event per blank-line-terminated
block), so this is the one place that framing is parsed; everything
downstream only ever sees a decoded JSON payload per event — a provider
adapter still owns translating that vendor-specific JSON shape into a
provider-agnostic LLMStreamChunk (see app.neurocore.providers.base).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

# OpenAI terminates its stream with a literal `data: [DONE]` line rather
# than a JSON payload — not a malformed event, just the end-of-stream
# sentinel, so it's swallowed here rather than yielded as (None, None).
_DONE_SENTINEL = "[DONE]"


async def iter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str | None, dict | None]]:
    """Consumes an async iterator of raw text lines (e.g. httpx's
    `Response.aiter_lines()`) and yields `(event_name, payload)` pairs, one
    per complete SSE event. `event_name` is Anthropic-style (from an
    `event:` line) or None (OpenAI never sends one, relying on `data`'s own
    embedded `type`/shape instead). `payload` is None for a `data:` line
    that isn't valid JSON — never raised — so one malformed event can never
    take down the whole stream; callers simply skip a None payload (see the
    objective's "(13) malformed provider events" testing requirement).
    """
    event_name: str | None = None
    data_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.rstrip("\r\n")

        if line == "":
            # Blank line: end of one SSE event.
            if data_lines:
                async for pair in _flush(event_name, data_lines):
                    yield pair
            event_name, data_lines = None, []
            continue

        if line.startswith(":"):
            continue  # comment / heartbeat line — not a real event
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].strip())
        # Any other field (id:, retry:) is intentionally ignored — neither
        # adapter needs it.

    # A stream that ends without a final blank line still carries one
    # complete trailing event — don't silently drop it.
    if data_lines:
        async for pair in _flush(event_name, data_lines):
            yield pair


async def _flush(event_name: str | None, data_lines: list[str]) -> AsyncIterator[tuple[str | None, dict | None]]:
    raw_data = "\n".join(data_lines)
    if raw_data == _DONE_SENTINEL:
        return
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError:
        payload = None
    yield event_name, payload
