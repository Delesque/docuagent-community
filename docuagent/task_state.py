"""Task state and the guards shared by every pipeline stage.

The bottom layer of the tasks.py split. Plan, generate, patch, verify and docs all
read and write the same task state and reuse the same path and payload guards, so
those live here and depend on nothing above them - keeping the split acyclic, which
the backend import graph has so far stayed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import telemetry
from agent_tools import _checkpoint_path, has_loop_checkpoint
from core import WorkspaceError
from workspace import atomic_write_json, managed_path, read_json, read_stale_modules, utc_now


TASK_SCHEMA_VERSION = 1
TASK_STATE_FILE = "tasks.json"
TASK_DONE_STATUSES = {"applied", "verified", "rejected"}
MAX_REPAIR_ATTEMPTS = 5
DOCUMENTATION_STATUSES = {
    "not_required",
    "pending",
    "running",
    "synced",
    "retrying",
    "blocked",
}
MAX_DOCUMENTATION_AUTO_RETRIES = 2


def read_task_state(project_root: Path) -> dict[str, Any] | None:
    raw = read_json(managed_path(project_root, TASK_STATE_FILE))
    if not isinstance(raw, dict):
        return None
    ensure_documentation_state(raw)
    for task in raw.get("tasks", []):
        if isinstance(task, dict) and task.get("id"):
            checkpoint_path = _checkpoint_path(project_root, str(task["id"]))
            task["checkpoint_available"] = has_loop_checkpoint(
                project_root, str(task["id"])
            )
            if checkpoint_path.exists():
                task["checkpoint_size"] = checkpoint_path.stat().st_size
                task["checkpoint_updated_at"] = checkpoint_path.stat().st_mtime
    return raw


def write_task_state(project_root: Path, state: dict[str, Any]) -> None:
    """Single write entry for tasks.json — also reconciles task error nodes.

    The reconcile lives here rather than in every failure path: any current or
    future code that flips a task to/from `failed` gets correct error nodes
    for free, and the reconcile itself is idempotent (it rewrites
    error_nodes.json only when something actually moved).
    """
    atomic_write_json(managed_path(project_root, TASK_STATE_FILE), state)
    # The registry is a UI projection, not a second task state machine.
    from agents import read_registry, write_registry

    registry = read_registry(project_root)
    changed = False
    for task in state.get("tasks", []):
        entry = registry.get("agents", {}).get(task.get("module_id") or task.get("id"))
        if not isinstance(entry, dict) or entry.get("task_id") != task.get("id"):
            continue
        status = task.get("status")
        projected = ("running" if status in {"running", "applying", "verifying"}
                     else "review" if status in {"review", "partially_applied"}
                     else "blocked" if status in {"failed", "blocked"} else "idle")
        error = task.get("last_error", "") if projected == "blocked" else ""
        if entry.get("status") != projected or entry.get("last_error", "") != error:
            entry.update(status=projected, last_error=error, updated_at=utc_now())
            changed = True
    if changed:
        write_registry(project_root, registry)
    import error_nodes

    error_nodes.reconcile_task_error_nodes(project_root, state.get("tasks", []))


def mark_docs_dirty(state: dict[str, Any]) -> None:
    """Mark the final Doc Maintainer close step as required after a write."""
    state["docs_in_sync"] = False
    state["docs_sync_required"] = True
    update_delivery_status(state)


def architecture_fingerprint(architecture: dict[str, Any]) -> str:
    """Return a stable hash of architecture facts that feed generated documents."""
    payload = {
        key: value
        for key, value in architecture.items()
        if key not in {"architecture_version", "generated_at"}
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def mark_architecture_docs_dirty(
    project_root: Path,
    architecture: dict[str, Any],
) -> bool:
    """Mark docs stale when the architecture source fingerprint changed."""
    state = read_task_state(project_root)
    if not state:
        return False
    ensure_documentation_state(state)
    documentation = state["documentation_task"]
    fingerprint = architecture_fingerprint(architecture)
    if documentation.get("source_fingerprint") == fingerprint:
        return False
    mark_docs_dirty(state)
    documentation["status"] = "pending"
    documentation["updated_at"] = utc_now()
    write_task_state(project_root, state)
    return True


def _new_documentation_task() -> dict[str, Any]:
    return {
        "status": "not_required",
        "code_task_id": "",
        "code_task_ids": [],
        "changed_files": [],
        "affected_modules": [],
        "document_version": "",
        "document_hash": "",
        "source_fingerprint": "",
        "last_error": "",
        "auto_retry_count": 0,
        "max_auto_retries": MAX_DOCUMENTATION_AUTO_RETRIES,
        "updated_at": "",
    }


def ensure_documentation_state(state: dict[str, Any]) -> dict[str, Any]:
    """Migrate legacy task state into the independent documentation loop shape."""
    documentation = state.get("documentation_task")
    if not isinstance(documentation, dict):
        documentation = _new_documentation_task()
        if state.get("docs_sync_required") or state.get("docs_in_sync") is False:
            documentation["status"] = "pending"
        state["documentation_task"] = documentation
    defaults = _new_documentation_task()
    for key, value in defaults.items():
        documentation.setdefault(key, value)
    if documentation.get("status") not in DOCUMENTATION_STATUSES:
        documentation["status"] = "pending"
    state.setdefault("docs_in_sync", documentation["status"] in {"not_required", "synced"})
    state.setdefault("docs_sync_required", documentation["status"] not in {"not_required", "synced"})
    state.setdefault("delivery_status", "ready" if documentation["status"] in {"not_required", "synced"} else "blocked")
    state.setdefault("overall_status", state["delivery_status"])
    return state


def update_delivery_status(state: dict[str, Any]) -> str:
    """Derive the delivery gate without changing the code task statuses."""
    ensure_documentation_state(state)
    documentation_status = state["documentation_task"]["status"]
    code_blocked = any(
        task.get("status") in {"failed", "blocked"}
        for task in state.get("tasks", [])
        if isinstance(task, dict)
    )
    if code_blocked or documentation_status in {"pending", "running", "retrying", "blocked"}:
        status = "blocked"
    elif documentation_status in {"not_required", "synced"} and all(
        task.get("status") in TASK_DONE_STATUSES
        for task in state.get("tasks", [])
        if isinstance(task, dict)
    ):
        status = "ready"
    else:
        status = "blocked"
    state["delivery_status"] = status
    state["overall_status"] = status
    return status


def register_documentation_task(
    state: dict[str, Any],
    code_task_id: str,
    changed_files: list[str],
    affected_module: str,
) -> dict[str, Any]:
    """Create or refresh the sole document-maintainer task after a real write."""
    ensure_documentation_state(state)
    documentation = state["documentation_task"]
    existing_ids = [
        str(item)
        for item in documentation.get("code_task_ids", [])
        if str(item).strip()
    ]
    if code_task_id and code_task_id not in existing_ids:
        existing_ids.append(code_task_id)
    existing_files = [
        str(item).replace("\\", "/")
        for item in documentation.get("changed_files", [])
        if str(item).strip()
    ]
    for path in changed_files:
        normalized = str(path).replace("\\", "/")
        if normalized and normalized not in existing_files:
            existing_files.append(normalized)
    modules = [
        str(item)
        for item in documentation.get("affected_modules", [])
        if str(item).strip()
    ]
    if affected_module and affected_module not in modules:
        modules.append(affected_module)
    documentation.update({
        "status": "pending",
        "code_task_id": code_task_id,
        "code_task_ids": existing_ids,
        "changed_files": existing_files,
        "affected_modules": modules,
        "last_error": "",
        "auto_retry_count": 0,
        "updated_at": utc_now(),
    })
    state["docs_in_sync"] = False
    state["docs_sync_required"] = True
    update_delivery_status(state)
    return documentation


def register_completed_documentation(state: dict[str, Any]) -> None:
    """Materialize documentation tasks for sandbox writes completed in a worker."""
    ensure_documentation_state(state)
    for task in state.get("tasks", []):
        if not isinstance(task, dict):
            continue
        changed_files = task.get("code_changed_files") or []
        if (
            task.get("status") not in {"applied", "verified"}
            or not changed_files
            or task.get("documentation_registered")
        ):
            continue
        register_documentation_task(
            state,
            str(task.get("id") or ""),
            [str(path) for path in changed_files],
            str(task.get("module_id") or ""),
        )
        task["documentation_registered"] = True
        documentation_error = str(task.get("documentation_error") or "").strip()
        if documentation_error:
            documentation = record_documentation_failure(state, documentation_error)
            documentation["status"] = "blocked"
    update_delivery_status(state)


def record_documentation_failure(
    state: dict[str, Any],
    error: str,
    *,
    retry_count: int | None = None,
) -> dict[str, Any]:
    ensure_documentation_state(state)
    documentation = state["documentation_task"]
    documentation["last_error"] = str(error)
    if retry_count is not None:
        documentation["auto_retry_count"] = retry_count
    documentation["updated_at"] = utc_now()
    state["docs_in_sync"] = False
    state["docs_sync_required"] = True
    update_delivery_status(state)
    telemetry.record_documentation_failure(
        retry=bool(retry_count and retry_count > 1),
    )
    return documentation


def document_version(project_root: Path) -> dict[str, str]:
    """Return a stable version/hash pair for the current root architecture document."""
    target = project_root / "AI_ARCH.md"
    try:
        content = target.read_bytes()
    except OSError:
        content = b""
    return {
        "document_version": utc_now(),
        "document_hash": hashlib.sha256(content).hexdigest(),
    }


def mark_stale_tasks(project_root: Path, state: dict[str, Any]) -> dict[str, Any]:
    """Block active work items owned by architecture modules marked stale."""
    stale = read_stale_modules(project_root)
    for task in state["tasks"]:
        if (
            task.get("module_id") in stale
            and task["status"] in {"pending", "running", "review"}
        ):
            task["status"] = "blocked"
            task["last_error"] = "模块已过期，需要重新确认后生成。"
    return state


def normalize_generated_files(raw: Any, task_id: str) -> list[dict[str, str]]:
    if not isinstance(raw, dict):
        raise WorkspaceError("Implementation Agent 返回值必须是 JSON 对象。")
    files_raw = raw.get("files") or raw.get("patch")
    if not isinstance(files_raw, list):
        raise WorkspaceError(f"任务 `{task_id}` 的生成结果缺少 `files` 数组。")
    files: list[dict[str, str]] = []
    for index, entry in enumerate(files_raw):
        if not isinstance(entry, dict):
            raise WorkspaceError(f"任务 `{task_id}` 的 files[{index}] 必须是对象。")
        path = str(entry.get("path") or "").strip().replace("\\", "/")
        candidate = Path(path)
        if not path or candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspaceError(f"任务 `{task_id}` 包含不安全的文件路径。")
        content = entry.get("content")
        if not isinstance(content, str):
            raise WorkspaceError(f"任务 `{task_id}` 的 files[{index}] 缺少 content。")
        files.append({"path": path, "content": content})
    return files


def _task_by_id(state: dict[str, Any], task_id: str) -> dict[str, Any]:
    for task in state["tasks"]:
        if task["id"] == task_id:
            return task
    raise WorkspaceError(f"找不到任务：{task_id}")


def _assert_safe_target(project_root: Path, path: str) -> Path:
    candidate = (project_root / path).resolve()
    root = project_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceError(f"补丁目标超出项目目录：{path}") from exc
    return candidate


def _read_current_text(target: Path) -> str:
    try:
        return target.read_text(encoding="utf-8")
    except OSError:
        return ""


APPLY_MODES = ("review", "auto")
APPLY_MODE_FILE = "apply-mode.json"


def read_apply_mode(project_root: Path) -> str:
    """Project-level switch: does a verified sandbox apply itself, or wait for review?

    - `review` (default): the sandbox becomes a patch and the task stops at
      `review`. Nothing reaches the project until a human applies it.
    - `auto`: a verified sandbox is applied to the project immediately.

    Stored in its own file rather than `state.json`, which finalize rewrites
    wholesale and would silently reset the choice.
    """
    raw = read_json(managed_path(project_root, APPLY_MODE_FILE))
    mode = str((raw or {}).get("mode") or "").strip().lower()
    return mode if mode in APPLY_MODES else "review"


def write_apply_mode(project_root: Path, mode: str) -> str:
    if mode not in APPLY_MODES:
        raise WorkspaceError(f"应用模式必须是 {' / '.join(APPLY_MODES)}。")
    atomic_write_json(
        managed_path(project_root, APPLY_MODE_FILE),
        {"schema_version": 1, "mode": mode, "updated_at": utc_now()},
    )
    return mode
