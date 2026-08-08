"""OpenAIProvider — calls the OpenAI Chat Completions API directly over
httpx (already a project dependency) rather than requiring the `openai`
SDK as a new dependency. See AnthropicProvider for the parallel adapter.

Translates the provider-agnostic LLMMessage/ToolCall shapes (see
app.neurocore.providers.base) to and from OpenAI's own function-calling
wire format: a tool result is a `role: "tool"` message, and an assistant
turn that called tools carries a `tool_calls` list whose `arguments` are a
JSON-encoded *string* (OpenAI's wire format, unlike Anthropic's — this is
the one place that string-encoding/decoding happens).
"""

from __future__ import annotations

import json

import httpx

from app.neurocore.providers.base import LLMMessage, LLMResponse, ProviderError, ToolCall, ToolSpec

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "openai"

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
            "messages": _to_openai_messages(system, messages),
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ]

        # The bearer token never gets logged or included in any exception
        # message raised below — see ProviderError's docstring.
        headers = {"authorization": f"Bearer {self._api_key}", "content-type": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                response = await client.post(OPENAI_API_URL, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(f"OpenAI API returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI API request failed: {type(exc).__name__}") from exc

        try:
            text, tool_calls = _parse_openai_message(data["choices"][0]["message"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenAI API returned an unexpected response shape") from exc

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            tool_calls=tool_calls,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )


def _to_openai_messages(system: str, messages: list[LLMMessage]) -> list[dict]:
    converted: list[dict] = [{"role": "system", "content": system}]
    for message in messages:
        role = message["role"]
        if role == "tool":
            converted.append(
                {"role": "tool", "tool_call_id": message["tool_call_id"], "content": message.get("content", "")}
            )
        elif role == "assistant" and message.get("tool_calls"):
            converted.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or None,
                    "tool_calls": [
                        {
                            "id": call["id"],
                            "type": "function",
                            "function": {"name": call["name"], "arguments": json.dumps(call["arguments"])},
                        }
                        for call in message["tool_calls"]
                    ],
                }
            )
        else:
            converted.append({"role": role, "content": message.get("content", "")})
    return converted


def _parse_openai_message(message: dict) -> tuple[str, list[ToolCall]]:
    text = message.get("content") or ""
    tool_calls: list[ToolCall] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        tool_calls.append(ToolCall(id=call["id"], name=function.get("name", ""), arguments=arguments))
    return text, tool_calls
