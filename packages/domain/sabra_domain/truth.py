"""Candidate truth boundary primitives (PRD requirement 5).

The rule the whole product hangs on: generated application content may only
assert what the candidate record supports. `TruthValue` carries a value together
with the level of support for it, and a `TruthLedger` collects the provenance of
everything a generated document claims.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import TruthLevel


@dataclass(frozen=True, slots=True)
class Evidence:
    """A pointer back to the candidate record that supports a claim."""

    field_path: str
    excerpt: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {"field_path": self.field_path, "excerpt": self.excerpt}


@dataclass(slots=True)
class TruthValue:
    """A value with its level of support.

    `UNKNOWN` is a first-class state, not an absence: an application question the
    system cannot answer from candidate information must surface as UNKNOWN so it
    can be asked or flagged, never guessed.
    """

    value: Any = None
    level: TruthLevel = TruthLevel.UNKNOWN
    evidence: list[Evidence] = field(default_factory=list)
    note: str | None = None

    @property
    def is_usable(self) -> bool:
        """Only VERIFIED values may appear in submitted application content."""

        return self.level is TruthLevel.VERIFIED and self.value not in (None, "", [])

    @classmethod
    def verified(cls, value: Any, *evidence: Evidence) -> TruthValue:
        return cls(value=value, level=TruthLevel.VERIFIED, evidence=list(evidence))

    @classmethod
    def inferred(cls, value: Any, note: str | None = None, *evidence: Evidence) -> TruthValue:
        return cls(value=value, level=TruthLevel.INFERRED, evidence=list(evidence), note=note)

    @classmethod
    def unknown(cls, note: str | None = None) -> TruthValue:
        return cls(value=None, level=TruthLevel.UNKNOWN, note=note)

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "level": self.level.value,
            "evidence": [e.as_dict() for e in self.evidence],
            "note": self.note,
        }


@dataclass(slots=True)
class TruthClaim:
    """One assertion made by a generated document, with its provenance."""

    claim: str
    level: TruthLevel
    evidence: list[Evidence] = field(default_factory=list)
    artifact: str | None = None
    redacted: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "level": self.level.value,
            "evidence": [e.as_dict() for e in self.evidence],
            "artifact": self.artifact,
            "redacted": self.redacted,
        }


@dataclass(slots=True)
class TruthLedger:
    """Provenance for one generated artifact set.

    A package is only `truth_clean` when it contains no unsupported assertion and
    no unresolved question has been silently filled in.
    """

    claims: list[TruthClaim] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)

    def record(
        self,
        claim: str,
        level: TruthLevel,
        evidence: list[Evidence] | None = None,
        artifact: str | None = None,
    ) -> TruthClaim:
        entry = TruthClaim(
            claim=claim, level=level, evidence=list(evidence or []), artifact=artifact
        )
        self.claims.append(entry)
        return entry

    def unresolved_question(self, question: str) -> None:
        if question not in self.unresolved:
            self.unresolved.append(question)

    @property
    def unsupported(self) -> list[TruthClaim]:
        return [c for c in self.claims if c.level is TruthLevel.INFERRED and not c.evidence]

    @property
    def truth_clean(self) -> bool:
        return not self.unsupported

    def summary(self) -> dict[str, int]:
        counts = {level.value: 0 for level in TruthLevel}
        for claim in self.claims:
            counts[claim.level.value] += 1
        counts["UNRESOLVED_QUESTIONS"] = len(self.unresolved)
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "claims": [c.as_dict() for c in self.claims],
            "unresolved": list(self.unresolved),
            "summary": self.summary(),
            "truth_clean": self.truth_clean,
        }
