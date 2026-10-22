"""Review fixes for phase 3: splitter headings, Ashby apply URLs, probe edge cases."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from p3_support import ScriptedHttp, ashby_board
from sabra_automation.manual_adapter import manual_apply_url
from sabra_domain.enums import SourceKind
from sabra_domain.errors import ValidationFailure
from sabra_domain.schemas import LocationIntent, SearchProfile
from sabra_providers.ashby import AshbyProvider
from sabra_providers.normalize import split_sections
from sabra_providers.probe import BoardLocator, parse_locator, probe_board

# ---------------------------------------------------------------------------
# section headings
# ---------------------------------------------------------------------------


def siepe(index: int = 0):
    provider = AshbyProvider(
        "siepe", display_name="Siepe", http=ScriptedHttp({"job-board/siepe": ashby_board()})
    )
    return provider.normalize(provider.discover().raw_jobs[index])


def test_the_real_ashby_description_gets_requirements_and_responsibilities() -> None:
    job = siepe(0).job
    assert job.requirements and "5+ years building and owning end-to-end" in job.requirements
    assert "next generation of our reporting and analytics UI" in job.requirements, (
        "'Who We Are Looking For' is a requirements heading, not the company 'about'"
    )
    assert job.responsibilities
    assert "Contribute as a hands-on subject matter expert" in job.responsibilities
    assert "Highcharts" in job.responsibilities
    # The marketing sections did not leak into either bucket.
    for text in (job.requirements, job.responsibilities):
        assert "fast-growing technology company" not in text
        assert "WHY SIEPE" not in text and "WHAT YOU'LL GET" not in text.upper().replace("\u2019", "'")


@pytest.mark.parametrize(
    "heading",
    [
        "What You'll Bring",
        "What You\u2019ll Bring",
        "What You Bring",
        "What You Bring to the Team",
        "What We're Looking For",
        "What We\u2019re Looking For:",
        "Who We Are Looking For",
        "Who You Are",
        "Your Background",
        "About You",
        "WHAT YOU\u2019LL BRING",
    ],
)
def test_requirements_headings(heading) -> None:
    sections = split_sections(f"Intro text\n\n{heading}\n- Five years of SQL\n- A degree")
    assert "Five years of SQL" in sections.get("requirements", ""), heading
    assert "Five years" not in sections.get("body", "")
    assert "Five years" not in sections.get("responsibilities", "")


@pytest.mark.parametrize(
    "heading",
    [
        "What You'll Be Doing",
        "What You\u2019ll Be Doing",
        "What You'll Do",
        "What You\u2019ll Do",
        "What You'll Be Working On",
        "The Role",
        "A Day in the Life",
        "A Day in the Life of a Territory Manager",
        "Day-to-Day",
    ],
)
def test_responsibilities_headings(heading) -> None:
    sections = split_sections(f"Intro text\n\n{heading}\n- Own the Dallas region\n- Coach reps")
    assert "Own the Dallas region" in sections.get("responsibilities", ""), heading
    assert "Own the Dallas region" not in sections.get("requirements", "")


def test_a_sentence_that_starts_like_a_heading_is_not_one() -> None:
    text = (
        "What you bring to this role matters a great deal to us.\n"
        "What we're looking for is someone who loves data and teams.\n"
        "About you: we hope you enjoy it.\n"
    )
    assert set(split_sections(text)) == {"body"}


def test_the_existing_headings_still_work() -> None:
    sections = split_sections(
        "Responsibilities\n- A\n\nRequirements\n- B\n\nAbout Us\nWe are a company.\n"
    )
    assert sections["responsibilities"] == "- A" and sections["requirements"] == "- B"
    assert sections["about"] == "We are a company."
    assert split_sections("What You'll Need\n- C")["requirements"] == "- C"


# ---------------------------------------------------------------------------
# Ashby apply URL
# ---------------------------------------------------------------------------


def test_a_posting_without_a_job_url_gets_its_own_page_not_the_board_index() -> None:
    board = ashby_board()
    posting = board["jobs"][0]
    del posting["jobUrl"]
    provider = AshbyProvider(
        "siepe", display_name="Siepe", http=ScriptedHttp({"job-board/siepe": board})
    )
    raw = provider.discover().raw_jobs[0]
    expected = f"https://jobs.ashbyhq.com/siepe/{posting['id']}"
    assert raw.ref.url == expected
    assert provider.normalize(raw).job.source_url == expected
    assert provider.fetch(raw.ref).ref.url == expected
    posting["jobUrl"] = "not a url"
    again = AshbyProvider(
        "siepe", display_name="Siepe", http=ScriptedHttp({"job-board/siepe": board})
    )
    assert again.discover().raw_jobs[0].ref.url == expected


def test_a_real_job_url_is_kept() -> None:
    job = siepe(0).job
    assert job.source_url == ashby_board()["jobs"][0]["jobUrl"]


# ---------------------------------------------------------------------------
# manual apply URL
# ---------------------------------------------------------------------------


def test_the_manual_fallback_url_must_be_http_or_https() -> None:
    for bad in ("javascript:alert(1)", "file:///C:/secret", "", None, "jobs.ashbyhq.com/x"):
        assert manual_apply_url(SimpleNamespace(source_url=bad, raw_payload={})) == ""
    assert manual_apply_url(SimpleNamespace(source_url="http://a.test/x", raw_payload={})) == (
        "http://a.test/x"
    )


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("https://jobs.lever.co./beta", ("LEVER", "beta")),
        ("https://JOBS.ASHBYHQ.COM./siepe", ("ASHBY", "siepe")),
        ("https://job-boards.greenhouse.io./acme", ("GREENHOUSE", "acme")),
    ],
)
def test_trailing_dot_hosts_are_accepted(text, expected) -> None:
    located = parse_locator(text)
    assert (str(located.kind), located.token) == expected


@pytest.mark.parametrize("text", ["http://[::1/jobs.lever.co/x", "https://[bad/x", "https://a b/c"])
def test_a_malformed_url_is_a_validation_failure_not_a_crash(text) -> None:
    with pytest.raises(ValidationFailure):
        parse_locator(text)


def test_city_matching_is_whole_word(settings) -> None:
    board = ashby_board()
    for index, (title, place) in enumerate(
        [
            ("Planogram Analyst", "Planogram City"),
            ("Austine Rep", "Austine, TX"),
            ("Designer", "Plano, TX"),
            ("Designer", "North Plano / Frisco"),
            ("Remote Designer", "Anywhere"),
        ]
    ):
        board["jobs"][index]["title"] = title
        board["jobs"][index]["location"] = place
        board["jobs"][index]["secondaryLocations"] = []
        board["jobs"][index]["workplaceType"] = "OnSite"
        board["jobs"][index]["isRemote"] = False
    board["jobs"] = board["jobs"][:5]
    profile = SearchProfile(
        id="p",
        name="n",
        candidate_id="",
        locations=LocationIntent(cities=["Plano", "Austin"]),
    )
    result = probe_board(
        BoardLocator(SourceKind.ASHBY, "siepe"),
        http=ScriptedHttp({"job-board/siepe": copy.deepcopy(board)}),
        settings=settings,
        profile=profile,
    )
    # Plano, North Plano and the "Remote" title match; Planogram and Austine do not.
    assert result.posting_count == 5 and result.local_or_remote_count == 3


# ---------------------------------------------------------------------------
# Rich markup in network text
# ---------------------------------------------------------------------------


def test_sources_list_shows_markup_looking_text_literally(tmp_path, monkeypatch) -> None:
    from sabra_domain.settings import reset_settings_cache
    from sabra_engine.candidate_intake import ensure_candidate, ensure_search_profile
    from sabra_engine.cli import app as cli_app
    from sabra_engine.context import EngineContext
    from typer.testing import CliRunner

    path = tmp_path / "sources.yaml"
    path.write_text(
        "version: 1\nsources:\n  - {id: 'g-[bold]x', kind: GREENHOUSE, token: t, display_name: T}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SABRA_SOURCE_REGISTRY_PATH", str(path))
    reset_settings_cache()
    ctx = EngineContext.create()
    try:
        ensure_search_profile(ctx, ensure_candidate(ctx, create_placeholder=True))
        ctx.sync_source_registry()
        with ctx.repos() as repos:
            row = repos.providers.upsert(kind="GREENHOUSE", token="t", display_name="T")
            repos.providers.record_failure(row.id, "boom [/red][link=file:///x]click[/link]")
    finally:
        ctx.close()
    result = CliRunner().invoke(cli_app, ["sources", "list"], env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    assert "g-[bold]x" in result.output
    assert "[/red][link=file:///x]click[/link]" in result.output
    reset_settings_cache()
    assert json.loads(CliRunner().invoke(cli_app, ["sources", "list", "--json"]).output)


def test_ordinary_whitespace_in_a_board_name_is_normalised_not_refused() -> None:
    from sabra_providers.registry_file import yaml_scalar

    assert yaml_scalar("One\u00a0Medical") == "One Medical"
    assert yaml_scalar("line one\r\nline two") == yaml_scalar("line one\nline two")


def test_eu_lever_boards_are_refused_with_a_clear_message() -> None:
    from sabra_domain.errors import ValidationFailure
    with pytest.raises(ValidationFailure, match="api.eu.lever.co"):
        parse_locator("https://jobs.eu.lever.co/somecompany")
