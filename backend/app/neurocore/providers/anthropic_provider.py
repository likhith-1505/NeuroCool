"""AnthropicProvider — calls the Anthropic Messages API directly over
httpx (already a project dependency) rather than requiring the `anthropic`
SDK as a new dependency. Structurally identical in spirit to
OpenAIProvider — the only place either vendor's wire format is known.

Translates the provider-agnostic LLMMessage/ToolCall shapes (see
app.neurocore.providers.base) to and from Anthropic's own tool-use wire
format: a tool result is a "user" message containing a `tool_result`
content block, and an assistant turn that called tools carries `tool_use`
content blocks alongside (or instead of) a text block.
"""

from __future__ import annotations

import httpx

from app.neurocore.providers.base import LLMMessage, LLMResponse, ProviderError, ToolCall, ToolSpec

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "anthropic"

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
