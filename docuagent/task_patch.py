"""Patch review, editing, and atomic application for task work items."""
from __future__ import annotations

import difflib
import json
from pathlib import Path
from typing import Any

from agents import append_work_log
import contract_lint
import contract_registry as _contract_registry
from core import WorkspaceError
from task_diff import apply_hunks, diff_hunks
from task_state import (
    _assert_safe_target,
    _read_current_text,
    _task_by_id,
    mark_stale_tasks,
    record_documentation_failure,
    register_documentation_task,
    read_task_state,
    update_delivery_status,
    write_task_state,
)
from workspace import apply_text_files_atomic, read_contracts


def ensure_file_unchanged(target: Path, path: str, expected_before: str) -> None:
    """Guard a patch write against concurrent edits (version guard).

    Raises WorkspaceError when the file on disk no longer matches the content
    captured when the patch was generated, so a stale patch never silently
    overwrites a newer edit. Mirrors the version-guarded write on DSH's fs seam
    (replaceIfVersion / STALE_VERSION).
    """
    if _read_current_text(target) != expected_before:
        raise WorkspaceError(
            f"文件 `{path}` 在补丁生成后被修改（STALE_VERSION），"
            "请重新生成该任务或人工处理冲突。"
        )


def _patch_apply_payload(
    entries: list[dict[str, Any]],
    only_paths: set[str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    """Build atomic-write changes and sparse version guards from patch entries."""
    changes: dict[str, str] = {}
    expected_before: dict[str, str] = {}
    for entry in entries:
        path = str(entry.get("path") or "")
        if not path or (only_paths is not None and path not in only_paths):
            continue
        changes[path] = str(entry.get("after") or "")
        if "before" in entry:
            expected_before[path] = str(entry.get("before") or "")
    return changes, expected_before


def _merge_contract_delta(
    project_root: Path,
    contract_delta: list[dict[str, Any]],
    changes: dict[str, str],
) -> None:
    """Add the reviewed contract delta to the same atomic apply operation."""
    updated = contract_lint.contracts_with_delta(project_root, contract_delta)
    if updated is None:
        # An empty delta means the patch introduced no NEW public symbols — but
        # that same patch may be the first real consumer of symbols ANOTHER
        # module declared (the cli-entry case: it only implements what was
        # already promised). Promotion must not be gated on the delta.
        updated = read_contracts(project_root)
    if not isinstance(updated, dict) or not isinstance(updated.get("modules"), list):
        return
    # A declared (proposed) export turns active once real production files
    # outside its owner reference it — tests never count. The patch's own
    # pending files are passed along: the bootstrap-time scan cannot know about
    # files sub-agents create after it (a greenfield scan is empty).
    updated = _contract_registry.promote_proposed_exports(
        project_root, updated, extra_texts=dict(changes)
    )
    changes[".docuagent/contracts.json"] = json.dumps(
        updated, ensure_ascii=False, indent=2
    ) + "\n"


def _post_write_documentation(
    project_root: Path,
    state: dict[str, Any],
    task: dict[str, Any],
    changed_files: list[str],
) -> list[str]:
    """Register code changes for the independent model-backed documentation pass."""
    register_documentation_task(
        state,
        str(task.get("id") or ""),
        changed_files,
        str(task.get("module_id") or ""),
    )
    return []


def apply_task_patch(project_root: Path, task_id: str) -> dict[str, Any]:
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    mark_stale_tasks(project_root, state)
    task = _task_by_id(state, task_id)
    if task["status"] not in {"review", "partially_applied"}:
        raise WorkspaceError(f"任务 `{task_id}` 还没有可应用的补丁。")
    task["status"] = "applying"
    write_task_state(project_root, state)
    try:
        lint = contract_lint.check_patch(project_root, task["patch"])
        if lint["violations"]:
            raise WorkspaceError(
                "契约校验未通过：\n" + "\n".join(f"- {item}" for item in lint["violations"])
            )
        task["contract_lint_violations"] = lint["violations"]
        task["contract_delta"] = lint["contract_delta"]
        task["contract_lint_skipped"] = lint["skipped"]
        changes, expected_before = _patch_apply_payload(task["patch"])
        _merge_contract_delta(project_root, lint["contract_delta"], changes)
        apply_text_files_atomic(project_root, changes, expected_before)
        task["status"] = "applied"
        task["last_error"] = ""
        changed_files = [entry["path"] for entry in task["patch"]]
        task["code_changed_files"] = list(
            dict.fromkeys([*(task.get("code_changed_files") or []), *changed_files])
        )
        task["documentation_updated"] = _post_write_documentation(
            project_root, state, task, changed_files
        )
        append_work_log(
            project_root,
            str(task.get("module_id") or task_id),
            "补丁已应用",
            task_id,
            "文件："
            + "、".join(entry["path"] for entry in task["patch"])
            + "\n\n等待验证。",
        )
    except (OSError, WorkspaceError) as exc:
        task["last_error"] = str(exc)
        task["status"] = "failed"
        update_delivery_status(state)
    state["last_error"] = task["last_error"]
    write_task_state(project_root, state)
    return state


def apply_task_partial(
    project_root: Path,
    task_id: str,
    file_decisions: dict[str, str],
) -> dict[str, Any]:
    """Apply only accepted files from a task patch.

    file_decisions maps file path -> decision: "accept" | "reject" | "pending"
    Only files with decision="accept" are written to disk.
    Task status becomes "partially_applied" if not all files accepted.
    """
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    mark_stale_tasks(project_root, state)
    task = _task_by_id(state, task_id)
    if task["status"] not in {"review", "partially_applied"}:
        raise WorkspaceError(f"任务 `{task_id}` 还没有可应用的补丁。")
    task["status"] = "applying"
    write_task_state(project_root, state)

    # Initialize file decision tracking fields
    if "applied_files" not in task:
        task["applied_files"] = []
    if "rejected_files" not in task:
        task["rejected_files"] = []
    if "pending_files" not in task:
        task["pending_files"] = []

    accepted = set(task.get("applied_files") or [])
    rejected = set(task.get("rejected_files") or [])
    pending = set(task.get("pending_files") or [])

    for entry in task["patch"]:
        path = entry["path"]
        previous = (
            "accept"
            if path in accepted
            else "reject"
            if path in rejected
            else "pending"
        )
        decision = file_decisions.get(path, previous)

        if decision == "accept":
            accepted.add(path)
            rejected.discard(path)
            pending.discard(path)
        elif decision == "reject":
            rejected.add(path)
            accepted.discard(path)
            pending.discard(path)
        else:
            pending.add(path)
            accepted.discard(path)
            rejected.discard(path)

    newly_accepted = sorted(accepted - set(task.get("applied_files") or []))
    accepted = sorted(accepted)
    rejected = sorted(rejected)
    pending = sorted(pending)

    # Apply accepted files atomically. Until every guard has passed and every write
    # has succeeded, the previous decision state remains the source of truth.
    try:
        accepted_entries = [
            entry for entry in task["patch"]
            if entry.get("path") in set(newly_accepted)
        ]
        lint = contract_lint.check_patch(project_root, accepted_entries)
        if lint["violations"]:
            raise WorkspaceError(
                "契约校验未通过：\n" + "\n".join(f"- {item}" for item in lint["violations"])
            )
        task["contract_lint_violations"] = lint["violations"]
        task["contract_delta"] = lint["contract_delta"]
        task["contract_lint_skipped"] = lint["skipped"]
        changes, expected_before = _patch_apply_payload(
            task["patch"], set(newly_accepted)
        )
        _merge_contract_delta(project_root, lint["contract_delta"], changes)
        apply_text_files_atomic(project_root, changes, expected_before)
        if newly_accepted:
            task["code_changed_files"] = list(
                dict.fromkeys(
                    [*(task.get("code_changed_files") or []), *newly_accepted]
                )
            )
            task["documentation_updated"] = _post_write_documentation(
                project_root, state, task, newly_accepted
            )

        # Update task state
        task["applied_files"] = accepted
        task["rejected_files"] = rejected
        task["pending_files"] = pending

        if not pending and not rejected:
            # All files accepted
            task["status"] = "applied"
            status_msg = "全部应用"
        elif not pending and not accepted:
            # All files rejected
            task["status"] = "rejected"
            status_msg = "全部拒绝"
        else:
            # Partial application
            task["status"] = "partially_applied"
            status_msg = f"部分应用（{len(accepted)}/{len(task['patch'])}）"

        task["last_error"] = ""

        log_parts = [status_msg]
        if accepted:
            log_parts.append(f"已接受：{', '.join(accepted)}")
        if rejected:
            log_parts.append(f"已拒绝：{', '.join(rejected)}")
        if pending:
            log_parts.append(f"待决策：{', '.join(pending)}")

        append_work_log(
            project_root,
            str(task.get("module_id") or task_id),
            "部分应用",
            task_id,
            "\n".join(log_parts),
        )
    except (OSError, WorkspaceError) as exc:
        task["last_error"] = str(exc)
        task["status"] = "failed"
        update_delivery_status(state)

    state["last_error"] = task["last_error"]
    write_task_state(project_root, state)
    return state


def edit_task_patch(
    project_root: Path,
    task_id: str,
    path: str,
    content: str,
    base_after: str | None = None,
) -> dict[str, Any]:
    """Update one proposed file in a task patch after a user edit.

    Rewrites `after` and regenerates the unified diff for that entry while
    keeping `before` unchanged, so the version guard still applies at apply
    time. Powers the editable code viewer before a patch is accepted.

    When `base_after` is provided, it must match the patch content the editor
    loaded. This prevents a stale editor draft from silently overwriting a
    newer repair result.
    """
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    mark_stale_tasks(project_root, state)
    task = _task_by_id(state, task_id)
    if task["status"] not in {"review", "partially_applied"}:
        raise WorkspaceError(f"任务 `{task_id}` 当前状态不可编辑补丁。")

    entry = next(
        (entry for entry in task.get("patch", []) if entry.get("path") == path),
        None,
    )
    if entry is None:
        raise WorkspaceError(f"补丁中没有文件：{path}")
    if base_after is not None and str(entry.get("after") or "") != base_after:
        raise WorkspaceError(
            f"补丁文件 `{path}` 已在别处更新（STALE_PATCH），请放弃旧草稿后重试。"
        )

    entry["after"] = content
    entry["diff"] = "".join(
        difflib.unified_diff(
            entry.get("before", "").splitlines(keepends=True),
            content.splitlines(keepends=True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            n=3,
        )
    )
    entry["hunks"] = diff_hunks(entry.get("before", ""), content)
    append_work_log(
        project_root,
        str(task.get("module_id") or task_id),
        "编辑补丁",
        task_id,
        path,
    )
    state["last_error"] = ""
    write_task_state(project_root, state)
    return state


def apply_task_hunks(
    project_root: Path,
    task_id: str,
    path: str,
    accepted_hunk_ids: list[int],
) -> dict[str, Any]:
    """Apply a selected subset of diff hunks for one patch file.

    One-shot semantics: accepted hunks are merged into the on-disk file (version
    guarded), rejected hunks are dropped, and the entry is left with no remaining
    diff.
    """
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    mark_stale_tasks(project_root, state)
    task = _task_by_id(state, task_id)
    if task["status"] not in {"review", "partially_applied"}:
        raise WorkspaceError(f"任务 `{task_id}` 当前状态不可应用。")
    entry = next(
        (entry for entry in task.get("patch", []) if entry.get("path") == path),
        None,
    )
    if entry is None:
        raise WorkspaceError(f"补丁中没有文件：{path}")

    before = entry.get("before", "")
    after = entry.get("after", "")
    accepted = {int(item) for item in (accepted_hunk_ids or [])}
    partial = apply_hunks(before, after, accepted)
    lint = contract_lint.check_patch(project_root, [{"path": path, "after": partial}])
    if lint["violations"]:
        raise WorkspaceError(
            "契约校验未通过：\n" + "\n".join(f"- {item}" for item in lint["violations"])
        )
    task["contract_lint_violations"] = lint["violations"]
    task["contract_delta"] = lint["contract_delta"]
    task["contract_lint_skipped"] = lint["skipped"]

    target = _assert_safe_target(project_root, path)
    ensure_file_unchanged(target, path, before)
    changes = {path: partial}
    _merge_contract_delta(project_root, lint["contract_delta"], changes)
    task["status"] = "applying"
    write_task_state(project_root, state)
    apply_text_files_atomic(project_root, changes, {path: before})
    task["documentation_updated"] = _post_write_documentation(
        project_root, state, task, [path]
    )

    entry["before"] = partial
    entry["after"] = partial
    entry["diff"] = ""
    entry["hunks"] = []
    task["applied_files"] = list(dict.fromkeys([*(task.get("applied_files") or []), path]))
    task["code_changed_files"] = list(
        dict.fromkeys([*(task.get("code_changed_files") or []), path])
    )
    task["pending_files"] = [p for p in (task.get("pending_files") or []) if p != path]
    task["rejected_files"] = [p for p in (task.get("rejected_files") or []) if p != path]
    all_paths = [str(item.get("path") or "") for item in task.get("patch", [])]
    pending_paths = [
        item
        for item in all_paths
        if item
        and item not in set(task["applied_files"])
        and item not in set(task["rejected_files"])
    ]
    task["pending_files"] = pending_paths
    task["status"] = "partially_applied" if pending_paths else "applied"

    append_work_log(
        project_root,
        str(task.get("module_id") or task_id),
        "部分应用",
        task_id,
        f"{path}（接受 {len(accepted)} 个改动块）",
    )
    state["last_error"] = ""
    write_task_state(project_root, state)
    return state


def task_hunks(project_root: Path, task_id: str, path: str) -> list[dict[str, Any]]:
    """Return the independently selectable change hunks for one patch file."""
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = _task_by_id(state, task_id)
    entry = next(
        (entry for entry in task.get("patch", []) if entry.get("path") == path),
        None,
    )
    if entry is None:
        raise WorkspaceError(f"补丁中没有文件：{path}")
    return diff_hunks(entry.get("before", ""), entry.get("after", ""))


def reject_task(project_root: Path, task_id: str) -> dict[str, Any]:
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = _task_by_id(state, task_id)
    if task["status"] not in {"review", "pending", "blocked"}:
        raise WorkspaceError(f"任务 `{task_id}` 当前不能拒绝。")
    if task["status"] == "blocked":
        append_work_log(
            project_root,
            str(task.get("module_id") or task_id),
            "越界转接已关闭",
            task_id,
            "用户已知晓该越界转接，并选择不再处理。",
        )
    else:
        append_work_log(
            project_root,
            str(task.get("module_id") or task_id),
            "补丁已拒绝",
            task_id,
            "用户在对话流审阅后拒绝了该补丁。",
        )
    task["status"] = "rejected"
    task["last_error"] = ""
    write_task_state(project_root, state)
    return state
