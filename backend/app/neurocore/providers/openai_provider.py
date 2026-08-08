"""OpenAIProvider — calls the OpenAI Chat Completions API directly over
httpx (already a project dependency) rather than requiring the `openai`
SDK as a new dependency. See AnthropicProvider for the parallel adapter.
"""

from __future__ import annotations

import httpx

from app.neurocore.providers.base import LLMMessage, LLMResponse, ProviderError

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"


class OpenAIProvider:
    """Conforms structurally to app.neurocore.providers.base.LLMProvider."""

    name = "openai"

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float = 30.0) -> None:
        self._api_key = api_key
        self.model = model
        self._timeout_seconds = timeout_seconds

    async def generate(self, *, system: str, messages: list[LLMMessage], max_tokens: int) -> LLMResponse:
        payload = {
            "model": self.model,
            "max_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                *[{"role": message["role"], "content": message["content"]} for message in messages],
            ],
        }
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
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenAI API returned an unexpected response shape") from exc

        usage = data.get("usage") or {}
        return LLMResponse(
            text=text,
            model=data.get("model", self.model),
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
        )
