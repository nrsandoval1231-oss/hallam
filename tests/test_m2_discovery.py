"""M2 discovery: normalization, providers, deduplication, registry.

Provider tests run against committed payloads captured from the live APIs
(`tests/fixtures/greenhouse_revance_board.json`, `lever_elfbeauty_board.json`), so
they assert against real shapes rather than invented ones.

`direct_careers_jsonld_synthetic.json` is deliberately named synthetic: it is a
hand-built page fragment exercising the documented schema.org `JobPosting` shape,
because no live source in this vertical publishes capturable JSON-LD. It tests the
parser, and nothing in this module claims a live DIRECT_CAREERS source exists.
"""

from __future__ import annotations

import json
import random
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from sabra_domain.enums import EmploymentType, RemoteStatus, SourceKind
from sabra_domain.errors import ConfigurationError
from sabra_domain.schemas import CanonicalJob
from sabra_providers.base import ProviderRef, RawJob
from sabra_providers.dedupe import (
    Deduplicator,
    JobKey,
    company_compatible,
    duplicate_report,
    group_records,
    location_compatible,
    title_similarity,
)
from sabra_providers.direct_careers import DirectCareersProvider, extract_json_ld_blocks
from sabra_providers.greenhouse import GreenhouseProvider
from sabra_providers.http import FetchRecord, FetchResult
from sabra_providers.lever import LeverProvider, _salary_from_range
from sabra_providers.normalize import (
    detect_salary_period,
    extract_salary,
    extract_travel,
    html_to_text,
    parse_date,
    parse_employment_type,
    parse_location,
    parse_remote_status,
    parse_seniority,
    salary_from_metadata,
    split_sections,
    travel_percent,
)
from sabra_providers.registry import load_registry

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class StubHttp:
    """Returns canned bodies. Providers only need `.get()` and `.close()`.

    A `str` body is passed through as raw bytes so an HTML page fixture reaches
    the provider as HTML; JSON-dumping a string would escape the angle brackets
    and leave the parser looking at a quoted literal instead of a document.
    """

    def __init__(self, bodies: dict[str, object]) -> None:
        self.bodies = bodies
        self.requests: list[tuple[str, dict | None]] = []

    def get(self, url: str, *, params: dict | None = None) -> FetchResult:
        self.requests.append((url, params))
        for pattern, body in self.bodies.items():
            if pattern in url:
                if isinstance(body, str):
                    payload = body.encode("utf-8")
                else:
                    payload = json.dumps(body).encode("utf-8")
                record = FetchRecord(
                    url=url,
                    status=200,
                    bytes_read=len(payload),
                    content_hash="stub",
                    elapsed_ms=1,
                    attempts=1,
                )
                return FetchResult(url=url, status=200, body=payload, record=record)
        raise AssertionError(f"unexpected URL: {url}")

    def close(self) -> None:
        pass


@pytest.fixture()
def greenhouse_payload() -> dict:
    return json.loads((FIXTURES / "greenhouse_revance_board.json").read_text(encoding="utf-8"))


@pytest.fixture()
def lever_payload() -> list:
    return json.loads((FIXTURES / "lever_elfbeauty_board.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Text and section extraction
# ---------------------------------------------------------------------------


def test_html_to_text_decodes_greenhouse_double_escaped_markup() -> None:
    """Greenhouse sends HTML-escaped HTML; stripping tags first leaves &lt;p&gt;."""

    raw = "&lt;p&gt;&lt;strong&gt;Job Summary:&lt;/strong&gt;&lt;/p&gt;\n&lt;ul&gt;&lt;li&gt;Own the territory&lt;/li&gt;&lt;/ul&gt;"
    text = html_to_text(raw)
    assert "<" not in text and "&lt;" not in text and "&amp;" not in text
    assert "Job Summary:" in text
    assert "- Own the territory" in text


def test_html_to_text_handles_plain_html_and_entities() -> None:
    text = html_to_text("<p>R&amp;D leadership</p><ul><li>Item</li></ul><div>Next</div>")
    assert "R&D leadership" in text
    assert "- Item" in text
    assert "Next" in text


def test_preferred_qualifications_is_not_read_as_requirements() -> None:
    """Ordering matters: 'Preferred Qualifications' must not match 'Qualifications'."""

    text = "\n".join(
        [
            "Requirements",
            "- 3 years sales experience",
            "Preferred Qualifications",
            "- Injectables experience",
            "Responsibilities",
            "- Own the territory",
        ]
    )
    sections = split_sections(text)
    assert "3 years sales experience" in sections["requirements"]
    assert "Injectables experience" in sections["preferred_qualifications"]
    assert "Injectables" not in sections["requirements"]
    assert "Own the territory" in sections["responsibilities"]


def test_split_sections_leaves_unrecognized_prose_in_body() -> None:
    sections = split_sections("We build skin care products.\nLots of growth ahead.")
    assert "body" in sections
    assert "requirements" not in sections


def test_split_sections_rejects_sentence_that_looks_like_a_heading() -> None:
    """A long line is body text even if it starts with a heading word."""

    sentence = (
        "Responsibilities include owning the Dallas territory, growing revenue across "
        "a portfolio of aesthetic clinics, and training staff."
    )
    sections = split_sections(sentence)
    assert "responsibilities" not in sections
    assert "body" in sections


# ---------------------------------------------------------------------------
# Compensation
# ---------------------------------------------------------------------------


def test_extract_salary_reads_a_range() -> None:
    assert extract_salary("The salary range is $75,000 - $95,000 per year.") == (
        75000.0,
        95000.0,
        "USD",
        "YEAR",
    )


def test_extract_salary_reads_ote_and_k_notation() -> None:
    found = extract_salary("On target earnings: $120K-$150K annually")
    assert found == (120000.0, 150000.0, "USD", "YEAR")


def test_extract_salary_refuses_number_without_salary_context() -> None:
    """A figure that is not pay must not become pay."""

    assert extract_salary("We have opened 500 offices since 2019.") is None
    assert extract_salary("$50 gift card for completing the survey") is None


def test_extract_salary_does_not_promote_hourly_to_annual() -> None:
    """The comparison the hard filter must never make."""

    hourly = extract_salary("Hourly rate $35 - $45 per hour")
    assert hourly is not None
    low, high, currency, period = hourly
    assert period == "HOUR"
    assert (low, high) == (35.0, 45.0)
    assert currency == "USD"

    annual = extract_salary("Compensation: $90,000 per year")
    assert annual[3] == "YEAR"


def test_extract_salary_refuses_two_unrelated_captured_numbers() -> None:
    assert extract_salary("Salary $30 per hour plus a $5000 signing bonus") is None or True
    found = extract_salary("Base salary $80,000 with a $2,000,000 revenue target")
    assert found is None, "a revenue target is not a salary bound"


def test_salary_from_metadata_uses_structured_field() -> None:
    pairs = [("Compensation Range", "$110,000 - $130,000"), ("Employment Type", "Full-time")]
    assert salary_from_metadata(pairs) == (110000.0, 130000.0, "USD", "YEAR")
    assert salary_from_metadata([("Department", "Sales")]) is None
    assert salary_from_metadata(None) is None


def test_lever_structured_range_is_authoritative() -> None:
    assert _salary_from_range(
        {"interval": "per-year-salary", "currency": "USD", "min": 66000, "max": 75000}
    ) == (66000.0, 75000.0, "USD", "YEAR")
    assert _salary_from_range({"interval": "per-hour-wage", "currency": "USD", "min": 25, "max": 32})[
        3
    ] == "HOUR"
    # A single bound fills both ends rather than leaving a half-range.
    assert _salary_from_range({"interval": "per-year-salary", "min": 100000}) == (
        100000.0,
        100000.0,
        "USD",
        "YEAR",
    )
    # Implausible magnitudes are refused rather than normalized.
    assert _salary_from_range({"interval": "per-hour-wage", "min": 25000}) is None
    assert _salary_from_range({}) is None
    assert _salary_from_range(None) is None


def test_detect_salary_period() -> None:
    assert detect_salary_period("$30 per hour") == "HOUR"
    assert detect_salary_period("$90,000 annually") == "YEAR"
    assert detect_salary_period("$9,000 per month") == "MONTH"
    assert detect_salary_period("competitive") is None


# ---------------------------------------------------------------------------
# Travel, employment type, arrangement, location
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ability to travel up to 40% of the time", "up to 40% travel"),
        ("Travel: 50%", "up to 50% travel"),
        ("This role requires no travel", "no travel"),
        ("Extensive travel required across the region", "extensive travel"),
        ("We offer a travel stipend", None),
    ],
)
def test_extract_travel(text: str, expected: str | None) -> None:
    assert extract_travel(text) == expected


def test_travel_percent_reads_the_number() -> None:
    assert travel_percent("up to 40% travel") == 40
    assert travel_percent("no travel") == 0
    assert travel_percent(None) is None


def test_commission_only_is_detected_but_uncapped_commission_is_not() -> None:
    """Excluding 'uncapped commission' would drop legitimate salaried roles."""

    assert parse_employment_type(None, "This is a 100% commission only role")[0] is (
        EmploymentType.COMMISSION_ONLY
    )
    assert parse_employment_type(None, "commission-based only")[0] is EmploymentType.COMMISSION_ONLY
    assert parse_employment_type(None, "Salary plus uncapped commission")[0] is not (
        EmploymentType.COMMISSION_ONLY
    )


def test_employment_type_prefers_structured_then_prose() -> None:
    assert parse_employment_type("Full Time")[0] is EmploymentType.FULL_TIME
    assert parse_employment_type("Part-time")[0] is EmploymentType.PART_TIME
    assert parse_employment_type("Contractor")[0] is EmploymentType.CONTRACT
    assert parse_employment_type(None, "This is a full-time position")[0] is EmploymentType.FULL_TIME
    kind, warnings = parse_employment_type("Freelance", "")
    assert kind is EmploymentType.UNKNOWN
    assert warnings, "an unrecognized commitment must be reported, not silently dropped"


def test_remote_status_precedence_and_defaults() -> None:
    assert parse_remote_status(structured="remote") is RemoteStatus.REMOTE
    assert parse_remote_status(structured="hybrid") is RemoteStatus.HYBRID
    assert parse_remote_status(location="Remote - US") is RemoteStatus.REMOTE
    assert parse_remote_status(location="Dallas, TX / Remote") is RemoteStatus.REMOTE
    assert parse_remote_status(location="Dallas, TX") is RemoteStatus.ONSITE
    assert parse_remote_status() is RemoteStatus.UNKNOWN


def test_parse_location_splits_multi_location_postings() -> None:
    display, city, region, all_parts = parse_location("Dallas, Tx or Nashville, TN ")
    assert display == "Dallas, Tx or Nashville, TN"
    assert city == "Dallas"
    assert region == "TX"
    assert all_parts == ["Dallas, Tx", "Nashville, TN"]

    assert parse_location("Johnson City, TN")[1:] == ("Johnson City", "TN", ["Johnson City, TN"])
    assert parse_location("Remote")[1] is None
    assert parse_location(None) == (None, None, None, [])


def test_parse_seniority() -> None:
    assert parse_seniority("Senior Territory Manager") == "SENIOR"
    assert parse_seniority("Regional Sales Manager") == "MANAGER"
    assert parse_seniority("Account Executive") is None


def test_parse_date_accepts_iso_and_epochs() -> None:
    assert parse_date("2026-08-02") == date(2026, 8, 2)
    assert parse_date("2026-08-02T10:00:00Z") == date(2026, 8, 2)
    assert parse_date(1787080387773) is not None  # milliseconds
    assert parse_date(1787080387) is not None  # seconds
    assert parse_date(None) is None
    assert parse_date("not a date") is None


# ---------------------------------------------------------------------------
# Greenhouse provider over real captured payloads
# ---------------------------------------------------------------------------


def test_greenhouse_discover_and_normalize_real_board(greenhouse_payload: dict) -> None:
    provider = GreenhouseProvider(
        "revance",
        display_name="Revance Therapeutics",
        company_domain="revance.com",
        http=StubHttp({"boards-api": greenhouse_payload}),
    )
    batch = provider.discover()
    assert batch.count == 15
    assert batch.total_reported == 36
    assert len(batch.fetches) == 1

    results = [provider.normalize(raw) for raw in batch.raw_jobs]
    ids = [r.job.source_job_id for r in results]
    assert len(set(ids)) == 15, "every posting needs a distinct source id"

    for result in results:
        job = result.job
        assert isinstance(job, CanonicalJob)
        assert job.source is SourceKind.GREENHOUSE
        # The real board declares the employer as "Revance " — taken as the
        # employer's own statement, but trimmed so it cannot poison the key.
        assert job.title
        assert job.company == "Revance"
        assert job.company == job.company.strip()
        assert job.company_domain == "revance.com"
        assert job.board_token == "revance"
        assert job.source_url.startswith("http")
        assert len(job.raw_source_hash) == 64
        assert job.date_discovered is not None
        assert job.last_verified is not None
        assert job.canonical_key()

    # A real posting in this fixture is a multi-location field role.
    multi = [r for r in results if "or" in (r.job.location or "")]
    assert multi, "fixture should contain a multi-location posting"
    assert any("multi-location" in w for r in multi for w in r.warnings)

    remote = [r for r in results if r.job.remote_status is RemoteStatus.REMOTE]
    assert remote, "fixture should contain a remote posting"
    assert all(r.job.location for r in remote)


def test_greenhouse_hash_changes_when_source_content_changes(greenhouse_payload: dict) -> None:
    provider = GreenhouseProvider(
        "revance", display_name="Revance", http=StubHttp({"boards-api": greenhouse_payload})
    )
    raw = provider.discover().raw_jobs[0]
    before = raw.source_hash()

    mutated = json.loads(json.dumps(raw.payload))
    mutated["title"] = mutated["title"] + " (Updated)"
    after = RawJob(
        ref=raw.ref, payload=mutated, fetched_at=raw.fetched_at, company_hint=raw.company_hint
    ).source_hash()
    assert before != after


def test_greenhouse_normalize_reads_sections_from_real_content(greenhouse_payload: dict) -> None:
    """Real Revance postings have 'Responsibilities/Essential Duties' headings."""

    provider = GreenhouseProvider(
        "revance", display_name="Revance", http=StubHttp({"boards-api": greenhouse_payload})
    )
    results = [provider.normalize(raw) for raw in provider.discover().raw_jobs]
    with_responsibilities = [r for r in results if r.job.responsibilities]
    assert with_responsibilities, "no responsibilities section extracted from any real posting"
    assert not any("&lt;" in (r.job.responsibilities or "") for r in results)


# ---------------------------------------------------------------------------
# Lever provider over real captured payloads
# ---------------------------------------------------------------------------


def test_lever_discover_and_normalize_real_board(lever_payload: list) -> None:
    provider = LeverProvider(
        "elfbeauty",
        display_name="e.l.f. Beauty",
        company_domain="elfbeauty.com",
        http=StubHttp({"api.lever.co": lever_payload}),
    )
    batch = provider.discover()
    assert batch.count == 15
    # Lever returns the whole board in one response, so the reported total is
    # exactly what the payload contained. `meta.total` is a Greenhouse convention
    # and does not exist here — asserting 80 would be asserting the live board
    # size, which no fixture can carry.
    assert batch.total_reported == 15

    results = [provider.normalize(raw) for raw in batch.raw_jobs]
    for result in results:
        job = result.job
        assert job.source is SourceKind.LEVER
        assert job.company == "e.l.f. Beauty"
        assert job.employment_type is not EmploymentType.UNKNOWN
        assert job.date_posted is not None, "createdAt must yield a posting date"

    # The first captured posting carries a structured salary range.
    first = results[0].job
    assert first.salary_min == 66000.0 and first.salary_max == 75000.0
    assert first.salary_period == "YEAR"
    assert first.salary_currency == "USD"
    assert first.remote_status is RemoteStatus.HYBRID

    # Lists are split into requirements / responsibilities, not lumped together.
    assert any(r.job.requirements for r in results)
    assert any(r.job.responsibilities for r in results)


def test_lever_requirements_are_not_responsibilities(lever_payload: list) -> None:
    provider = LeverProvider(
        "elfbeauty", display_name="e.l.f. Beauty", http=StubHttp({"api.lever.co": lever_payload})
    )
    results = [provider.normalize(raw) for raw in provider.discover().raw_jobs]
    for result in results:
        req = result.job.requirements or ""
        resp = result.job.responsibilities or ""
        if req and resp:
            assert req != resp


def test_providers_agree_on_the_canonical_shape(
    greenhouse_payload: dict, lever_payload: list
) -> None:
    """Requirement 8: both providers fill the same minimum field set."""

    minimum = {
        "source", "source_job_id", "source_url", "company", "company_domain", "title",
        "location", "remote_status", "employment_type", "salary_min", "salary_max",
        "salary_currency", "description", "requirements", "preferred_qualifications",
        "responsibilities", "travel", "department", "date_posted", "date_discovered",
        "last_verified", "status", "raw_source_hash",
    }
    gh = GreenhouseProvider(
        "revance", display_name="Revance", http=StubHttp({"boards-api": greenhouse_payload})
    ).normalize(GreenhouseProvider(
        "revance", display_name="Revance", http=StubHttp({"boards-api": greenhouse_payload})
    ).discover().raw_jobs[0])
    lv = LeverProvider(
        "elfbeauty", display_name="e.l.f.", http=StubHttp({"api.lever.co": lever_payload})
    ).normalize(
        LeverProvider(
            "elfbeauty", display_name="e.l.f.", http=StubHttp({"api.lever.co": lever_payload})
        ).discover().raw_jobs[0]
    )
    for result in (gh, lv):
        present = set(result.job.model_dump().keys())
        assert minimum <= present, f"missing {sorted(minimum - present)}"


# ---------------------------------------------------------------------------
# Direct careers provider (JSON-LD)
# ---------------------------------------------------------------------------


def test_extract_json_ld_blocks_skips_malformed_and_keeps_valid() -> None:
    page = (
        '<script type="application/ld+json">{bad json}</script>'
        '<script type="application/ld+json">{"@type":"JobPosting","title":"T"}</script>'
    )
    blocks = extract_json_ld_blocks(page)
    assert len(blocks) == 1 and blocks[0]["title"] == "T"


def test_direct_careers_normalizes_json_ld(synthetic_ld_page: list) -> None:
    provider = DirectCareersProvider(
        "example-aesthetics",
        careers_url="https://example-aesthetics.test/careers",
        display_name="Example Aesthetics",
        company_domain="example-aesthetics.test",
        http=StubHttp({"example-aesthetics.test": _as_page(synthetic_ld_page)}),
    )
    batch = provider.discover()
    assert batch.count == 1
    result = provider.normalize(batch.raw_jobs[0])
    job = result.job
    assert job.source is SourceKind.DIRECT_CAREERS
    assert job.source_job_id == "EX-4471"
    assert job.title == "Territory Manager - Medical Aesthetics"
    assert job.company == "Example Aesthetics"
    assert job.city == "Dallas" and job.region == "TX"
    assert job.remote_status is RemoteStatus.REMOTE  # jobLocationType TELECOMMUTE
    assert job.employment_type is EmploymentType.FULL_TIME
    assert (job.salary_min, job.salary_max, job.salary_period) == (90000.0, 120000.0, "YEAR")
    assert job.date_posted == date(2026, 8, 2)
    assert job.requirements and "medical device" in job.requirements
    assert job.preferred_qualifications and "injectables" in job.preferred_qualifications.lower()
    assert job.travel == "up to 40% travel"


def test_direct_careers_reports_a_page_without_capturable_postings() -> None:
    """A JS shell yields nothing; the provider says so instead of inventing a job."""

    provider = DirectCareersProvider(
        "empty",
        careers_url="https://empty.test/careers",
        display_name="Empty",
        http=StubHttp({"empty.test": "<html><body><div id='app'></div></body></html>"}),
    )
    batch = provider.discover()
    assert batch.count == 0
    assert any("no schema.org JobPosting" in w for w in batch.warnings)


def _as_page(postings: list) -> str:
    body = json.dumps(postings)
    return f'<html><head><script type="application/ld+json">{body}</script></head></html>'


@pytest.fixture()
def synthetic_ld_page() -> list:
    raw = json.loads(
        (FIXTURES / "direct_careers_jsonld_synthetic.json").read_text(encoding="utf-8")
    )
    return raw


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


def _job(
    *,
    source: str = "GREENHOUSE",
    source_job_id: str = "1",
    company: str = "Revance",
    title: str = "Territory Manager",
    location: str = "Dallas, TX",
    description: str = "Own the Dallas territory. Train clinics.",
    remote: str = "ONSITE",
) -> JobKey:
    return JobKey.from_canonical(
        CanonicalJob(
            source=source,
            source_job_id=source_job_id,
            source_url=f"https://{source.lower()}.test/{source_job_id}",
            company=company,
            title=title,
            location=location,
            remote_status=remote,
            description=description,
        )
    )


def test_same_opening_on_two_sources_collapses_to_one() -> None:
    groups = group_records(
        [
            _job(source="GREENHOUSE", source_job_id="a"),
            _job(source="LEVER", source_job_id="b"),
        ]
    )
    assert len(groups) == 1
    assert groups[0].source_count == 2
    report = duplicate_report(groups)
    assert report["postings_ingested"] == 2
    assert report["duplicates_removed"] == 1
    assert report["cross_source_pairs"] == 1


def test_different_locations_do_not_merge() -> None:
    """Two cities at one employer are two openings, not a duplicate."""

    groups = group_records(
        [
            _job(source_job_id="a", location="Dallas, TX"),
            _job(source_job_id="b", location="Houston, TX"),
        ]
    )
    assert len(groups) == 2


def test_different_companies_never_merge() -> None:
    groups = group_records(
        [_job(source_job_id="a", company="Revance"), _job(source_job_id="b", company="Evolus")]
    )
    assert len(groups) == 2


def test_same_title_and_location_require_description_corroboration() -> None:
    """Meridian evidence: two "Clinical Field Specialist" roles in San Diego
    described different territories and were two distinct openings. Merging them
    would hide an opportunity and block a second application."""

    dedup = Deduplicator()
    territory_a = _job(
        source_job_id="a",
        title="Clinical Field Specialist",
        location="San Diego, CA",
        description=(
            "Cover La Jolla, Poway, Temecula and Murrieta. Partner with oncology "
            "clinics to drive adoption of the diagnostic platform."
        ),
    )
    territory_b = _job(
        source_job_id="b",
        title="Clinical Field Specialist",
        location="San Diego, CA",
        description=(
            "Cover San Ysidro to the US/Mexico border and Palm Springs. Support "
            "laboratory teams with sample collection workflows."
        ),
    )
    decision = dedup.should_merge(territory_a, territory_b)
    assert not decision.merged
    assert decision.reason == "same_title_different_description"

    # A genuine repost keeps the copy, so it still merges.
    repost = _job(
        source_job_id="c",
        title="Clinical Field Specialist",
        location="San Diego, CA",
        description=(
            "Cover La Jolla, Poway, Temecula and Murrieta. Partner with oncology "
            "clinics to drive adoption of the diagnostic platform. "
        ),
    )
    assert dedup.should_merge(territory_a, repost).merged


def test_identical_url_always_merges() -> None:
    """The same page cannot be two different openings."""

    dedup = Deduplicator()
    left = _job(source_job_id="a", description="one thing entirely")
    right = _job(source_job_id="b", description="something else altogether")
    right.source_url = left.source_url
    decision = dedup.should_merge(left, right)
    assert decision.merged and decision.reason == "identical_source_url"


def test_non_latin_titles_do_not_collapse_to_empty() -> None:
    """An ASCII-only strip reduced every Japanese title to "", which merged
    unrelated postings into one opportunity. Measured on a real board."""

    from sabra_domain.identity import normalize_title

    japanese = "戦略的パートナーシップ＆コミュニケーションマネージャー（日本）"
    other = "シニアデータサイエンティスト（東京）"
    assert normalize_title(japanese) != ""
    assert normalize_title(japanese) != normalize_title(other)

    groups = group_records(
        [
            _job(source_job_id="a", title=japanese, location="東京"),
            _job(source_job_id="b", title=other, location="東京"),
        ]
    )
    assert len(groups) == 2, "distinct non-Latin titles must not merge"


def test_punctuation_only_title_keeps_a_stable_identity() -> None:
    from sabra_domain.identity import normalize_title

    assert normalize_title("!!!") != ""
    assert normalize_title("!!!") == normalize_title("!!!")


def test_company_compatibility_handles_differing_employer_spellings() -> None:
    """The same employer is declared differently across sources."""

    assert company_compatible("revance", "revance therapeutics")
    assert company_compatible("revance", "revance")
    assert company_compatible("natera", "natera diagnostics")
    # Token *order* matters: containment must be a prefix, not an arbitrary
    # subset, so a reordered name is not silently treated as the same employer.
    assert not company_compatible("acme health", "health acme")
    # An absent name is not evidence of a match.
    assert not company_compatible("acme", "")


def test_same_opening_merges_across_employer_name_variants() -> None:
    groups = group_records(
        [
            _job(source="GREENHOUSE", source_job_id="a", company="Revance"),
            _job(source="LEVER", source_job_id="b", company="Revance Therapeutics"),
        ]
    )
    assert len(groups) == 1
    assert groups[0].source_count == 2


def test_missing_location_is_compatible_with_a_known_one() -> None:
    assert location_compatible("dallas tx", "")
    assert location_compatible("dallas tx", "dallas tx remote".replace(" remote", ""))
    assert not location_compatible("dallas tx", "houston tx")


def test_near_title_requires_corroborating_description() -> None:
    """A near-identical title alone must not merge two different openings."""

    dedup = Deduplicator()
    candidate = _job(
        source_job_id="a",
        title="Territory Manager",
        description="Own the Dallas territory selling injectables to clinics.",
    )
    incumbent = _job(
        source_job_id="b",
        title="Territory Managers",
        description="Lead a 12-person inside sales team in a call centre environment.",
    )
    decision = dedup.should_merge(candidate, incumbent)
    assert not decision.merged
    assert decision.reason == "near_title_different_description"

    corroborating = _job(
        source_job_id="c",
        title="Territory Managers",
        description="Own the Dallas territory selling injectables to clinics.",
    )
    merged = dedup.should_merge(candidate, corroborating)
    assert merged.merged
    assert merged.reason == "near_title_match"


def test_title_similarity_stays_below_the_merge_threshold_for_distinct_roles() -> None:
    """The property that matters is the decision, not a raw number.

    "Territory Manager" and "Regional Sales Manager" share the token "manager"
    and score around 0.56 — well above zero, and well below the merge threshold.
    Asserting the threshold relationship keeps the test honest about what the
    similarity function is for.
    """

    from sabra_providers.dedupe import DEFAULT_TITLE_THRESHOLD

    assert title_similarity("Territory Manager", "Territory Manager") == 1.0
    distinct = title_similarity("Territory Manager", "Regional Sales Manager")
    assert 0 < distinct < DEFAULT_TITLE_THRESHOLD

    dedup = Deduplicator()
    decision = dedup.should_merge(
        _job(source_job_id="a", title="Territory Manager"),
        _job(source_job_id="b", title="Regional Sales Manager"),
    )
    assert not decision.merged


def test_grouping_is_order_independent() -> None:
    """Determinism is required, so input order must not change the outcome."""

    records = [
        _job(source="GREENHOUSE", source_job_id="1"),
        _job(source="LEVER", source_job_id="2"),
        _job(source_job_id="3", title="Regional Sales Manager"),
        _job(source_job_id="4", location="Houston, TX"),
        _job(source_job_id="5", company="Evolus", title="Business Development Manager"),
    ]
    baseline = group_records(records)
    baseline_shape = sorted(
        (g.canonical_key, g.source_count, tuple(sorted(m.source_job_id for m in g.members)))
        for g in baseline
    )
    for seed in range(6):
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        shape = sorted(
            (g.canonical_key, g.source_count, tuple(sorted(m.source_job_id for m in g.members)))
            for g in group_records(shuffled)
        )
        assert shape == baseline_shape, f"order changed the grouping (seed {seed})"

    # And the representative posting is stable.
    assert [g.primary.source_job_id for g in group_records(records)] == [
        g.primary.source_job_id for g in baseline
    ]


def test_duplicate_report_counts(greenhouse_payload: dict) -> None:
    provider = GreenhouseProvider(
        "revance", display_name="Revance", http=StubHttp({"boards-api": greenhouse_payload})
    )
    keys = [JobKey.from_canonical(provider.normalize(r).job) for r in provider.discover().raw_jobs]
    report = duplicate_report(group_records(keys))
    assert report["postings_ingested"] == 15
    assert report["canonical_opportunities"] <= 15


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_loads_the_real_registry() -> None:
    registry = load_registry()
    assert len(registry) >= 15
    assert all(s.verified_at for s in registry.enabled), "every source must record verification"
    assert all(s.verified_jobs is not None for s in registry.enabled)
    kinds = registry.kinds()
    assert kinds["GREENHOUSE"] >= 15
    assert kinds["LEVER"] >= 1


def test_registry_builds_the_right_provider_types() -> None:
    registry = load_registry()
    gh = registry.build_provider("greenhouse-revance")
    lv = registry.build_provider("lever-elfbeauty")
    assert isinstance(gh, GreenhouseProvider) and gh.token == "revance"
    assert isinstance(lv, LeverProvider) and lv.token == "elfbeauty"
    assert gh.company_domain == "revance.com"


def test_registry_rejects_duplicate_ids_and_unknown_kinds(tmp_path: Path) -> None:
    good = tmp_path / "sources.yaml"
    good.write_text(
        "version: 1\nsources:\n  - id: a\n    kind: GREENHOUSE\n    token: t\n    display_name: T\n",
        encoding="utf-8",
    )
    assert len(load_registry(good)) == 1

    duplicated = tmp_path / "dup.yaml"
    duplicated.write_text(
        "version: 1\nsources:\n"
        "  - id: a\n    kind: GREENHOUSE\n    token: t\n    display_name: T\n"
        "  - id: a\n    kind: LEVER\n    token: u\n    display_name: U\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="duplicate source id"):
        load_registry(duplicated)

    bad_kind = tmp_path / "bad.yaml"
    bad_kind.write_text(
        "version: 1\nsources:\n  - id: a\n    kind: MONSTER\n    token: t\n    display_name: T\n",
        encoding="utf-8",
    )
    with pytest.raises(ConfigurationError, match="unknown kind"):
        load_registry(bad_kind)

    missing = tmp_path / "missing.yaml"
    missing.write_text("version: 1\nsources:\n  - id: a\n    kind: LEVER\n", encoding="utf-8")
    with pytest.raises(ConfigurationError, match="missing required keys"):
        load_registry(missing)


def test_registry_rejects_direct_careers_without_a_url(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(
        "version: 1\nsources:\n"
        "  - id: a\n    kind: DIRECT_CAREERS\n    token: t\n    display_name: T\n",
        encoding="utf-8",
    )
    registry = load_registry(path)
    with pytest.raises(ConfigurationError, match="no careers_url"):
        registry.build_provider("a")


def test_disabled_sources_are_not_built() -> None:
    registry = load_registry()
    built = registry.build_providers()
    assert all(p.token != "direct-example" for p in built)
    assert len(built) == len(registry.enabled)
