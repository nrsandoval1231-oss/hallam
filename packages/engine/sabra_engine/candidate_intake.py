"""Candidate and search-profile intake.

Two entry paths, and the difference between them is the point:

* `load_candidate_profile` reads an owner-supplied file. Values in it are the
  candidate's own statements and are treated as VERIFIED.
* `bootstrap_placeholder_candidate` creates an empty, explicitly-labelled
  candidate so the discovery/ranking loop can be exercised before the owner has
  supplied personal data. It asserts nothing: every content field is UNKNOWN, and
  document generation refuses to invent the rest (requirement 5).

The seed search profile is the contract's own example intent (requirement 6 /
section 40), not invented candidate data — it is product specification.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from sabra_domain.enums import EmploymentType, EntityType, EventType, SourceKind, TruthLevel
from sabra_domain.errors import ConfigurationError, NotFound
from sabra_domain.identity import new_id
from sabra_domain.schemas import (
    AnswerRecord,
    CandidateProfile,
    CompensationPreference,
    LocationIntent,
    SearchProfile,
)

from .context import EngineContext

SEED_SEARCH_PROFILE_ID = "srch_seed_vertical_dfw"


def load_candidate_profile(path: Path) -> CandidateProfile:
    """Read an owner-supplied candidate profile from YAML or JSON."""

    if not path.exists():
        raise ConfigurationError(f"candidate profile not found: {path}")
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"{path} is not valid {path.suffix.lstrip('.')}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path} must contain a mapping")
    profile = CandidateProfile.model_validate(raw)
    # Everything the owner supplied is explicitly supported.
    for field_path in profile.populated_field_paths():
        profile.provenance.setdefault(
            field_path,
            __import__("sabra_domain.schemas", fromlist=["Provenance"]).Provenance(
                level=TruthLevel.VERIFIED, evidence=[f"candidate_profile:{path.name}"]
            ),
        )
    return profile


def candidate_profile_from_master_resume(
    candidate_id: str,
    resume_text: str,
    *,
    parsed: dict[str, Any] | None = None,
) -> CandidateProfile:
    """Build a profile from a résumé.

    Everything read out of the résumé is INFERRED: a résumé is evidence *for* the
    candidate model, not the model itself, so values it supplies can never reach
    an application as though the candidate had stated them directly.
    """

    from sabra_documents.master_resume import parse_master_resume

    document = parse_master_resume(resume_text, parsed=parsed)
    profile = CandidateProfile(
        candidate_id=candidate_id,
        identity=document.contact,
        headline=document.headline,
        summary=document.summary,
        employment=document.employment,
        education=document.education,
        certifications=document.certifications,
        skills=document.skills,
        projects=document.projects,
    )
    from sabra_domain.schemas import Provenance

    for field_path in profile.populated_field_paths():
        profile.provenance[field_path] = Provenance(
            level=TruthLevel.INFERRED,
            evidence=["master_resume"],
            note="read from the master résumé, not stated by the candidate",
        )
    return profile


def placeholder_candidate_profile(candidate_id: str) -> CandidateProfile:
    """An empty profile for pipeline bring-up. Asserts nothing about the person."""

    from sabra_domain.schemas import Provenance

    profile = CandidateProfile(candidate_id=candidate_id)
    for field_path in ("identity.full_name", "identity.email", "employment", "education"):
        profile.provenance[field_path] = Provenance(
            level=TruthLevel.UNKNOWN,
            note="no candidate data supplied yet; nothing may be inferred from this",
        )
    return profile


def seed_search_profile(candidate_id: str) -> SearchProfile:
    """The seed-vertical search intent (requirements 6 and 40)."""

    return SearchProfile(
        id=SEED_SEARCH_PROFILE_ID,
        name="Medical aesthetics commercial — DFW + remote",
        candidate_id=candidate_id,
        target_titles=[
            "Regional Sales Manager",
            "Business Development Manager",
            "Territory Manager",
            "Account Executive",
        ],
        acceptable_titles=[
            "Area Sales Manager",
            "Strategic Account Manager",
            "Area Business Manager",
            "Sales Manager",
            "Senior Territory Manager",
            "Territory Business Manager",
            "Director of Sales",
        ],
        excluded_titles=[
            "Intern",
            "Internship",
            "Entry Level",
            "Warehouse",
            "Machine Operator",
            "Technician",
            "Nurse",
            "Recruiter",
            "Engineer",
            "Software",
            "Data Scientist",
        ],
        # Declared role families and level markers. Real postings do not reuse the
        # target titles verbatim ("Regional Account Director", "Clinical Sales
        # Exec"), so intent screening needs the candidate's own description of what
        # counts as a commercial role rather than a table inside the scorer.
        role_family_terms=[
            "sales",
            "account",
            "territory",
            "business development",
            "partnerships",
            "commercial",
            "revenue",
            "distributor",
            "alliance",
            "client",
        ],
        role_level_terms=[
            "manager",
            "director",
            "executive",
            "specialist",
            "representative",
            "consultant",
            "vice president",
            "vp",
            "head",
            "lead",
            "coordinator",
            "officer",
        ],
        industries=[
            "Medical aesthetics",
            "Skincare",
            "Medical devices",
            "Beauty technology",
        ],
        excluded_industries=["MLM", "Multi-level marketing"],
        locations=LocationIntent(
            cities=["Dallas", "Fort Worth", "Arlington", "Plano", "Irving", "Frisco"],
            states=["TX"],
            metro_areas=["Dallas-Fort Worth"],
            allow_remote=True,
            allow_hybrid=True,
            allow_onsite=True,
            allow_relocation=False,
        ),
        compensation=CompensationPreference(
            minimum=75000.0, target=100000.0, currency="USD", period="YEAR",
            accepts_commission=True,
        ),
        travel_max_percent=40,
        employment_types=[EmploymentType.FULL_TIME],
        excluded_employment_types=[EmploymentType.PART_TIME, EmploymentType.INTERNSHIP],
        # A title with no declared seniority is not excluded: `None` from the
        # seniority parser means "the title does not say", not "entry level".
        seniority_levels=["SENIOR", "MANAGER", "DIRECTOR", "ENTRY", "UNKNOWN"],
        excluded_seniority_levels=["INTERN", "ENTRY"],
        excluded_keywords=[
            "commission only",
            "100% commission",
            "MLM",
            "multi-level marketing",
            "1099 only",
            "unpaid",
        ],
        minimum_qualifications=[],
        allow_commission_only=False,
        max_age_days=120,
        source_filter=[],
        shortlist_target=10,
        prepare_target=3,
        created_at=datetime.now(UTC),
    )


def ensure_candidate(
    ctx: EngineContext,
    *,
    profile_path: Path | None = None,
    display_name: str | None = None,
    create_placeholder: bool = True,
) -> str:
    """Return a candidate id, creating one if necessary.

    Re-running against the same profile path is idempotent: the profile is
    versioned rather than duplicated, so the same person does not accumulate
    parallel candidate rows.
    """

    with ctx.repos() as repos:
        existing = repos.candidates.primary()
        if existing is not None:
            candidate_id = existing.id
        else:
            row = repos.candidates.create(display_name)
            candidate_id = row.id
            repos.events.append(
                EntityType.CANDIDATE,
                candidate_id,
                EventType.CANDIDATE_UPDATED,
                {"created": True, "display_name": display_name},
            )

    if profile_path is not None:
        profile = load_candidate_profile(profile_path)
        profile.candidate_id = candidate_id
        with ctx.repos() as repos:
            repos.candidates.save_profile(candidate_id, profile, source=profile_path.name)
        return candidate_id

    if not create_placeholder:
        return candidate_id

    with ctx.repos() as repos:
        latest = repos.candidates.latest_profile_row(candidate_id)
        if latest is None:
            repos.candidates.save_profile(
                candidate_id, placeholder_candidate_profile(candidate_id), source="placeholder"
            )
            repos.events.append(
                EntityType.CANDIDATE,
                candidate_id,
                EventType.CANDIDATE_UPDATED,
                {
                    "placeholder": True,
                    "note": "no candidate data supplied; profile asserts nothing",
                },
            )
    return candidate_id


def ensure_search_profile(
    ctx: EngineContext, candidate_id: str, *, profile: SearchProfile | None = None
) -> str:
    profile = profile or seed_search_profile(candidate_id)
    profile.candidate_id = candidate_id
    with ctx.repos() as repos:
        repos.search_profiles.upsert(profile)
        repos.events.append(
            EntityType.SEARCH_PROFILE,
            profile.id,
            EventType.SEARCH_PROFILE_CREATED,
            {
                "name": profile.name,
                "target_titles": profile.target_titles,
                "compensation_minimum": profile.compensation.minimum,
                "travel_max_percent": profile.travel_max_percent,
            },
        )
    return profile.id


def load_answer_memory(candidate_id: str, answers: dict[str, Any]) -> CandidateProfile:
    """Build AnswerRecords from owner-supplied answers (requirement 18).

    Sensitive and voluntary disclosures are marked `user_controlled` and are never
    auto-filled, regardless of how complete they are.
    """

    sensitive_keys = {
        "veteran_status",
        "disability_status",
        "gender",
        "race",
        "ethnicity",
        "date_of_birth",
        "criminal_history",
        "background_check",
    }
    profile = CandidateProfile(candidate_id=candidate_id)
    for key, value in answers.items():
        is_sensitive = key in sensitive_keys
        profile.answer_memory[key] = AnswerRecord(
            question_key=key,
            value=value,
            level=TruthLevel.VERIFIED,
            sensitive=is_sensitive,
            user_controlled=is_sensitive,
            evidence=["owner_supplied"],
        )
    return profile


def new_source_entry(kind: SourceKind, token: str) -> str:
    """Deterministic provider id so a source keeps its identity across runs."""

    return new_id(f"src_{kind.value.lower()}_{token}")


__all__ = [
    "SEED_SEARCH_PROFILE_ID",
    "ensure_candidate",
    "ensure_search_profile",
    "load_answer_memory",
    "load_candidate_profile",
    "placeholder_candidate_profile",
    "seed_search_profile",
    "NotFound",
]
