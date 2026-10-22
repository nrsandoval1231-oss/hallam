"""Anthropic (Claude) provider, on the official `anthropic` SDK.

Request shape, verified against anthropic 2.1.0:

* `client.messages.create(...)` is used for structured calls rather than
  `client.messages.parse(...)`. `parse` validates the text against the pydantic
  model *before* returning, so a refusal or a `max_tokens` cut-off would surface as
  an opaque validation error with `stop_reason` out of reach. With `create` the stop
  reason is read first and mapped onto `LLMRefused` / `LLMTruncated`, then the text
  is validated here. The wire format is the same: the schema goes in
  `output_config={"format": {"type": "json_schema", ...}}`, built with the SDK's own
  `transform_schema`.
* `parse` also has no top-level `cache_control`. The stable system prompt (candidate
  profile + master résumé, identical across packages) is cached with a
  `cache_control` marker on the system text block, which is what actually caches it:
  a top-level marker would sit on the last block, the per-package user turn.
* Thinking is left at the model default (no `budget_tokens`), `output_config` carries
  `effort: medium`, and no assistant turn is ever pre-filled.
* The key comes from `SABRA_LLM_API_KEY`; when unset the SDK resolves its own
  environment. It is never logged or placed in an error message.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError
from sabra_domain.identity import sha256_text
from sabra_domain.settings import Settings

from .base import LLMProvider, LLMResponse, SchemaT, StructuredResult, Usage
from .errors import (
    LLMError,
    LLMInvalidOutput,
    LLMRateLimited,
    LLMRefused,
    LLMTruncated,
    LLMUnavailable,
    redact,
)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_TOKENS = 16000
MAX_RETRIES = 2
EFFORT = "medium"


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    def __init__(
        self,
        settings: Settings,
        *,
        client: Any | None = None,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        self.settings = settings
        self.model = settings.llm_model or DEFAULT_MODEL
        self.max_tokens = max_tokens
        self._client = client
        self._owns_client = client is None

    @property
    def available(self) -> bool:
        return True

    # -- client ----------------------------------------------------------

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import anthropic
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise LLMUnavailable(
                "the anthropic package is not installed; install the 'llm' extra"
            ) from exc
        kwargs: dict[str, Any] = {
            "timeout": self.settings.llm_timeout_seconds,
            "max_retries": MAX_RETRIES,
        }
        if self.settings.llm_api_key:
            kwargs["api_key"] = self.settings.llm_api_key
        if self.settings.llm_base_url:
            kwargs["base_url"] = self.settings.llm_base_url
        self._client = anthropic.Anthropic(**kwargs)
        return self._client

    def close(self) -> None:
        closer = getattr(self._client, "close", None) if self._owns_client else None
        if callable(closer):
            closer()

    # -- request ---------------------------------------------------------

    def _request(
        self, system: str | None, user: str, *, output_format: dict[str, Any] | None
    ) -> Any:
        output_config: dict[str, Any] = {"effort": EFFORT}
        if output_format is not None:
            output_config["format"] = output_format
        kwargs: dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": user}],
            "output_config": output_config,
        }
        if system:
            kwargs["system"] = [
                {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
            ]
        client = self._get_client()
        try:
            return client.messages.create(**kwargs)
        except LLMError:
            raise
        except Exception as exc:
            raise _map_exception(exc, self.settings.llm_api_key) from exc

    @staticmethod
    def _check_stop(response: Any, purpose: str) -> None:
        stop = getattr(response, "stop_reason", None)
        if stop == "refusal":
            raise LLMRefused(f"the model declined to answer ({purpose})")
        if stop in {"max_tokens", "model_context_window_exceeded"}:
            raise LLMTruncated(f"the model's answer was cut off ({stop}) for {purpose}")
        if stop not in {None, "end_turn", "stop_sequence"}:
            raise LLMError(f"unexpected stop_reason {stop!r} for {purpose}")

    @staticmethod
    def _text(response: Any) -> str:
        parts = [
            getattr(block, "text", "") or ""
            for block in (getattr(response, "content", None) or [])
            if getattr(block, "type", None) == "text"
        ]
        return "".join(parts)

    @staticmethod
    def _usage(response: Any) -> Usage:
        usage = getattr(response, "usage", None)
        return Usage(
            input_tokens=getattr(usage, "input_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
        )

    # -- interface -------------------------------------------------------

    def complete(
        self, prompt: str, *, system: str | None = None, purpose: str = "generic"
    ) -> LLMResponse:
        started = time.perf_counter()
        prompt_hash = sha256_text((system or "") + "\n\n" + prompt)
        try:
            response = self._request(system, prompt, output_format=None)
            self._check_stop(response, purpose)
        except LLMError as exc:
            self.emit(self._record(purpose, started, prompt_hash, None, ok=False, exc=exc))
            raise
        text = self._text(response)
        usage = self._usage(response)
        self.emit(self._record(purpose, started, prompt_hash, None, ok=True, usage=usage))
        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tokens_prompt=usage.input_tokens,
            tokens_completion=usage.output_tokens,
            raw={"stop_reason": getattr(response, "stop_reason", None)},
        )

    def _structured(
        self, system: str, user: str, schema: type[SchemaT], *, purpose: str
    ) -> StructuredResult:
        try:
            from anthropic import transform_schema
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise LLMUnavailable(
                "the anthropic package is not installed; install the 'llm' extra"
            ) from exc
        output_format = {
            "type": "json_schema",
            "schema": transform_schema(schema.model_json_schema()),
        }
        response = self._request(system, user, output_format=output_format)
        self._check_stop(response, purpose)
        text = self._text(response)
        try:
            parsed = schema.model_validate_json(text)
        except ValidationError as exc:
            raise LLMInvalidOutput(
                f"model output for {purpose} did not match {schema.__name__}: "
                f"{exc.error_count()} validation error(s)"
            ) from exc
        return StructuredResult(parsed=parsed, text=text, usage=self._usage(response))



def _map_exception(exc: Exception, api_key: str | None = None) -> LLMError:
    """Map the SDK's typed exceptions onto the one `LLMError` family."""

    try:
        import anthropic
    except ImportError:  # pragma: no cover
        return LLMError(f"{type(exc).__name__}: {redact(str(exc), api_key)}")

    detail = f"{type(exc).__name__}: {redact(str(exc), api_key)[:300]}"
    if isinstance(exc, anthropic.RateLimitError):
        return LLMRateLimited(f"rate limited by the provider ({detail})")
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return LLMError(f"the provider rejected the credentials ({type(exc).__name__})")
    if isinstance(
        exc,
        anthropic.APITimeoutError
        | anthropic.APIConnectionError
        | anthropic.InternalServerError
        | anthropic.OverloadedError
        | anthropic.ServiceUnavailableError
        | anthropic.DeadlineExceededError,
    ):
        return LLMError(f"transient provider failure ({detail})", retryable=True)
    if isinstance(exc, anthropic.APIError):
        return LLMError(f"provider error ({detail})")
    return LLMError(detail)
