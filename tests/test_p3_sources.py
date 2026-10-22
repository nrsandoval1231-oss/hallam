"""Job boards: URL detection, probing, the registry file edits, the service, CLI and API.

Every HTTP read goes through `ScriptedHttp`; nothing here reaches a network, and every
registry file written is a temporary copy.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from p3_support import ScriptedHttp, ashby_board
from sabra_api.main import app
from sabra_domain.enums import SourceKind
from sabra_domain.errors import ConfigurationError, NotFound, ProviderError, ValidationFailure
from sabra_domain.schemas import LocationIntent, SearchProfile
from sabra_domain.settings import reset_settings_cache
from sabra_engine.candidate_intake import ensure_candidate, ensure_search_profile
from sabra_engine.cli import app as cli_app
from sabra_engine.context import EngineContext
from sabra_engine.sources import SourcesService
from sabra_providers.probe import (
    ALLOWED_API_HOSTS,
    BoardLocator,
    api_url,
    assert_safe_api_url,
    build_probe_http,
    fetch_board_name,
    parse_locator,
    probe_board,
)
from sabra_providers.registry import load_registry
from sabra_providers.registry_file import (
    append_entry_text,
    append_source,
    format_entry,
    set_enabled_text,
    set_source_enabled,
    yaml_scalar,
)
from typer.testing import CliRunner

REPO_REGISTRY = Path(__file__).parents[1] / "config" / "sources.yaml"

SMALL_REGISTRY = """\
# Header comment that must survive every edit.
version: 1
notes: A small registry for tests.

sources:
  # -- Group one ---------------------------------------------------------
  - id: greenhouse-acme
    kind: GREENHOUSE
    token: acme
    display_name: Acme Aesthetics
    enabled: true  # trailing comment on the flag
    verified_jobs: 4
    verified_at: 2026-09-16
    notes: >
      A folded note
      over two lines.

  # -- Group two ---------------------------------------------------------
  - id: lever-beta
    kind: LEVER
    token: beta
    display_name: Beta Beauty
    verified_jobs: 2
    verified_at: 2026-09-16
"""


def profile(candidate_id: str = "") -> SearchProfile:
    return SearchProfile(
        id="srch_p3",
        name="Dallas engineers",
        candidate_id=candidate_id,
        target_titles=["Software Engineer"],
        locations=LocationIntent(cities=["Dallas"], states=["TX"], allow_remote=True),
    )


# ---------------------------------------------------------------------------
# URL detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "expected"),
    [
        ("https://job-boards.greenhouse.io/revance", None, ("GREENHOUSE", "revance")),
        ("https://job-boards.greenhouse.io/revance/jobs/4001", None, ("GREENHOUSE", "revance")),
        ("https://boards.greenhouse.io/glossier", None, ("GREENHOUSE", "glossier")),
        ("boards.greenhouse.io/glossier/", None, ("GREENHOUSE", "glossier")),
        (
            "https://boards.greenhouse.io/embed/job_board?for=sol_de_janeiro",
            None,
            ("GREENHOUSE", "sol_de_janeiro"),
        ),
        ("https://jobs.lever.co/elfbeauty", None, ("LEVER", "elfbeauty")),
        ("https://jobs.lever.co/elfbeauty/abc-123?lever-source=x", None, ("LEVER", "elfbeauty")),
        ("https://jobs.ashbyhq.com/siepe", None, ("ASHBY", "siepe")),
        ("https://jobs.ashbyhq.com/siepe/8f17f764-aa6f/application", None, ("ASHBY", "siepe")),
        (
            "https://api.ashbyhq.com/posting-api/job-board/siepe?includeCompensation=true",
            None,
            ("ASHBY", "siepe"),
        ),
        ("HTTPS://JOBS.LEVER.CO/Beta", None, ("LEVER", "Beta")),
        ("https://jobs.lever.co/beta", "lever", ("LEVER", "beta")),
        ("siepe", "ASHBY", ("ASHBY", "siepe")),
        ("  acme  ", "greenhouse", ("GREENHOUSE", "acme")),
    ],
)
def test_detects_the_kind_and_token(text, kind, expected) -> None:
    located = parse_locator(text, kind)
    assert (str(located.kind), located.token) == expected


@pytest.mark.parametrize(
    ("text", "kind", "message"),
    [
        ("", None, "enter a careers URL"),
        ("siepe", None, "--kind"),
        ("siepe", "WORKDAY", "unknown kind"),
        ("siepe", "DIRECT_CAREERS", "cannot be probed"),
        ("https://jobs.lever.co/beta", "ASHBY", "is a LEVER board"),
        ("https://example.com/careers", None, "could not find a board"),
        ("https://boards.greenhouse.io/", None, "could not find a board"),
        ("https://boards.greenhouse.io/embed/job_board", None, "could not find a board"),
        ("https://api.ashbyhq.com/other/path", None, "could not find a board"),
        ("ftp://jobs.lever.co/beta", None, "unsupported URL scheme"),
        ("../etc/passwd", "LEVER", "could not find a board"),
        ("a b", "LEVER", "valid board token"),
        ("acme?x=1", "LEVER", "could not find a board"),
    ],
)
def test_refuses_what_it_cannot_place(text, kind, message) -> None:
    with pytest.raises(ValidationFailure, match=message):
        parse_locator(text, kind)


@pytest.mark.parametrize(
    "text",
    [
        "https://evil.example/jobs.lever.co/beta",
        "https://jobs.lever.co.evil.example/beta",
        "http://169.254.169.254/latest/meta-data",
        "http://localhost:8787/api/settings",
        "https://user@jobs.lever.co@evil.example/beta",
    ],
)
def test_a_pasted_url_on_another_host_is_never_read(text) -> None:
    http = ScriptedHttp({})
    with pytest.raises(ValidationFailure):
        located = parse_locator(text)
        probe_board(located, http=http, settings=None)  # pragma: no cover - not reached
    assert http.requests == []


def test_only_the_three_api_hosts_can_be_contacted() -> None:
    assert {"boards-api.greenhouse.io", "api.lever.co", "api.ashbyhq.com"} == ALLOWED_API_HOSTS
    assert api_url(BoardLocator(SourceKind.GREENHOUSE, "acme")).startswith(
        "https://boards-api.greenhouse.io/"
    )
    assert api_url(BoardLocator(SourceKind.LEVER, "beta")).startswith("https://api.lever.co/")
    assert api_url(BoardLocator(SourceKind.ASHBY, "siepe")) == (
        "https://api.ashbyhq.com/posting-api/job-board/siepe"
    )
    for bad in (
        "http://api.lever.co/v0/postings/x",
        "https://api.lever.co.evil.example/x",
        "https://169.254.169.254/x",
        "https://jobs.lever.co/x",
    ):
        with pytest.raises(ValidationFailure, match="not a known job-board API"):
            assert_safe_api_url(bad)
    with pytest.raises(ValidationFailure):
        api_url(BoardLocator(SourceKind.LEVER, "../../admin"))


def test_the_probe_client_makes_one_attempt_and_follows_no_redirects(settings) -> None:
    http = build_probe_http(settings)
    try:
        assert http.max_attempts == 1
        assert http._client.follow_redirects is False
        assert http.timeout == settings.discovery_timeout_seconds
    finally:
        http.close()


# ---------------------------------------------------------------------------
# Probing
# ---------------------------------------------------------------------------


def test_probe_counts_postings_cities_and_titles(settings) -> None:
    http = ScriptedHttp({"job-board/siepe": ashby_board()})
    result = probe_board(
        BoardLocator(SourceKind.ASHBY, "siepe"),
        http=http,
        settings=settings,
        profile=profile(),
    )
    assert result.live and result.error is None
    assert result.posting_count == 10
    # Six Dallas roles plus the Houston one that is also open in Dallas.
    assert result.local_or_remote_count == 7
    assert result.sample_titles == [
        "Senior Software Engineer - Reporting",
        "Senior Software Engineer - CLO Compliance",
    ]
    assert result.profile_name == "Dallas engineers" and result.profile_has_cities
    assert len(http.requests) == 1


def test_probe_caps_the_samples_at_five(settings) -> None:
    board = ashby_board()
    for index, posting in enumerate(board["jobs"]):
        posting["title"] = f"Software Engineer {index}"
    result = probe_board(
        BoardLocator(SourceKind.ASHBY, "siepe"),
        http=ScriptedHttp({"job-board/siepe": board}),
        settings=settings,
        profile=profile(),
    )
    assert len(result.sample_titles) == 5 and result.posting_count == 10


def test_probe_without_a_profile_counts_only_remote(settings) -> None:
    board = ashby_board()
    board["jobs"][3]["workplaceType"] = "Remote"
    result = probe_board(
        BoardLocator(SourceKind.ASHBY, "siepe"),
        http=ScriptedHttp({"job-board/siepe": board}),
        settings=settings,
    )
    assert result.live and result.local_or_remote_count == 1 and result.sample_titles == []


def test_probe_uses_family_and_level_terms_too(settings) -> None:
    wide = profile().model_copy(
        update={
            "target_titles": [],
            "role_family_terms": ["analyst"],
            "role_level_terms": ["senior"],
        }
    )
    result = probe_board(
        BoardLocator(SourceKind.ASHBY, "siepe"),
        http=ScriptedHttp({"job-board/siepe": ashby_board()}),
        settings=settings,
        profile=wide,
    )
    assert result.sample_titles == ["CLO Compliance Senior Analyst"]


@pytest.mark.parametrize(
    "failure",
    [
        404,
        500,
        ProviderError("http", "GET x failed: ConnectTimeout: slow", retryable=True),
        "<html>not json</html>",
        {"not": "a board"},
    ],
)
def test_a_dead_board_is_a_result_not_an_exception(settings, failure) -> None:
    result = probe_board(
        BoardLocator(SourceKind.ASHBY, "gone"),
        http=ScriptedHttp({"job-board/gone": failure}),
        settings=settings,
        profile=profile(),
    )
    assert not result.live and result.error and result.posting_count == 0


def test_probe_reads_greenhouse_and_lever_boards(settings) -> None:
    greenhouse = probe_board(
        BoardLocator(SourceKind.GREENHOUSE, "acme"),
        http=ScriptedHttp(
            {
                "boards/acme/jobs": {
                    "jobs": [
                        {
                            "id": 1,
                            "title": "Software Engineer",
                            "location": {"name": "Dallas, TX"},
                            "absolute_url": "https://x/1",
                            "content": "Build things.",
                        }
                    ],
                    "meta": {"total": 1},
                }
            }
        ),
        settings=settings,
        profile=profile(),
    )
    assert greenhouse.live and greenhouse.posting_count == 1
    assert greenhouse.sample_titles == ["Software Engineer"]
    lever = probe_board(
        BoardLocator(SourceKind.LEVER, "beta"),
        http=ScriptedHttp({"postings/beta": []}),
        settings=settings,
    )
    assert lever.live and lever.posting_count == 0


def test_the_greenhouse_board_name_is_read_once_and_only_for_greenhouse() -> None:
    http = ScriptedHttp({"v1/boards/acme": {"name": "Acme Aesthetics", "content": ""}})
    assert fetch_board_name(BoardLocator(SourceKind.GREENHOUSE, "acme"), http=http) == (
        "Acme Aesthetics"
    )
    assert fetch_board_name(BoardLocator(SourceKind.LEVER, "beta"), http=http) is None
    assert len(http.requests) == 1
    failing = ScriptedHttp({"v1/boards/acme": 404})
    assert fetch_board_name(BoardLocator(SourceKind.GREENHOUSE, "acme"), http=failing) is None


# ---------------------------------------------------------------------------
# The registry file keeps its comments
# ---------------------------------------------------------------------------


def entry(**overrides) -> dict:
    base = {
        "id": "ashby-siepe",
        "kind": "ASHBY",
        "token": "siepe",
        "display_name": "Siepe",
        "enabled": True,
        "verified_jobs": 10,
        "verified_at": date(2026, 10, 22),
    }
    base.update(overrides)
    return base


def test_appending_keeps_every_existing_byte() -> None:
    new = append_entry_text(SMALL_REGISTRY, entry())
    assert new.startswith(SMALL_REGISTRY.rstrip("\n"))
    assert "# Header comment that must survive every edit." in new
    assert "# -- Group two" in new and "# trailing comment on the flag" in new
    tail = new[len(SMALL_REGISTRY.rstrip("\n")) :]
    assert tail == (
        "\n\n  - id: ashby-siepe\n    kind: ASHBY\n    token: siepe\n"
        "    display_name: Siepe\n    enabled: true\n    verified_jobs: 10\n"
        "    verified_at: 2026-10-22\n"
    )


def test_an_appended_entry_loads_and_reads_back_exactly(tmp_path: Path) -> None:
    tricky = entry(
        display_name="Smith & Sons: Skin # Care",
        industry="Yes",
        notes="Line one\nline two with \"quotes\" and 'apostrophes'",
    )
    path = tmp_path / "sources.yaml"
    path.write_text(append_entry_text(SMALL_REGISTRY, tricky), encoding="utf-8")
    registry = load_registry(path)
    source = registry.by_id("ashby-siepe")
    assert source.entry.display_name == "Smith & Sons: Skin # Care"
    assert source.entry.industry == "Yes"
    assert source.entry.notes == "Line one\nline two with \"quotes\" and 'apostrophes'"
    assert source.verified_jobs == 10 and source.verified_at == date(2026, 10, 22)
    assert [s.id for s in registry] == ["greenhouse-acme", "lever-beta", "ashby-siepe"]


def test_appending_to_the_real_registry_changes_nothing_else() -> None:
    original = REPO_REGISTRY.read_bytes().decode("utf-8")
    new = append_entry_text(original, entry())
    # A blank separator line and the seven lines of the entry; line endings follow the file
    # (a checkout may have either, so this does not assume which).
    assert len(new.splitlines()) == len(original.splitlines()) + 8
    assert ("\r\n" in original) == ("\r\n" in new)
    assert new.replace("\r\n", "\n").startswith(original.replace("\r\n", "\n").rstrip("\n"))
    comments = [line for line in original.splitlines() if line.lstrip().startswith("#")]
    assert comments and all(line in new for line in comments)


def test_crlf_files_stay_crlf_and_lf_files_stay_lf() -> None:
    crlf = SMALL_REGISTRY.replace("\n", "\r\n")
    new = append_entry_text(crlf, entry())
    assert "\n" not in new.replace("\r\n", "")
    assert "\r" not in append_entry_text(SMALL_REGISTRY, entry())


def test_appending_before_another_top_level_key() -> None:
    text = SMALL_REGISTRY + "\nextra:\n  keep: me\n"
    new = append_entry_text(text, entry())
    assert new.index("ashby-siepe") < new.index("extra:")
    assert new.endswith("extra:\n  keep: me\n")


def test_appending_into_an_empty_flow_list(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text("version: 1\n# c\nsources: []\n", encoding="utf-8")
    new = append_entry_text(path.read_text(encoding="utf-8"), entry())
    path.write_text(new, encoding="utf-8")
    assert "# c" in new and len(load_registry(path)) == 1


def test_a_file_without_a_sources_list_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="no block-style"):
        append_entry_text("version: 1\n", entry())


def test_enabling_and_disabling_edits_in_place() -> None:
    off = set_enabled_text(SMALL_REGISTRY, "greenhouse-acme", False)
    assert "    enabled: false  # trailing comment on the flag" in off
    assert off.replace("enabled: false", "enabled: true") == SMALL_REGISTRY
    # `lever-beta` has no `enabled` key (so it is enabled): disabling adds one at the end
    # of that entry only.
    off_beta = set_enabled_text(SMALL_REGISTRY, "lever-beta", False)
    assert off_beta.count("enabled: false") == 1
    assert off_beta.index("enabled: false") > off_beta.index("id: lever-beta")
    assert set_enabled_text(SMALL_REGISTRY, "lever-beta", True) == SMALL_REGISTRY
    assert set_enabled_text(off_beta, "lever-beta", True).count("enabled: true") == 2
    with pytest.raises(ConfigurationError, match="not found"):
        set_enabled_text(SMALL_REGISTRY, "nope", False)


def test_enabling_one_entry_never_touches_its_neighbour() -> None:
    # The first entry's folded note ends right before a comment and the next entry.
    flipped = set_enabled_text(SMALL_REGISTRY, "greenhouse-acme", False)
    assert flipped.splitlines()[:8] == SMALL_REGISTRY.splitlines()[:8]
    assert flipped.count("\n") == SMALL_REGISTRY.count("\n")
    assert flipped.endswith(SMALL_REGISTRY[SMALL_REGISTRY.index("  # -- Group two") :])


def test_yaml_scalars_read_back_as_the_same_value() -> None:
    import yaml

    for value in (
        "Plain Name",
        "Acme & Co.",
        "true",
        "no",
        "123",
        "1.5",
        "2026-09-16",
        "a: b",
        "has #hash",
        "trailing:",
        "",
        "multi\nline",
        "quote\"d",
        "unicode éè",
        "- leading dash",
        "{braces}",
        "null",
    ):
        assert yaml.safe_load(yaml_scalar(value)) == value, value
    assert yaml_scalar(True) == "true" and yaml_scalar(12) == "12"
    assert format_entry({"id": "a", "kind": "LEVER", "token": "t", "display_name": "T"}) == [
        "  - id: a",
        "    kind: LEVER",
        "    token: t",
        "    display_name: T",
    ]


# ---------------------------------------------------------------------------
# The service
# ---------------------------------------------------------------------------


@pytest.fixture()
def registry_file(settings) -> Path:
    settings.source_registry_path.write_text(SMALL_REGISTRY, encoding="utf-8")
    return settings.source_registry_path


@pytest.fixture()
def service_ctx(settings, registry_file):
    ctx = EngineContext.create(
        settings=settings,
        database_url=settings.database_url,
        registry_path=str(registry_file),
    )
    try:
        candidate_id = ensure_candidate(ctx, display_name="Tester", create_placeholder=True)
        ensure_search_profile(ctx, candidate_id, profile=profile(candidate_id), activate=True)
        ctx.sync_source_registry()
        yield ctx
    finally:
        ctx.close()


def ashby_http() -> ScriptedHttp:
    return ScriptedHttp({"job-board/siepe": ashby_board(), "job-board/gone": 404})


def test_list_joins_the_registry_with_the_providers_table(service_ctx) -> None:
    with service_ctx.repos() as repos:
        row = repos.providers.upsert(
            kind="GREENHOUSE", token="acme", display_name="Acme Aesthetics"
        )
        repos.providers.record_success(row.id, 4)
        beta = repos.providers.upsert(kind="LEVER", token="beta", display_name="Beta Beauty")
        repos.providers.record_failure(beta.id, "HTTP 503")
    rows = {r["id"]: r for r in SourcesService(service_ctx).list_sources()}
    assert set(rows) == {"greenhouse-acme", "lever-beta"}
    acme = rows["greenhouse-acme"]
    assert acme["enabled"] is True and acme["health"] == "HEALTHY"
    assert acme["total_discovered"] == 4 and acme["last_success_at"]
    assert acme["verified_jobs"] == 4 and acme["verified_at"] == "2026-09-16"
    beta_row = rows["lever-beta"]
    assert beta_row["health"] == "DEGRADED" and beta_row["last_error"] == "HTTP 503"


def test_add_appends_to_the_configured_file_and_syncs(service_ctx, registry_file) -> None:
    http = ashby_http()
    result = SourcesService(service_ctx, http=http).add(
        "https://jobs.ashbyhq.com/siepe", industry="Fintech", notes="Dallas software"
    )
    source = result["source"]
    assert source["id"] == "ashby-siepe" and source["kind"] == "ASHBY"
    assert source["display_name"] == "siepe"  # no --display-name, and Ashby has no board name
    assert source["verified_jobs"] == 10 and source["enabled"] is True
    assert source["verified_at"] == date.today().isoformat()
    assert result["probe"]["posting_count"] == 10 and result["warnings"] == []
    assert result["registry_file"] == str(registry_file)

    text = registry_file.read_text(encoding="utf-8")
    assert text.startswith(SMALL_REGISTRY.rstrip("\n"))
    assert "industry: Fintech" in text and "notes: Dallas software" in text
    # Re-read from disk by an independent loader, and mirrored into the providers table.
    assert "ashby-siepe" in {s.id for s in load_registry(registry_file)}
    assert "ashby-siepe" in {s.id for s in service_ctx.registry}
    with service_ctx.read_repos() as repos:
        assert ("ASHBY", "siepe") in {(r.kind, r.token) for r in repos.providers.list_all()}


def test_add_uses_the_given_name_then_the_greenhouse_board_name(service_ctx) -> None:
    http = ScriptedHttp(
        {
            "v1/boards/newco/jobs": {"jobs": [], "meta": {"total": 0}},
            "v1/boards/newco": {"name": "NewCo Skincare"},
        }
    )
    named = SourcesService(service_ctx, http=http).add("newco", "GREENHOUSE")
    assert named["source"]["display_name"] == "NewCo Skincare"
    assert named["warnings"] == ["the board is live but lists no postings right now"]
    chosen = SourcesService(service_ctx, http=ashby_http()).add(
        "siepe", "ASHBY", display_name="Siepe Software"
    )
    assert chosen["source"]["display_name"] == "Siepe Software"


def test_add_refuses_a_dead_board_and_changes_nothing(service_ctx, registry_file) -> None:
    before = registry_file.read_bytes()
    with pytest.raises(ValidationFailure, match="did not respond as a live job board"):
        SourcesService(service_ctx, http=ashby_http()).add("https://jobs.ashbyhq.com/gone")
    assert registry_file.read_bytes() == before
    assert len(service_ctx.registry) == 2


def test_add_refuses_a_duplicate_board_before_any_request(service_ctx, registry_file) -> None:
    before = registry_file.read_bytes()
    http = ScriptedHttp({})
    service = SourcesService(service_ctx, http=http)
    for text, kind in (
        ("https://job-boards.greenhouse.io/acme", None),
        ("https://boards.greenhouse.io/ACME", None),
        ("acme", "GREENHOUSE"),
        ("https://jobs.lever.co/beta", None),
    ):
        with pytest.raises(ValidationFailure, match="already in the registry"):
            service.add(text, kind)
    assert http.requests == [] and registry_file.read_bytes() == before


def test_add_needs_a_kind_for_a_bare_token(service_ctx) -> None:
    with pytest.raises(ValidationFailure, match="--kind"):
        SourcesService(service_ctx, http=ScriptedHttp({})).add("siepe")


def test_probe_changes_nothing_and_reports_a_known_board(service_ctx, registry_file) -> None:
    before = registry_file.read_bytes()
    service = SourcesService(service_ctx, http=ashby_http())
    payload = service.probe("https://jobs.ashbyhq.com/siepe")
    assert payload["live"] and payload["posting_count"] == 10
    assert payload["local_or_remote_count"] == 7 and payload["already_in_registry"] is None
    assert payload["profile_name"] == "Dallas engineers"
    assert registry_file.read_bytes() == before
    known = SourcesService(
        service_ctx, http=ScriptedHttp({"postings/beta": []})
    ).probe("https://jobs.lever.co/beta")
    assert known["already_in_registry"] == "lever-beta"


def test_disable_and_enable_flip_the_file_and_the_providers_row(
    service_ctx, registry_file
) -> None:
    service = SourcesService(service_ctx)
    off = service.set_enabled("greenhouse-acme", False)
    assert off["changed"] and off["source"]["enabled"] is False
    assert "enabled: false  # trailing comment on the flag" in registry_file.read_text("utf-8")
    assert "greenhouse-acme" not in {s.id for s in service_ctx.registry.enabled}
    with service_ctx.read_repos() as repos:
        row = next(r for r in repos.providers.list_all() if r.token == "acme")
        assert row.enabled is False
    again = service.set_enabled("greenhouse-acme", False)
    assert again["changed"] is False
    on = service.set_enabled("greenhouse-acme", True)
    assert on["changed"] and on["source"]["enabled"] is True
    assert registry_file.read_text("utf-8") == SMALL_REGISTRY
    with pytest.raises(NotFound):
        service.set_enabled("missing", True)


def test_a_failed_validation_leaves_the_file_alone(service_ctx, registry_file, monkeypatch) -> None:
    import sabra_providers.registry_file as registry_file_module

    before = registry_file.read_bytes()
    monkeypatch.setattr(
        registry_file_module,
        "append_entry_text",
        lambda text, entry: text + "\n  - broken: [unterminated\n",
    )
    with pytest.raises(Exception):  # noqa: B017, PT011 - any parse failure must abort the write
        SourcesService(service_ctx, http=ashby_http()).add("https://jobs.ashbyhq.com/siepe")
    assert registry_file.read_bytes() == before
    assert not list(registry_file.parent.glob(".sources-*"))


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------


@pytest.fixture()
def cli_world(tmp_path: Path, monkeypatch):
    path = tmp_path / "cli-sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    monkeypatch.setenv("SABRA_SOURCE_REGISTRY_PATH", str(path))
    reset_settings_cache()
    http = ashby_http()
    monkeypatch.setattr("sabra_engine.sources.build_probe_client", lambda _ctx: http)
    ctx = EngineContext.create()
    try:
        ensure_search_profile(ctx, ensure_candidate(ctx, create_placeholder=True), profile=profile())
        ctx.sync_source_registry()
    finally:
        ctx.close()
    yield path, http
    reset_settings_cache()


def test_cli_list_shows_enabled_health_and_counts(cli_world) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_app, ["sources", "list", "--json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.output)
    assert [r["id"] for r in rows] == ["greenhouse-acme", "lever-beta"]
    assert {"enabled", "health", "total_discovered", "last_success_at"} <= set(rows[0])
    table = runner.invoke(cli_app, ["sources", "list"], env={"COLUMNS": "200"})
    assert table.exit_code == 0 and "greenhouse-acme" in table.output


def test_cli_probe_prints_the_summary_and_exits_nonzero_for_a_dead_board(cli_world) -> None:
    runner = CliRunner()
    live = runner.invoke(cli_app, ["sources", "probe", "https://jobs.ashbyhq.com/siepe"])
    assert live.exit_code == 0, live.output
    assert "live yes" in live.output and "postings: 10" in live.output
    assert "Senior Software Engineer - Reporting" in live.output
    as_json = runner.invoke(
        cli_app, ["sources", "probe", "siepe", "--kind", "ASHBY", "--json"]
    )
    assert json.loads(as_json.output)["posting_count"] == 10
    dead = runner.invoke(cli_app, ["sources", "probe", "gone", "--kind", "ASHBY"])
    assert dead.exit_code == 1 and "live no" in dead.output


def test_cli_add_disable_enable_round_trip(cli_world) -> None:
    path, _http = cli_world
    runner = CliRunner()
    added = runner.invoke(
        cli_app,
        ["sources", "add", "https://jobs.ashbyhq.com/siepe", "--display-name", "Siepe", "--json"],
    )
    assert added.exit_code == 0, added.output
    assert json.loads(added.output)["source"]["id"] == "ashby-siepe"
    text = path.read_text(encoding="utf-8")
    assert text.startswith(SMALL_REGISTRY.rstrip("\n")) and "display_name: Siepe" in text

    twice = runner.invoke(cli_app, ["sources", "add", "https://jobs.ashbyhq.com/siepe"])
    assert twice.exit_code != 0

    off = runner.invoke(cli_app, ["sources", "disable", "ashby-siepe"])
    assert off.exit_code == 0 and "ashby-siepe is disabled" in off.output
    assert load_registry(path).by_id("ashby-siepe").entry.enabled is False
    on = runner.invoke(cli_app, ["sources", "enable", "ashby-siepe"])
    assert on.exit_code == 0 and "ashby-siepe is enabled" in on.output
    assert load_registry(path).by_id("ashby-siepe").entry.enabled is True


def test_cli_bare_token_without_kind_is_refused(cli_world) -> None:
    from sabra_engine.cli import main

    result = CliRunner().invoke(cli_app, ["sources", "add", "siepe"])
    assert result.exit_code != 0
    assert main  # the real entry point turns the error into one line, not a traceback


# ---------------------------------------------------------------------------
# The API
# ---------------------------------------------------------------------------


def test_api_lists_probes_adds_and_toggles(cli_world) -> None:
    path, _http = cli_world
    client = TestClient(app)

    listed = client.get("/api/sources").json()["sources"]
    assert [s["id"] for s in listed] == ["greenhouse-acme", "lever-beta"]

    probed = client.post("/api/sources/probe", json={"url_or_token": "https://jobs.ashbyhq.com/siepe"})
    assert probed.status_code == 200 and probed.json()["posting_count"] == 10
    assert load_registry(path).by_id("greenhouse-acme")  # probing wrote nothing
    assert len(load_registry(path)) == 2

    dead = client.post("/api/sources/probe", json={"url_or_token": "gone", "kind": "ASHBY"})
    assert dead.status_code == 200 and dead.json()["live"] is False
    refused = client.post("/api/sources", json={"url_or_token": "gone", "kind": "ASHBY"})
    assert refused.status_code == 422 and "did not respond" in refused.json()["detail"]

    created = client.post(
        "/api/sources",
        json={"url_or_token": "https://jobs.ashbyhq.com/siepe", "display_name": "Siepe"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["source"]["display_name"] == "Siepe"
    assert len(load_registry(path)) == 3

    duplicate = client.post("/api/sources", json={"url_or_token": "https://jobs.ashbyhq.com/siepe"})
    assert duplicate.status_code == 409

    off = client.post("/api/sources/ashby-siepe/disable")
    assert off.status_code == 200 and off.json()["source"]["enabled"] is False
    on = client.post("/api/sources/ashby-siepe/enable")
    assert on.json()["source"]["enabled"] is True
    assert client.post("/api/sources/missing/enable").status_code == 404

    bad = client.post("/api/sources/probe", json={"url_or_token": "https://example.com/x"})
    assert bad.status_code == 422 and "could not find a board" in bad.json()["detail"]
    assert client.post("/api/sources/probe", json={"url_or_token": ""}).status_code == 422


# ---------------------------------------------------------------------------
# values YAML would read as something else
# ---------------------------------------------------------------------------

AMBIGUOUS = [
    "0x1F",
    "0b101",
    "0o17",
    "1_000",
    "1e3",
    ".5",
    "1:30",
    "09",
    "+12",
    "-3",
    "~",
    "=",
    "<<",
    ".inf",
    ".NaN",
    "2026-09-16",
    "12:30:45",
    "1,000",
]


@pytest.mark.parametrize("value", AMBIGUOUS)
def test_a_value_yaml_would_read_as_another_type_is_quoted(value) -> None:
    import yaml

    rendered = yaml_scalar(value)
    assert yaml.safe_load("k: " + rendered) == {"k": value}


def test_a_numeric_looking_token_and_industry_survive_the_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    append_source(
        path,
        entry(id="lever-0x1F", kind="LEVER", token="0x1F", industry="1_000", display_name="0b101"),
    )
    source = load_registry(path).by_id("lever-0x1F")
    assert (source.entry.token, source.entry.industry, source.entry.display_name) == (
        "0x1F",
        "1_000",
        "0b101",
    )
    append_source(path, entry(id="ashby-500", token="500", industry="500"))
    assert load_registry(path).by_id("ashby-500").entry.industry == "500"


@pytest.mark.parametrize(
    "bad", ["a\ud800b", "nul\x00byte", "bell\x07", "line sep", "esc\x1b[0m"]
)
def test_characters_that_cannot_be_saved_are_refused(tmp_path: Path, bad) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    with pytest.raises(ValidationFailure, match="cannot be saved"):
        yaml_scalar(bad)
    with pytest.raises(ValidationFailure):
        append_source(path, entry(display_name=bad))
    assert path.read_text(encoding="utf-8") == SMALL_REGISTRY


def test_an_edit_that_would_not_read_back_as_intended_is_refused(
    tmp_path: Path, monkeypatch
) -> None:
    import sabra_providers.registry_file as module

    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    real = module.append_entry_text
    # A plain 0x1F: valid YAML, loads fine, and is silently a different board.
    monkeypatch.setattr(
        module, "append_entry_text", lambda text, e: real(text, e).replace('"0x1F"', "0x1F")
    )
    with pytest.raises(ConfigurationError, match="read back"):
        append_source(path, entry(id="lever-x", kind="LEVER", token="0x1F"))
    assert path.read_text(encoding="utf-8") == SMALL_REGISTRY


# ---------------------------------------------------------------------------
# concurrent and interrupted edits
# ---------------------------------------------------------------------------


def run_threads(*targets) -> list[BaseException]:
    import threading

    errors: list[BaseException] = []
    barrier = threading.Barrier(len(targets))

    def wrap(target):
        def go():
            barrier.wait()
            try:
                target()
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        return go

    threads = [threading.Thread(target=wrap(t)) for t in targets]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return errors


def test_two_threads_editing_different_entries_both_land(tmp_path: Path) -> None:
    for round_number in range(8):
        path = tmp_path / f"sources-{round_number}.yaml"
        path.write_text(SMALL_REGISTRY, encoding="utf-8")
        errors = run_threads(
            lambda p=path: set_source_enabled(p, "greenhouse-acme", False),
            lambda p=path: set_source_enabled(p, "lever-beta", False),
        )
        assert errors == []
        registry = load_registry(path)
        assert [s.entry.enabled for s in registry] == [False, False], round_number
        assert "# -- Group two" in path.read_text(encoding="utf-8")


def test_concurrent_adds_and_toggles_all_land(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    errors = run_threads(
        lambda: append_source(path, entry(id="ashby-one", token="one")),
        lambda: append_source(path, entry(id="ashby-two", token="two")),
        lambda: set_source_enabled(path, "greenhouse-acme", False),
        lambda: append_source(path, entry(id="ashby-three", token="three")),
    )
    assert errors == []
    registry = load_registry(path)
    assert {s.id for s in registry} == {
        "greenhouse-acme",
        "lever-beta",
        "ashby-one",
        "ashby-two",
        "ashby-three",
    }
    assert registry.by_id("greenhouse-acme").entry.enabled is False


def test_two_adds_of_the_same_board_leave_one_entry(tmp_path: Path) -> None:
    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    errors = run_threads(
        lambda: append_source(path, entry()), lambda: append_source(path, entry())
    )
    assert len(errors) == 1 and isinstance(errors[0], ValidationFailure)
    assert "already in the registry" in str(errors[0])
    assert [s.id for s in load_registry(path)].count("ashby-siepe") == 1


def test_a_write_by_another_process_mid_edit_is_not_lost(tmp_path: Path, monkeypatch) -> None:
    import sabra_providers.registry_file as module

    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    real = module._validated
    interfered: list[int] = []

    def validated(new_text, expected, settings):
        real(new_text, expected, settings)
        if not interfered:  # the "other process" writes after we read, before we replace
            interfered.append(1)
            path.write_bytes(path.read_bytes() + b"# written by another process\n")

    monkeypatch.setattr(module, "_validated", validated)
    assert set_source_enabled(path, "greenhouse-acme", False) is True
    text = path.read_text(encoding="utf-8")
    assert "# written by another process" in text, "their write survived"
    assert "enabled: false" in text, "and so did ours"
    assert interfered == [1]


def test_a_file_that_keeps_changing_is_a_configuration_error(tmp_path: Path, monkeypatch) -> None:
    import sabra_providers.registry_file as module

    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    real = module._validated
    counter = [0]

    def validated(new_text, expected, settings):
        real(new_text, expected, settings)
        counter[0] += 1
        path.write_bytes(path.read_bytes() + f"# {counter[0]}\n".encode())

    monkeypatch.setattr(module, "_validated", validated)
    with pytest.raises(ConfigurationError, match="kept changing"):
        set_source_enabled(path, "greenhouse-acme", False)
    assert "enabled: false" not in path.read_text(encoding="utf-8")


def test_the_new_file_is_flushed_before_it_replaces_the_old_one(
    tmp_path: Path, monkeypatch
) -> None:
    import os

    import sabra_providers.registry_file as module

    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    order: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def fsync(fd):
        order.append("fsync")
        real_fsync(fd)

    def replace(src, dst):
        order.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(module.os, "fsync", fsync)
    monkeypatch.setattr(module.os, "replace", replace)
    set_source_enabled(path, "greenhouse-acme", False)
    assert order == ["fsync", "replace"]


def test_a_briefly_locked_file_is_retried_and_a_stuck_one_is_a_clear_error(
    tmp_path: Path, monkeypatch
) -> None:
    import os

    import sabra_providers.registry_file as module

    path = tmp_path / "sources.yaml"
    path.write_text(SMALL_REGISTRY, encoding="utf-8")
    monkeypatch.setattr(module, "_REPLACE_PAUSE", 0)
    real_replace = os.replace
    failures = [2]

    def flaky(src, dst):
        if failures[0] > 0:
            failures[0] -= 1
            raise PermissionError(13, "Access is denied")
        return real_replace(src, dst)

    monkeypatch.setattr(module.os, "replace", flaky)
    assert set_source_enabled(path, "greenhouse-acme", False) is True

    def stuck(src, dst):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(module.os, "replace", stuck)
    before = path.read_bytes()
    with pytest.raises(ConfigurationError, match="could not update the source registry file"):
        set_source_enabled(path, "lever-beta", False)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob(".sources-*")), "no temp file is left behind"


def test_two_quick_clicks_through_the_service_both_land(settings, registry_file) -> None:
    contexts = [
        EngineContext.create(
            settings=settings, database_url=settings.database_url, registry_path=str(registry_file)
        )
        for _ in range(2)
    ]
    try:
        contexts[0].sync_source_registry()
        errors = run_threads(
            lambda: SourcesService(contexts[0]).set_enabled("greenhouse-acme", False),
            lambda: SourcesService(contexts[1]).set_enabled("lever-beta", False),
        )
        assert errors == []
        assert [s.entry.enabled for s in load_registry(registry_file)] == [False, False]
    finally:
        for ctx in contexts:
            ctx.close()
