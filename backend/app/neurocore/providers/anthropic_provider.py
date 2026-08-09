"""AnthropicProvider — calls the Anthropic Messages API directly over
httpx (already a project dependency) rather than requiring the `anthropic`
SDK as a new dependency. Structurally identical in spirit to
OpenAIProvider — the only place either vendor's wire format is known.

Translates the provider-agnostic LLMMessage/ToolCall shapes (see
app.neurocore.providers.base) to and from Anthropic's own tool-use wire
format: a tool result is a "user" message containing a `tool_result`
content block, and an assistant turn that called tools carries `tool_use`
content blocks alongside (or instead of) a text block.

generate_stream() speaks Anthropic's Messages API streaming format
directly (`stream: true`): `message_start` -> a series of
`content_block_start`/`content_block_delta`/`content_block_stop` per
content block -> `message_delta` (final usage) -> `message_stop`. A text
block's deltas are `text_delta` events; a tool-use block's deltas are
`input_json_delta` events whose `partial_json` fragments are accumulated
per block index and only parsed/emitted once that block's
`content_block_stop` arrives — see LLMStreamChunk's docstring for why a
provider adapter never exposes partial tool-call JSON. An `error` event
(Anthropic can send one mid-stream, e.g. an overloaded backend) becomes a
ProviderError instead of a chunk, same as a non-2xx HTTP response.
"""

from __future__ import annotations

import json

import httpx

from app.neurocore.providers.base import LLMMessage, LLMResponse, LLMStreamChunk, ProviderError, ToolCall, ToolSpec
from app.neurocore.providers.sse import iter_sse_events

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "anthropic"
    supports_streaming = True

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout_seconds = timeout_seconds

    async def generate(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ) -> LLMResponse:
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": _to_anthropic_messages(messages),
        }
        if tools:
            payload["tools"] = [
                {"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]}
                for tool in tools
            ]

        # x-api-key never gets logged or included in any exception message
        # raised below — see ProviderError's docstring.
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(ANTHROPIC_API_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"Anthropic API returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic API request failed: {type(exc).__name__}") from exc

        try:
            text, tool_calls = _parse_anthropic_content(data["content"])
        except (KeyError, TypeError) as exc:
            raise ProviderError("Anthropic API returned an unexpected response shape") from exc

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            tool_calls=tool_calls,
            input_tokens=usage.get("input_tokens"),
            output_tokens=usage.get("output_tokens"),
        )

    async def generate_stream(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ):
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": _to_anthropic_messages(messages),
            "stream": True,
        }
        if tools:
            payload["tools"] = [
                {"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]}
                for tool in tools
            ]

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

        model_name = self.model
        input_tokens: int | None = None
        output_tokens: int | None = None
        # Tool-use content blocks stream their `input` as incremental JSON
        # fragments, keyed by block index — accumulated here and only ever
        # turned into a ToolCall once complete (content_block_stop).
        pending_tools: dict[int, dict[str, str]] = {}

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                async with client.stream("POST", ANTHROPIC_API_URL, json=payload, headers=headers) as response:
                    if response.status_code >= 400:
                        raise ProviderError(f"Anthropic API returned HTTP {response.status_code}")

                    async for event_name, data in iter_sse_events(response.aiter_lines()):
                        if data is None:
                            continue  # malformed/undecodable event — skip, never crash the stream
                        event_type = data.get("type") or event_name

                        if event_type == "message_start":
                            message = data.get("message") or {}
                            model_name = message.get("model", model_name)
                            usage = message.get("usage") or {}
                            input_tokens = usage.get("input_tokens", input_tokens)

                        elif event_type == "content_block_start":
                            index = data.get("index")
                            block = data.get("content_block") or {}
                            if block.get("type") == "tool_use" and index is not None:
                                pending_tools[index] = {"id": block.get("id", ""), "name": block.get("name", ""), "json": ""}

                        elif event_type == "content_block_delta":
                            index = data.get("index")
                            delta = data.get("delta") or {}
                            if delta.get("type") == "text_delta":
                                text = delta.get("text", "")
                                if text:
                                    yield LLMStreamChunk(text_delta=text)
                            elif delta.get("type") == "input_json_delta" and index in pending_tools:
                                pending_tools[index]["json"] += delta.get("partial_json", "")

                        elif event_type == "content_block_stop":
                            index = data.get("index")
                            entry = pending_tools.pop(index, None) if index is not None else None
                            if entry is not None:
                                try:
                                    arguments = json.loads(entry["json"]) if entry["json"] else {}
                                except json.JSONDecodeError:
                                    arguments = {}
                                yield LLMStreamChunk(tool_calls=[ToolCall(id=entry["id"], name=entry["name"], arguments=arguments)])

                        elif event_type == "message_delta":
                            usage = data.get("usage") or {}
                            if "output_tokens" in usage:
                                output_tokens = usage["output_tokens"]

                        elif event_type == "message_stop":
                            break

                        elif event_type == "error":
                            error = data.get("error") or {}
                            raise ProviderError(f"Anthropic API stream error: {error.get('type', 'unknown_error')}")

                        # Any other event type (e.g. "ping", or a future
                        # vendor addition) is intentionally ignored rather
                        # than raising.
        except httpx.HTTPError as exc:
            raise ProviderError(f"Anthropic API request failed: {type(exc).__name__}") from exc

        yield LLMStreamChunk(finished=True, model=model_name, input_tokens=input_tokens, output_tokens=output_tokens)


def _to_anthropic_messages(messages: list[LLMMessage]) -> list[dict]:
    converted: list[dict] = []
    for message in messages:
        role = message["role"]
        if role == "tool":
            converted.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": message["tool_call_id"],
                            "content": message.get("content", ""),
                        }
                    ],
                }
            )
        elif role == "assistant" and message.get("tool_calls"):
            blocks: list[dict] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": message["content"]})
            for call in message["tool_calls"]:
                blocks.append({"type": "tool_use", "id": call["id"], "name": call["name"], "input": call["arguments"]})
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    return converted


def _parse_anthropic_content(blocks: list[dict]) -> tuple[str, list[ToolCall]]:
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    for block in blocks:
        block_type = block.get("type")
        if block_type == "text":
            text_parts.append(block.get("text", ""))
        elif block_type == "tool_use":
            tool_calls.append(ToolCall(id=block["id"], name=block["name"], arguments=block.get("input") or {}))
    return "".join(text_parts), tool_calls
