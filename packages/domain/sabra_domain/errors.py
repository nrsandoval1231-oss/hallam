"""Typed failures. Every one is catchable and names what went wrong."""

from __future__ import annotations


class SabraError(Exception):
    """Base class for all Sabra errors."""


class ConfigurationError(SabraError):
    """The environment or configuration cannot support the requested operation."""


class NotFound(SabraError):
    def __init__(self, entity: str, key: object) -> None:
        super().__init__(f"{entity} not found: {key!r}")
        self.entity = entity
        self.key = key


class ValidationFailure(SabraError):
    """Input or persisted data does not satisfy its contract."""


class InvalidTransition(SabraError):
    def __init__(self, entity: str, current: object, target: object) -> None:
        super().__init__(f"invalid {entity} transition: {current} -> {target}")
        self.entity = entity
        self.current = current
        self.target = target


class TruthBoundaryViolation(SabraError):
    """Generated content asserted something the candidate record does not support.

    This is the failure mode requirement 5 exists to prevent, so it is a hard
    error rather than a warning.
    """

    def __init__(self, message: str, claims: list[str] | None = None) -> None:
        super().__init__(message)
        self.claims = claims or []


class ApprovalRequired(SabraError):
    """An irreversible action was attempted without a bound approval."""


class ApprovalScopeMismatch(SabraError):
    """An approval exists but does not bind to the exact package being submitted."""


class SafetyStop(SabraError):
    """Automation encountered a condition requiring human intervention."""

    def __init__(self, kind: object, message: str, details: dict | None = None) -> None:
        super().__init__(f"{kind}: {message}")
        self.kind = kind
        self.message = message
        self.details = details or {}


class ProviderError(SabraError):
    """A discovery source failed. Never fatal to the surrounding run."""

    def __init__(self, source: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(f"provider {source}: {message}")
        self.source = source
        self.message = message
        self.retryable = retryable


class ModelError(SabraError):
    """The reasoning layer is unavailable or returned unusable output."""
