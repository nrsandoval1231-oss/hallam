"""Model provider interface and its deterministic fallback."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel
from sabra_domain.identity import sha256_text
from sabra_domain.settings import Settings, get_settings

from .errors import LLMError, LLMUnavailable

log = logging.getLogger("sabra.llm")

SchemaT = TypeVar("SchemaT", bound=BaseModel)


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str | None = None
    latency_ms: int | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Usage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None


@dataclass(slots=True)
class StructuredResult:
    parsed: BaseModel
    text: str = ""
    usage: Usage = field(default_factory=Usage)


@dataclass(slots=True)
class CallRecord:
    """What one model call cost and whether it worked. Never holds prompt text.

    Prompts and answers are represented by hashes only, so the audit trail proves
    what was asked without keeping candidate data in a second place.
    """

    provider: str
    model: str | None
    purpose: str
    ok: bool
    error: str | None = None
    retryable: bool = False
    latency_ms: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    prompt_hash: str = ""
    response_hash: str = ""
    entity_type: str | None = None
    entity_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "purpose": self.purpose,
            "ok": self.ok,
            "error": self.error,
            "retryable": self.retryable,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "prompt_hash": self.prompt_hash,
            "response_hash": self.response_hash,
        }


Recorder = Callable[[CallRecord], None]


class LLMProvider(ABC):
    """Minimal completion interface.

    Deliberately small: the product uses models for judgement over supplied
    evidence, not for tool use or autonomous action, so anything richer would be
    an affordance the loop must not have.
    """

    name: str = "none"
    model: str | None = None
    # Optional provider-wide sink, in addition to a per-call `recorder`.
    call_sink: Recorder | None = None

    @property
    def available(self) -> bool:
        return False

    @abstractmethod
    def complete(
        self, prompt: str, *, system: str | None = None, purpose: str = "generic"
    ) -> LLMResponse:
        """Return a completion or raise `ModelError`."""

    # -- structured output ---------------------------------------------

    def _structured(
        self, system: str, user: str, schema: type[SchemaT], *, purpose: str
    ) -> StructuredResult:
        """Provider hook: one structured call. Raise an `LLMError` subclass on failure."""

        raise LLMUnavailable(f"{self.name} does not implement structured output")

    def complete_structured(
        self,
        system: str,
        user: str,
        schema: type[SchemaT],
        *,
        purpose: str,
        entity: tuple[str, str] | None = None,
        recorder: Recorder | None = None,
    ) -> SchemaT:
        """Return an instance of `schema`, or raise an `LLMError`.

        Every attempt, successful or not, is reported as a `CallRecord` to
        `recorder` (and `call_sink`) so the caller can write the audit row.
        """

        started = time.perf_counter()
        prompt_hash = sha256_text(f"{system}\n\n{user}")
        try:
            result = self._structured(system, user, schema, purpose=purpose)
        except LLMError as exc:
            self.emit(
                self._record(purpose, started, prompt_hash, entity, ok=False, exc=exc),
                recorder,
            )
            raise
        except Exception as exc:  # a provider bug must not escape its family
            wrapped = LLMError(f"{type(exc).__name__}: {exc}")
            self.emit(
                self._record(purpose, started, prompt_hash, entity, ok=False, exc=wrapped),
                recorder,
            )
            raise wrapped from exc
        self.emit(
            self._record(
                purpose,
                started,
                prompt_hash,
                entity,
                ok=True,
                usage=result.usage,
                response_hash=sha256_text(result.text or result.parsed.model_dump_json()),
            ),
            recorder,
        )
        return result.parsed  # type: ignore[return-value]

    # -- bookkeeping -----------------------------------------------------

    def _record(
        self,
        purpose: str,
        started: float,
        prompt_hash: str,
        entity: tuple[str, str] | None,
        *,
        ok: bool,
        exc: LLMError | None = None,
        usage: Usage | None = None,
        response_hash: str = "",
    ) -> CallRecord:
        usage = usage or Usage()
        return CallRecord(
            provider=self.name,
            model=self.model,
            purpose=purpose,
            ok=ok,
            error=str(exc)[:500] if exc is not None else None,
            retryable=bool(exc and exc.retryable),
            latency_ms=int((time.perf_counter() - started) * 1000),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_write_tokens=usage.cache_write_tokens,
            prompt_hash=prompt_hash,
            response_hash=response_hash,
            entity_type=entity[0] if entity else None,
            entity_id=entity[1] if entity else None,
        )

    def emit(self, record: CallRecord, recorder: Recorder | None = None) -> None:
        log.info(
            "llm call purpose=%s ok=%s in=%s out=%s cache_read=%s latency_ms=%s",
            record.purpose,
            record.ok,
            record.input_tokens,
            record.output_tokens,
            record.cache_read_tokens,
            record.latency_ms,
        )
        for sink in (recorder, self.call_sink):
            if sink is not None:
                sink(record)


class NullProvider(LLMProvider):
    """Used when no model is configured.

    Raises rather than returning an empty string so a caller cannot mistake
    "no model" for "the model said nothing", and so the fallback path is the one
    that actually runs.
    """

    name = "none"

    def complete(
        self, prompt: str, *, system: str | None = None, purpose: str = "generic"
    ) -> LLMResponse:
        raise LLMUnavailable(
            "no language model is configured; set SABRA_LLM_PROVIDER and SABRA_LLM_MODEL to enable "
            f"model-assisted {purpose}"
        )

    def complete_structured(
        self,
        system: str,
        user: str,
        schema: type[SchemaT],
        *,
        purpose: str,
        entity: tuple[str, str] | None = None,
        recorder: Recorder | None = None,
    ) -> SchemaT:
        # No call is made, so there is nothing to record.
        raise LLMUnavailable(
            "no language model is configured; set SABRA_LLM_PROVIDER and SABRA_LLM_MODEL to enable "
            f"model-assisted {purpose}"
        )


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Build the configured provider, defaulting to the null provider."""

    settings = settings or get_settings()
    provider = (settings.llm_provider or "none").strip().lower()
    if provider in {"", "none", "null", "disabled"}:
        return NullProvider()
    if provider in {"anthropic", "claude"}:
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider(settings)
    if provider in {"openai", "openai_compatible", "openai-compatible", "ollama", "vllm"}:
        from .openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(settings)
    raise LLMUnavailable(f"unsupported SABRA_LLM_PROVIDER: {provider!r}")
