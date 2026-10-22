"""Outcome recording is all-or-nothing, board queries are bounded, and the review nits."""

from __future__ import annotations

import itertools
import json
from datetime import UTC, datetime, timedelta

import pytest
from conftest_app import build_job
from p2b_support import api, approve, mark_applied
from sabra_domain.enums import ApplicationStatus, EntityType, EventType, JobStatus
from sabra_domain.errors import ValidationFailure
from sabra_engine.cli import app as cli_app
from sabra_engine.ledger import (
    NOTE_MAX,
    LedgerService,
    allowed_outcomes,
)
from sabra_engine.package_review import package_actions
from sqlalchemy import event
from typer.testing import CliRunner


def walk(prepared, *steps: ApplicationStatus) -> None:
    with prepared.ctx.repos() as repos:
        for step in steps:
            repos.applications.set_status(prepared.application_id, step)


def snapshot(prepared) -> dict:
    """Everything a refused outcome must leave exactly as it was."""

    with prepared.ctx.read_repos() as repos:
        application = repos.applications.get(prepared.application_id)
        return {
            "application": application.status,
            "opportunity": repos.opportunities.get(prepared.opportunity_id).status,
            "jobs": sorted(j.status for j in repos.opportunities.list_jobs(prepared.opportunity_id)),
            "outcomes": len(repos.outcomes.for_application(prepared.application_id)),
            "events": len(repos.events.for_entity(EntityType.APPLICATION, prepared.application_id)),
        }


# ---------------------------------------------------------------------------
# 1. a withdrawal moves both or neither
# ---------------------------------------------------------------------------


def test_withdrawing_while_a_submission_is_in_progress_moves_nothing(prepared) -> None:
    approve(prepared)
    walk(prepared, ApplicationStatus.APPLYING)
    assert snapshot(prepared)["opportunity"] == JobStatus.APPROVED.value  # as a real attempt leaves it
    before = snapshot(prepared)

    with pytest.raises(ValidationFailure, match="cannot withdraw: a submission is in progress"):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, "withdrawal")

    assert snapshot(prepared) == before
    assert before["application"] == "APPLYING" and before["opportunity"] == "APPROVED"


def test_a_refused_withdrawal_is_a_409_over_http_and_non_zero_on_the_command_line(
    prepared, monkeypatch
) -> None:
    approve(prepared)
    walk(prepared, ApplicationStatus.APPLYING)
    before = snapshot(prepared)

    response = api(prepared, monkeypatch).post(
        f"/api/applications/{prepared.application_id}/outcomes", json={"kind": "withdrawal"}
    )
    assert response.status_code == 409
    assert "cannot withdraw" in response.json()["detail"]

    result = CliRunner().invoke(cli_app, ["outcome", prepared.application_id, "withdrawal"])
    assert result.exit_code != 0 and isinstance(result.exception, ValidationFailure)
    assert snapshot(prepared) == before


def test_a_withdrawal_the_opportunity_graph_forbids_moves_nothing(prepared) -> None:
    mark_applied(prepared)
    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "rejection")
    before = snapshot(prepared)
    with pytest.raises(ValidationFailure, match="REJECTED_BY_EMPLOYER, which cannot be withdrawn"):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, "withdrawal")
    assert snapshot(prepared) == before


# ---------------------------------------------------------------------------
# 2. employer outcomes need a submitted application
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["OFFER", "INTERVIEW", "NO_RESPONSE", "RECRUITER_CONTACT"])
def test_an_unsubmitted_application_takes_no_employer_outcome(prepared, kind) -> None:
    approve(prepared)
    before = snapshot(prepared)
    with pytest.raises(ValidationFailure, match="only a submitted application"):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, kind)
    assert snapshot(prepared) == before
    assert LedgerService(prepared.ctx).export_learning_records(prepared.candidate_id)[0][
        "outcome"
    ] is None


def test_a_premature_outcome_is_a_422_over_http(prepared, monkeypatch) -> None:
    response = api(prepared, monkeypatch).post(
        f"/api/applications/{prepared.application_id}/outcomes", json={"kind": "offer"}
    )
    assert response.status_code == 422 and "AWAITING_APPROVAL" in response.json()["detail"]


def test_the_outcome_remembers_the_application_status_it_was_recorded_against(prepared) -> None:
    mark_applied(prepared)
    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "INTERVIEW")
    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")
    with prepared.ctx.read_repos() as repos:
        rows = repos.outcomes.for_application(prepared.application_id)
    assert [r.payload["application_status_at_record"] for r in rows] == ["APPLIED", "APPLIED"]


def test_an_approved_application_is_withdrawn_and_remembers_it_was_approved(prepared) -> None:
    approve(prepared)
    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")
    with prepared.ctx.read_repos() as repos:
        row = repos.outcomes.for_application(prepared.application_id)[0]
    assert row.payload["application_status_at_record"] == "APPROVED"


# ---------------------------------------------------------------------------
# 3. a withdrawal that may follow a send is refused, as a reject is
# ---------------------------------------------------------------------------


def unconfirmed_click(prepared) -> None:
    with prepared.ctx.repos() as repos:
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.APPLICATION_STARTED,
            {},
        )
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.APPLICATION_UNCONFIRMED,
            {"message": "clicked"},
        )


def board_row(prepared) -> dict:
    board = LedgerService(prepared.ctx).pipeline(prepared.candidate_id).board
    rows = [r for stage in board for r in stage["applications"]]
    assert len(rows) == 1
    return rows[0]


@pytest.mark.parametrize("final", [ApplicationStatus.FAILED, ApplicationStatus.BLOCKED])
def test_withdrawing_a_failed_or_blocked_application_that_may_have_been_sent_is_refused(
    prepared, final
) -> None:
    approve(prepared)
    walk(prepared, ApplicationStatus.APPLYING, final)
    assert "WITHDRAWAL" in board_row(prepared)["allowed_outcomes"]  # nothing suggests a send yet

    unconfirmed_click(prepared)
    before = snapshot(prepared)
    assert "WITHDRAWAL" not in board_row(prepared)["allowed_outcomes"]
    with pytest.raises(ValidationFailure, match="unconfirmed and may have reached the employer"):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")
    assert snapshot(prepared) == before


def test_a_failed_application_nothing_was_sent_for_can_be_withdrawn(prepared) -> None:
    approve(prepared)
    walk(prepared, ApplicationStatus.APPLYING, ApplicationStatus.FAILED)
    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")
    assert snapshot(prepared)["application"] == "WITHDRAWN"


def test_a_failed_application_with_a_recorded_submission_cannot_be_withdrawn(prepared) -> None:
    approve(prepared)
    walk(prepared, ApplicationStatus.APPLYING, ApplicationStatus.FAILED)
    with prepared.ctx.repos() as repos:
        repos.applications.get(prepared.application_id).submission_time = datetime.now(UTC)
    with pytest.raises(ValidationFailure, match="already submitted"):
        LedgerService(prepared.ctx).record_outcome(prepared.application_id, "WITHDRAWAL")


def test_allowed_outcomes_can_withhold_the_withdrawal() -> None:
    assert allowed_outcomes("FAILED", "FAILED") == ["WITHDRAWAL"]
    assert allowed_outcomes("FAILED", "FAILED", may_withdraw=False) == []


# ---------------------------------------------------------------------------
# 4. bounded queries
# ---------------------------------------------------------------------------


_SERIAL = itertools.count(1)


def add_applications(prepared, count: int, status: str = "APPLIED") -> None:
    with prepared.ctx.repos() as repos:
        for _ in range(count):
            index = next(_SERIAL)
            row, _, _ = repos.jobs.upsert_canonical(
                build_job(source_job_id=f"bulk-{index}", title=f"Bulk role {status} {index}"),
                raw_payload={},
            )
            row.status = str(JobStatus.NORMALIZED)
            opportunity = repos.opportunities.create(
                candidate_id=prepared.candidate_id,
                search_profile_id=prepared.search_profile_id,
                canonical_key=row.canonical_key,
                title=row.title,
                company=row.company,
                location=row.location,
                primary_job_id=row.id,
                source_ids=[],
            )
            row.opportunity_id = opportunity.id
            opportunity.status = status if status == "APPLIED" else "AWAITING_APPROVAL"
            application = repos.applications.create(
                opportunity_id=opportunity.id,
                package_id=prepared.package_id,
                job_id=row.id,
                candidate_id=prepared.candidate_id,
                master_resume_id=None,
                tailored_resume_id=None,
                answers=[],
            )
            application.status = status
            if status == "APPLIED":
                application.submission_time = datetime.now(UTC)
            repos.events.append(
                EntityType.APPLICATION, application.id, EventType.PACKAGE_APPROVED, {"n": index}
            )
            repos.events.append(
                EntityType.APPLICATION, application.id, EventType.APPLICATION_SUBMITTED, {"n": index}
            )


class QueryCounter:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.statements: list[str] = []

    def _count(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.statements.append(statement)

    def __enter__(self) -> QueryCounter:
        event.listen(self.engine, "before_cursor_execute", self._count)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._count)


def pipeline_queries(prepared) -> int:
    with QueryCounter(prepared.ctx.db.engine) as counter:
        LedgerService(prepared.ctx).pipeline(prepared.candidate_id)
    return len(counter.statements)


def test_the_board_issues_the_same_queries_for_3_applications_as_for_30(prepared) -> None:
    add_applications(prepared, 3)
    small = pipeline_queries(prepared)
    add_applications(prepared, 27)
    add_applications(prepared, 5, status="AWAITING_APPROVAL")  # on no board, loaded nowhere
    large = pipeline_queries(prepared)
    assert small == large
    assert large <= 8


def test_the_board_holds_every_application_and_only_those_past_approval(prepared) -> None:
    add_applications(prepared, 4)
    add_applications(prepared, 2, status="AWAITING_APPROVAL")
    summary = LedgerService(prepared.ctx).pipeline(prepared.candidate_id)
    applied = next(s for s in summary.board if s["key"] == "applied")
    assert applied["count"] == 4
    assert sum(s["count"] for s in summary.board) == 4
    assert summary.counts["opportunities"] == 7 and summary.counts["applications"] == 7
    assert summary.opportunities_by_status["APPLIED"] == 4
    assert applied["applications"][0]["last_event"]["event_type"] == "APPLICATION_SUBMITTED"


def test_the_board_keeps_each_applications_own_latest_outcome(prepared) -> None:
    mark_applied(prepared)
    ledger = LedgerService(prepared.ctx)
    ledger.record_outcome(prepared.application_id, "EMPLOYER_RESPONSE")
    ledger.record_outcome(prepared.application_id, "PHONE_SCREEN")
    add_applications(prepared, 2)
    summary = ledger.pipeline(prepared.candidate_id)
    rows = {r["application_id"]: r for s in summary.board for r in s["applications"]}
    assert rows[prepared.application_id]["latest_outcome"] == "PHONE_SCREEN"
    assert rows[prepared.application_id]["outcome_count"] == 2
    assert {r["outcome_count"] for k, r in rows.items() if k != prepared.application_id} == {0}


def test_latest_for_entities_returns_each_entitys_newest_event_only(prepared) -> None:
    with prepared.ctx.repos() as repos:
        repos.events.append("application", "a1", EventType.PACKAGE_APPROVED, {"n": 1})
        repos.events.append("application", "a2", EventType.PACKAGE_APPROVED, {"n": 2})
        newest = repos.events.append("application", "a1", EventType.APPLICATION_STARTED, {"n": 3})
        repos.events.append("job", "a1", EventType.JOB_DISCOVERED, {"n": 4})  # another type
        other = repos.events.append("application", "a3", EventType.APPLICATION_FAILED, {"n": 5})
    with prepared.ctx.read_repos() as repos:
        latest = repos.events.latest_for_entities("application", ["a1", "a2", "a3", "a4", "a1"])
        assert set(latest) == {"a1", "a2", "a3"}
        assert latest["a1"].seq == newest.seq and latest["a1"].payload == {"n": 3}
        assert latest["a2"].payload == {"n": 2} and latest["a3"].seq == other.seq
        assert repos.events.latest_for_entities("application", []) == {}
        # More ids than one query takes: every chunk is read.
        many = [f"none-{i}" for i in range(900)] + ["a1", "a2"]
        assert set(repos.events.latest_for_entities("application", many)) == {"a1", "a2"}


def test_latest_for_entities_loads_one_row_per_entity(prepared) -> None:
    with prepared.ctx.repos() as repos:
        for n in range(40):
            repos.events.append("application", "long", EventType.PACKAGE_APPROVED, {"n": n})
    with prepared.ctx.read_repos() as repos:
        latest = repos.events.latest_for_entities("application", ["long"])
        assert latest["long"].payload == {"n": 39}
        sql = []

        def grab(conn, cursor, statement, parameters, context, executemany):
            sql.append(statement)

        event.listen(prepared.ctx.db.engine, "before_cursor_execute", grab)
        try:
            repos.events.latest_for_entities("application", ["long"])
        finally:
            event.remove(prepared.ctx.db.engine, "before_cursor_execute", grab)
    assert len(sql) == 1 and "max(" in sql[0].lower() and "group by" in sql[0].lower()


# ---------------------------------------------------------------------------
# 5. rank total is a scoped count
# ---------------------------------------------------------------------------


def test_the_rank_total_counts_only_this_searchs_ranked_opportunities(prepared) -> None:
    from p2b_support import seed_scoring
    from sabra_engine.opportunity_detail import OpportunityDetailService

    seed_scoring(prepared)  # ranks this opportunity: 1 of 1
    add_applications(prepared, 3)  # unranked
    with prepared.ctx.repos() as repos:
        extra = repos.opportunities.list_for_candidate(prepared.candidate_id)
        ranked_elsewhere = [o for o in extra if o.id != prepared.opportunity_id][:2]
        ranked_elsewhere[0].search_profile_id = "srch_other"
        ranked_elsewhere[0].rank_score = 0.5
        ranked_elsewhere[1].rank_score = 0.4  # same search, ranked
    body = OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)
    assert body["rank"]["of"] == 2

    with QueryCounter(prepared.ctx.db.engine) as counter:
        OpportunityDetailService(prepared.ctx).detail(prepared.opportunity_id)
    assert not [s for s in counter.statements if "rank_score DESC" in s]


# ---------------------------------------------------------------------------
# nits
# ---------------------------------------------------------------------------


def test_an_outcome_cannot_predate_the_submission(prepared) -> None:
    mark_applied(prepared, submitted_days_ago=10)
    ledger = LedgerService(prepared.ctx)
    with pytest.raises(ValidationFailure, match="before the application was submitted"):
        ledger.record_outcome(
            prepared.application_id, "INTERVIEW", occurred_at=datetime.now(UTC) - timedelta(days=12)
        )
    assert snapshot(prepared)["outcomes"] == 0
    # A date typed without a time may land hours before the submission; a day of slack.
    ledger.record_outcome(
        prepared.application_id, "INTERVIEW", occurred_at=datetime.now(UTC) - timedelta(days=9)
    )
    ledger.record_outcome(
        prepared.application_id,
        "OFFER",
        occurred_at=datetime.now(UTC) - timedelta(days=10, hours=3),
    )


def test_note_contact_and_kind_have_one_limit_everywhere(prepared, monkeypatch) -> None:
    mark_applied(prepared)
    with pytest.raises(ValidationFailure, match="limit is 4000"):
        LedgerService(prepared.ctx).record_outcome(
            prepared.application_id, "INTERVIEW", notes="x" * (NOTE_MAX + 1)
        )
    LedgerService(prepared.ctx).record_outcome(
        prepared.application_id, "INTERVIEW", notes="x" * NOTE_MAX
    )
    client = api(prepared, monkeypatch)
    url = f"/api/applications/{prepared.application_id}/outcomes"
    assert client.post(url, json={"kind": "offer", "note": "x" * (NOTE_MAX + 1)}).status_code == 422
    assert client.post(url, json={"kind": "offer", "contact": "x" * 501}).status_code == 422
    assert client.post(url, json={"kind": "X" * 41}).status_code == 422
    result = CliRunner().invoke(
        cli_app, ["outcome", prepared.application_id, "offer", "--note", "x" * (NOTE_MAX + 1)]
    )
    assert result.exit_code != 0 and isinstance(result.exception, ValidationFailure)
    from sabra_api.review_pages import PIPELINE_BODY

    assert 'maxlength="4000" aria-label="Note"' in PIPELINE_BODY


def test_the_handoffs_filter_uses_the_same_rule_as_the_today_count(prepared, monkeypatch) -> None:
    client = api(prepared, monkeypatch)
    approve(prepared)
    walk(prepared, ApplicationStatus.APPLYING)

    def attention() -> str | None:
        return client.get("/api/packages").json()["packages"][0]["attention"]

    # A run that has not stopped is not waiting for the person.
    assert attention() is None
    assert client.get("/api/today").json()["counts"]["handoffs_waiting"] == 0

    with prepared.ctx.repos() as repos:
        repos.events.append(
            EntityType.APPLICATION,
            prepared.application_id,
            EventType.HANDED_TO_HUMAN,
            {"state": "needs_report"},
        )
    assert attention() == "handoffs"
    assert client.get("/api/today").json()["counts"]["handoffs_waiting"] == 1


def test_reject_is_not_offered_for_an_expired_posting_or_a_withdrawn_application(prepared) -> None:
    with prepared.ctx.read_repos() as repos:
        row = repos.packages.get(prepared.package_id)
        application = repos.applications.get(prepared.application_id)
        assert package_actions(row, application, "AWAITING_APPROVAL")["can_reject"] is True
        expired = package_actions(row, application, "EXPIRED")
    assert expired["can_reject"] is False and "expired" in expired["reject_reason"]

    walk(prepared, ApplicationStatus.WITHDRAWN)
    with prepared.ctx.read_repos() as repos:
        withdrawn = package_actions(
            repos.packages.get(prepared.package_id),
            repos.applications.get(prepared.application_id),
            "REJECTED",
        )
    assert withdrawn["can_reject"] is False
    assert withdrawn["reject_reason"] == "The application was already withdrawn."
    assert "Pipeline" not in withdrawn["reject_reason"]


def test_the_review_payload_passes_the_opportunity_status_to_the_actions(prepared) -> None:
    from sabra_engine.approval import ApprovalService

    with prepared.ctx.repos() as repos:
        repos.opportunities.set_status(prepared.opportunity_id, JobStatus.EXPIRED)
    actions = ApprovalService(prepared.ctx).review(prepared.package_id)["actions"]
    assert actions["can_reject"] is False and "expired" in actions["reject_reason"]


def test_the_refresh_message_does_not_name_an_empty_revision() -> None:
    from sabra_api.review_pages import REVIEW_BODY

    assert "revision '+(b.d.revision" not in REVIEW_BODY
    assert "Package rebuilt. Review it" in REVIEW_BODY


def test_the_export_file_for_an_applied_application_is_unchanged_by_the_guards(prepared) -> None:
    mark_applied(prepared)
    LedgerService(prepared.ctx).record_outcome(prepared.application_id, "OFFER", contact="Pat Lee")
    text = LedgerService(prepared.ctx).learning_jsonl(prepared.candidate_id)
    assert json.loads(text)["outcome"] == "OFFER" and "Pat Lee" not in text
