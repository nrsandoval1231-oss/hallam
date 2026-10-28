"""Daily operator loop for Sabra Job Engine.

Two phases, each safe to run on a schedule (Windows Task Scheduler):

* `prepare` — discover, rank, then build application packages for the top
  shortlisted opportunities that do not have a current package yet. Fully
  autonomous (requirements 7-15); nothing is submitted.
* `submit` — submit up to N owner-APPROVED applications per calendar day,
  oldest approval first. Approval is the human gate: this only ever touches
  applications whose owner-approved package hash is intact. A challenged form
  opens one headed browser window and the run stops for the day: the person
  reports the outcome, and the next scheduled run continues.

Submission requires `--real` on the command line (or SABRA_AUTOMATION_DRY_RUN=false
in the environment). Without it the script refuses, so a scheduled task cannot
accidentally submit while dry-run is the standing default.

Usage:
    uv run python scripts/daily_run.py prepare --limit 3
    uv run python scripts/daily_run.py submit --limit 3 --real
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

LOG_DIR = REPO_ROOT / "var" / "automation"


def _log(phase: str, record: dict) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"at": datetime.now(UTC).isoformat(), "phase": phase, **record}, default=str)
    with (LOG_DIR / "daily_run.log").open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    print(line)


def _candidate_id(ctx) -> str:
    from sabra_engine.candidate_intake import ensure_candidate

    return ensure_candidate(ctx, create_placeholder=False)


def cmd_prepare(limit: int) -> int:
    from sabra_domain.errors import SabraError
    from sabra_engine.candidate_intake import ensure_search_profile
    from sabra_engine.context import EngineContext
    from sabra_engine.discovery import DiscoveryService
    from sabra_engine.documents import DocumentService
    from sabra_engine.intelligence import IntelligenceService

    ctx = EngineContext.create()
    prepared: list[str] = []
    try:
        candidate_id = _candidate_id(ctx)
        search_profile_id = ensure_search_profile(ctx, candidate_id)

        discovery = DiscoveryService(ctx).run(
            candidate_id=candidate_id, search_profile_id=search_profile_id
        )
        _log("prepare", {"step": "discovery", **discovery.summary()})

        ranking = IntelligenceService(ctx).run(
            candidate_id=candidate_id, search_profile_id=search_profile_id
        )
        _log("prepare", {"step": "rank", **ranking.summary()})

        with ctx.repos() as repos:
            shortlist = repos.opportunities.shortlist(candidate_id)

        def fit_score(opportunity_id: str) -> float:
            with ctx.repos() as repos:
                row = repos.fit.for_opportunity(opportunity_id)
            payload = getattr(row, "payload", None) or {}
            return float(payload.get("fit_score") or payload.get("rank_score") or 0.0)

        candidates = [
            opp
            for opp in shortlist
            if not _has_current_package(ctx, opp.id)
            and not _withdrawn_or_dead(ctx, opp.id)
        ]
        candidates.sort(key=lambda opp: fit_score(opp.id), reverse=True)

        for opp in candidates[:limit]:
            try:
                result = DocumentService(ctx).prepare_package(opp.id)
            except SabraError as exc:
                _log("prepare", {"opportunity_id": opp.id, "error": str(exc)})
                continue
            _log(
                "prepare",
                {
                    "opportunity_id": opp.id,
                    "package_id": result.package_id,
                    "application_id": result.application_id,
                    "status": str(result.status),
                    "complete": result.complete,
                    "company": getattr(opp, "company", None),
                    "title": getattr(opp, "title", None),
                },
            )
            prepared.append(result.application_id)
    finally:
        ctx.close()

    print(f"prepared {len(prepared)} package(s); approve them at the dashboard")
    return 0


def _has_current_package(ctx, opportunity_id: str) -> bool:
    from sabra_domain.enums import PackageStatus

    with ctx.repos() as repos:
        row = repos.packages.latest_for_opportunity(opportunity_id)
    return row is not None and row.status == PackageStatus.COMPLETE.value


def _withdrawn_or_dead(ctx, opportunity_id: str) -> bool:
    with ctx.repos() as repos:
        row = repos.applications.for_opportunity(opportunity_id)
    if row is None:
        return False
    return row.status in {"WITHDRAWN", "APPLIED"}


def cmd_submit(limit: int, real: bool) -> int:
    import os

    from sabra_domain.enums import ApplicationStatus
    from sabra_engine.approval import ApprovalService
    from sabra_engine.context import EngineContext
    from sabra_engine.execution import ExecutionService

    if real:
        # `--real` on the command line is the explicit, auditable switch: the
        # standing default stays dry-run unless the scheduled task says otherwise.
        os.environ["SABRA_AUTOMATION_DRY_RUN"] = "false"

    ctx = EngineContext.create()
    try:
        if ctx.settings.automation_dry_run and not real:
            print(
                "Refused: submit requires --real (or SABRA_AUTOMATION_DRY_RUN=false). "
                "Nothing was opened or submitted."
            )
            return 2

        candidate_id = _candidate_id(ctx)
        today = datetime.now(UTC).date().isoformat()

        with ctx.repos() as repos:
            applications = repos.applications.list_for_candidate(candidate_id)

        # A pending handoff owns the human's attention: no new windows today.
        if any(ApplicationStatus(a.status) is ApplicationStatus.APPLYING for a in applications):
            print(
                "An application is APPLYING (a handoff or run is pending a human report). "
                "Report its outcome first; the next scheduled run continues."
            )
            return 0

        used = sum(
            1
            for a in applications
            if ApplicationStatus(a.status) is ApplicationStatus.APPLIED
            and str(getattr(a, "submission_time", "") or "").startswith(today)
        )
        remaining = max(0, limit - used)
        if remaining == 0:
            print(f"daily limit of {limit} submission(s) already reached today")
            return 0

        approved = [
            a for a in applications if ApplicationStatus(a.status) is ApplicationStatus.APPROVED
        ]
        approved.sort(key=_approved_at)

        service = ExecutionService(ctx, background_handoff=True, ctx_factory=EngineContext.create)
        for app in approved[:remaining]:
            try:
                ApprovalService(ctx).assert_submission_authorised(app.id)
            except Exception as exc:  # noqa: BLE001 - report and move to the next package
                _log("submit", {"application_id": app.id, "refused": str(exc)})
                continue
            result = service.submit(app.id, allow_real_submission=True)
            record = result.as_dict()
            _log("submit", record)
            if record.get("outcome") == "HANDED_TO_HUMAN":
                print(
                    "A challenged form was handed to the headed browser. "
                    "Report the outcome there; the next scheduled run continues."
                )
                return 0
    finally:
        ctx.close()
    return 0


def _approved_at(app) -> str:
    raw = getattr(app, "approval", None)
    if isinstance(raw, str) and raw:
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = {}
    if isinstance(raw, dict):
        return str(raw.get("approved_at") or "")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare", help="discover, rank and build packages")
    prepare.add_argument("--limit", type=int, default=3)
    submit = sub.add_parser("submit", help="submit up to N approved applications today")
    submit.add_argument("--limit", type=int, default=3)
    submit.add_argument("--real", action="store_true", help="allow real submission")
    args = parser.parse_args()

    if args.command == "prepare":
        return cmd_prepare(args.limit)
    return cmd_submit(args.limit, args.real)


if __name__ == "__main__":
    sys.exit(main())
