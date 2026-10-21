"""Explicit state machines.

Requirements 10 and 19 make transitions part of the product contract, so they are
declared here (code owns truth) rather than inferred at call sites. Every
transition helper returns the audit event it produced.
"""

from __future__ import annotations

from .enums import ApplicationStatus, JobStatus
from .errors import InvalidTransition

# Job/opportunity lifecycle. Ordering follows requirement 10; the extra edges are
# documented implementation refinements:
#   * any pre-submission state, FAILED included (nothing was submitted), may expire when
#     the posting disappears;
#   * research is optional, so SHORTLISTED may go straight to PREPARED (req 15
#     says not every job requires every artifact);
#   * FAILED returns to APPLYING so a blocked submission can be retried after
#     repair instead of forcing a new package.
JOB_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.DISCOVERED: frozenset({JobStatus.NORMALIZED, JobStatus.REJECTED, JobStatus.EXPIRED}),
    JobStatus.NORMALIZED: frozenset(
        {JobStatus.QUALIFIED, JobStatus.REJECTED, JobStatus.SHORTLISTED, JobStatus.EXPIRED}
    ),
    JobStatus.QUALIFIED: frozenset({JobStatus.SHORTLISTED, JobStatus.REJECTED, JobStatus.EXPIRED}),
    JobStatus.SHORTLISTED: frozenset(
        {JobStatus.RESEARCHED, JobStatus.PREPARED, JobStatus.REJECTED, JobStatus.EXPIRED}
    ),
    JobStatus.RESEARCHED: frozenset({JobStatus.PREPARED, JobStatus.REJECTED, JobStatus.EXPIRED}),
    JobStatus.PREPARED: frozenset(
        {JobStatus.AWAITING_APPROVAL, JobStatus.REJECTED, JobStatus.EXPIRED}
    ),
    JobStatus.AWAITING_APPROVAL: frozenset(
        {JobStatus.APPROVED, JobStatus.REJECTED, JobStatus.PREPARED, JobStatus.EXPIRED}
    ),
    JobStatus.APPROVED: frozenset({JobStatus.APPLYING, JobStatus.WITHDRAWN, JobStatus.EXPIRED}),
    JobStatus.APPLYING: frozenset({JobStatus.APPLIED, JobStatus.FAILED}),
    JobStatus.APPLIED: frozenset(
        {
            JobStatus.RESPONSE,
            JobStatus.SCREEN,
            JobStatus.INTERVIEW,
            JobStatus.OFFER,
            JobStatus.REJECTED_BY_EMPLOYER,
            JobStatus.WITHDRAWN,
            JobStatus.EXPIRED,
        }
    ),
    JobStatus.RESPONSE: frozenset(
        {
            JobStatus.SCREEN,
            JobStatus.INTERVIEW,
            JobStatus.OFFER,
            JobStatus.REJECTED_BY_EMPLOYER,
            JobStatus.WITHDRAWN,
        }
    ),
    JobStatus.SCREEN: frozenset(
        {JobStatus.INTERVIEW, JobStatus.OFFER, JobStatus.REJECTED_BY_EMPLOYER, JobStatus.WITHDRAWN}
    ),
    JobStatus.INTERVIEW: frozenset(
        {JobStatus.OFFER, JobStatus.REJECTED_BY_EMPLOYER, JobStatus.WITHDRAWN}
    ),
    JobStatus.OFFER: frozenset({JobStatus.WITHDRAWN}),
    JobStatus.FAILED: frozenset(
        {JobStatus.APPLYING, JobStatus.REJECTED, JobStatus.WITHDRAWN, JobStatus.EXPIRED}
    ),
    JobStatus.REJECTED: frozenset({JobStatus.EXPIRED}),
    JobStatus.REJECTED_BY_EMPLOYER: frozenset(),
    JobStatus.WITHDRAWN: frozenset(),
    JobStatus.EXPIRED: frozenset(),
}

# Terminal job states: nothing may leave them.
TERMINAL_JOB_STATES = frozenset(
    {
        JobStatus.REJECTED_BY_EMPLOYER,
        JobStatus.WITHDRAWN,
        JobStatus.EXPIRED,
    }
)

# States in which an external submission is permitted (requirement 19).
SUBMISSION_READY_STATES = frozenset({JobStatus.APPROVED})

# States from which a job can still be prepared or approved.
PRE_SUBMISSION_STATES = frozenset(
    {
        JobStatus.DISCOVERED,
        JobStatus.NORMALIZED,
        JobStatus.QUALIFIED,
        JobStatus.SHORTLISTED,
        JobStatus.RESEARCHED,
        JobStatus.PREPARED,
        JobStatus.AWAITING_APPROVAL,
    }
)

APPLICATION_TRANSITIONS: dict[ApplicationStatus, frozenset[ApplicationStatus]] = {
    ApplicationStatus.DRAFT: frozenset(
        {ApplicationStatus.AWAITING_APPROVAL, ApplicationStatus.WITHDRAWN}
    ),
    ApplicationStatus.AWAITING_APPROVAL: frozenset(
        {ApplicationStatus.APPROVED, ApplicationStatus.DRAFT, ApplicationStatus.WITHDRAWN}
    ),
    # APPROVED -> BLOCKED is a direct edge for a refusal before any claim (the posting
    # is gone, no adapter supports it): the application was never APPLYING, and walking
    # through APPLYING would claim an approval-gated state that nothing claimed.
    ApplicationStatus.APPROVED: frozenset(
        {ApplicationStatus.APPLYING, ApplicationStatus.WITHDRAWN, ApplicationStatus.BLOCKED}
    ),
    ApplicationStatus.APPLYING: frozenset(
        {ApplicationStatus.APPLIED, ApplicationStatus.FAILED, ApplicationStatus.BLOCKED}
    ),
    # BLOCKED is recoverable: a human resolved the blocker and the attempt resumes
    # (BLOCKED -> APPLYING, re-authorised and claimed again). An abandoned or timed-out
    # human handoff lands here, never in WITHDRAWN. There is deliberately no plain
    # BLOCKED -> AWAITING_APPROVAL edge: with it `application_path` would find
    # APPROVED -> APPLYING -> BLOCKED -> AWAITING_APPROVAL, a way back into the
    # approval queue that does not pass through `revoke=True`. The way back for a
    # blocked or failed application whose package must change is the package refresh,
    # which revokes the approval (see REVOKE_APPLICATION_EDGES).
    ApplicationStatus.BLOCKED: frozenset(
        {ApplicationStatus.APPLYING, ApplicationStatus.WITHDRAWN, ApplicationStatus.FAILED}
    ),
    ApplicationStatus.FAILED: frozenset(
        {ApplicationStatus.APPLYING, ApplicationStatus.WITHDRAWN, ApplicationStatus.BLOCKED}
    ),
    ApplicationStatus.APPLIED: frozenset({ApplicationStatus.WITHDRAWN}),
    ApplicationStatus.WITHDRAWN: frozenset(),
}

# Statuses that may never be reached without a bound human approval.
APPROVAL_GATED_STATUSES = frozenset({ApplicationStatus.APPROVED, ApplicationStatus.APPLYING})

# Returning an approved application to the approval queue is not a normal edge.
# `application_path` and a plain `set_status` must not take it. Only the package
# refresh command passes `revoke=True`, and only for an application that has not
# been submitted: APPROVED, or a BLOCKED / FAILED attempt that never reached the
# employer. The refresh clears the bound approval hash, so the application must be
# approved again on the new package revision.
REVOKE_APPLICATION_EDGES = frozenset(
    {
        (ApplicationStatus.APPROVED, ApplicationStatus.AWAITING_APPROVAL),
        (ApplicationStatus.BLOCKED, ApplicationStatus.AWAITING_APPROVAL),
        (ApplicationStatus.FAILED, ApplicationStatus.AWAITING_APPROVAL),
    }
)
REVOKE_JOB_EDGES = frozenset(
    {
        (JobStatus.APPROVED, JobStatus.AWAITING_APPROVAL),
        (JobStatus.FAILED, JobStatus.AWAITING_APPROVAL),
    }
)


def can_transition_job(current: JobStatus, target: JobStatus) -> bool:
    return target in JOB_TRANSITIONS.get(current, frozenset())


def assert_job_transition(current: JobStatus, target: JobStatus, *, revoke: bool = False) -> None:
    if can_transition_job(current, target):
        return
    if revoke and (current, target) in REVOKE_JOB_EDGES:
        return
    raise InvalidTransition("job", current.value, target.value)


def can_transition_application(current: ApplicationStatus, target: ApplicationStatus) -> bool:
    return target in APPLICATION_TRANSITIONS.get(current, frozenset())


def assert_application_transition(
    current: ApplicationStatus, target: ApplicationStatus, *, revoke: bool = False
) -> None:
    if can_transition_application(current, target):
        return
    if revoke and (current, target) in REVOKE_APPLICATION_EDGES:
        return
    raise InvalidTransition("application", current.value, target.value)


def application_path(
    current: ApplicationStatus, target: ApplicationStatus
) -> list[ApplicationStatus]:
    """Shortest legal sequence of transitions from `current` to `target`.

    Returns the states to step through, excluding `current` and including `target`.
    Raises `InvalidTransition` when no path exists.

    Exists because ad-hoc two-step chains scattered across call sites are how an
    illegal jump gets written: the submission path moved an APPROVED application
    straight to APPLIED, which the graph forbids (APPROVED -> APPLYING -> APPLIED), so
    a *successful* submission raised after the employer had already received it and
    the application was left unrecorded. Deriving the path from the graph means the
    graph is the only place the order is written down.
    """

    if current is target:
        return []
    from collections import deque

    queue: deque[list[ApplicationStatus]] = deque([[current]])
    seen: set[ApplicationStatus] = {current}
    while queue:
        path = queue.popleft()
        node = path[-1]
        for nxt in sorted(APPLICATION_TRANSITIONS.get(node, frozenset()), key=lambda s: s.value):
            if nxt is target:
                return [*path[1:], nxt]
            if nxt not in seen:
                seen.add(nxt)
                queue.append([*path, nxt])
    raise InvalidTransition("application", current.value, target.value)
