"""What the review page says about a package that the package itself does not state.

Two things live here, both pure functions of stored rows so they can be tested without a
database and cannot drift from the page:

* `generation_notes`: a plain-language account of how each artifact was produced (model
  or template), what the truth gate did to it, and what the model calls cost; and
* `package_actions`: whether this package can be approved, refreshed or rejected right
  now, and, when it cannot, the reason a person can read.
"""

from __future__ import annotations

from typing import Any

from sabra_domain.enums import ApplicationStatus, JobStatus, PackageStatus

ARTIFACT_LABELS: dict[str, str] = {
    "tailored_resume": "Tailored résumé",
    "cover_letter": "Cover letter",
    "fit_explanation": "Fit explanation",
    "candidate_summary": "Candidate summary",
    "recruiter_message": "Recruiter message",
    "hiring_manager_message": "Hiring-manager message",
    "interview_preparation": "Interview preparation",
}

# The truth gate names its artifacts after the generation step, not the package field.
GATE_LABELS: dict[str, str] = {
    "tailoring": "Tailored résumé",
    "cover_letter": "Cover letter",
    "narrative": "Fit explanation",
}


def _text(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def generation_notes(truth_ledger: dict[str, Any] | None) -> list[str]:
    """One sentence per fact about how the artifacts were written.

    With no `generation` record the package came from the deterministic template alone.
    """

    generation = (truth_ledger or {}).get("generation")
    if not isinstance(generation, dict) or not generation:
        return ["No model was used for this package: every artifact came from the template."]

    notes: list[str] = []
    provider = generation.get("provider")
    model = generation.get("model")
    who = "/".join(str(part) for part in (provider, model) if part) or "a model"
    unavailable = generation.get("unavailable")
    if unavailable:
        notes.append(
            f"The model ({who}) was not available, so the template was used: {_text(unavailable)}"
        )
    if generation.get("reused"):
        notes.append(
            "Model-written text from an earlier revision was reused because none of its "
            "inputs changed."
        )

    generated_by = generation.get("generated_by")
    if isinstance(generated_by, dict):
        for key, label in ARTIFACT_LABELS.items():
            origin = str(generated_by.get(key, "template"))
            if origin.startswith("model"):
                notes.append(f"{label}: written by the model ({origin.removeprefix('model:')}).")
            else:
                notes.append(f"{label}: built from the template.")

    gate = generation.get("truth_gate")
    if isinstance(gate, dict):
        for name, outcome in gate.items():
            if not isinstance(outcome, dict):
                continue
            label = GATE_LABELS.get(str(name), str(name).replace("_", " ").capitalize())
            first = [str(v) for v in outcome.get("violations") or []]
            final = [str(v) for v in outcome.get("final_violations") or []]
            source = outcome.get("source")
            if outcome.get("fell_back"):
                reason = outcome.get("reason")
                notes.append(
                    f"{label}: the truth gate rejected the model text"
                    + (f" ({len(first)} violation(s): {_text('; '.join(first[:3]))})" if first else "")
                    + "; the template was used instead"
                    + (f" ({_text(reason)})." if reason else ".")
                )
            elif outcome.get("regenerated"):
                if final:
                    notes.append(
                        f"{label}: regenerated once; still {len(final)} violation(s): "
                        f"{_text('; '.join(final[:3]))}."
                    )
                else:
                    notes.append(
                        f"{label}: the first draft broke the truth gate "
                        f"({len(first)} violation(s): {_text('; '.join(first[:3]))}); "
                        "it was regenerated once and the second draft passed."
                    )
            elif source in {"model", "reused"}:
                notes.append(f"{label}: passed the truth gate on the first draft.")
            elif first:
                notes.append(
                    f"{label}: {len(first)} violation(s) found: {_text('; '.join(first[:3]))}."
                )

    calls = generation.get("calls")
    if isinstance(calls, list) and calls:
        records = [c for c in calls if isinstance(c, dict)]
        failed = [c for c in records if not c.get("ok", True)]
        tokens_in = sum(_count(c.get("input_tokens")) for c in records)
        tokens_out = sum(_count(c.get("output_tokens")) for c in records)
        line = (
            f"Model calls: {len(records)}"
            + (f", {len(failed)} failed" if failed else "")
            + f". Tokens: {tokens_in:,} in, {tokens_out:,} out."
        )
        notes.append(line)
        for call in failed[:3]:
            notes.append(
                f"A {call.get('purpose', 'model')} call failed: {_text(call.get('error') or 'unknown error')}"
            )
    return notes


def package_actions(
    package_row: Any, application: Any, opportunity_status: str | None = None
) -> dict[str, Any]:
    """Whether the review page may offer Approve, Refresh and Reject, and why not.

    `approve` uses the same conditions as the Applications list (a COMPLETE, truth-clean
    package whose application is awaiting approval), plus that this is the application's
    current package: an older revision is never approvable.
    """

    status = package_row.status
    app_status = application.status if application is not None else None
    current = application is not None and application.package_id == package_row.id

    approve_reason: str | None
    if application is None:
        approve_reason = "No application record exists for this package. Prepare the package first."
    elif not current or status == PackageStatus.SUPERSEDED.value:
        approve_reason = (
            "This revision has been replaced or withdrawn. Open the application's current "
            "package to approve it."
        )
    elif app_status in {ApplicationStatus.APPLYING.value, ApplicationStatus.APPLIED.value}:
        approve_reason = f"The application is already {app_status}; it cannot be approved again."
    elif status == PackageStatus.APPROVED.value or app_status == ApplicationStatus.APPROVED.value:
        approve_reason = (
            "Already approved. The approval is bound to this package's content hash."
        )
    elif app_status == ApplicationStatus.WITHDRAWN.value:
        approve_reason = "The application was withdrawn."
    elif status == PackageStatus.INCOMPLETE.value:
        count = _count(getattr(package_row, "unresolved_count", 0))
        approve_reason = (
            f"This package is INCOMPLETE: {count} unresolved question(s) must be answered "
            "(Answers page), then use Refresh package."
        )
    elif not package_row.truth_clean:
        approve_reason = (
            "This package is not truth-clean: it contains a claim the candidate's evidence "
            "does not support, so it cannot be approved."
        )
    elif status != PackageStatus.COMPLETE.value:
        approve_reason = f"The package is {status}, not COMPLETE."
    elif app_status != ApplicationStatus.AWAITING_APPROVAL.value:
        approve_reason = (
            f"The application is {app_status}. Use Refresh package to revoke and rebuild, "
            "then approve the new revision."
        )
    else:
        approve_reason = None

    refresh: str | None = None
    refresh_reason: str | None = None
    if not current or application is None:
        refresh_reason = "Only the application's current package can be refreshed."
    elif app_status in {
        ApplicationStatus.APPROVED.value,
        ApplicationStatus.BLOCKED.value,
        ApplicationStatus.FAILED.value,
    }:
        refresh = "refresh"  # revokes the approval, then rebuilds
    elif app_status in {ApplicationStatus.DRAFT.value, ApplicationStatus.AWAITING_APPROVAL.value}:
        refresh = "prepare"  # nothing to revoke: rebuild into a new revision
    else:
        refresh_reason = f"The application is {app_status}; its package can no longer change."

    reject_reason: str | None
    if not current or application is None or status == PackageStatus.SUPERSEDED.value:
        reject_reason = "Only the application's current package can be rejected."
    elif app_status in {
        ApplicationStatus.DRAFT.value,
        ApplicationStatus.AWAITING_APPROVAL.value,
        ApplicationStatus.BLOCKED.value,
        ApplicationStatus.FAILED.value,
    }:
        reject_reason = None
    elif app_status == ApplicationStatus.APPROVED.value:
        reject_reason = (
            "An approved application cannot be rejected. Refresh the package to revoke the "
            "approval first."
        )
    elif app_status == ApplicationStatus.WITHDRAWN.value:
        reject_reason = "The application was already withdrawn."
    elif app_status == ApplicationStatus.APPLYING.value:
        reject_reason = (
            "A submission is in progress or waiting for your report; report how it ended on "
            "the Applications page."
        )
    else:
        reject_reason = f"The application is {app_status}; record an outcome on the Pipeline page."
    if reject_reason is None and opportunity_status == JobStatus.EXPIRED.value:
        reject_reason = "The posting has expired, so the role is already off your list."

    return {
        "can_approve": approve_reason is None,
        "approve_reason": approve_reason,
        "refresh": refresh,
        "refresh_reason": refresh_reason,
        "can_reject": reject_reason is None,
        "reject_reason": reject_reason,
    }
