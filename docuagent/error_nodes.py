"""Unified error nodes that can be attached to architecture graph nodes.

Every failure that should outlive its request becomes an error node: a typed
record with a stable id, an `owner_node_id` linking it to the affected
functional node, a source, a severity, retry bookkeeping and the actions a
human may take. Error nodes persist in their own file (not `tasks.json`,
which finalize rewrites wholesale) and are only ever resolved by the flow
that fixes the underlying problem - there is no ignore, dismiss, or close
path from outside that flow.

This module is a leaf of the backend import graph: it depends on nothing
above `workspace`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workspace import atomic_write_json, managed_path, read_json, utc_now

ERROR_NODE_SCHEMA_VERSION = 1
ERROR_NODE_FILE = "error_nodes.json"

ERROR_NODE_TYPE = "error"
ERROR_SOURCES = {"generation", "verification", "documentation", "handoff", "system"}
ERROR_SEVERITIES = {"critical", "warning", "info"}
ERROR_NODE_STATUSES = {"active", "resolved"}

# Documentation errors are delivery errors. The only exits are a successful
# documentation sync; a human may retry or inspect, never dismiss.
DOCUMENTATION_ERROR_ACTIONS = [
    {"id": "retry_documentation", "label": "重试文档"},
    {"id": "view_error_detail", "label": "查看错误详情"},
    {"id": "open_related_docs", "label": "打开相关文档"},
    {"id": "view_code_change", "label": "查看触发错误的代码变更"},
]

# Failed code tasks: generation failure or verification failure. The reconcile
# (reconcile_task_error_nodes) resolves a node automatically once its task
# leaves `failed`; a human can regenerate or re-verify in the meantime.
GENERATION_ERROR_ACTIONS = [
    {"id": "retry_task", "label": "重新生成"},
    {"id": "view_error_detail", "label": "查看错误详情"},
]

VERIFICATION_ERROR_ACTIONS = [
    {"id": "retry_task", "label": "重新生成"},
    {"id": "verify_task", "label": "重新验证"},
    {"id": "view_error_detail", "label": "查看错误详情"},
]

TASK_ERROR_KIND = "task-failed"

# Mirrors task_state.MAX_REPAIR_ATTEMPTS; kept local so this module stays a
# leaf of the import graph (task_state pulls in agent_tools).
MAX_TASK_REPAIR_RETRIES = 5


class ErrorNodeError(Exception):
    """Raised for invalid error node arguments."""


def _validate(source: str, severity: str) -> None:
    if source not in ERROR_SOURCES:
        raise ErrorNodeError(
            f"错误来源必须是 {' / '.join(sorted(ERROR_SOURCES))}，收到：{source}"
        )
    if severity not in ERROR_SEVERITIES:
        raise ErrorNodeError(
            f"严重级别必须是 {' / '.join(sorted(ERROR_SEVERITIES))}，收到：{severity}"
        )


def error_node_id(source: str, kind: str, owner_node_id: str) -> str:
    """Stable id: re-blocking the same problem refreshes, never duplicates."""
    return f"{ERROR_NODE_TYPE}:{source}:{kind}:{owner_node_id}"


def read_error_nodes(project_root: Path) -> list[dict[str, Any]]:
    raw = read_json(managed_path(project_root, ERROR_NODE_FILE))
    if not isinstance(raw, dict):
        return []
    nodes = raw.get("nodes", [])
    return [node for node in nodes if isinstance(node, dict)]


def _write_error_nodes(project_root: Path, nodes: list[dict[str, Any]]) -> None:
    atomic_write_json(
        managed_path(project_root, ERROR_NODE_FILE),
        {
            "schema_version": ERROR_NODE_SCHEMA_VERSION,
            "updated_at": utc_now(),
            "nodes": nodes,
        },
    )


def create_or_refresh_error_node(
    project_root: Path,
    *,
    owner_node_id: str,
    source: str,
    kind: str,
    severity: str,
    title: str,
    detail: str,
    retry_count: int,
    max_retries: int,
    actions: list[dict[str, str]] | None = None,
    code_task_id: str = "",
    changed_files: list[str] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Create an active error node or refresh the existing one with the same id.

    Returns `(node, created)`; `created` is False when an active node with the
    same stable id already existed and was refreshed in place.
    """
    _validate(source, severity)
    owner = str(owner_node_id or "").strip() or "project"
    node_id = error_node_id(source, kind, owner)
    now = utc_now()
    node: dict[str, Any] = {
        "id": node_id,
        "type": ERROR_NODE_TYPE,
        "owner_node_id": owner,
        "source": source,
        "kind": kind,
        "severity": severity,
        "title": str(title),
        "detail": str(detail),
        "retry_count": max(0, int(retry_count)),
        "max_retries": max(0, int(max_retries)),
        "status": "active",
        "actions": [dict(action) for action in (actions or [])],
        "code_task_id": str(code_task_id or ""),
        "changed_files": [str(path).replace("\\", "/") for path in (changed_files or [])],
        "created_at": now,
        "updated_at": now,
        "resolved_at": "",
    }
    nodes = read_error_nodes(project_root)
    for index, existing in enumerate(nodes):
        if existing.get("id") == node_id:
            created = existing.get("status") != "active"
            node["created_at"] = str(existing.get("created_at") or now)
            nodes[index] = node
            _write_error_nodes(project_root, nodes)
            return node, created
    nodes.append(node)
    _write_error_nodes(project_root, nodes)
    return node, True


def resolve_error_nodes(
    project_root: Path,
    *,
    source: str,
    kind: str = "",
    owner_node_id: str = "",
) -> list[dict[str, Any]]:
    """Resolve matching active error nodes; only the fixing flow calls this.

    Returns the nodes resolved by this call (empty when nothing was active,
    in which case no resolve event should be emitted).
    """
    _validate(source, "info")
    nodes = read_error_nodes(project_root)
    resolved: list[dict[str, Any]] = []
    now = utc_now()
    for node in nodes:
        if node.get("source") != source or node.get("status") != "active":
            continue
        if kind and node.get("kind") != kind:
            continue
        if owner_node_id and node.get("owner_node_id") != owner_node_id:
            continue
        node["status"] = "resolved"
        node["resolved_at"] = now
        node["updated_at"] = now
        resolved.append(node)
    if resolved:
        _write_error_nodes(project_root, nodes)
    return resolved


def active_error_nodes(project_root: Path) -> list[dict[str, Any]]:
    return [node for node in read_error_nodes(project_root) if node.get("status") == "active"]


def _task_error_source(task: dict[str, Any]) -> str:
    """A failed task failed during verification iff it left a verification output."""
    return "verification" if task.get("verification_output") else "generation"


def reconcile_task_error_nodes(
    project_root: Path,
    tasks: list[Any],
) -> list[dict[str, Any]]:
    """Align task-failed error nodes with the current task statuses.

    Every `failed` task gets an active node (source: verification when it has a
    verification output, generation otherwise); every task-failed node whose
    task is no longer failed is resolved. Called from the task-state write
    entry, so all failure paths — current and future — are covered without
    touching each one. Returns the created/resolved nodes (empty when the
    persisted file is already consistent).
    """
    nodes = read_error_nodes(project_root)
    changed = False
    touched: list[dict[str, Any]] = []
    now = utc_now()

    expected_ids: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict) or task.get("status") != "failed":
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id:
            continue
        owner = str(task.get("module_id") or "").strip() or task_id
        source = _task_error_source(task)
        expected_ids.add(error_node_id(source, TASK_ERROR_KIND, owner))
        node_id = error_node_id(source, TASK_ERROR_KIND, owner)
        detail = str(task.get("last_error") or "").strip()
        actions = (
            VERIFICATION_ERROR_ACTIONS
            if source == "verification"
            else GENERATION_ERROR_ACTIONS
        )
        severity = "critical" if task.get("repair_attempts") else "warning"
        existing = next((n for n in nodes if n.get("id") == node_id), None)
        if existing:
            # Same id already persisted: refresh in place (covers both "already
            # active, detail moved on" and "resolved earlier, failed again").
            if existing.get("status") == "active" and existing.get("detail") == detail:
                continue
            existing["detail"] = detail
            existing["retry_count"] = max(0, int(task.get("repair_attempts") or 0))
            existing["status"] = "active"
            existing["resolved_at"] = ""
            existing["updated_at"] = now
            changed = True
            touched.append(existing)
            continue
        nodes.append({
            "id": node_id,
            "type": ERROR_NODE_TYPE,
            "owner_node_id": owner,
            "source": source,
            "kind": TASK_ERROR_KIND,
            "severity": severity,
            "title": f"任务 `{task_id}` 失败",
            "detail": detail,
            "retry_count": max(0, int(task.get("repair_attempts") or 0)),
            "max_retries": MAX_TASK_REPAIR_RETRIES,
            "status": "active",
            "actions": [dict(action) for action in actions],
            "code_task_id": task_id,
            "changed_files": [
                str(path).replace("\\", "/")
                for path in (task.get("target_files") or [])
            ],
            "created_at": now,
            "updated_at": now,
            "resolved_at": "",
        })
        touched.append(nodes[-1])
        changed = True

    for node in nodes:
        if (
            node.get("kind") == TASK_ERROR_KIND
            and node.get("status") == "active"
            and node.get("id") not in expected_ids
        ):
            node["status"] = "resolved"
            node["resolved_at"] = now
            node["updated_at"] = now
            touched.append(node)
            changed = True

    if changed:
        _write_error_nodes(project_root, nodes)
    return touched
