"""Pipeline and outcomes (PRD sections 22-24): the lifecycle, the board, the API and the CLI."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from p2b_support import api, approve, job_statuses, mark_applied, opportunity_status
from sabra_domain.enums import ApplicationStatus, EntityType, JobStatus, OutcomeKind
from sabra_domain.errors import ValidationFailure
from sabra_engine.cli import app as cli_app
from sabra_engine.ledger import (
    OUTCOME_LABELS,
    PIPELINE_STAGES,
    LedgerService,
    allowed_outcomes,
    normalize_outcome_kind,
    parse_occurred_at,
    pipeline_stage,
)
from typer.testing import CliRunner


def application_events(prepared) -> list:
    with prepared.ctx.read_repos() as repos:
        return list(repos.events.for_entity(EntityType.APPLICATION, prepared.application_id))


def application_status(prepared) -> str:
    with prepared.ctx.read_repos() as repos:
        return repos.applications.get(prepared.application_id).status


# ---------------------------------------------------------------------------
# outcome -> lifecycle
# ---------------------------------------------------------------------------

EXPECTED = {
    "EMPLOYER_RESPONSE": ("RESPONSE", "EMPLOYER_RESPONSE"),
    "RECRUITER_CONTACT": ("RESPONSE", "EMPLOYER_RESPONSE"),
    "PHONE_SCREEN": ("SCREEN", "SCREEN_REQUESTED"),
    "INTERVIEW": ("INTERVIEW", "INTERVIEW_REQUESTED"),
    "OFFER": ("OFFER", "OFFER_RECEIVED"),
    "REJECTION": ("REJECTED_BY_EMPLOYER", "REJECTED"),
    "WITHDRAWAL": ("WITHDRAWN", "WITHDRAWN"),
    "NO_RESPONSE": (None, "NO_RESPONSE"),
}


def test_every_outcome_kind_has_a_mapping_and_a_label() -> None:
    assert set(EXPECTED) == {k.value for k in OutcomeKind} == set(OUTCOME_LABELS)


@pytest.mark.parametrize("kind", sorted(EXPECTED))
def test_each_outcome_moves_the_lifecycle_along_a_graph_edge_and_leaves_an_event(
    prepared, kind
) -> None:
    mark_applied(prepared)
    state, event_type = EXPECTED[kind]

    result = LedgerService(prepared.ctx).record_outcome(prepared.application_id, kind, notes="n")

    assert result["applied_state"] == state and result["note"] is None
    assert opportunity_status(prepared) == (state or "APPLIED")
    assert job_statuses(prepared) == {state or "APPLIED"}
    events = application_events(prepared)
    assert events[-1].event_type == event_type
    assert events[-1].payload["outcome_kind"] == kind
    assert events[-1].payload["applied_state"] == state
    # The application row itself ends at APPLIED -> WITHDRAWN; only a withdrawal moves it.
    expected_application = "WITHDRAWN" if kind == "WITHDRAWAL" else "APPLIED"
    assert application_status(prepared) == expected_application
    assert result["application_status"] == expected_application
    with prepared.ctx.read_repos() as repos:
        assert [o.kind for o in repos.outcomes.for_application(prepared.application_id)] == [kind]


def test_the_lifecycle_follows_a_whole_hiring_process(prepared) -> None:
    mark_applied(prepared)
    ledger = LedgerService(prepared.ctx)
    for kind, expected in (
        ("employer_response", "RESPONSE"),
        ("phone-screen", "SCREEN"),
        ("Interview", "INTERVIEW"),
        ("OFFER", "OFFER"),
    ):
        assert ledger.record_outcome(prepared.application_id, kind)["applied_state"] == expected
    assert opportunity_status(prepared) == "OFFER"
    assert [e.event_type for e in application_events(prepared)][-4:] == [
        "EMPLOYER_RESPONSE",
        "SCREEN_REQUESTED",
        "INTERVIEW_REQUESTED",
        "OFFER_RECEIVED",
    ]


def test_a_kind_the_lifecycle_cannot_follow_is_recorded_without_moving_it(prepared) -> None:
    mark_applied(prepared)
    ledger = LedgerService(prepared.ctx)
    ledger.record_outcome(prepared.application_id, "OFFER")
    late = ledger.record_outcome(prepared.application_id, "PHONE_SCREEN", notes="stale email")
    assert late["applied_state"] is None
    assert "OFFER" in late["note"] and "SCREEN" in late["note"]
    assert opportunity_status(prepared) == "OFFER"
    with prepared.ctx.read_repos() as repos:
        assert len(repos.outcomes.for_application(prepared.application_id)) == 2


def test_a_withdrawal_ends_application_opportunity_and_postings_together(prepared) -> None:
    mark_applied(prepared)
    result = LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")
    assert result["applied_state"] == "WITHDRAWN"
    assert application_status(prepared) == ApplicationStatus.WITHDRAWN.value
    assert opportunity_status(prepared) == JobStatus.WITHDRAWN.value
    assert job_statuses(prepared) == {JobStatus.WITHDRAWN.value}
    assert application_events(prepared)[-1].payload["application_state"] == "WITHDRAWN"
    # Terminal: nothing is recorded against it any more.
    with pytest.raises(ValidationFailure, match="only a submitted application"):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, "INTERVIEW")
    with pytest.raises(ValidationFailure, match="already withdrawn"):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")


def test_an_approved_application_can_be_withdrawn_before_it_is_sent(prepared) -> None:
    approve(prepared)
    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")
    assert application_status(prepared) == ApplicationStatus.WITHDRAWN.value
    assert opportunity_status(prepared) == JobStatus.WITHDRAWN.value


def test_the_contact_stays_on_the_outcome_and_off_the_event(prepared) -> None:
    mark_applied(prepared)
    LedgerService(prepared.ctx).record_outcome(
        prepared.application_id, "RECRUITER_CONTACT", contact="  Pat Lee, Acme  "
    )
    with prepared.ctx.read_repos() as repos:
        outcome = repos.outcomes.for_application(prepared.application_id)[0]
    assert outcome.payload == {
        "contact": "Pat Lee, Acme",
        "application_status_at_record": "APPLIED",
    }
    assert "Pat Lee" not in json.dumps(application_events(prepared)[-1].payload)
    ledger = LedgerService(prepared.ctx).application_ledger(prepared.application_id)
    assert ledger.outcomes[0]["payload"]["contact"] == "Pat Lee, Acme"


def test_a_backdated_outcome_stamps_the_outcome_and_the_event(prepared) -> None:
    mark_applied(prepared)
    earlier = datetime.now(UTC) - timedelta(days=3)
    result = LedgerService(prepared.ctx).record_outcome(
        prepared.application_id, "INTERVIEW", occurred_at=earlier
    )
    assert result["occurred_at"] == earlier.isoformat()
    assert application_events(prepared)[-1].occurred_at == earlier


def test_an_outcome_in_the_future_or_of_an_unknown_kind_is_refused(prepared) -> None:
    mark_applied(prepared)
    ledger = LedgerService(prepared.ctx)
    with pytest.raises(ValidationFailure, match="future"):
        ledger.record_outcome(
            prepared.application_id, "INTERVIEW", occurred_at=datetime.now(UTC) + timedelta(days=2)
        )
    with pytest.raises(ValidationFailure, match="unknown outcome kind"):
        ledger.record_outcome(prepared.application_id, "ghosted")
    with prepared.ctx.read_repos() as repos:
        assert repos.outcomes.for_application(prepared.application_id) == []
    assert opportunity_status(prepared) == "APPLIED"


def test_kind_and_date_parsing() -> None:
    assert normalize_outcome_kind(" phone-screen ") == "PHONE_SCREEN"
    assert normalize_outcome_kind("No response") == "NO_RESPONSE"
    assert parse_occurred_at(None) is None and parse_occurred_at("") is None
    aware = parse_occurred_at("2026-10-20T14:30:00+00:00")
    assert aware == datetime(2026, 10, 20, 14, 30, tzinfo=UTC)
    local = parse_occurred_at("2026-10-20")
    assert local is not None and local.tzinfo is not None
    assert local.astimezone().date().isoformat() == "2026-10-20"
    with pytest.raises(ValidationFailure, match="not a date"):
        parse_occurred_at("last tuesday")


# ---------------------------------------------------------------------------
# stages and the board
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("application", "opportunity", "stage"),
    [
        ("DRAFT", "AWAITING_APPROVAL", None),
        ("AWAITING_APPROVAL", "AWAITING_APPROVAL", None),
        ("WITHDRAWN", "REJECTED", None),
        ("APPROVED", "APPROVED", "approved"),
        ("APPLYING", "APPLYING", "applying"),
        ("BLOCKED", "APPROVED", "applying"),
        ("FAILED", "FAILED", "applying"),
        ("APPLIED", "APPLIED", "applied"),
        ("APPLIED", "RESPONSE", "response"),
        ("APPLIED", "SCREEN", "screen"),
        ("APPLIED", "INTERVIEW", "interview"),
        ("APPLIED", "OFFER", "offer"),
        ("APPLIED", "REJECTED_BY_EMPLOYER", "rejected"),
        ("WITHDRAWN", "WITHDRAWN", "withdrawn"),
        ("APPROVED", "EXPIRED", "expired"),
    ],
)
def test_pipeline_stage_is_a_projection_of_both_statuses(application, opportunity, stage) -> None:
    assert pipeline_stage(application, opportunity) == stage


def test_the_stages_are_the_ten_the_page_shows() -> None:
    assert [key for key, _ in PIPELINE_STAGES] == [
        "approved",
        "applying",
        "applied",
        "response",
        "screen",
        "interview",
        "offer",
        "rejected",
        "withdrawn",
        "expired",
    ]


def test_allowed_outcomes_follow_the_lifecycle() -> None:
    applied = allowed_outcomes("APPLIED", "APPLIED")
    assert set(applied) == set(EXPECTED)
    assert "EMPLOYER_RESPONSE" in allowed_outcomes("APPLIED", "RESPONSE")  # a further word
    assert "PHONE_SCREEN" not in allowed_outcomes("APPLIED", "INTERVIEW")
    assert "RECRUITER_CONTACT" in allowed_outcomes("APPLIED", "INTERVIEW")
    assert allowed_outcomes("APPROVED", "APPROVED") == ["WITHDRAWAL"]
    assert allowed_outcomes("APPLYING", "APPLYING") == []
    assert allowed_outcomes("APPLIED", "REJECTED_BY_EMPLOYER") == []
    assert allowed_outcomes("APPLIED", "NOT_A_STATE") == []


def test_the_board_lists_applications_past_approval_by_stage(prepared) -> None:
    ledger = LedgerService(prepared.ctx)
    assert all(stage["applications"] == [] for stage in ledger.pipeline(prepared.candidate_id).board)

    mark_applied(prepared)
    ledger.record_outcome(prepared.application_id, "PHONE_SCREEN", notes="30 minutes")
    summary = ledger.pipeline(prepared.candidate_id)
    board = {stage["key"]: stage for stage in summary.board}
    assert [s["key"] for s in summary.board] == [key for key, _ in PIPELINE_STAGES]
    assert board["screen"]["count"] == 1
    row = board["screen"]["applications"][0]
    assert row["company"] == "Revance Therapeutics"
    assert row["title"].startswith("Regional Sales Manager")
    assert row["application_id"] == prepared.application_id
    assert row["package_id"] == prepared.package_id
    assert row["submission_method"] == "HEADED_HUMAN" and row["submitted_at"]
    assert row["last_event"]["event_type"] == "SCREEN_REQUESTED"
    assert "outcome kind: PHONE_SCREEN" in row["last_event"]["summary"]
    assert row["latest_outcome"] == "PHONE_SCREEN" and row["outcome_count"] == 1
    assert "INTERVIEW" in row["allowed_outcomes"] and "PHONE_SCREEN" not in row["allowed_outcomes"]
    assert sum(s["count"] for s in summary.board) == 1
    assert summary.as_dict()["board"] == summary.board
    # The older summary fields are still there.
    assert summary.as_dict()["counts"]["submitted"] == 1


def test_an_application_passed_on_before_approval_is_not_in_the_pipeline(prepared) -> None:
    from sabra_engine.intelligence import pass_opportunity

    pass_opportunity(prepared.ctx, prepared.opportunity_id)
    board = LedgerService(prepared.ctx).pipeline(prepared.candidate_id).board
    assert all(stage["applications"] == [] for stage in board)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_the_pipeline_endpoint_returns_the_board_and_the_outcome_labels(
    prepared, monkeypatch
) -> None:
    client = api(prepared, monkeypatch)
    mark_applied(prepared)
    body = client.get("/api/pipeline").json()
    assert body["candidate_id"] == prepared.candidate_id
    assert [s["key"] for s in body["board"]][:3] == ["approved", "applying", "applied"]
    applied = next(s for s in body["board"] if s["key"] == "applied")
    assert applied["applications"][0]["application_id"] == prepared.application_id
    assert {"kind": "PHONE_SCREEN", "label": "Phone screen"} in body["outcome_kinds"]
    assert body["counts"]["applications"] == 1


def test_the_pipeline_endpoint_on_an_empty_database(monkeypatch) -> None:
    from fastapi.testclient import TestClient
    from sabra_api.main import app

    body = TestClient(app).get("/api/pipeline").json()
    assert body["board"] == [] and body["candidate_id"] is None and body["outcome_kinds"]


def test_recording_an_outcome_over_http(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    mark_applied(prepared)
    response = client.post(
        f"/api/applications/{prepared.application_id}/outcomes",
        json={
            "kind": "interview",
            "note": "Panel on Friday",
            "contact": "Pat Lee",
            "occurred_at": "2026-10-01T09:00:00+00:00",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["kind"] == "INTERVIEW" and body["applied_state"] == "INTERVIEW"
    assert body["occurred_at"] == "2026-10-01T09:00:00+00:00"
    assert opportunity_status(prepared) == "INTERVIEW"
    ledger = client.get(f"/api/applications/{prepared.application_id}/ledger").json()
    assert ledger["status"] == "APPLIED"
    assert ledger["outcomes"][0]["kind"] == "INTERVIEW" and ledger["outcomes"][0]["notes"]
    assert ledger["events"][-1]["event_type"] == "INTERVIEW_REQUESTED"
    assert ledger["application_package"]["content_hash"] == prepared.package_hash


def test_outcome_errors_are_json_the_page_can_show(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    url = f"/api/applications/{prepared.application_id}/outcomes"
    bad_kind = client.post(url, json={"kind": "ghosted"})
    assert bad_kind.status_code == 422 and "unknown outcome kind" in bad_kind.json()["detail"]
    tomorrow = (datetime.now(UTC) + timedelta(days=2)).date().isoformat()
    future = client.post(url, json={"kind": "interview", "occurred_at": tomorrow})
    assert future.status_code == 422 and "future" in future.json()["detail"]
    bad_date = client.post(url, json={"kind": "interview", "occurred_at": "soon"})
    assert bad_date.status_code == 422 and "not a date" in bad_date.json()["detail"]
    assert client.post(url, json={}).status_code == 422
    assert (
        client.post("/api/applications/app_missing/outcomes", json={"kind": "offer"}).status_code
        == 404
    )
    assert client.get("/api/applications/app_missing/ledger").status_code == 404


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def run(prepared, monkeypatch, *args):
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    return CliRunner().invoke(cli_app, list(args))


def test_sabra_outcome_records_and_moves_the_lifecycle(prepared, monkeypatch) -> None:
    mark_applied(prepared)
    result = run(
        prepared,
        monkeypatch,
        "outcome",
        prepared.application_id,
        "phone-screen",
        "--note",
        "recruiter call",
        "--at",
        "2026-10-02T10:00:00+00:00",
        "--json",
    )
    assert result.exit_code == 0, result.output
    body = json.loads(result.stdout)
    assert body["kind"] == "PHONE_SCREEN" and body["applied_state"] == "SCREEN"
    assert body["occurred_at"] == "2026-10-02T10:00:00+00:00"
    assert opportunity_status(prepared) == "SCREEN"


def test_sabra_outcome_refuses_a_bad_kind_and_an_unknown_application(
    prepared, monkeypatch
) -> None:
    bad = run(prepared, monkeypatch, "outcome", prepared.application_id, "ghosted")
    assert bad.exit_code != 0 and isinstance(bad.exception, ValidationFailure)
    missing = run(prepared, monkeypatch, "outcome", "app_missing", "offer")
    assert missing.exit_code != 0


def test_sabra_pipeline_prints_json_and_a_table(prepared, monkeypatch) -> None:
    empty = run(prepared, monkeypatch, "pipeline")
    assert empty.exit_code == 0 and "Nothing is past approval yet." in empty.output

    mark_applied(prepared)
    as_json = run(prepared, monkeypatch, "pipeline", "--json")
    body = json.loads(as_json.stdout)
    applied = next(s for s in body["board"] if s["key"] == "applied")
    assert applied["applications"][0]["application_id"] == prepared.application_id

    table = run(prepared, monkeypatch, "pipeline")
    assert table.exit_code == 0
    assert "Revance" in table.output and "Applied" in table.output
