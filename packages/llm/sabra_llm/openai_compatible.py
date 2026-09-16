"""OpenAI-compatible chat completion provider.

Works against any OpenAI-compatible endpoint (OpenAI, Azure-compatible gateways,
vLLM, Ollama's compatibility layer) because the product must not be coupled to
one vendor. Credentials come from the environment only; the engine never reads a
key from the database and never logs one.
"""

from __future__ import annotations

import time

import httpx
from sabra_domain.errors import ModelError
from sabra_domain.settings import Settings

from .base import LLMProvider, LLMResponse

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAICompatibleProvider(LLMProvider):
    name = "openai-compatible"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self.settings = settings
        self.model = settings.llm_model
        self.base_url = (settings.llm_base_url or DEFAULT_BASE_URL).rstrip("/")
        self._api_key = settings.llm_api_key
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=settings.llm_timeout_seconds)

    @property
    def available(self) -> bool:
        return bool(self.model)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete(
        self, prompt: str, *, system: str | None = None, purpose: str = "generic"
    ) -> LLMResponse:
        if not self.model:
            raise ModelError("SABRA_LLM_MODEL is not set; refusing to call an unnamed model")

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.2,
                },
            )
        except httpx.HTTPError as exc:
            raise ModelError(
                f"model request failed for {purpose}: {type(exc).__name__}: {exc}"
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            raise ModelError(
                f"model returned HTTP {response.status_code} for {purpose}: {response.text[:400]}"
            )
        try:
            payload = response.json()
            text = payload["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise ModelError(f"unusable model response for {purpose}: {exc}") from exc

        usage = payload.get("usage") or {}
        return LLMResponse(
            text=text or "",
            provider=self.name,
            model=self.model,
            latency_ms=latency_ms,
            tokens_prompt=usage.get("prompt_tokens"),
            tokens_completion=usage.get("completion_tokens"),
            raw={"finish_reason": (payload.get("choices") or [{}])[0].get("finish_reason")},
        )
