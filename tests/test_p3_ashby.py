"""Ashby provider, against one real board response captured from the public API.

`tests/fixtures/ashby_board_sample.json` is the `siepe` board (10 postings, Dallas),
with the long description text shortened. Tests that need a shape the board does not
have (a salary, an unlisted posting) copy a real posting and change one field.
"""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest
from p3_support import ScriptedHttp, ashby_board
from sabra_domain.enums import EmploymentType, RemoteStatus, SourceKind
from sabra_domain.errors import ConfigurationError, ProviderError
from sabra_providers.ashby import AshbyProvider, salary_from_compensation
from sabra_providers.base import ProviderRef
from sabra_providers.dedupe import JobKey
from sabra_providers.registry import PROVIDER_TYPES, load_registry

BOARD_API = "https://api.ashbyhq.com/posting-api/job-board/siepe"


def provider(board: dict | None = None, **kwargs) -> tuple[AshbyProvider, ScriptedHttp]:
    http = ScriptedHttp({"job-board/siepe": board if board is not None else ashby_board()})
    return AshbyProvider("siepe", display_name="Siepe", http=http, **kwargs), http


def ref(job_id: str) -> ProviderRef:
    return ProviderRef(kind=SourceKind.ASHBY, token="siepe", source_job_id=job_id, url="")


def normalized(board: dict, index: int = 0):
    prov, _ = provider(board)
    batch = prov.discover()
    return prov.normalize(batch.raw_jobs[index]).job


def test_the_fixture_is_a_small_real_response() -> None:
    path = Path(__file__).parent / "fixtures" / "ashby_board_sample.json"
    assert path.stat().st_size < 60_000
    board = ashby_board()
    assert set(board) == {"apiVersion", "jobs"} and len(board["jobs"]) == 10


def test_discover_reads_the_whole_board_in_one_request() -> None:
    prov, http = provider()
    batch = prov.discover()
    assert batch.count == 10 and batch.total_reported == 10 and not batch.truncated
    assert len(http.requests) == 1
    url, params = http.requests[0]
    assert url == BOARD_API and params == {"includeCompensation": "true"}
    assert {raw.ref.source_job_id for raw in batch.raw_jobs} == {
        job["id"] for job in ashby_board()["jobs"]
    }
    assert prov.board_url() == "https://jobs.ashbyhq.com/siepe"


def test_unlisted_postings_are_skipped() -> None:
    board = ashby_board()
    board["jobs"][3]["isListed"] = False
    gone_id = board["jobs"][3]["id"]
    prov, _ = provider(board)
    batch = prov.discover()
    assert batch.count == 9 and batch.total_reported == 9
    assert gone_id not in {raw.ref.source_job_id for raw in batch.raw_jobs}
    assert any("unlisted" in warning for warning in batch.warnings)


def test_max_items_caps_the_postings_and_says_so() -> None:
    prov, _ = provider(max_items=3)
    batch = prov.discover()
    assert batch.count == 3 and batch.truncated and batch.total_reported == 10


def test_an_empty_board_and_a_malformed_one() -> None:
    prov, _ = provider({"apiVersion": "1", "jobs": []})
    assert prov.discover().count == 0
    bad, _ = provider({"unexpected": True})
    with pytest.raises(ProviderError, match="unexpected board payload"):
        bad.discover()


def test_normalizes_one_posting_onto_the_canonical_job() -> None:
    board = ashby_board()
    posting = board["jobs"][0]
    job = normalized(board)
    assert job.source is SourceKind.ASHBY
    assert job.source_job_id == posting["id"]
    assert job.source_url == posting["jobUrl"]
    # The API has no company field: the registry's display name stands in.
    assert job.company == "Siepe"
    assert job.title == "Senior Software Engineer - Reporting"
    assert job.location == "Dallas, TX"
    assert (job.city, job.region, job.country) == ("Dallas", "TX", "United States")
    assert job.employment_type is EmploymentType.FULL_TIME
    assert job.department == "Engineering"
    assert job.date_posted == date(2026, 9, 1)
    assert job.seniority == "SENIOR"
    assert job.board_token == "siepe" and job.discovered_via == "ashby:siepe"
    assert job.description.startswith("WHY SIEPE")
    assert job.raw_payload["applyUrl"] == posting["applyUrl"]
    assert job.raw_source_hash


def test_the_whole_fixture_normalizes() -> None:
    prov, _ = provider()
    jobs = [prov.normalize(raw).job for raw in prov.discover().raw_jobs]
    assert len(jobs) == 10 and all(j.title and j.location for j in jobs)
    kl = [j for j in jobs if j.location == "Kuala Lumpur"]
    assert kl and all(j.country == "Malaysia" and j.region == "Kuala Lumpur" for j in kl)


def test_remote_status_prefers_workplace_type_then_is_remote() -> None:
    board = ashby_board()
    # Real data: a hybrid role carries isRemote=true, and must not read as fully remote.
    assert board["jobs"][0]["isRemote"] is True
    assert normalized(board, 0).remote_status is RemoteStatus.HYBRID
    assert board["jobs"][3]["isRemote"] is False
    assert normalized(board, 3).remote_status is RemoteStatus.ONSITE

    remote = copy.deepcopy(board)
    remote["jobs"][0]["workplaceType"] = "Remote"
    assert normalized(remote, 0).remote_status is RemoteStatus.REMOTE

    # Without workplaceType, isRemote decides.
    flag_only = copy.deepcopy(board)
    del flag_only["jobs"][0]["workplaceType"]
    assert normalized(flag_only, 0).remote_status is RemoteStatus.REMOTE
    del flag_only["jobs"][3]["workplaceType"]
    assert normalized(flag_only, 3).remote_status is RemoteStatus.ONSITE


def test_secondary_locations_stay_visible_to_the_geography_filter() -> None:
    board = ashby_board()
    posting = next(j for j in board["jobs"] if j["secondaryLocations"])
    index = board["jobs"].index(posting)
    prov, _ = provider(board)
    result = prov.normalize(prov.discover().raw_jobs[index])
    assert result.job.location == "Houston, TX / Dallas, TX"
    assert result.job.city == "Houston"
    assert any("multi-location" in w for w in result.warnings)


def test_the_address_fills_in_a_missing_location() -> None:
    board = ashby_board()
    board["jobs"][0]["location"] = ""
    job = normalized(board)
    assert job.location == "Dallas, Texas"
    assert (job.city, job.region) == ("Dallas", "TEXAS")  # the shared parser upper-cases


def comp(*components: dict) -> dict:
    return {"compensationTierSummary": "x", "summaryComponents": list(components)}


def salary(low=81000, high=87000, interval="1 YEAR", kind="Salary", currency="USD") -> dict:
    return {
        "summary": "x",
        "compensationType": kind,
        "interval": interval,
        "currencyCode": currency,
        "minValue": low,
        "maxValue": high,
    }


def test_salary_comes_from_the_annual_salary_component() -> None:
    equity = {"compensationType": "EquityPercentage", "interval": "NONE", "minValue": 1}
    assert salary_from_compensation(comp(salary(), equity)) == (81000.0, 87000.0, "USD", "YEAR")
    board = ashby_board()
    board["jobs"][0]["compensation"] = comp(salary(), equity)
    job = normalized(board)
    assert (job.salary_min, job.salary_max) == (81000.0, 87000.0)
    assert (job.salary_currency, job.salary_period) == ("USD", "YEAR")


def test_salary_records_the_period_and_never_annualises() -> None:
    hourly = salary_from_compensation(comp(salary(30, 40, interval="1 HOUR")))
    assert hourly == (30.0, 40.0, "USD", "HOUR")
    monthly = salary_from_compensation(comp(salary(5000, 7000, interval="1 MONTH")))
    assert monthly == (5000.0, 7000.0, "USD", "MONTH")
    # An annual figure wins over an hourly one when both are published.
    both = comp(salary(30, 40, interval="1 HOUR"), salary())
    assert salary_from_compensation(both)[3] == "YEAR"


def test_salary_ignores_what_is_not_base_pay_or_not_a_number() -> None:
    assert salary_from_compensation(None) is None
    assert salary_from_compensation({"summaryComponents": []}) is None
    assert salary_from_compensation(comp(salary(kind="Bonus"))) is None
    assert salary_from_compensation(comp(salary(None, None))) is None
    assert salary_from_compensation(comp(salary(interval="1 WEEK"))) is None
    # One bound is still a figure; the widest range wins across tiers of the same period.
    assert salary_from_compensation(comp(salary(90000, None))) == (90000.0, 90000.0, "USD", "YEAR")
    tiers = comp(salary(80000, 90000), salary(95000, 120000))
    assert salary_from_compensation(tiers) == (80000.0, 120000.0, "USD", "YEAR")


def test_a_board_without_compensation_leaves_the_salary_empty() -> None:
    job = normalized(ashby_board())
    assert job.salary_min is None and job.salary_period is None


def test_the_description_falls_back_to_the_html() -> None:
    board = ashby_board()
    board["jobs"][0]["descriptionPlain"] = ""
    board["jobs"][0]["descriptionHtml"] = (
        "<h2>Responsibilities</h2><ul><li>Own the reports</li></ul>"
        "<h2>Requirements</h2><ul><li>5 years of SQL</li></ul>"
    )
    job = normalized(board)
    assert "Own the reports" in job.description and "<" not in job.description
    assert job.responsibilities and "Own the reports" in job.responsibilities
    assert job.requirements and "5 years of SQL" in job.requirements


def test_sections_are_split_from_the_plain_description() -> None:
    board = ashby_board()
    board["jobs"][0]["descriptionPlain"] = (
        "Intro\n\nResponsibilities\n- Build things\n\nRequirements\n- 3 years\n"
    )
    job = normalized(board)
    assert "Build things" in job.responsibilities and "3 years" in job.requirements


# -- verify --------------------------------------------------------------


def test_verify_reports_live_and_a_changed_hash() -> None:
    board = ashby_board()
    prov, _ = provider(board)
    live = prov.verify(ref(board["jobs"][0]["id"]))
    assert live.available and not live.changed and live.detail == "live" and live.source_hash
    again = prov.verify(ref(board["jobs"][0]["id"]), known_hash=live.source_hash)
    assert again.available and not again.changed
    edited = copy.deepcopy(board)
    edited["jobs"][0]["title"] = "Principal Engineer"
    other, _ = provider(edited)
    assert other.verify(ref(board["jobs"][0]["id"]), known_hash=live.source_hash).changed


def test_verify_says_gone_when_the_posting_is_absent_or_unlisted() -> None:
    from sabra_engine.execution import posting_is_gone

    board = ashby_board()
    target = board["jobs"][0]["id"]
    board["jobs"] = board["jobs"][1:]
    prov, _ = provider(board)
    absent = prov.verify(ref(target))
    assert not absent.available and absent.http_status == 404
    assert posting_is_gone(absent)

    unlisted = ashby_board()
    unlisted["jobs"][0]["isListed"] = False
    prov, _ = provider(unlisted)
    assert posting_is_gone(prov.verify(ref(unlisted["jobs"][0]["id"])))


def test_verify_distinguishes_a_transient_failure_from_gone() -> None:
    from sabra_engine.execution import posting_is_gone

    target = ashby_board()["jobs"][0]["id"]
    for failure in (
        ProviderError("http", "GET x returned HTTP 503", retryable=True),
        ProviderError("http", "GET x failed: ReadTimeout: slow", retryable=True),
        ProviderError("http", "x returned non-JSON body: boom", retryable=False),
    ):
        http = ScriptedHttp({"job-board/siepe": failure})
        result = AshbyProvider("siepe", display_name="Siepe", http=http).verify(ref(target))
        assert not result.available and result.http_status is None
        assert not posting_is_gone(result), failure


def test_verify_treats_a_missing_board_as_closed() -> None:
    from sabra_engine.execution import posting_is_gone

    http = ScriptedHttp({"job-board/siepe": 404})
    result = AshbyProvider("siepe", display_name="Siepe", http=http).verify(ref("anything"))
    assert result.http_status == 404 and posting_is_gone(result)


def test_fetch_finds_a_posting_on_the_board_or_raises() -> None:
    board = ashby_board()
    prov, _ = provider(board)
    raw = prov.fetch(ref(board["jobs"][2]["id"]))
    assert raw.payload["title"] == board["jobs"][2]["title"]
    with pytest.raises(ProviderError, match="not on the siepe board"):
        prov.fetch(ref("nope"))


# -- identity ------------------------------------------------------------


def test_dedupe_key_and_hash_are_stable_across_reads() -> None:
    first = normalized(ashby_board())
    second = normalized(ashby_board())
    assert first.canonical_key() == second.canonical_key()
    assert first.raw_source_hash == second.raw_source_hash
    assert JobKey.from_canonical(first).group_key() == JobKey.from_canonical(second).group_key()

    # Key order in the source JSON must not change the hash.
    board = ashby_board()
    board["jobs"][0] = dict(reversed(list(board["jobs"][0].items())))
    assert normalized(board).raw_source_hash == first.raw_source_hash

    # A real content change does.
    edited = ashby_board()
    edited["jobs"][0]["descriptionPlain"] += " New paragraph."
    changed = normalized(edited)
    assert changed.raw_source_hash != first.raw_source_hash
    assert changed.canonical_key() == first.canonical_key()


def test_two_postings_with_different_ids_have_different_identities() -> None:
    prov, _ = provider()
    jobs = [prov.normalize(raw).job for raw in prov.discover().raw_jobs]
    assert len({j.source_job_id for j in jobs}) == 10
    assert len({j.raw_source_hash for j in jobs}) == 10


# -- registry ------------------------------------------------------------


def write_registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "sources.yaml"
    path.write_text("version: 1\nsources:\n" + body, encoding="utf-8")
    return path


def test_the_registry_builds_an_ashby_provider(tmp_path: Path) -> None:
    path = write_registry(
        tmp_path,
        "  - id: ashby-siepe\n    kind: ASHBY\n    token: siepe\n    display_name: Siepe\n"
        "    max_items: 4\n",
    )
    registry = load_registry(path)
    assert PROVIDER_TYPES[SourceKind.ASHBY] is AshbyProvider
    built = registry.build_provider("ashby-siepe")
    assert isinstance(built, AshbyProvider) and built.token == "siepe"
    assert built.display_name == "Siepe" and built.max_items == 4
    assert registry.kinds() == {"ASHBY": 1}
    # The run's cap and the entry's cap: the tighter one wins.
    assert registry.build_provider("ashby-siepe", max_items=2).max_items == 2
    assert registry.build_provider("ashby-siepe", max_items=50).max_items == 4


def test_the_registry_rejects_a_bad_max_items(tmp_path: Path) -> None:
    for bad in ("0", "-3", "many", "true", "2.5"):
        path = write_registry(
            tmp_path,
            f"  - id: a\n    kind: ASHBY\n    token: t\n    display_name: T\n    max_items: {bad}\n",
        )
        with pytest.raises(ConfigurationError, match="max_items"):
            load_registry(path)


def test_the_committed_registry_documents_ashby() -> None:
    text = (Path(__file__).parents[1] / "config" / "sources.yaml").read_text(encoding="utf-8")
    assert "ASHBY" in text and "# - id: ashby-" in text


def test_the_fixture_text_is_real_utf8_not_mojibake() -> None:
    text = (Path(__file__).parent / "fixtures" / "ashby_board_sample.json").read_bytes()
    decoded = text.decode("utf-8")
    assert "\u00e2\u20ac" not in decoded, "a UTF-8 capture was decoded as cp1252"
    assert "\u2019" in decoded, "the real curly apostrophes are present"
