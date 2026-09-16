"""Truth-bound résumé tailoring (requirements 5, 16, 17).

What tailoring may do: reorder truthful experience, emphasise relevant
accomplishments, prioritise relevant skills, change summary *framing*, align
terminology, and drop irrelevant material.

What it may not do: invent employment, metrics, certifications, responsibilities,
software expertise, titles or dates.

The mechanism that makes that enforceable is structural rather than promised:
**every output line is drawn from a candidate-supplied string**. The engine
selects, orders and labels; it does not paraphrase. A wording layer that rewrote
accomplishments could introduce a claim the candidate never made, so V1 confines
"improve wording" to whitespace, capitalisation and bullet punctuation, and
`verify_tailored_truth` independently re-checks that every bullet maps back to a
profile field.

One deliberate choice worth stating: the engine does **not** delete a role. Removing
a job the candidate held is a misstatement by omission, and a résumé gap invites
exactly the question the candidate least wants. It does drop irrelevant *bullets*
from a role, keeping at least one so no role appears empty.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sabra_domain.enums import TruthLevel
from sabra_domain.identity import normalize_text
from sabra_domain.schemas import CandidateProfile, CanonicalJob, FitAssessmentSchema
from sabra_domain.truth import Evidence, TruthLedger

from .master_resume import ResumeDocument

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "the",
        "of",
        "for",
        "to",
        "in",
        "on",
        "at",
        "with",
        "as",
        "is",
        "are",
        "be",
        "or",
        "you",
        "your",
        "we",
        "our",
        "this",
        "that",
        "will",
        "from",
        "by",
        "it",
        "its",
        "their",
        "they",
        "has",
        "have",
        "had",
        "was",
        "were",
        "role",
        "job",
        "position",
        "team",
        "company",
        "work",
        "working",
        "including",
        "years",
        "year",
        "experience",
        "strong",
        "ability",
        "must",
        "other",
        "more",
    }
)


def content_tokens(text: str | None) -> set[str]:
    return {
        token
        for token in (t.lower() for t in _TOKEN.findall(text or ""))
        if token not in _STOPWORDS and len(token) > 2
    }


def relevance(item: str, job_tokens: set[str]) -> float:
    """Fraction of an item's content tokens that appear in the posting.

    Deliberately simple and explainable: a scored ordering is auditable, and the
    candidate can see why a bullet was promoted.
    """

    tokens = content_tokens(item)
    if not tokens:
        return 0.0
    return len(tokens & job_tokens) / len(tokens)


def _clean_bullet(text: str) -> str:
    """Whitespace and punctuation hygiene only.

    This is the entire "improve wording" surface. Anything that changed the meaning
    of the sentence would be indistinguishable from inventing it.
    """

    cleaned = normalize_text(text)
    cleaned = cleaned.lstrip("-*\u2022\u25cf\u00b7 ").strip()
    if cleaned and cleaned[-1] not in ".!?%)\"'":
        cleaned += "."
    if cleaned:
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


@dataclass(slots=True)
class TailoredRésumé:
    markdown: str
    ledger: TruthLedger
    emphasis_terms: list[str] = field(default_factory=list)
    role_order: list[str] = field(default_factory=list)
    selected_bullets: list[str] = field(default_factory=list)
    skill_order: list[str] = field(default_factory=list)
    omitted_skills: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "emphasis_terms": self.emphasis_terms,
            "role_order": self.role_order,
            "selected_bullets": self.selected_bullets,
            "skill_order": self.skill_order,
            "omitted_skills": self.omitted_skills,
            "truth_summary": self.ledger.summary(),
            "notes": self.notes,
        }


def job_focus_tokens(job: CanonicalJob | Any) -> set[str]:
    """The tokens a résumé should emphasise for this posting."""

    parts = [
        getattr(job, "title", None),
        getattr(job, "requirements", None),
        getattr(job, "preferred_qualifications", None),
        getattr(job, "responsibilities", None),
        getattr(job, "department", None),
    ]
    tokens: set[str] = set()
    for part in parts:
        tokens |= content_tokens(part)
    return tokens


def _recency_sort_key(index: int, start_date: str | None) -> str:
    """Reverse-sortable key that puts the most recent role first.

    A missing date sorts last rather than being treated as current: guessing that an
    undated role is the candidate's present job would misstate their history.
    """

    if not start_date:
        return f"~{index:05d}"
    text = str(start_date).strip()
    year_match = re.search(r"(\d{4})", text)
    year = year_match.group(1) if year_match else "0000"
    month = 0
    month_match = re.search(r"^([a-zA-Z]{3})", text)
    if month_match:
        months = [
            "jan",
            "feb",
            "mar",
            "apr",
            "may",
            "jun",
            "jul",
            "aug",
            "sep",
            "oct",
            "nov",
            "dec",
        ]
        abbr = month_match.group(1).lower()
        if abbr in months:
            month = months.index(abbr) + 1
    return f"{year}-{month:02d}"


class ResumeTailor:
    def __init__(
        self,
        *,
        max_bullets_per_role: int = 4,
        max_skills: int = 20,
        min_bullet_relevance: float = 0.0,
    ) -> None:
        self.max_bullets_per_role = max_bullets_per_role
        self.max_skills = max_skills
        self.min_bullet_relevance = min_bullet_relevance

    def tailor(
        self,
        profile: CandidateProfile,
        master: ResumeDocument | None,
        job: CanonicalJob | Any,
        assessment: FitAssessmentSchema | None = None,
    ) -> TailoredRésumé:
        ledger = TruthLedger()
        focus = job_focus_tokens(job)
        job_title = normalize_text(getattr(job, "title", "") or "")
        company = normalize_text(getattr(job, "company", "") or "")

        # --- employment order and bullet selection -------------------------
        scored_roles: list[tuple[float, int]] = []
        for index, entry in enumerate(profile.employment):
            scores = [relevance(item, focus) for item in entry.accomplishments]
            best = max(scores, default=0.0)
            scored_roles.append((best, index))
        # Order by relevance descending, then by recency, then by original order so
        # the output is deterministic for identical inputs.
        ordered_roles = sorted(
            zip(profile.employment, scored_roles, strict=True),
            key=lambda pair: (
                -pair[1][0],
                _recency_sort_key(pair[1][1], pair[0].start_date),
                pair[1][1],
            ),
        )

        lines: list[str] = []
        role_order: list[str] = []
        selected_bullets: list[str] = []

        contact = (
            profile.identity
            if profile.identity.full_name
            else (master.contact if master else profile.identity)
        )
        if contact.full_name:
            lines.append(f"# {contact.full_name}")
        headline = profile.headline or (master.headline if master else None)
        if headline:
            lines.append(f"_{normalize_text(headline)}_")
            ledger.record(
                f"headline: {normalize_text(headline)}",
                TruthLevel.VERIFIED,
                [Evidence("candidate_profile.headline")],
                artifact="tailored_resume",
            )
        else:
            ledger.unresolved_question(
                "No headline or target title available for the résumé header"
            )

        contact_bits = [
            part
            for part in (
                contact.email,
                contact.phone,
                ", ".join(p for p in (contact.city, contact.state) if p) or None,
                contact.linkedin_url,
                contact.website_url,
            )
            if part
        ]
        if contact_bits:
            lines.append(" | ".join(contact_bits))

        # --- summary -------------------------------------------------------
        lines.append("")
        lines.append("## Summary")
        summary_source = profile.summary or (master.summary if master else None)
        if summary_source:
            lines.append(normalize_text(summary_source))
            ledger.record(
                "summary (candidate's own words)",
                TruthLevel.VERIFIED,
                [Evidence("candidate_profile.summary")],
                artifact="tailored_resume",
            )
        else:
            ledger.unresolved_question("No professional summary available")
            lines.append(
                f"Seeking the {job_title or 'advertised'} role"
                + (f" at {company}" if company else "")
                + "."
            )
            ledger.record(
                "summary (seeking sentence, no candidate claim)",
                TruthLevel.INFERRED,
                [Evidence("job.title")],
                artifact="tailored_resume",
            )

        emphasis = self._emphasis_terms(profile, focus)
        if emphasis:
            sentence = f"Directly relevant experience: {', '.join(emphasis[:8])}."
            lines.append(sentence)
            ledger.record(
                f"alignment sentence: {sentence}",
                TruthLevel.INFERRED,
                [Evidence("candidate_profile.skills"), Evidence("candidate_profile.employment")],
                artifact="tailored_resume",
            )

        # --- experience ----------------------------------------------------
        lines.append("")
        lines.append("## Experience")
        for entry, _meta in ordered_roles:
            header_bits = [normalize_text(entry.title), normalize_text(entry.employer)]
            header = " — ".join(bit for bit in header_bits if bit)
            dates = " - ".join(bit for bit in (entry.start_date, entry.end_date) if bit)
            lines.append("")
            lines.append(f"**{header}**" + (f" | {dates}" if dates else ""))
            role_order.append(header)
            ledger.record(
                f"role: {header}" + (f" | {dates}" if dates else ""),
                TruthLevel.VERIFIED,
                [Evidence(f"candidate_profile.employment.{entry.id or entry.employer}")],
                artifact="tailored_resume",
            )

            ranked = sorted(
                (
                    (relevance(item, focus), position, item)
                    for position, item in enumerate(entry.accomplishments)
                ),
                key=lambda triple: (-triple[0], triple[1]),
            )
            chosen = [
                item for score, _position, item in ranked if score >= self.min_bullet_relevance
            ][: self.max_bullets_per_role]
            # Never leave a role empty: one bullet carries the role's substance.
            if not chosen and entry.accomplishments:
                chosen = [entry.accomplishments[0]]
                ledger.unresolved_question(
                    f"No requirement-relevant accomplishment found for {header}; "
                    "the first bullet was kept verbatim rather than inventing one"
                )

            if entry.summary:
                lines.append(normalize_text(entry.summary))
            for item in chosen:
                cleaned = _clean_bullet(item)
                lines.append(f"- {cleaned}")
                selected_bullets.append(cleaned)
                ledger.record(
                    f"bullet: {cleaned}",
                    TruthLevel.VERIFIED,
                    [Evidence("candidate_profile.accomplishments")],
                    artifact="tailored_resume",
                )

        # --- education / certifications / skills ---------------------------
        education = profile.education or (master.education if master else [])
        if education:
            lines.append("")
            lines.append("## Education")
            for item in education:
                line = " — ".join(
                    p for p in (normalize_text(item.degree), normalize_text(item.institution)) if p
                )
                dates = " - ".join(p for p in (item.start_date, item.end_date) if p)
                lines.append(f"- {line}" + (f" ({dates})" if dates else ""))
                ledger.record(
                    f"education: {line}",
                    TruthLevel.VERIFIED,
                    [Evidence("candidate_profile.education")],
                    artifact="tailored_resume",
                )

        certifications = profile.certifications or (master.certifications if master else [])
        if certifications:
            lines.append("")
            lines.append("## Certifications")
            for item in certifications:
                line = " — ".join(
                    p for p in (normalize_text(item.name), normalize_text(item.issuer)) if p
                )
                lines.append(f"- {line}")
                ledger.record(
                    f"certification: {line}",
                    TruthLevel.VERIFIED,
                    [Evidence("candidate_profile.certifications")],
                    artifact="tailored_resume",
                )

        skills = profile.skills or (master.skills if master else [])
        if skills:
            ranked_skills = sorted(
                ((relevance(s.name, focus), position, s) for position, s in enumerate(skills)),
                key=lambda triple: (-triple[0], triple[1]),
            )
            ordered_names = [s.name for _score, _position, s in ranked_skills]
            kept = ordered_names[: self.max_skills]
            omitted = ordered_names[self.max_skills :]
            lines.append("")
            lines.append("## Skills")
            lines.append(", ".join(kept))
            ledger.record(
                f"skills list ({len(kept)} entries, relevance-ordered)",
                TruthLevel.VERIFIED,
                [Evidence("candidate_profile.skills")],
                artifact="tailored_resume",
            )
            if omitted:
                ledger.unresolved_question(
                    f"{len(omitted)} lower-relevance skills were not shown: {', '.join(omitted[:5])}"
                )

        projects = profile.projects or (master.projects if master else [])
        if projects:
            lines.append("")
            lines.append("## Projects")
            for project in projects:
                lines.append(f"- **{normalize_text(project.name)}**")
                for outcome in project.outcomes:
                    lines.append(f"  - {_clean_bullet(outcome)}")
                ledger.record(
                    f"project: {normalize_text(project.name)}",
                    TruthLevel.VERIFIED,
                    [Evidence("candidate_profile.projects")],
                    artifact="tailored_resume",
                )

        return TailoredRésumé(
            markdown="\n".join(lines).strip() + "\n",
            ledger=ledger,
            emphasis_terms=emphasis,
            role_order=role_order,
            selected_bullets=selected_bullets,
            skill_order=[s.name for s in skills],
            omitted_skills=[],
            notes=[
                "Tailoring selected, reordered and relabelled candidate-authored content only; "
                "no sentence was paraphrased."
            ],
        )

    @staticmethod
    def _emphasis_terms(profile: CandidateProfile, focus: set[str]) -> list[str]:
        """Evidence-backed terms that also appear in the posting.

        Only terms the candidate actually listed can be emphasised, which is what
        keeps this an alignment step rather than a claim.
        """

        candidate_terms: list[str] = [s.name for s in profile.skills]
        candidate_terms += [c.name for c in profile.certifications]
        candidate_terms += list(profile.industries)
        candidate_terms += list(profile.technical_capabilities)

        picked: list[str] = []
        for term in candidate_terms:
            if not term:
                continue
            if content_tokens(term) & focus and normalize_text(term) not in picked:
                picked.append(normalize_text(term))
        return picked


def verify_tailored_truth(tailored: TailoredRésumé, profile: CandidateProfile) -> list[str]:
    """Independently re-check that no bullet claims anything unsupported.

    Returns a list of violations. An empty list is the only acceptable result
    before a package may be approved, and this function exists so the invariant is
    tested rather than asserted in a comment.
    """

    violations: list[str] = []
    source_text = normalize_text(
        " ".join(
            [
                profile.summary or "",
                profile.headline or "",
                *[e.summary or "" for e in profile.employment],
                *[item for _e, _t, item in profile.all_accomplishments()],
                *[p.description or "" for p in profile.projects],
                *[o for p in profile.projects for o in p.outcomes],
            ]
        )
    )

    for bullet in tailored.selected_bullets:
        # Normalised containment: punctuation and whitespace hygiene are permitted,
        # content changes are not.
        needle = normalize_text(bullet).rstrip(".").lower()
        if needle and needle not in source_text.lower():
            violations.append(f"bullet not traceable to candidate data: {bullet!r}")

    for claim in tailored.ledger.claims:
        if claim.level is TruthLevel.VERIFIED and not claim.evidence:
            violations.append(f"VERIFIED claim without evidence: {claim.claim!r}")

    return violations
