"""Learning dataset export (PRD section 25): JSON lines with no personal contact data."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from p2b_support import api, mark_applied
from sabra_api.main import app
from sabra_domain.errors import ValidationFailure
from sabra_engine.cli import app as cli_app
from sabra_engine.ledger import LedgerService, _strip_identity
from typer.testing import CliRunner

# Everything the fixture candidate is, and what the owner typed about an employer's people.
PERSONAL = (
    "jordan.rivera@example.test",
    "(214) 555-0142",
    "555-0142",
    "RIVERA",
    "Rivera",
    "JORDAN",
    "Test Candidate",
    "Pat Lee",
    "recruiter call",
    "pat.lee@acme.test",
)


def with_outcome(prepared) -> None:
    mark_applied(prepared)
    LedgerService(prepared.ctx).record_outcome(
        prepared.application_id,
        "PHONE_SCREEN",
        notes="recruiter call with Pat Lee, pat.lee@acme.test",
        contact="Pat Lee, pat.lee@acme.test",
    )


def assert_no_personal_data(text: str, prepared) -> None:
    lowered = text.lower()
    for needle in (*PERSONAL, prepared.candidate_id):
        assert needle.lower() not in lowered, needle


def test_the_export_has_the_evidence_and_none_of_the_identity(prepared) -> None:
    with_outcome(prepared)
    records = LedgerService(prepared.ctx).export_learning_records(prepared.candidate_id)
    assert len(records) == 1
    record = records[0]
    assert set(record) == {
        "application_id",
        "job_features",
        "candidate_features",
        "strategy",
        "artifacts",
        "outcome",
        "outcome_recorded_at",
    }
    assert record["application_id"] == prepared.application_id
    assert record["job_features"]["title"].startswith("Regional Sales Manager")
    assert record["job_features"]["company"] == "Revance Therapeutics"
    assert record["candidate_features"]["populated"] is True
    assert record["strategy"]["submission_method"] == "HEADED_HUMAN"
    assert record["strategy"]["rank_score"] == pytest.approx(0.71)
    assert record["artifacts"]["package_id"] == prepared.package_id
    assert record["artifacts"]["content_hash"] == prepared.package_hash
    assert record["outcome"] == "PHONE_SCREEN" and record["outcome_recorded_at"]
    assert "candidate_id" not in record["candidate_features"]
    assert_no_personal_data(json.dumps(records), prepared)


def test_an_application_with_no_outcome_exports_with_a_null_outcome(prepared) -> None:
    record = LedgerService(prepared.ctx).export_learning_records(prepared.candidate_id)[0]
    assert record["outcome"] is None and record["outcome_recorded_at"] is None


def test_jsonl_is_one_sorted_record_per_line(prepared) -> None:
    with_outcome(prepared)
    text = LedgerService(prepared.ctx).learning_jsonl(prepared.candidate_id)
    lines = text.splitlines()
    assert text.endswith("\n") and len(lines) == 1
    assert json.loads(lines[0])["outcome"] == "PHONE_SCREEN"
    assert list(json.loads(lines[0])) == sorted(json.loads(lines[0]))
    assert LedgerService(prepared.ctx).learning_jsonl("cand_empty") == ""


def test_strip_identity_removes_identity_keys_anywhere_and_scrubs_emails() -> None:
    cleaned = _strip_identity(
        {
            "candidate_id": "c1",
            "name": "N",
            "email": "a@b.test",
            "applicant_phone": "1",
            "keep": {"full_name": "X", "city": "Dallas", "note": "write to me@x.test now"},
            "rows": [{"phone": "1", "ok": 3}],
        }
    )
    assert cleaned == {
        "keep": {"city": "Dallas", "note": "write to [removed] now"},
        "rows": [{"ok": 3}],
    }


def test_an_email_that_reaches_a_feature_is_scrubbed_on_the_way_out(prepared, monkeypatch) -> None:
    with_outcome(prepared)
    original = LedgerService.learning_records

    def leaky(self, candidate_id):
        records = original(self, candidate_id)
        records[0].job_features["department"] = "Sales, jordan.rivera@example.test"
        return records

    monkeypatch.setattr(LedgerService, "learning_records", leaky)
    text = LedgerService(prepared.ctx).learning_jsonl(prepared.candidate_id)
    assert "jordan.rivera@example.test" not in text and "[removed]" in text


# ---------------------------------------------------------------------------
# file and CLI
# ---------------------------------------------------------------------------


def test_the_export_file_is_written_atomically_and_not_overwritten(prepared, tmp_path) -> None:
    with_outcome(prepared)
    target = tmp_path / "out" / "learning.jsonl"
    result = LedgerService(prepared.ctx).write_learning_export(prepared.candidate_id, target)
    assert result == {"path": str(target), "records": 1}
    assert_no_personal_data(target.read_text(encoding="utf-8"), prepared)
    assert [p.name for p in target.parent.iterdir()] == ["learning.jsonl"]

    with pytest.raises(ValidationFailure, match="already exists"):
        LedgerService(prepared.ctx).write_learning_export(prepared.candidate_id, target)
    target.write_text("old", encoding="utf-8")
    LedgerService(prepared.ctx).write_learning_export(
        prepared.candidate_id, target, overwrite=True
    )
    assert target.read_text(encoding="utf-8").startswith("{")


def test_sabra_ledger_export_writes_the_file(prepared, monkeypatch, tmp_path) -> None:
    with_outcome(prepared)
    monkeypatch.setattr("sabra_engine.context.get_settings", lambda: prepared.ctx.settings)
    target = tmp_path / "learning.jsonl"
    runner = CliRunner()

    first = runner.invoke(cli_app, ["ledger", "export", str(target), "--json"])
    assert first.exit_code == 0, first.output
    assert json.loads(first.stdout) == {"path": str(target), "records": 1}
    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["outcome"] == "PHONE_SCREEN"
    assert_no_personal_data(target.read_text(encoding="utf-8"), prepared)

    again = runner.invoke(cli_app, ["ledger", "export", str(target)])
    assert again.exit_code != 0 and isinstance(again.exception, ValidationFailure)
    forced = runner.invoke(cli_app, ["ledger", "export", str(target), "--force", "--json"])
    assert forced.exit_code == 0, forced.output


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_the_learning_records_endpoint_returns_json_lines_as_an_attachment(prepared, monkeypatch) -> None:
    with_outcome(prepared)
    response = api(prepared, monkeypatch).get("/api/learning-records")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert "attachment" in response.headers["content-disposition"]
    assert ".jsonl" in response.headers["content-disposition"]
    rows = [json.loads(line) for line in response.text.splitlines()]
    assert len(rows) == 1 and rows[0]["outcome"] == "PHONE_SCREEN"
    assert_no_personal_data(response.text, prepared)


def test_the_learning_records_endpoint_with_no_candidate_is_empty() -> None:
    response = TestClient(app).get("/api/learning-records")
    assert response.status_code == 200 and response.text == ""
