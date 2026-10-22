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
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError
from sabra_domain.enums import EmploymentType, EntityType, EventType, SourceKind, TruthLevel
from sabra_domain.errors import ConfigurationError, NotFound, ValidationFailure
from sabra_domain.identity import new_id
from sabra_domain.schemas import (
    AnswerRecord,
    CandidateProfile,
    CompensationPreference,
    LocationIntent,
    Provenance,
    SearchProfile,
)

from .context import EngineContext

SEED_SEARCH_PROFILE_ID = "srch_seed_vertical_dfw"


def load_candidate_profile(path: Path) -> CandidateProfile:
    """Read an owner-supplied candidate profile from YAML or JSON."""

    if not path.exists():
        raise ConfigurationError(f"candidate profile not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
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
            minimum=75000.0,
            target=100000.0,
            currency="USD",
            period="YEAR",
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


def missing_candidate_message(database_url: str) -> str:
    """What the CLI prints when the opened database has no candidate row."""

    return (
        f"No candidate is stored in {database_url}. "
        "Load the profile with `sabra init --profile <path-to-profile.yaml>`. "
        "When SABRA_PRIVATE_DIR is set, that database file is "
        "<private-dir>/var/sabra.db. "
        "To open a different file, set SABRA_DATABASE_URL to its sqlite+pysqlite URL."
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


def format_validation_errors(exc: ValidationError) -> list[dict[str, str]]:
    """Pydantic errors as `{field, message}` rows a person can read."""

    rows: list[dict[str, str]] = []
    for error in exc.errors():
        field = ".".join(str(part) for part in error.get("loc", ()))
        rows.append({"field": field or "(profile)", "message": str(error.get("msg", "invalid"))})
    return rows


def search_profile_from_mapping(
    raw: dict[str, Any], *, source: str = "search profile"
) -> SearchProfile:
    """Validate a search-profile mapping through the schema.

    `id` and `candidate_id` are optional in a file: a person writing a profile has no
    ids to give, so an id is generated and the candidate is attached when it is stored.
    A YAML key left blank (`excluded_titles:`) means "use the default", except
    `max_age_days`, where an explicit null means "no age limit".
    """

    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None and key != "max_age_days":
            continue
        if isinstance(value, dict):
            value = {k: v for k, v in value.items() if v is not None}
        cleaned[key] = value
    if not cleaned.get("id"):
        # Derived from the name, so loading the same file again replaces the stored
        # profile instead of adding a copy of it.
        slug = re.sub(r"[^a-z0-9]+", "_", str(cleaned.get("name", "")).lower()).strip("_")[:48]
        cleaned["id"] = f"srch_{slug}" if slug else new_id("srch")
    cleaned.setdefault("candidate_id", "")
    try:
        return SearchProfile.model_validate(cleaned)
    except ValidationError as exc:
        details = "; ".join(
            f"{row['field']}: {row['message']}" for row in format_validation_errors(exc)
        )
        raise ConfigurationError(f"{source} is not a valid search profile: {details}") from exc


def load_search_profile(path: Path) -> SearchProfile:
    """Read a search profile from YAML or JSON (same shape as `SearchProfile`)."""

    path = Path(path)
    if not path.exists():
        raise ConfigurationError(f"search profile not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
    try:
        raw = json.loads(text) if path.suffix.lower() == ".json" else yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise ConfigurationError(f"{path} is not valid {path.suffix.lstrip('.')}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError(f"{path} must contain a mapping")
    return search_profile_from_mapping(raw, source=str(path))


def ensure_search_profile(
    ctx: EngineContext,
    candidate_id: str,
    *,
    profile: SearchProfile | None = None,
    activate: bool | None = None,
) -> str:
    """The id of the candidate's active search profile.

    With no `profile`, a stored profile is returned untouched: it is never overwritten
    or re-seeded, because the person may have edited it. The seed is created only when
    the candidate has no search profile at all.

    With a `profile`, it is stored (replacing a stored profile of the same id) and made
    active when `activate` is true, or when `activate` is None and nothing is active.
    """

    with ctx.repos() as repos:
        if profile is None:
            current = repos.search_profiles.active_one(candidate_id)
            if current is not None:
                return current.id
            stored = repos.search_profiles.list_for_candidate(candidate_id)
            if stored:
                # Profiles exist but none is active (all were deactivated by hand).
                chosen = stored[-1].id
                repos.search_profiles.activate(candidate_id, chosen)
                repos.events.append(
                    EntityType.SEARCH_PROFILE,
                    chosen,
                    EventType.SEARCH_PROFILE_ACTIVATED,
                    {"reason": "no profile was active"},
                )
                return chosen
            profile = seed_search_profile(candidate_id)
            activate = True
        profile.candidate_id = candidate_id
        existed = repos.search_profiles.exists(profile.id)
        had_active = repos.search_profiles.active_one(candidate_id) is not None
        if existed:
            # Activation is its own act; storing a profile does not flip it.
            profile.is_active = repos.search_profiles.get(profile.id).is_active
        else:
            profile.is_active = False
        repos.search_profiles.upsert(profile)
        repos.events.append(
            EntityType.SEARCH_PROFILE,
            profile.id,
            EventType.SEARCH_PROFILE_UPDATED if existed else EventType.SEARCH_PROFILE_CREATED,
            {
                "name": profile.name,
                "target_titles": profile.target_titles,
                "compensation_minimum": profile.compensation.minimum,
                "travel_max_percent": profile.travel_max_percent,
            },
        )
        if activate or (activate is None and not had_active):
            repos.search_profiles.activate(candidate_id, profile.id)
            repos.events.append(
                EntityType.SEARCH_PROFILE,
                profile.id,
                EventType.SEARCH_PROFILE_ACTIVATED,
                {"name": profile.name},
            )
    return profile.id


def _profile_content(profile: SearchProfile) -> dict[str, Any]:
    """What a search says, without who it belongs to or whether it is active."""

    dump = profile.model_dump(mode="json")
    for key in ("id", "candidate_id", "is_active", "created_at"):
        dump.pop(key, None)
    return dump


def store_search_profile_file(
    ctx: EngineContext,
    candidate_id: str,
    profile: SearchProfile,
    *,
    replace: bool = False,
    activate: bool | None = True,
) -> dict[str, Any]:
    """Store a profile loaded from a file, refusing to silently replace a different one.

    A file's id (written, or derived from its name) can collide with a stored search of the
    same id. When that stored search has a different name or different content, the load
    is refused unless `replace` is true: replacing it discards what was there, and a person
    who only reused a name should not lose a search without being told. Loading a file that
    matches the stored search exactly is harmless and is not a replacement.

    Returns `{"id", "replaced"}` where `replaced` is the old name when one was replaced.
    """

    replaced: str | None = None
    with ctx.read_repos() as repos:
        stored = repos.search_profiles.get(profile.id) if repos.search_profiles.exists(
            profile.id
        ) else None
    if stored is not None and (
        stored.name != profile.name or _profile_content(stored) != _profile_content(profile)
    ):
        if not replace:
            raise ValidationFailure(
                f"a different search with id {profile.id!r} already exists "
                f"(named {stored.name!r}). Pass --replace to replace it, or give the new "
                "file its own id"
            )
        replaced = stored.name
    profile_id = ensure_search_profile(ctx, candidate_id, profile=profile, activate=activate)
    return {"id": profile_id, "replaced": replaced}


# -- owner identity -------------------------------------------------------

OWNER_IDENTITY_SOURCE = "owner_identity"
# Form field names. `street_address` has no profile field: packages read it from a
# verified `street_address` answer, so that is where it is stored.
IDENTITY_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "street_address",
    "city",
    "state",
    "postal_code",
    "country",
    "linkedin_url",
    "headline",
)


def owner_identity_view(profile: CandidateProfile) -> dict[str, str]:
    """The identity form's current values, read back from the stored profile."""

    identity = profile.identity
    street = profile.answer_memory.get("street_address")
    values = {
        "first_name": identity.first_name,
        "last_name": identity.last_name,
        "email": identity.email,
        "phone": identity.phone,
        "street_address": str(street.value) if street and street.value else None,
        "city": identity.city,
        "state": identity.state,
        "postal_code": identity.postal_code,
        "country": identity.country,
        "linkedin_url": identity.linkedin_url,
        "headline": profile.headline,
    }
    return {key: (value or "") for key, value in values.items()}


def _current_identity_value(profile: CandidateProfile, key: str) -> tuple[str, TruthLevel, list[str]]:
    """`(value, level, evidence)` for one identity-form field; level UNKNOWN when empty."""

    if key == "street_address":
        record = profile.answer_memory.get("street_address")
        if record is None or record.value in (None, ""):
            return "", TruthLevel.UNKNOWN, []
        return str(record.value).strip(), record.level, list(record.evidence)
    if key == "headline":
        value = (profile.headline or "").strip()
        path = "headline"
    else:
        value = str(getattr(profile.identity, key, None) or "").strip()
        path = f"identity.{key}"
    if not value:
        return "", TruthLevel.UNKNOWN, []
    record = profile.provenance_for(path)
    return value, record.level, list(record.evidence)


def owner_identity_levels(profile: CandidateProfile) -> dict[str, str]:
    """How well supported each identity-form value is: VERIFIED, INFERRED or UNKNOWN."""

    return {key: _current_identity_value(profile, key)[1].value for key in IDENTITY_FIELDS}


def save_owner_identity(
    ctx: EngineContext, candidate_id: str, fields: dict[str, Any]
) -> CandidateProfile:
    """Store the owner's own statements as VERIFIED, in a new profile version.

    Only what is submitted is touched; every other value keeps the level it had. A blank
    field is no statement and leaves the stored value alone. Submitting a value that equals
    the stored one is a *confirmation*: an INFERRED or unverified value becomes VERIFIED,
    and its provenance records what it was confirmed from and its previous level. Submitting
    a different value replaces it and is VERIFIED (the owner typed it). Résumé ingestion only
    replaces INFERRED values, so what is stored here wins over anything read from a résumé.
    """

    unknown = sorted(set(fields) - set(IDENTITY_FIELDS))
    if unknown:
        raise ValidationFailure(f"unknown identity field(s): {', '.join(unknown)}")
    cleaned = {
        key: str(value).strip()
        for key, value in fields.items()
        if value is not None and str(value).strip()
    }
    email = cleaned.get("email")
    if email and ("@" not in email or " " in email):
        raise ValidationFailure(f"email does not look like an email address: {email!r}")

    with ctx.repos() as repos:
        repos.candidates.get(candidate_id)
        try:
            profile = repos.candidates.active_profile(candidate_id)
        except NotFound:
            profile = CandidateProfile(candidate_id=candidate_id)

        changed: list[str] = []
        confirmed: list[str] = []
        for key, value in cleaned.items():
            current, level, evidence = _current_identity_value(profile, key)
            if value == current and level is TruthLevel.VERIFIED:
                continue  # already the owner's, nothing to do
            if value == current:
                prior = "+".join(evidence) or "unrecorded"
                provenance = Provenance(
                    level=TruthLevel.VERIFIED,
                    evidence=[OWNER_IDENTITY_SOURCE, f"confirmed_from:{prior}"],
                    note=f"confirmed by the owner; it was {level.value} before",
                )
                confirmed.append(key)
            else:
                note = "stated by the owner"
                if current:
                    note += f"; replaces a {level.value} value"
                provenance = Provenance(
                    level=TruthLevel.VERIFIED, evidence=[OWNER_IDENTITY_SOURCE], note=note
                )
                changed.append(key)
            if key == "headline":
                profile.headline = value
                profile.provenance["headline"] = provenance
            elif key == "street_address":
                profile.answer_memory["street_address"] = AnswerRecord(
                    question_key="street_address",
                    question_text="Street address",
                    value=value,
                    level=TruthLevel.VERIFIED,
                    evidence=[*provenance.evidence, "owner_verified"],
                    note=provenance.note,
                )
            else:
                setattr(profile.identity, key, value)
                profile.provenance[f"identity.{key}"] = provenance
        if not changed and not confirmed:
            return profile
        # The full name is rebuilt only when BOTH parts are the owner's after this save.
        # Changing just the first name must not vouch for a surname that is still only read
        # from the resume: the full name is what gets typed onto a form.
        if {"first_name", "last_name"} & {*changed, *confirmed}:
            first, last = profile.identity.first_name, profile.identity.last_name
            both_verified = all(
                profile.provenance_for(f"identity.{part}").level is TruthLevel.VERIFIED
                for part in ("first_name", "last_name")
            )
            if first and last and both_verified:
                profile.identity.full_name = f"{first} {last}"
                profile.provenance["identity.full_name"] = Provenance(
                    level=TruthLevel.VERIFIED,
                    evidence=[OWNER_IDENTITY_SOURCE],
                    note="built from the first and last name the owner gave",
                )
        repos.candidates.save_profile(candidate_id, profile, source=OWNER_IDENTITY_SOURCE)
        repos.events.append(
            EntityType.CANDIDATE,
            candidate_id,
            EventType.CANDIDATE_UPDATED,
            {
                "action": "owner_identity_saved",
                "fields": sorted([*changed, *confirmed]),
                "changed": sorted(changed),
                "confirmed": sorted(confirmed),
            },
        )
        return profile


# -- resume files ---------------------------------------------------------

TEXT_EXTENSIONS = (".txt", ".md")


def _docx_text(content: bytes) -> str:
    """Text of a .docx in reading order: headers, the body (paragraphs and table cells), footers.

    Contact details often live in the page header or footer, so those are read too. A header
    or footer that is linked to the previous section is the same text and is read once.
    """

    import io

    from docx import Document
    from docx.table import Table

    document = Document(io.BytesIO(content))

    def blocks_to_lines(blocks: Any) -> list[str]:
        lines: list[str] = []
        for block in blocks:
            if isinstance(block, Table):
                for row in block.rows:
                    seen: set[int] = set()
                    for cell in row.cells:
                        # A merged cell is returned once per column it spans.
                        if id(cell._tc) in seen:
                            continue
                        seen.add(id(cell._tc))
                        lines.extend(blocks_to_lines(cell.iter_inner_content()))
            else:
                text = block.text.strip()
                if text:
                    lines.append(text)
        return lines

    def part_lines(parts: list[Any]) -> list[str]:
        lines: list[str] = []
        for part in parts:
            if part.is_linked_to_previous:
                continue
            for line in blocks_to_lines(part.iter_inner_content()):
                if line not in lines:
                    lines.append(line)
        return lines

    headers: list[str] = []
    footers: list[str] = []
    for section in document.sections:
        headers.extend(
            line
            for line in part_lines(
                [section.header, section.first_page_header, section.even_page_header]
            )
            if line not in headers
        )
        footers.extend(
            line
            for line in part_lines(
                [section.footer, section.first_page_footer, section.even_page_footer]
            )
            if line not in footers
        )
    body = blocks_to_lines(document.iter_inner_content())
    return "\n".join([*headers, *body, *footers])


def _decode_text(content: bytes) -> str:
    if content.startswith(b"\xef\xbb\xbf"):
        text = content.decode("utf-8-sig")
    elif content.startswith((b"\xff\xfe", b"\xfe\xff")):
        # Windows Notepad's "Unicode" save. The BOM tells the byte order.
        try:
            text = content.decode("utf-16")
        except UnicodeDecodeError as exc:
            raise ValidationFailure("the text file looks like UTF-16 but could not be read") from exc
    else:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = content.decode("cp1252")
            except UnicodeDecodeError as exc:
                raise ValidationFailure(
                    "the text file is neither UTF-8 nor Windows-1252; "
                    "save it as UTF-8, .docx or .pdf"
                ) from exc
    if "\x00" in text:
        raise ValidationFailure(
            "the file contains binary data, so it is not a plain text resume; "
            "save it as UTF-8 text, .docx or .pdf"
        )
    return text


def extract_resume_text(filename: str | None, content: bytes) -> tuple[str, str]:
    """`(text, source_format)` for an uploaded resume: .pdf, .docx, .txt or .md.

    Raises `ValidationFailure` for a file that cannot be read (including any other file
    type, which is never guessed at as text), so the caller can answer 422 instead of
    storing a garbled resume.
    """

    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            try:
                import pymupdf
            except ImportError:  # PyMuPDF older than 1.24.3
                import fitz as pymupdf

            with pymupdf.open(stream=content, filetype="pdf") as pdf:
                return "\n".join(page.get_text() for page in pdf), "pdf"
        except Exception as exc:
            raise ValidationFailure(f"the PDF could not be read: {exc}") from exc
    if name.endswith(".docx"):
        try:
            return _docx_text(content), "docx"
        except Exception as exc:
            raise ValidationFailure(f"the Word document could not be read: {exc}") from exc
    if name.endswith(".doc"):
        raise ValidationFailure(
            "old .doc files are not supported; save the resume as .docx, .pdf or .txt"
        )
    if name.endswith(TEXT_EXTENSIONS):
        return _decode_text(content), "text"
    raise ValidationFailure(
        "only .pdf, .docx, .txt and .md resumes can be read; "
        f"{filename or 'this file'!r} is not one of them"
    )


def load_answer_memory(
    candidate_id: str,
    answers: dict[str, Any],
    *,
    verified_keys: set[str] | None = None,
) -> CandidateProfile:
    """Build AnswerRecords from owner-supplied answers (requirement 18).

    Sensitive answers are recorded but not verified unless `verified_keys` names
    them. An unverified sensitive answer is stored and will not be typed onto a
    form. Non-sensitive owner-supplied answers are verified, because the owner
    put them in the file as statements.
    """

    from sabra_documents.answers import SENSITIVE_KEYS

    profile = CandidateProfile(candidate_id=candidate_id)
    verified = verified_keys or set()
    for key, value in answers.items():
        is_sensitive = key in SENSITIVE_KEYS
        is_verified = (not is_sensitive) or key in verified
        profile.answer_memory[key] = AnswerRecord(
            question_key=key,
            value=value,
            level=TruthLevel.VERIFIED if is_verified else TruthLevel.INFERRED,
            sensitive=is_sensitive,
            user_controlled=is_sensitive and not is_verified,
            evidence=["owner_supplied"] + (["owner_verified"] if is_verified else []),
        )
    return profile


def new_source_entry(kind: SourceKind, token: str) -> str:
    """Deterministic provider id so a source keeps its identity across runs."""

    return new_id(f"src_{kind.value.lower()}_{token}")


__all__ = [
    "IDENTITY_FIELDS",
    "SEED_SEARCH_PROFILE_ID",
    "ensure_candidate",
    "ensure_search_profile",
    "extract_resume_text",
    "format_validation_errors",
    "load_answer_memory",
    "load_candidate_profile",
    "load_search_profile",
    "owner_identity_levels",
    "store_search_profile_file",
    "owner_identity_view",
    "placeholder_candidate_profile",
    "save_owner_identity",
    "search_profile_from_mapping",
    "seed_search_profile",
    "NotFound",
]
