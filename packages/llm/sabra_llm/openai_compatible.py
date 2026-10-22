"""OpenAI-compatible chat completion provider.

Works against any OpenAI-compatible endpoint (OpenAI, Azure-compatible gateways,
vLLM, Ollama's compatibility layer) because the product must not be coupled to
one vendor. Credentials come from the environment only; the engine never reads a
key from the database and never logs one.

Structured output uses JSON mode plus pydantic validation: the schema is stated in
the system message and the reply is validated here, because not every compatible
endpoint supports server-side schema enforcement.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from pydantic import ValidationError
from sabra_domain.settings import Settings

from .base import LLMProvider, LLMResponse, SchemaT, StructuredResult, Usage
from .errors import (
    LLMError,
    LLMInvalidOutput,
    LLMRateLimited,
    LLMRefused,
    LLMTruncated,
    redact,
)

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

    def _post(
        self, messages: list[dict[str, str]], purpose: str, extra: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any], int]:
        if not self.model:
            raise LLMError("SABRA_LLM_MODEL is not set; refusing to call an unnamed model")

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": 0.2}
        body.update(extra or {})

        started = time.perf_counter()
        try:
            response = self._client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=body
            )
        except httpx.HTTPError as exc:
            raise LLMError(
                f"model request failed for {purpose}: {type(exc).__name__}: {exc}",
                retryable=True,
            ) from exc

        latency_ms = int((time.perf_counter() - started) * 1000)
        if response.status_code >= 400:
            message = f"model returned HTTP {response.status_code} for {purpose}: {redact(response.text[:400], self._api_key)}"
            if response.status_code == 429:
                raise LLMRateLimited(message)
            raise LLMError(message, retryable=response.status_code >= 500)
        try:
            return response.json(), latency_ms
        except ValueError as exc:
            raise LLMError(f"unusable model response for {purpose}: {exc}") from exc

    def complete(
        self, prompt: str, *, system: str | None = None, purpose: str = "generic"
    ) -> LLMResponse:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload, latency_ms = self._post(messages, purpose)
        try:
            text = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unusable model response for {purpose}: {exc}") from exc

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

    def _structured(
        self, system: str, user: str, schema: type[SchemaT], *, purpose: str
    ) -> StructuredResult:
        schema_text = json.dumps(schema.model_json_schema(), separators=(",", ":"))
        instruction = (
            f"{system}\n\nReply with ONE JSON object and nothing else. It must validate against "
            f"this JSON Schema:\n{schema_text}"
        )
        payload, _latency = self._post(
            [
                {"role": "system", "content": instruction},
                {"role": "user", "content": user},
            ],
            purpose,
            {"response_format": {"type": "json_object"}},
        )
        try:
            choice = payload["choices"][0]
            message = choice.get("message") or {}
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMError(f"unusable model response for {purpose}: {exc}") from exc

        finish = choice.get("finish_reason")
        if message.get("refusal") or finish == "content_filter":
            raise LLMRefused(f"the model declined to answer ({purpose})")
        if finish == "length":
            raise LLMTruncated(f"the model's answer was cut off for {purpose}")
        text = message.get("content") or ""
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMInvalidOutput(
                f"model output for {purpose} did not match {schema.__name__}: "
                f"{exc.error_count()} validation error(s)"
            ) from exc

        usage = payload.get("usage") or {}
        details = usage.get("prompt_tokens_details") or {}
        return StructuredResult(
            parsed=parsed,
            text=text,
            usage=Usage(
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                cache_read_tokens=details.get("cached_tokens"),
            ),
        )
