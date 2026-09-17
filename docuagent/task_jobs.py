"""Durable, project-scoped execution jobs for long task operations.

The original cancellation registry was a process-global mapping from task id to
event. That was enough for one active streaming wave, but it could not represent
sync execution, project isolation, or a process restart. This module owns the
small lifecycle layer shared by generation, retry, resume and repair:

- every long execution gets a durable ``job_id``;
- cancellation is requested per project, job or task;
- an in-memory event is used for cooperative checks between model turns;
- job ownership is recorded so a new process can classify stale ``running``
  records as orphaned instead of leaving them stuck forever.

The job file is intentionally a small audit/recovery record, not a second task
state machine. Task status remains in ``tasks.json``.
"""

from __future__ import annotations

import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import telemetry
from workspace import atomic_write_json, managed_path, read_json, utc_now

JOB_SCHEMA_VERSION = 1
JOB_STATE_FILE = "execution-jobs.json"
MAX_JOB_HISTORY = 32

PROCESS_INSTANCE_ID = uuid.uuid4().hex

_LOCK = threading.RLock()
_HANDLES: dict[tuple[str | None, str], "JobHandle"] = {}
_LEGACY_LATEST: "JobHandle | None" = None


def _project_key(project_root: Path | None) -> str | None:
    return str(project_root.resolve()) if project_root is not None else None


def _jobs_path(project_root: Path) -> Path:
    return managed_path(project_root, JOB_STATE_FILE)


def _read_document(project_root: Path) -> dict[str, Any]:
    raw = read_json(_jobs_path(project_root))
    if not isinstance(raw, dict):
        return {"schema_version": JOB_SCHEMA_VERSION, "jobs": []}
    jobs = raw.get("jobs")
    return {
        "schema_version": JOB_SCHEMA_VERSION,
        "jobs": [item for item in jobs if isinstance(item, dict)] if isinstance(jobs, list) else [],
    }


def _trim_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    active = [item for item in jobs if item.get("status") == "running"]
    history = [item for item in jobs if item.get("status") != "running"]
    history.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    return active[-MAX_JOB_HISTORY:] + history[:MAX_JOB_HISTORY]


def _write_document(project_root: Path, document: dict[str, Any]) -> None:
    atomic_write_json(
        _jobs_path(project_root),
        {
            "schema_version": JOB_SCHEMA_VERSION,
            "jobs": _trim_jobs(document.get("jobs", [])),
        },
    )


def _replace_record(project_root: Path, record: dict[str, Any]) -> None:
    document = _read_document(project_root)
    jobs = document["jobs"]
    for index, item in enumerate(jobs):
        if item.get("job_id") == record.get("job_id"):
            jobs[index] = record
            break
    else:
        jobs.append(record)
    _write_document(project_root, document)


def _record_for(handle: "JobHandle") -> dict[str, Any]:
    return {
        "job_id": handle.job_id,
        "project_root": str(handle.project_root) if handle.project_root else "",
        "task_ids": list(handle.task_ids),
        "kind": handle.kind,
        "transport": handle.transport,
        "status": handle.status,
        "owner_id": handle.owner_id,
        "owner_pid": handle.owner_pid,
        "cancel_requested": handle.cancel_requested,
        "cancelled_task_ids": sorted(handle.cancelled_task_ids),
        "created_at": handle.created_at,
        "updated_at": handle.updated_at,
        "finished_at": handle.finished_at,
        "last_error": handle.last_error,
    }


def _duration_ms(started_at: str, finished_at: str) -> int | None:
    try:
        start = datetime.fromisoformat(started_at)
        finish = datetime.fromisoformat(finished_at)
    except (TypeError, ValueError):
        return None
    return max(0, int((finish - start).total_seconds() * 1000))


def _telemetry(call: Any, *args: Any, **kwargs: Any) -> None:
    try:
        call(*args, **kwargs)
    except Exception:
        # Metrics must never affect task execution.
        pass


@dataclass
class JobHandle:
    """One live long-running operation."""

    job_id: str
    project_root: Path | None
    task_ids: tuple[str, ...]
    kind: str
    transport: str
    owner_id: str
    owner_pid: int
    created_at: str
    updated_at: str
    status: str = "running"
    cancel_requested: bool = False
    cancelled_task_ids: set[str] = field(default_factory=set)
    finished_at: str = ""
    last_error: str = ""
    _all_event: threading.Event = field(default_factory=threading.Event, repr=False)
    _task_events: dict[str, threading.Event] = field(default_factory=dict, repr=False)

    def cancelled(self, task_id: str | None = None) -> bool:
        """Return whether the job or one of its tasks has been cancelled."""
        if self._all_event.is_set():
            return True
        if task_id is None:
            return False
        event = self._task_events.get(task_id)
        return bool(event and event.is_set())

    def cancel(self, task_ids: Iterable[str] | None = None) -> int:
        """Request cancellation and return the number of task slots affected."""
        selected = list(dict.fromkeys(str(item) for item in task_ids or () if str(item)))
        if task_ids is None:
            requested = len(self.task_ids)
            self._all_event.set()
            self.cancelled_task_ids.update(self.task_ids)
        else:
            known = set(self.task_ids)
            requested = sum(1 for task_id in selected if task_id in known)
            for task_id in selected:
                event = self._task_events.get(task_id)
                if event is not None:
                    event.set()
                    self.cancelled_task_ids.add(task_id)
        if requested:
            self.cancel_requested = True
        return requested


def _new_handle(
    project_root: Path | None,
    task_ids: Iterable[str],
    kind: str,
    transport: str,
) -> JobHandle:
    normalized = tuple(dict.fromkeys(str(item) for item in task_ids if str(item)))
    if not normalized:
        raise ValueError("execution job must own at least one task")
    now = utc_now()
    return JobHandle(
        job_id="job-" + uuid.uuid4().hex[:16],
        project_root=project_root.resolve() if project_root is not None else None,
        task_ids=normalized,
        kind=str(kind or "task"),
        transport=str(transport or "sync"),
        owner_id=PROCESS_INSTANCE_ID,
        owner_pid=os.getpid(),
        created_at=now,
        updated_at=now,
        _task_events={task_id: threading.Event() for task_id in normalized},
    )


def begin_job(
    project_root: Path | None,
    task_ids: Iterable[str],
    *,
    kind: str,
    transport: str,
) -> JobHandle:
    """Create and persist one execution job."""
    global _LEGACY_LATEST

    handle = _new_handle(project_root, task_ids, kind, transport)
    with _LOCK:
        if project_root is None and _LEGACY_LATEST is not None:
            finish_job(_LEGACY_LATEST, "completed")
        _HANDLES[(_project_key(project_root), handle.job_id)] = handle
        if project_root is None:
            _LEGACY_LATEST = handle
        if project_root is not None:
            reconcile_jobs(project_root)
            _replace_record(project_root, _record_for(handle))
    _telemetry(telemetry.record_job_started, handle.kind)
    return handle


def finish_job(handle: JobHandle, status: str, error: str = "") -> None:
    """Close one job and persist its terminal status."""
    if handle.status != "running":
        return
    handle.status = str(status or "completed")
    if handle.cancel_requested and handle.status == "completed":
        handle.status = "cancelled"
    handle.last_error = str(error or "")
    handle.updated_at = utc_now()
    handle.finished_at = handle.updated_at
    key = (_project_key(handle.project_root), handle.job_id)
    with _LOCK:
        _HANDLES.pop(key, None)
        if handle.project_root is not None:
            _replace_record(handle.project_root, _record_for(handle))
    _telemetry(
        telemetry.record_job_finished,
        handle.kind,
        handle.status,
        duration_ms=_duration_ms(handle.created_at, handle.finished_at),
    )


def _matches(
    handle: JobHandle,
    *,
    project_root: Path | None,
    job_id: str,
    task_ids: list[str],
) -> bool:
    if job_id and handle.job_id != job_id:
        return False
    if project_root is not None and handle.project_root != project_root.resolve():
        return False
    if task_ids and not any(task_id in handle.task_ids for task_id in task_ids):
        return False
    return True


def cancel_jobs(
    task_ids: Iterable[str] | None = None,
    *,
    project_root: Path | None = None,
    job_id: str = "",
) -> int:
    """Cancel matching live jobs and return the number of task slots affected."""
    requested_ids = list(dict.fromkeys(str(item) for item in task_ids or () if str(item)))
    with _LOCK:
        handles = [
            handle
            for handle in _HANDLES.values()
            if handle.status == "running"
            and _matches(
                handle,
                project_root=project_root,
                job_id=str(job_id or ""),
                task_ids=requested_ids,
            )
        ]
        affected = 0
        for handle in handles:
            affected += handle.cancel(requested_ids if task_ids is not None else None)
            handle.updated_at = utc_now()
            if handle.project_root is not None:
                _replace_record(handle.project_root, _record_for(handle))
        return affected


def task_cancelled(project_root: Path | None, task_id: str) -> bool:
    """Return whether a live job for this project cancelled ``task_id``."""
    key = _project_key(project_root)
    with _LOCK:
        return any(
            handle.cancelled(task_id)
            for (handle_key, _), handle in _HANDLES.items()
            if handle_key == key
        )


def any_task_cancelled(task_id: str) -> bool:
    """Compatibility helper for callers that do not carry a project path."""
    with _LOCK:
        return any(handle.cancelled(task_id) for handle in _HANDLES.values())


def active_task_ids(project_root: Path | None) -> set[str]:
    """Return all task ids owned by live jobs for one project."""
    key = _project_key(project_root)
    with _LOCK:
        return {
            task_id
            for (handle_key, _), handle in _HANDLES.items()
            if handle_key == key and handle.status == "running"
            for task_id in handle.task_ids
        }


def reconcile_jobs(project_root: Path) -> int:
    """Mark jobs owned by another process instance as orphaned."""
    if project_root is None:
        return 0
    document = _read_document(project_root)
    live_ids = {
        job_id
        for (key, job_id), handle in _HANDLES.items()
        if key == _project_key(project_root) and handle.status == "running"
    }
    changed = 0
    for record in document["jobs"]:
        if record.get("status") != "running":
            continue
        job_id = str(record.get("job_id") or "")
        if job_id in live_ids:
            continue
        record.update(
            status="orphaned",
            updated_at=utc_now(),
            finished_at=utc_now(),
            last_error="执行进程已退出，任务状态待恢复。",
        )
        _telemetry(
            telemetry.record_job_finished,
            str(record.get("kind") or "task"),
            "orphaned",
            duration_ms=_duration_ms(
                str(record.get("created_at") or ""),
                str(record.get("finished_at") or ""),
            ),
        )
        changed += 1
    if changed:
        _write_document(project_root, document)
    return changed


def read_jobs(project_root: Path) -> list[dict[str, Any]]:
    """Return the durable job history for tests and diagnostics."""
    reconcile_jobs(project_root)
    return list(_read_document(project_root)["jobs"])
