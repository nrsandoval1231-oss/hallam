"""Model provider interface and its deterministic fallback."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from sabra_domain.errors import ModelError
from sabra_domain.settings import Settings, get_settings


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str | None = None
    latency_ms: int | None = None
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class LLMProvider(ABC):
    """Minimal completion interface.

    Deliberately small: the product uses models for judgement over supplied
    evidence, not for tool use or autonomous action, so anything richer would be
    an affordance the loop must not have.
    """

    name: str = "none"
    model: str | None = None

    @property
    def available(self) -> bool:
        return False

    @abstractmethod
    def complete(self, prompt: str, *, system: str | None = None, purpose: str = "generic") -> LLMResponse:
        """Return a completion or raise `ModelError`."""


class NullProvider(LLMProvider):
    """Used when no model is configured.

    Raises rather than returning an empty string so a caller cannot mistake
    "no model" for "the model said nothing", and so the fallback path is the one
    that actually runs.
    """

    name = "none"

    def complete(self, prompt: str, *, system: str | None = None, purpose: str = "generic") -> LLMResponse:
        raise ModelError(
            "no language model is configured; set SABRA_LLM_PROVIDER and SABRA_LLM_MODEL to enable "
            f"model-assisted {purpose}"
        )


def get_provider(settings: Settings | None = None) -> LLMProvider:
    """Build the configured provider, defaulting to the null provider."""

    settings = settings or get_settings()
    provider = (settings.llm_provider or "none").strip().lower()
    if provider in {"", "none", "null", "disabled"}:
        return NullProvider()
    if provider in {"openai", "openai_compatible", "openai-compatible", "ollama", "vllm"}:
        from .openai_compatible import OpenAICompatibleProvider

        return OpenAICompatibleProvider(settings)
    raise ModelError(f"unsupported SABRA_LLM_PROVIDER: {provider!r}")
