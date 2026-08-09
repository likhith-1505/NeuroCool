"""OpenAIProvider — calls the OpenAI Chat Completions API directly over
httpx (already a project dependency) rather than requiring the `openai`
SDK as a new dependency. See AnthropicProvider for the parallel adapter.

Translates the provider-agnostic LLMMessage/ToolCall shapes (see
app.neurocore.providers.base) to and from OpenAI's own function-calling
wire format: a tool result is a `role: "tool"` message, and an assistant
turn that called tools carries a `tool_calls` list whose `arguments` are a
JSON-encoded *string* (OpenAI's wire format, unlike Anthropic's — this is
the one place that string-encoding/decoding happens).

generate_stream() speaks the Chat Completions API's streaming format
(`stream: true`, plus `stream_options.include_usage` to get a final usage
chunk): a series of `chat.completion.chunk` objects, each carrying
`choices[0].delta` — `delta.content` is a text fragment, `delta.tool_calls`
is a list of *partial* tool-call fragments keyed by `index` (OpenAI splits
even a tool call's `id`/`name` from its `arguments` string across several
chunks). Fragments are accumulated per index and only turned into a
ToolCall once `finish_reason == "tool_calls"` arrives — see
LLMStreamChunk's docstring for why partial tool-call JSON is never
exposed outside a provider adapter. The stream ends with a literal
`data: [DONE]` line, already swallowed by app.neurocore.providers.sse.
"""

from __future__ import annotations

import json

import httpx

from app.neurocore.providers.base import LLMMessage, LLMResponse, LLMStreamChunk, ProviderError, ToolCall, ToolSpec
from app.neurocore.providers.sse import iter_sse_events

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "openai"
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

    async def generate_stream(
        self, *, system: str, messages: list[LLMMessage], max_tokens: int, tools: list[ToolSpec] | None = None
    ):
        payload: dict[str, object] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": _to_openai_messages(system, messages),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {"name": tool["name"], "description": tool["description"], "parameters": tool["input_schema"]},
                }
                for tool in tools
            ]

        headers = {"authorization": f"Bearer {self._api_key}", "content-type": "application/json", "accept": "text/event-stream"}

        model_name = self.model
        input_tokens: int | None = None
        output_tokens: int | None = None
        pending_tools: dict[int, dict[str, str]] = {}

        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                async with client.stream("POST", OPENAI_API_URL, json=payload, headers=headers) as response:
                    if response.status_code >= 400:
                        raise ProviderError(f"OpenAI API returned HTTP {response.status_code}")

                    async for _event_name, data in iter_sse_events(response.aiter_lines()):
                        if data is None:
                            continue  # malformed/undecodable event — skip, never crash the stream

                        model_name = data.get("model", model_name)
                        usage = data.get("usage")
                        if usage:
                            input_tokens = usage.get("prompt_tokens", input_tokens)
                            output_tokens = usage.get("completion_tokens", output_tokens)

                        choices = data.get("choices") or []
                        if not choices:
                            continue  # the trailing usage-only chunk has no choices at all
                        choice = choices[0]
                        delta = choice.get("delta") or {}

                        content = delta.get("content")
                        if content:
                            yield LLMStreamChunk(text_delta=content)

                        for call_delta in delta.get("tool_calls") or []:
                            index = call_delta.get("index", 0)
                            entry = pending_tools.setdefault(index, {"id": "", "name": "", "json": ""})
                            if call_delta.get("id"):
                                entry["id"] = call_delta["id"]
                            function = call_delta.get("function") or {}
                            if function.get("name"):
                                entry["name"] = function["name"]
                            if function.get("arguments"):
                                entry["json"] += function["arguments"]

                        if choice.get("finish_reason") == "tool_calls" and pending_tools:
                            for entry in pending_tools.values():
                                try:
                                    arguments = json.loads(entry["json"]) if entry["json"] else {}
                                except json.JSONDecodeError:
                                    arguments = {}
                                yield LLMStreamChunk(tool_calls=[ToolCall(id=entry["id"], name=entry["name"], arguments=arguments)])
                            pending_tools.clear()
        except httpx.HTTPError as exc:
            raise ProviderError(f"OpenAI API request failed: {type(exc).__name__}") from exc

        yield LLMStreamChunk(finished=True, model=model_name, input_tokens=input_tokens, output_tokens=output_tokens)


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
