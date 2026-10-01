"""Record and verify reusable application answers.

Recording stores the owner's words at INFERRED. Verifying promotes that stored
value to VERIFIED, which is the only level the form fillers will type. Sensitive
questions are not a special case: an unverified salary or background-check answer
blocks the package, and a verified one may complete it.
"""

from __future__ import annotations

from typing import Any

from sabra_documents.answers import SENSITIVE_KEYS
from sabra_domain.enums import EntityType, EventType, TruthLevel
from sabra_domain.errors import NotFound, ValidationFailure
from sabra_domain.schemas import AnswerRecord

from .candidate_intake import missing_candidate_message
from .context import EngineContext


class AnswerMemoryService:
    def __init__(self, ctx: EngineContext) -> None:
        self.ctx = ctx

    def list_answers(self, candidate_id: str | None = None) -> list[dict[str, Any]]:
        with self.ctx.read_repos() as repos:
            candidate = self._candidate(repos, candidate_id)
            profile = self._profile(repos, candidate.id)
            rows = []
            for key, record in sorted(profile.answer_memory.items()):
                rows.append(
                    {
                        "question_key": key,
                        "question_text": record.question_text,
                        "value": record.value,
                        "level": str(record.level),
                        "sensitive": key in SENSITIVE_KEYS or record.sensitive,
                        "verified": record.level is TruthLevel.VERIFIED,
                        "auto_fillable": record.auto_fillable,
                        "evidence": list(record.evidence),
                    }
                )
            return rows

    def record(
        self,
        key: str,
        value: str,
        *,
        question_text: str | None = None,
        candidate_id: str | None = None,
    ) -> dict[str, Any]:
        """Store an answer without authorizing it. Verification is a second step."""

        cleaned_key = key.strip()
        cleaned_value = value.strip()
        if not cleaned_key:
            raise ValidationFailure("an answer needs a question key")
        if not cleaned_value:
            raise ValidationFailure("an answer needs a value; an empty value is not recorded")
        sensitive = cleaned_key in SENSITIVE_KEYS
        with self.ctx.repos() as repos:
            candidate = self._candidate(repos, candidate_id)
            profile = self._profile(repos, candidate.id)
            previous = profile.answer_memory.get(cleaned_key)
            profile.answer_memory[cleaned_key] = AnswerRecord(
                question_key=cleaned_key,
                question_text=question_text or (previous.question_text if previous else None),
                value=cleaned_value,
                level=TruthLevel.INFERRED,
                sensitive=sensitive,
                user_controlled=sensitive,
                evidence=["owner_recorded"],
                note="recorded by the owner; not verified for auto-fill",
            )
            repos.candidates.save_profile(candidate.id, profile, source="answer_memory")
            repos.events.append(
                EntityType.CANDIDATE,
                candidate.id,
                EventType.CANDIDATE_UPDATED,
                {
                    "answer_key": cleaned_key,
                    "action": "recorded",
                    "sensitive": sensitive,
                    "level": str(TruthLevel.INFERRED),
                },
            )
            return {
                "candidate_id": candidate.id,
                "question_key": cleaned_key,
                "level": str(TruthLevel.INFERRED),
                "sensitive": sensitive,
                "verified": False,
            }

    def verify(self, key: str, *, candidate_id: str | None = None) -> dict[str, Any]:
        """Authorize a previously recorded answer for use on application forms."""

        cleaned_key = key.strip()
        with self.ctx.repos() as repos:
            candidate = self._candidate(repos, candidate_id)
            profile = self._profile(repos, candidate.id)
            record = profile.answer_memory.get(cleaned_key)
            if record is None or record.value in (None, ""):
                raise ValidationFailure(
                    f"no recorded answer exists for {cleaned_key!r}; record it before verifying"
                )
            evidence = list(record.evidence)
            if "owner_verified" not in evidence:
                evidence.append("owner_verified")
            profile.answer_memory[cleaned_key] = record.model_copy(
                update={
                    "level": TruthLevel.VERIFIED,
                    "evidence": evidence,
                    "note": "verified by the owner for auto-fill",
                }
            )
            repos.candidates.save_profile(candidate.id, profile, source="answer_memory")
            repos.events.append(
                EntityType.CANDIDATE,
                candidate.id,
                EventType.CANDIDATE_UPDATED,
                {
                    "answer_key": cleaned_key,
                    "action": "verified",
                    "sensitive": cleaned_key in SENSITIVE_KEYS or record.sensitive,
                    "level": str(TruthLevel.VERIFIED),
                },
            )
            return {
                "candidate_id": candidate.id,
                "question_key": cleaned_key,
                "level": str(TruthLevel.VERIFIED),
                "sensitive": cleaned_key in SENSITIVE_KEYS or record.sensitive,
                "verified": True,
                "value": record.value,
            }

    @staticmethod
    def _profile(repos: Any, candidate_id: str) -> Any:
        try:
            return repos.candidates.active_profile(candidate_id)
        except NotFound:
            from .candidate_intake import placeholder_candidate_profile

            return placeholder_candidate_profile(candidate_id)

    def _candidate(self, repos: Any, candidate_id: str | None) -> Any:
        if candidate_id:
            return repos.candidates.get(candidate_id)
        candidate = repos.candidates.primary()
        if candidate is None:
            raise ValidationFailure(missing_candidate_message(self.ctx.settings.database_url))
        return candidate
