"""Pipeline runs: the whole loop, started by a person or by the scheduler.

A run is `discover -> rank -> research the shortlist -> (optionally) prepare the top
N`. It is recorded in the `runs` table with a JSON progress blob that is rewritten as
each source and stage finishes, so the dashboard can poll it while it works and a run
that was killed leaves an honest record of how far it got.

Rules this module keeps:

* One run at a time. A second request while one is active is refused ("already in
  progress"). A run whose process died shows as one that stopped updating, and is
  reaped as FAILED after `STALE_AFTER` so it cannot block every later run.
* A failure in one source, one research fetch or one package never fails the run; it is
  counted and named in the progress blob. Only a failure the loop cannot continue from
  (an exception in a stage, or every discovery source failing) marks the run FAILED.
* Nothing here submits anything. Preparing a package is not approving it.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from sabra_domain.enums import (
    EntityType,
    EventType,
    JobStatus,
    RunKind,
    RunStatus,
)
from sabra_domain.errors import NotFound, ValidationFailure
from sabra_providers.http import HttpClient
from sqlalchemy.exc import IntegrityError

from .candidate_intake import ensure_candidate, ensure_search_profile
from .context import EngineContext

# A run whose owner cannot be checked (another machine, or a record without one) is treated
# as dead after this long without a progress write. A single step (a slow paginated source,
# the grouping pass) can be quiet for a while, so this is generous.
STALE_AFTER = timedelta(minutes=60)
# A run whose owner process is alive on this machine is only reaped after this long: the
# process id may have been reused, or the run may be wedged.
HARD_STALE_AFTER = timedelta(hours=6)

ProgressSink = Callable[[dict[str, Any]], None]


def build_discovery_http(_ctx: EngineContext) -> HttpClient | None:
    """The HTTP client discovery reads boards with. None: the service builds its own.

    A seam for tests, which replace it with a client over a canned transport so a run
    never reaches the network.
    """

    return None


def build_research_http(_ctx: EngineContext) -> HttpClient | None:
    """The HTTP client research fetches company sites with. None: the service builds one."""

    return None


def _now() -> datetime:
    return datetime.now(UTC)


class RunAborted(BaseException):
    """The run's record was ended by someone else (reaped as dead) while it was running.

    A BaseException on purpose: the progress sinks in discovery and ranking swallow
    `Exception`, and a run that has been declared dead must stop at the next write, not
    carry on and then overwrite the verdict.
    """


def _owner() -> dict[str, Any]:
    """Who is running this: stored in the progress blob, so no extra column is needed.

    `started` is the process's creation time. A process id can be reused by an unrelated
    process after the owner dies; pid and creation time together cannot.
    """

    owner: dict[str, Any] = {"pid": os.getpid(), "host": socket.gethostname()}
    started = process_start_time(os.getpid())
    if started is not None:
        owner["started"] = started
    return owner


def _win_kernel32() -> Any:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    return kernel32


def process_start_time(pid: int) -> int | None:
    """An opaque creation-time stamp for a process, or None when it cannot be read.

    Windows: the process creation FILETIME. Linux: the start time field of /proc/<pid>/stat.
    Elsewhere: None, and the caller falls back to the pid alone.
    """

    if pid <= 0:
        return None
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = _win_kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
            if not kernel32.GetProcessTimes(
                handle,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            with open(f"/proc/{pid}/stat", encoding="utf-8") as handle:
                stat = handle.read()
            # Field 2 (the command) may contain spaces and parentheses; count after the last ")".
            return int(stat.rsplit(")", 1)[1].split()[19])
        except (OSError, ValueError, IndexError):
            return None
    return None


def pid_alive(pid: int) -> bool:
    """Whether a process with this id exists on this machine."""

    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = _win_kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            # Access denied still means the process exists.
            return ctypes.get_last_error() == 5
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_dead(row: Any) -> bool:
    """Whether an unfinished run's owner has gone away, so nothing will ever finish it."""

    heartbeat = row.updated_at or row.created_at
    age = _now() - heartbeat if heartbeat is not None else timedelta(0)
    owner = (row.progress or {}).get("owner") or {}
    pid = owner.get("pid")
    if isinstance(pid, int) and owner.get("host") == socket.gethostname():
        if not pid_alive(pid):
            return True
        recorded = owner.get("started")
        if isinstance(recorded, int):
            current = process_start_time(pid)
            if current is not None and current != recorded:
                return True  # the pid now belongs to a different process
        return age > HARD_STALE_AFTER
    return age > STALE_AFTER


def run_to_dict(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "prepare": bool(row.prepare),
        "candidate_id": row.candidate_id,
        "search_profile_id": row.search_profile_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "progress": row.progress or {},
        "error": row.error,
    }


class RunService:
    # Held while a run is created so two requests cannot both pass the "none active" check.
    _create_lock = threading.Lock()
    _threads: dict[str, threading.Thread] = {}

    def __init__(
        self,
        ctx: EngineContext,
        *,
        discovery_http: Any | None = None,
        research_http: Any | None = None,
        on_update: ProgressSink | None = None,
        only_new_hours: float | None = None,
    ) -> None:
        self.ctx = ctx
        # Skip boards read successfully within this many hours (`--only-new`).
        self._only_new_hours = only_new_hours
        self._discovery_http = discovery_http
        self._research_http = research_http
        self._on_update = on_update
        self._run_id: str | None = None
        self._progress: dict[str, Any] = {}

    # -- creating -------------------------------------------------------

    def create(self, kind: str, prepare: bool = False) -> str:
        """Record a QUEUED run, or refuse because one is already active."""

        try:
            run_kind = RunKind(kind)
        except ValueError as exc:
            raise ValidationFailure(
                f"unknown run kind {kind!r}; use one of: "
                + ", ".join(k.value for k in RunKind)
            ) from exc
        candidate_id = ensure_candidate(self.ctx)
        profile_id = ensure_search_profile(self.ctx, candidate_id)
        try:
            with self._create_lock, self.ctx.repos() as repos:
                self._reap_stale(repos)
                active = repos.pipeline_runs.unfinished()
                if active:
                    raise ValidationFailure(
                        f"a run is already in progress ({active[0].id}, {active[0].status}); "
                        "wait for it to finish"
                    )
                row = repos.pipeline_runs.create(
                    kind=run_kind.value,
                    prepare=bool(prepare) and run_kind is not RunKind.DISCOVER,
                    candidate_id=candidate_id,
                    search_profile_id=profile_id,
                )
                row.progress = {"owner": _owner()}
                return row.id
        except IntegrityError as exc:
            # The database's one-active-run index: another process won the race.
            raise ValidationFailure("a run is already in progress; wait for it to finish") from exc

    @classmethod
    def refuse_if_active(cls, ctx: EngineContext) -> None:
        """Raise "already in progress" when a run is active (used by plain discover/rank).

        A run and a bare `sabra discover` / `sabra rank` write the same tables; running
        both at once only makes each wait on the other's locks.
        """

        cls._reap_dead(ctx)
        with ctx.read_repos() as repos:
            active = repos.pipeline_runs.unfinished()
            if active:
                raise ValidationFailure(
                    f"a run is already in progress ({active[0].id}, {active[0].status}); "
                    "wait for it to finish, then try again"
                )

    @classmethod
    def start_background(
        cls,
        kind: str,
        prepare: bool = False,
        *,
        ctx_factory: Callable[[], EngineContext] = EngineContext.create,
        only_new_hours: float | None = None,
    ) -> dict[str, Any]:
        """Create a run and execute it on a daemon thread; return the QUEUED record.

        The thread builds its own context: the request's closes when the request ends.
        """

        ctx = ctx_factory()
        try:
            run_id = cls(ctx).create(kind, prepare)
            with ctx.read_repos() as repos:
                record = run_to_dict(repos.pipeline_runs.get(run_id))
        finally:
            ctx.close()
        thread = threading.Thread(
            target=cls._background,
            args=(run_id, ctx_factory, only_new_hours),
            name=f"run-{run_id}",
            daemon=True,
        )
        cls._threads[run_id] = thread
        thread.start()
        return record

    @classmethod
    def _background(
        cls,
        run_id: str,
        ctx_factory: Callable[[], EngineContext],
        only_new_hours: float | None = None,
    ) -> None:
        ctx = ctx_factory()
        try:
            cls(
                ctx,
                discovery_http=build_discovery_http(ctx),
                research_http=build_research_http(ctx),
                only_new_hours=only_new_hours,
            ).execute(run_id)
        except Exception:  # noqa: BLE001 - execute records its own failures; this is the net
            pass
        finally:
            ctx.close()

    @classmethod
    def record_server_stopped(
        cls, ctx_factory: Callable[[], EngineContext] = EngineContext.create
    ) -> int:
        """Mark this process's still-running background runs FAILED, as the server stops.

        The thread dies with the process, so nothing else would ever finish the record;
        without this it would read RUNNING until it went stale.
        """

        live = [run_id for run_id, thread in cls._threads.items() if thread.is_alive()]
        if not live:
            return 0
        ctx = ctx_factory()
        marked = 0
        try:
            with ctx.repos() as repos:
                for run_id in live:
                    row = repos.pipeline_runs.get(run_id)
                    if row.status in (RunStatus.QUEUED.value, RunStatus.RUNNING.value):
                        row.status = RunStatus.FAILED.value
                        row.finished_at = _now()
                        row.error = "the dashboard stopped while this run was in progress"
                        repos.events.append(
                            EntityType.PIPELINE_RUN,
                            run_id,
                            EventType.PIPELINE_RUN_FAILED,
                            {"error": row.error},
                        )
                        marked += 1
        finally:
            ctx.close()
        return marked

    @classmethod
    def wait(cls, run_id: str, timeout: float = 60.0) -> bool:
        """Block until a background run's thread ends (tests and the CLI). True if it did."""

        thread = cls._threads.get(run_id)
        if thread is None:
            return True
        thread.join(timeout)
        return not thread.is_alive()

    # -- reading --------------------------------------------------------

    def get(self, run_id: str) -> dict[str, Any]:
        self._reap_dead(self.ctx)
        with self.ctx.read_repos() as repos:
            return run_to_dict(repos.pipeline_runs.get(run_id))

    def list_recent(self, limit: int = 20) -> list[dict[str, Any]]:
        self._reap_dead(self.ctx)
        with self.ctx.read_repos() as repos:
            return [run_to_dict(row) for row in repos.pipeline_runs.list_recent(limit)]

    @classmethod
    def _reap_dead(cls, ctx: EngineContext) -> int:
        """Reap runs whose owner is gone. Reads first; takes the write lock only if needed.

        Every Today load and every run poll comes through here, so the common case (nothing
        to reap) must not queue behind a long write.
        """

        with ctx.read_repos() as repos:
            if not any(_is_dead(row) for row in repos.pipeline_runs.unfinished()):
                return 0
        with ctx.repos() as repos:
            before = [r.id for r in repos.pipeline_runs.unfinished()]
            cls._reap_stale(repos)
            return len(before) - len(repos.pipeline_runs.unfinished())

    @staticmethod
    def _reap_stale(repos: Any) -> None:
        for row in repos.pipeline_runs.unfinished():
            if _is_dead(row):
                row.status = RunStatus.FAILED.value
                row.finished_at = _now()
                row.error = (
                    "abandoned: the process that was running it has stopped (or it recorded "
                    "no progress for too long)"
                )
                repos.events.append(
                    EntityType.PIPELINE_RUN,
                    row.id,
                    EventType.PIPELINE_RUN_FAILED,
                    {"error": row.error},
                )

    # -- executing ------------------------------------------------------

    def run_sync(self, kind: str, prepare: bool = False) -> dict[str, Any]:
        return self.execute(self.create(kind, prepare))

    def execute(self, run_id: str) -> dict[str, Any]:
        """Run the pipeline for a QUEUED run. Never raises; the outcome is on the record."""

        self._run_id = run_id
        with self.ctx.repos() as repos:
            row = repos.pipeline_runs.get(run_id)
            if row.status != RunStatus.QUEUED.value:
                raise ValidationFailure(f"run {run_id} is {row.status}, not QUEUED")
            row.status = RunStatus.RUNNING.value
            row.started_at = _now()
            kind = RunKind(row.kind)
            prepare = bool(row.prepare)
            candidate_id = row.candidate_id or ""
            profile_id = row.search_profile_id or ""
            repos.events.append(
                EntityType.PIPELINE_RUN,
                run_id,
                EventType.PIPELINE_RUN_STARTED,
                {"kind": kind.value, "prepare": prepare, "search_profile_id": profile_id},
            )
        self._progress = {
            "owner": _owner(),
            "kind": kind.value,
            "prepare": prepare,
            "stage": "starting",
            "stages": {},
            "sources": [],
            "warnings": [],
        }

        error: str | None = None
        interrupt: BaseException | None = None
        try:
            self._flush()
            self._pipeline(kind, prepare, candidate_id, profile_id)
            failure = self._progress.get("failure")
            if failure:
                error = str(failure)
        except RunAborted:
            # The record was ended by someone else; leave their verdict alone.
            with self.ctx.read_repos() as repos:
                return run_to_dict(repos.pipeline_runs.get(run_id))
        except Exception as exc:  # noqa: BLE001 - the run must record why it stopped
            error = f"{type(exc).__name__}: {exc}"
        except BaseException as exc:
            # Ctrl-C, or the scheduler ending the task: record it, then let it propagate.
            error = f"interrupted ({type(exc).__name__})"
            interrupt = exc
        with suppress(RunAborted):
            self._finish(error)
        if interrupt is not None:
            raise interrupt
        with self.ctx.read_repos() as repos:
            return run_to_dict(repos.pipeline_runs.get(run_id))

    def _finish(self, error: str | None) -> None:
        status = RunStatus.FAILED if error else RunStatus.COMPLETED
        self._progress["stage"] = "failed" if error else "done"
        self._progress["summary"] = self._summary(status, error)
        with self.ctx.repos() as repos:
            row = repos.pipeline_runs.get(self._run_id or "")
            if row.status != RunStatus.RUNNING.value:
                raise RunAborted(row.status)
            row.status = status.value
            row.finished_at = _now()
            row.error = error
            row.progress = dict(self._progress)
            row.updated_at = _now()
            repos.events.append(
                EntityType.PIPELINE_RUN,
                row.id,
                EventType.PIPELINE_RUN_FAILED if error else EventType.PIPELINE_RUN_COMPLETED,
                {"kind": row.kind, "summary": self._progress["summary"], "error": error},
            )
        self._emit()

    def _summary(self, status: RunStatus, error: str | None) -> str:
        stages = self._progress.get("stages", {})
        parts: list[str] = []
        discover = stages.get("discover")
        if discover:
            parts.append(
                f"{discover.get('jobs_seen', 0)} postings from "
                f"{discover.get('sources_ok', 0)}/{discover.get('sources_total', 0)} sources "
                f"({discover.get('jobs_new', 0)} new)"
            )
        rank = stages.get("rank")
        if rank:
            parts.append(
                f"{rank.get('qualified', 0)} qualified, {rank.get('shortlisted', 0)} newly "
                f"shortlisted, {rank.get('rejected', 0)} filtered out"
            )
        research = stages.get("research")
        if research:
            parts.append(
                f"{research.get('researched', 0)} researched "
                f"({research.get('skipped_fresh', 0)} fresh, {research.get('failed', 0)} failed)"
            )
        prepare = stages.get("prepare")
        if prepare:
            parts.append(
                f"{prepare.get('prepared', 0)} package(s) prepared "
                f"({prepare.get('skipped_existing', 0)} already had one, "
                f"{prepare.get('failed', 0)} failed)"
            )
        text = "; ".join(parts) or "nothing to do"
        return f"{status.value.title()}: {text}" + (f". {error}" if error else "")

    # -- stages ---------------------------------------------------------

    def _pipeline(self, kind: RunKind, prepare: bool, candidate_id: str, profile_id: str) -> None:
        if kind in (RunKind.DISCOVER, RunKind.FULL):
            self._stage_discover(candidate_id, profile_id)
        if kind in (RunKind.RANK, RunKind.FULL):
            self._stage_rank(candidate_id, profile_id)
            self._stage_research(candidate_id, profile_id)
            if prepare:
                self._stage_prepare(candidate_id, profile_id)

    def _set_stage(self, name: str, **fields: Any) -> dict[str, Any]:
        stage = self._progress["stages"].setdefault(name, {})
        stage.update(fields)
        self._progress["stage"] = name
        return stage

    def _stage_discover(self, candidate_id: str, profile_id: str) -> None:
        from .discovery import DiscoveryService

        self._set_stage(
            "discover",
            status="running",
            sources_total=0,
            sources_done=0,
            sources_ok=0,
            jobs_seen=0,
            jobs_new=0,
            started_at=_now().isoformat(),
        )
        self._flush()
        stage = self._progress["stages"]["discover"]

        def on_progress(payload: dict[str, Any]) -> None:
            if payload.get("stage") == "sources":
                stage["sources_total"] = payload.get("sources_total", 0)
                stage["sources_done"] = payload.get("done", 0)
                outcome = payload.get("outcome")
                if outcome:
                    self._progress["sources"].append(
                        {
                            key: outcome.get(key)
                            for key in (
                                "source_id",
                                "display_name",
                                "ok",
                                "jobs_seen",
                                "jobs_new",
                                "jobs_updated",
                                "elapsed_ms",
                                "error",
                            )
                        }
                    )
                    stage["sources_ok"] += 1 if outcome.get("ok") else 0
                    stage["jobs_seen"] += outcome.get("jobs_seen", 0)
                    stage["jobs_new"] += outcome.get("jobs_new", 0)
            elif payload.get("stage") == "group":
                stage["status"] = "grouping"
            self._flush()

        result = DiscoveryService(self.ctx).run(
            candidate_id=candidate_id,
            search_profile_id=profile_id,
            http=self._discovery_http,
            progress=on_progress,
            only_new_hours=self._only_new_hours,
        )
        stage.update(
            status="done",
            sources_total=len(result.sources),
            sources_ok=result.sources_ok,
            sources_failed=result.sources_failed,
            jobs_seen=result.jobs_seen,
            jobs_new=result.jobs_new,
            jobs_updated=result.jobs_updated,
            duplicates_removed=result.duplicates_removed,
            canonical_opportunities=result.canonical_opportunities,
            sources_skipped_fresh=len(result.sources_skipped_fresh),
            errors=result.errors[:20],
        )
        if 0 < result.sources_failed < len(result.sources):
            # The run is "done" and the queue refreshed, but some boards were not read:
            # say so where the owner looks (Today and the run record), not only in the
            # per-source details.
            failed = [o.source_id for o in result.sources if not o.ok]
            shown = ", ".join(failed[:10]) + (f" and {len(failed) - 10} more" if len(failed) > 10 else "")
            self._progress["warnings"].append(
                f"{len(failed)} of {len(result.sources)} boards failed: {shown}"
            )
        if not result.sources and result.sources_skipped_fresh:
            self._progress["warnings"].append(
                f"all {len(result.sources_skipped_fresh)} boards were read within the last "
                f"{self._only_new_hours:g} hours; nothing needed refreshing"
            )
        elif not result.sources:
            self._progress["warnings"].append(
                "no discovery sources are enabled; the queue was not refreshed"
            )
        elif result.sources_ok == 0:
            self._progress["failure"] = (
                f"every discovery source failed ({result.sources_failed}); the queue was "
                "not refreshed"
            )
        self._flush()

    def _stage_rank(self, candidate_id: str, profile_id: str) -> None:
        from .intelligence import IntelligenceService

        stage = self._set_stage("rank", status="running", done=0, total=0)
        self._flush()

        def on_progress(payload: dict[str, Any]) -> None:
            for key in ("done", "total", "rejected", "qualified", "assessed"):
                if key in payload:
                    stage[key] = payload[key]
            stage["phase"] = payload.get("stage")
            self._flush()

        result = IntelligenceService(self.ctx).run(
            candidate_id=candidate_id, search_profile_id=profile_id, progress=on_progress
        )
        summary = result.summary()
        stage.update(
            status="done",
            considered=summary["considered"],
            rejected=summary["rejected"],
            qualified=summary["qualified"],
            already_decided=summary["already_decided"],
            assessed=summary["assessed"],
            ranked=summary["ranked"],
            shortlisted=summary["shortlisted"],
            rejection_breakdown=summary["rejection_breakdown"],
            top=[
                {
                    "opportunity_id": item["opportunity_id"],
                    "title": item["title"],
                    "company": item["company"],
                }
                for item in summary["top"][:10]
            ],
        )
        self._flush()

    def _stage_research(self, candidate_id: str, profile_id: str) -> None:
        from .research import ResearchService

        stage = self._set_stage("research", status="running")
        self._flush()
        with self.ctx.read_repos() as repos:
            limit = repos.search_profiles.get(profile_id).shortlist_target

        def on_progress(payload: dict[str, Any]) -> None:
            stage.update({k: v for k, v in payload.items() if k != "stage"})
            self._flush()

        summary = ResearchService(self.ctx, http=self._research_http).research_fresh(
            candidate_id, search_profile_id=profile_id, limit=limit, progress=on_progress
        )
        stage.update(summary)
        stage["status"] = "done"
        self._flush()

    def _stage_prepare(self, candidate_id: str, profile_id: str) -> None:
        from .documents import DocumentService

        stage = self._set_stage(
            "prepare", status="running", prepared=0, skipped_existing=0, failed=0, errors=[]
        )
        self._flush()
        with self.ctx.read_repos() as repos:
            limit = repos.search_profiles.get(profile_id).prepare_target
            has_master = repos.resumes.master(candidate_id) is not None
            shortlisted: list[str] = []
            for opportunity in repos.opportunities.shortlist(candidate_id):
                if (opportunity.search_profile_id or "") != profile_id:
                    continue
                if JobStatus(opportunity.status) is JobStatus.REJECTED:
                    continue
                shortlisted.append(opportunity.id)
            existing = {
                opportunity_id
                for opportunity_id in shortlisted
                if repos.applications.for_opportunity(opportunity_id) is not None
            }
        # `prepare_target` counts only roles that have no package yet, so a role prepared on
        # an earlier day does not use up a slot and new roles keep getting prepared. A role
        # that already has a package is left alone: preparing again would write a new
        # revision every run (and be refused once approved).
        targets = [o for o in shortlisted if o not in existing][: max(0, limit)]
        stage["total"] = len(targets)
        stage["skipped_existing"] = len(existing)
        if not has_master:
            stage.update(
                status="skipped",
                reason="no master resume is stored; upload one on the Candidate page",
            )
            self._progress["warnings"].append(stage["reason"])
            self._flush()
            return
        documents = DocumentService(self.ctx)
        for opportunity_id in targets:
            try:
                documents.prepare_package(opportunity_id)
                stage["prepared"] += 1
            except Exception as exc:  # noqa: BLE001 - one package must not fail the run
                stage["failed"] += 1
                stage["errors"].append(f"{opportunity_id}: {exc}"[:300])
            self._flush()
        stage["status"] = "done"
        self._flush()

    # -- progress -------------------------------------------------------

    def _flush(self) -> None:
        """Write the progress blob. Each write also moves `updated_at`, the heartbeat."""

        if self._run_id is None:
            return
        with self.ctx.repos() as repos:
            try:
                row = repos.pipeline_runs.get(self._run_id)
            except NotFound:
                return
            if row.status != RunStatus.RUNNING.value:
                raise RunAborted(row.status)
            row.progress = {**self._progress, "stages": {**self._progress["stages"]}}
            row.updated_at = _now()
        self._emit()

    def _emit(self) -> None:
        if self._on_update is not None:
            try:
                self._on_update(self._progress)
            except Exception:  # noqa: BLE001 - observability only
                return


def last_run_summary(repos: Any) -> dict[str, Any] | None:
    """The most recent finished run, for the Today view."""

    row = repos.pipeline_runs.latest_finished()
    return run_to_dict(row) if row is not None else None


__all__ = [
    "RunService",
    "build_discovery_http",
    "build_research_http",
    "last_run_summary",
    "run_to_dict",
]
