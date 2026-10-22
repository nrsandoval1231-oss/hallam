"""One failure family for the reasoning layer.

Every provider maps its own exceptions onto these, so a caller writes a single
`except LLMError` and then decides what to do from the subclass or the
`retryable` flag. All of them are `ModelError`s, which keeps existing handlers
working.
"""

from __future__ import annotations

import re

from sabra_domain.errors import ModelError


class LLMError(ModelError):
    """The model could not produce a usable answer."""

    retryable: bool = False

    def __init__(self, message: str, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        if retryable is not None:
            self.retryable = retryable


class LLMUnavailable(LLMError):
    """No model is configured, or its client library is not installed."""


class LLMRateLimited(LLMError):
    """The provider asked us to slow down. Worth retrying later."""

    retryable = True


class LLMRefused(LLMError):
    """The model declined to answer (a safety refusal). Retrying the same ask will not help."""


class LLMTruncated(LLMError):
    """The answer hit the output limit, so any structured payload is incomplete."""


class LLMInvalidOutput(LLMError):
    """The model answered, but not in the requested structure."""


_KEY_SHAPE = re.compile(r"sk-[A-Za-z0-9_\-]{4,}")


def redact(text: str, api_key: str | None = None) -> str:
    """Strip anything key-shaped, and the configured key itself, from an error message."""

    if api_key:
        text = text.replace(api_key, "[redacted]")
    return _KEY_SHAPE.sub("[redacted]", text)
