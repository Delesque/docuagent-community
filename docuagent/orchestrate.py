"""Advanced orchestration: note-driven cross-module task planning.

The advanced agent is the coordination layer of Phase 4. It reads the notes and
questions the user left on architecture nodes, understands the intent, and returns a
cross-module task DAG that sub-agents then execute. It never writes files itself; it
only plans, adjudicates conflicts, and summarizes acceptance criteria.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
from typing import Any

from llm_client import ProviderConfig, call_model_json, stream_json_model
from core import WorkspaceError, normalized_list, normalized_text, slugify
from standards import TASK_INTEGRITY_RULES
import token_usage
from tasks import (
    TASK_SCHEMA_VERSION,
    merge_planned_modules,
    normalize_task_plan,
    read_task_state,
    write_task_state,
)
from workspace import (
    atomic_write_json,
    atomic_write_text,
    managed_path,
    read_json,
    read_node_attachments,
    utc_now,
)

ORCHESTRATION_LATEST_FILE = "orchestrator/latest.json"
ORCHESTRATION_LOG_FILE = "orchestrator/log.md"
TRIAGE_LATEST_FILE = "orchestrator/triage-latest.json"
TRIAGE_PROMPT = """You are an error analysis expert for a local-first AI coding workspace.
Several sub-agent tasks failed at the same time. Analyze the verification and generation
errors below, then return JSON only.

Analyze:
1. Dependencies: does one failure cause another? Return task id chains in dependency_chains.
2. Severity groups: critical failures block downstream work, warnings can be skipped,
   info entries are context for later.
3. Recommended handling order using task ids.

Return exactly:
{"summary":"one sentence about the core problem","dependency_chains":[["task1","task2"]],
 "groups":[{"severity":"critical|warning|info","recommendation":"what to do",
  "errors":[{"task_id":"...","module_id":"...","title":"...","detail":"..."}]}],
 "suggested_order":["task1","task2"]}
"""
ORCHESTRATION_PROMPT = """You are the Advanced Orchestration Agent for a local-first AI coding workspace.
The user drives the whole project by marking features and leaving notes on architecture nodes.
You read those notes, the focused architecture, the project overview, project recipes, and a
user-profile excerpt, then decompose the request into a cross-module task DAG that bounded
sub-agents can execute.

Rules:
- Every task belongs to exactly one real module in `focus_scope`, using its exact `module_id`.
- You may create tasks for several modules at once; cross-module `depends_on` is allowed.
- Target files must be relative, safe, and inside the owning module's path.
- Prefer one verifiable change per task and add verification commands when possible.
- Keep existing task summaries in mind; do not plan duplicate work for them.
- Use the user profile to calibrate wording and acceptance criteria. If a repeated practice
  appears, reuse it from `recipes`; if the practice is new, name it in `summary` as a recipe
  candidate for the memory panel.
- Return a coordination summary, any cross-module conflicts you resolved, optional priorities,
  and the task array. Return JSON only:
{"summary":"...","conflicts":["..."],"priorities":{"<task_id>":"high|medium|low"},"tasks":[...]}
""" + "\n\n" + TASK_INTEGRITY_RULES

PROFILE_MAX_CHARS = 4000
DOC_TEXT_MAX_CHARS = 4000
NOTES_MAX = 30
EXISTING_TASKS_MAX = 60


def _read_optional_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n[context truncated]"


def _scope_module_ids(
    architecture: dict[str, Any],
    module_id: str | None,
) -> list[str]:
    modules = architecture.get("modules", [])
    if not isinstance(modules, list):
        raise WorkspaceError("架构缺少模块列表。")
    known = {
        str(module.get("id"))
        for module in modules
        if isinstance(module, dict) and module.get("id")
    }
    if not module_id:
        return sorted(known)
    if module_id not in known:
        raise WorkspaceError(f"架构中不存在模块：{module_id}")

    dependencies: dict[str, set[str]] = defaultdict(set)
    reverse: dict[str, set[str]] = defaultdict(set)
    for module in modules:
        if not isinstance(module, dict):
            continue
        current = str(module.get("id") or "")
        for dependency in module.get("depends_on", []):
            dependency = str(dependency or "")
            if dependency in known and dependency != current:
                dependencies[current].add(dependency)
                reverse[dependency].add(current)

    selected: set[str] = set()
    stack = [module_id]
    while stack:
        current = stack.pop()
        if current in selected:
            continue
        selected.add(current)
        for neighbor in dependencies.get(current, set()) | reverse.get(current, set()):
            if neighbor not in selected:
                stack.append(neighbor)
    return sorted(selected)


def _focused_architecture(
    architecture: dict[str, Any],
    scope: list[str],
) -> dict[str, Any]:
    scope_set = set(scope)
    by_id = {
        str(module.get("id")): module
        for module in architecture.get("modules", [])
        if isinstance(module, dict) and module.get("id")
    }
    focused_modules = [by_id[module_id] for module_id in scope if module_id in by_id]
    focused_edges = [
        edge
        for edge in architecture.get("edges", [])
        if isinstance(edge, dict)
        and str(edge.get("from")) in scope_set
        and str(edge.get("to")) in scope_set
    ]
    return {
        **architecture,
        "modules": focused_modules,
        "edges": focused_edges,
    }


def _build_orchestration_input(
    project_root: Path,
    architecture: dict[str, Any],
    project: dict[str, Any],
    scope: list[str],
    instruction: str,
) -> dict[str, Any]:
    scope_set = set(scope)
    attachments = read_node_attachments(project_root)
    unresolved: list[dict[str, str]] = []
    for module_id in scope:
        for attachment in attachments.get(module_id, []):
            if not isinstance(attachment, dict):
                continue
            if attachment.get("resolved") or attachment.get("archived"):
                continue
            text = str(attachment.get("text") or "").strip()
            if not text:
                continue
            unresolved.append({
                "module_id": module_id,
                "type": str(attachment.get("type") or "note"),
                "text": text[:500],
            })
    unresolved = unresolved[-NOTES_MAX:]

    existing_tasks: list[dict[str, str]] = []
    state = read_task_state(project_root)
    if isinstance(state, dict):
        for task in state.get("tasks", []):
            if not isinstance(task, dict):
                continue
            if task.get("module_id") not in scope_set:
                continue
            existing_tasks.append({
                "id": str(task.get("id") or ""),
                "module_id": str(task.get("module_id") or ""),
                "summary": str(task.get("summary") or ""),
                "status": str(task.get("status") or ""),
            })
    existing_tasks = existing_tasks[-EXISTING_TASKS_MAX:]

    profile_path = Path.home() / ".docuagent" / "user-profile.md"
    return {
        "project": project,
        "architecture": _focused_architecture(architecture, scope),
        "focus_scope": scope,
        "user_instruction": instruction[:2000],
        "unresolved_notes": unresolved,
        "existing_tasks": existing_tasks,
        "project_overview": _read_optional_text(
            managed_path(project_root, "project.md"),
            DOC_TEXT_MAX_CHARS,
        ),
        "standards": _read_optional_text(
            managed_path(project_root, "standards.md"),
            DOC_TEXT_MAX_CHARS,
        ),
        "recipes": _read_optional_text(
            managed_path(project_root, "recipes.md"),
            DOC_TEXT_MAX_CHARS,
        ),
        "user_profile": _read_optional_text(profile_path, PROFILE_MAX_CHARS),
    }


def normalize_orchestration(
    raw: Any,
    module_ids: list[str],
    scope_module_ids: list[str],
) -> dict[str, Any]:
    """Validate the advanced-agent result and attach coordination metadata."""
    if not isinstance(raw, dict):
        raise WorkspaceError("高级 Agent 返回值必须是 JSON 对象。")
    plan = normalize_task_plan(raw, module_ids)
    scope = set(scope_module_ids)
    for task in plan.get("tasks", []):
        if task.get("module_id") not in scope:
            raise WorkspaceError(
                f"任务 `{task['id']}` 超出了编排范围模块：{task['module_id']}"
            )

    summary = normalized_text(raw.get("summary"), "summary")[:2000]
    conflicts = normalized_list(raw.get("conflicts"), "conflicts")
    conflicts = [conflict[:500] for conflict in conflicts[:20]]

    task_ids = {task["id"] for task in plan.get("tasks", [])}
    priorities: dict[str, str] = {}
    raw_priorities = raw.get("priorities")
    if isinstance(raw_priorities, dict):
        for raw_task_id, raw_priority in raw_priorities.items():
            task_id = slugify(str(raw_task_id))
            priority = str(raw_priority or "").strip().lower()
            if task_id in task_ids and priority in {"high", "medium", "low"}:
                priorities[task_id] = priority
    for task in plan.get("tasks", []):
        task["priority"] = priorities.get(task["id"], "medium")

    plan["orchestration"] = {
        "summary": summary,
        "conflicts": conflicts,
        "priorities": priorities,
        "scope_module_ids": sorted(scope),
    }
    return plan


def write_orchestration_record(
    project_root: Path,
    plan: dict[str, Any],
    module_id: str | None,
) -> dict[str, Any]:
    orchestration = plan.get("orchestration") or {}
    record = {
        "schema_version": TASK_SCHEMA_VERSION,
        "created_at": utc_now(),
        "trigger_module": module_id or "global",
        "summary": orchestration.get("summary", ""),
        "conflicts": orchestration.get("conflicts", []),
        "priorities": orchestration.get("priorities", {}),
        "task_count": len(plan.get("tasks", [])),
        "module_ids": sorted(
            {
                task["module_id"]
                for task in plan.get("tasks", [])
                if task.get("module_id")
            }
        ),
    }
    latest_path = managed_path(project_root, ORCHESTRATION_LATEST_FILE)
    atomic_write_json(latest_path, record)

    log_path = managed_path(project_root, ORCHESTRATION_LOG_FILE)
    previous = ""
    if log_path.exists():
        try:
            previous = log_path.read_text(encoding="utf-8").strip()
        except OSError:
            previous = ""
    lines = [
        "",
        f"## {record['created_at']} — {record['trigger_module']}",
        "",
        f"任务数：{record['task_count']}；涉及模块：{', '.join(record['module_ids']) or '无'}",
    ]
    if record["summary"]:
        lines += ["", f"汇总：{record['summary']}"]
    if record["conflicts"]:
        lines += ["", "裁决："] + [f"- {conflict}" for conflict in record["conflicts"]]
    if record["priorities"]:
        lines += ["", "优先级："] + [
            f"- {task_id}: {priority}"
            for task_id, priority in record["priorities"].items()
        ]
    content = (previous + "\n" if previous else "") + "\n".join(lines) + "\n"
    atomic_write_text(log_path, content)
    return record


def _known_error_map(errors: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    known: dict[str, dict[str, Any]] = {}
    for error in errors:
        task_id = str(error.get("task_id") or "").strip()
        if task_id:
            known[task_id] = error
    return known


def _normalize_error_group(
    raw: Any,
    known: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    severity = str(raw.get("severity") or "").strip().lower()
    if severity not in {"critical", "warning", "info"}:
        severity = "warning"
    recommendation = str(raw.get("recommendation") or "").strip()[:800]
    raw_errors = raw.get("errors")
    if not isinstance(raw_errors, list):
        return None
    entries: list[dict[str, Any]] = []
    for item in raw_errors:
        if isinstance(item, str):
            source = known.get(item.strip())
            if source:
                entries.append(source)
            continue
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "").strip()
        title = str(item.get("title") or task_id or "未命名错误")[:200]
        detail = str(item.get("detail") or "")[:4000]
        module_id = str(item.get("module_id") or "")[:200]
        entries.append({
            "task_id": task_id,
            "module_id": module_id,
            "title": title,
            "detail": detail,
        })
    if not entries:
        return None
    return {
        "severity": severity,
        "recommendation": recommendation,
        "errors": entries,
    }


def normalize_triage_result(
    raw: Any,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WorkspaceError("错误汇总必须返回 JSON 对象。")
    known = _known_error_map(errors)
    known_ids = set(known)
    summary = normalized_text(raw.get("summary"), "summary")[:2000]

    groups: list[dict[str, Any]] = []
    raw_groups = raw.get("groups")
    if isinstance(raw_groups, list):
        for group in raw_groups:
            normalized = _normalize_error_group(group, known)
            if normalized:
                groups.append(normalized)
    if not groups:
        groups = [{
            "severity": "warning",
            "recommendation": "逐项检查错误并处理。",
            "errors": list(known.values()),
        }]

    dependency_chains: list[list[str]] = []
    raw_chains = raw.get("dependency_chains")
    if isinstance(raw_chains, list):
        for chain in raw_chains:
            if not isinstance(chain, list):
                continue
            cleaned = [
                str(task_id).strip()
                for task_id in chain
                if str(task_id).strip() in known_ids
            ]
            if cleaned:
                dependency_chains.append(cleaned)

    suggested_order: list[str] = []
    raw_order = raw.get("suggested_order")
    if isinstance(raw_order, list):
        for task_id in raw_order:
            cleaned = str(task_id).strip()
            if cleaned in known_ids and cleaned not in suggested_order:
                suggested_order.append(cleaned)
    if not suggested_order:
        suggested_order = list(known_ids)

    return {
        "summary": summary or "多个任务同时失败，建议按依赖顺序逐个检查。",
        "dependency_chains": dependency_chains,
        "groups": groups,
        "suggested_order": suggested_order,
    }

def _clean_triage_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned_errors: list[dict[str, Any]] = []
    for error in errors:
        if not isinstance(error, dict):
            continue
        task_id = str(error.get("task_id") or "").strip()
        if not task_id:
            continue
        cleaned_errors.append({
            "task_id": task_id[:200],
            "module_id": str(error.get("module_id") or "")[:200],
            "title": str(error.get("title") or task_id)[:200],
            "detail": str(error.get("detail") or "")[:4000],
            "source": str(error.get("source") or "verification")[:100],
            "files": normalized_list(error.get("files"), "files")[:20],
        })
    return cleaned_errors


def _triage_fingerprint(errors: list[dict[str, Any]]) -> str:
    payload = json.dumps(errors, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()




def latest_triage(
    project_root: Path,
    errors: list[dict[str, Any]],
) -> dict[str, Any] | None:
    cleaned_errors = _clean_triage_errors(errors)
    record = read_json(managed_path(project_root, TRIAGE_LATEST_FILE))
    if not isinstance(record, dict) or record.get("input_fingerprint") != _triage_fingerprint(cleaned_errors):
        return None
    return {
        "summary": record.get("summary") or "",
        "dependency_chains": record.get("dependency_chains") or [],
        "groups": record.get("groups") or [],
        "suggested_order": record.get("suggested_order") or [],
    }


def triage_errors(
    project_root: Path,
    provider: ProviderConfig,
    errors: list[dict[str, Any]],
) -> dict[str, Any]:
    """Ask the model to group recent task errors and recommend a handling order."""
    if not isinstance(errors, list):
        raise WorkspaceError("错误汇总需要 errors 列表。")
    cleaned_errors = _clean_triage_errors(errors)
    if not cleaned_errors:
        raise WorkspaceError("没有可汇总的错误。")

    fingerprint = _triage_fingerprint(cleaned_errors)
    previous = read_json(managed_path(project_root, TRIAGE_LATEST_FILE))
    if isinstance(previous, dict) and previous.get("input_fingerprint") == fingerprint:
        return {
            "summary": previous.get("summary") or "",
            "dependency_chains": previous.get("dependency_chains") or [],
            "groups": previous.get("groups") or [],
            "suggested_order": previous.get("suggested_order") or [],
        }

    with token_usage.usage_scope(project_root, feature="triage"):
        result = call_model_json(
            provider,
            TRIAGE_PROMPT,
            {"errors": cleaned_errors},
            timeout=180,
        )
    normalized = normalize_triage_result(result, cleaned_errors)
    record = {
        "created_at": utc_now(),
        "error_count": len(cleaned_errors),
        "input_fingerprint": fingerprint,
        **normalized,
    }
    atomic_write_json(
        managed_path(project_root, TRIAGE_LATEST_FILE),
        record,
    )
    return normalized


def orchestrate_tasks(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    module_id: str | None = None,
    instruction: str = "",
) -> dict[str, Any]:
    existing = read_task_state(project_root)
    if existing and existing.get("status") in {"confirmed", "generating", "review"}:
        raise WorkspaceError("已有进行中的任务计划，请先完成或重新规划。")

    known_module_ids = [
        str(module["id"])
        for module in architecture.get("modules", [])
        if isinstance(module, dict) and module.get("id")
    ]
    scope = _scope_module_ids(architecture, module_id)
    payload = _build_orchestration_input(
        project_root,
        architecture,
        project,
        scope,
        instruction,
    )
    with token_usage.usage_scope(
        project_root,
        module_id=module_id or "",
        feature="orchestration",
    ):
        result = call_model_json(provider, ORCHESTRATION_PROMPT, payload, timeout=240)
    plan = normalize_orchestration(result, known_module_ids, scope)
    plan = merge_planned_modules(existing, plan, scope)
    write_task_state(project_root, plan)
    write_orchestration_record(project_root, plan, module_id)
    return plan


def stream_orchestrate_tasks(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    module_id: str | None = None,
    instruction: str = "",
):
    """Streaming variant of `orchestrate_tasks`: reasoning plus a final done event."""
    existing = read_task_state(project_root)
    if existing and existing.get("status") in {"confirmed", "generating", "review"}:
        raise WorkspaceError("已有进行中的任务计划，请先完成或重新规划。")

    known_module_ids = [
        str(module["id"])
        for module in architecture.get("modules", [])
        if isinstance(module, dict) and module.get("id")
    ]
    scope = _scope_module_ids(architecture, module_id)
    payload = _build_orchestration_input(
        project_root,
        architecture,
        project,
        scope,
        instruction,
    )

    def emit():
        yield {"type": "started", "module_id": module_id, "scope_module_ids": scope}
        for event in stream_json_model(
            provider,
            ORCHESTRATION_PROMPT,
            payload,
            timeout=240,
        ):
            if event["type"] == "done":
                plan = normalize_orchestration(event["result"], known_module_ids, scope)
                plan = merge_planned_modules(existing, plan, scope)
                write_task_state(project_root, plan)
                write_orchestration_record(project_root, plan, module_id)
                yield {"type": "done", "tasks": plan}
            else:
                yield event

    return emit()
