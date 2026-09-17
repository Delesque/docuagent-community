"""Task planning and architecture-derived work-item orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import task_runtime as _task_runtime
import token_usage
from core import WorkspaceError, find_edge_cycle, normalized_list, normalized_text, slugify
from llm_client import ProviderConfig
from standards import TASK_INTEGRITY_RULES
from task_state import TASK_SCHEMA_VERSION, read_task_state, write_task_state

TASK_PLAN_PROMPT = """You are the Task Planning Agent for a local-first AI coding workspace.
Turn the architecture below into a minimal task DAG. Every task must:
- have a short slug id and a one-line summary;
- reference exactly one architecture module via `module_id` (use the module id from the architecture, usually one task per module);
- list relative target file paths that task will create or change;
- list `depends_on` ids whose tasks must be applied first;
- list verification commands (prefer `python -m unittest tests.<name>` or `python <file>`).
Do not invent framework layers that are not in the architecture. Return JSON only:
{"tasks":[{"id":"...","module_id":"...","summary":"...","target_files":["..."],"depends_on":["..."],"verification":["..."]}]}
""" + "\n\n" + TASK_INTEGRITY_RULES

TARGET_MODULE_PLAN_RULE = """
You are planning one module only. The architecture may include dependency modules for
context, but create tasks ONLY whose `module_id` equals the target module. Do not create
tasks for any other module. Keep `depends_on` empty unless it references another task in
this same plan for the target module.
"""


def _plan_prompt(module_id: str | None = None) -> str:
    if not module_id:
        return TASK_PLAN_PROMPT
    return (
        TASK_PLAN_PROMPT
        + "\n\n"
        + TARGET_MODULE_PLAN_RULE
        + f"\nTarget module: {module_id}"
    )


def _focused_architecture(
    architecture: dict[str, Any],
    module_id: str,
) -> dict[str, Any]:
    """Keep the target module and its transitive upstream dependencies as context."""
    modules = architecture.get("modules", [])
    if not isinstance(modules, list):
        raise WorkspaceError("架构缺少模块列表。")
    known = {module.get("id") for module in modules if isinstance(module, dict)}
    if module_id not in known:
        raise WorkspaceError(f"架构中不存在模块：{module_id}")

    selected = {module_id}
    changed = True
    while changed:
        changed = False
        for module in modules:
            if not isinstance(module, dict) or module.get("id") not in selected:
                continue
            for dependency in module.get("depends_on", []):
                if dependency in known and dependency not in selected:
                    selected.add(dependency)
                    changed = True

    by_id = {module.get("id"): module for module in modules if isinstance(module, dict)}
    focused_modules = [by_id[mid] for mid in selected if mid in by_id]
    focused_edges = [
        edge
        for edge in architecture.get("edges", [])
        if isinstance(edge, dict)
        and edge.get("from") in selected
        and edge.get("to") in selected
    ]
    return {
        **architecture,
        "modules": focused_modules,
        "edges": focused_edges,
    }


def _merge_planned_module(
    existing: dict[str, Any] | None,
    plan: dict[str, Any],
    module_id: str,
) -> dict[str, Any]:
    """Merge one module's plan into the persisted task DAG."""
    new_tasks = [task for task in plan.get("tasks", []) if task.get("module_id") == module_id]
    foreign = [
        task
        for task in plan.get("tasks", [])
        if task.get("module_id") and task.get("module_id") != module_id
    ]
    if foreign:
        ids = sorted({task["id"] for task in foreign})[:5]
        raise WorkspaceError(
            f"按模块规划只允许目标模块任务，模型还返回了：{', '.join(ids)}"
        )
    if not new_tasks:
        raise WorkspaceError(f"Task Agent 没有为模块 `{module_id}` 规划任务。")

    previous = (existing or {}).get("tasks", [])
    if not isinstance(previous, list):
        previous = []
    kept = [task for task in previous if task.get("module_id") != module_id]
    known_ids = {task["id"] for task in kept}
    known_ids.update(task["id"] for task in new_tasks)

    for task in kept + new_tasks:
        missing = [dep for dep in task.get("depends_on", []) if dep not in known_ids]
        if missing:
            raise WorkspaceError(
                f"任务 `{task['id']}` 依赖未规划任务：{', '.join(missing)}"
            )

    merged_tasks = kept + new_tasks
    graph = {task["id"]: task.get("depends_on", []) for task in merged_tasks}
    if find_edge_cycle(graph):
        raise WorkspaceError("合并后的任务 DAG 出现依赖环。")

    return {
        "schema_version": (existing or {}).get("schema_version", TASK_SCHEMA_VERSION),
        "task_version": ((existing or {}).get("task_version") or 0) + 1,
        "status": "planned",
        "tasks": merged_tasks,
        "last_error": "",
    }


def merge_planned_modules(
    existing: dict[str, Any] | None,
    plan: dict[str, Any],
    module_ids: list[str],
) -> dict[str, Any]:
    """Merge a cross-module plan, replacing only the requested modules.

    This is the orchestration counterpart of `_merge_planned_module`: the advanced
    orchestrator may return tasks for several modules at once, but must never wipe
    tasks belonging to modules it did not plan.
    """
    scope = set(module_ids)
    if not scope:
        raise WorkspaceError("编排至少需要一个目标模块。")

    new_tasks = [
        task for task in plan.get("tasks", []) if task.get("module_id") in scope
    ]
    foreign = [
        task
        for task in plan.get("tasks", [])
        if task.get("module_id") and task.get("module_id") not in scope
    ]
    if foreign:
        ids = sorted({task["id"] for task in foreign})[:5]
        raise WorkspaceError(
            f"编排只允许目标模块任务，模型还返回了：{', '.join(ids)}"
        )
    if not new_tasks:
        raise WorkspaceError("高级 Agent 没有为目标模块规划任务。")

    previous = (existing or {}).get("tasks", [])
    if not isinstance(previous, list):
        previous = []
    kept = [task for task in previous if task.get("module_id") not in scope]
    known_ids = {task["id"] for task in kept}
    known_ids.update(task["id"] for task in new_tasks)

    for task in kept + new_tasks:
        missing = [dep for dep in task.get("depends_on", []) if dep not in known_ids]
        if missing:
            raise WorkspaceError(
                f"任务 `{task['id']}` 依赖未规划任务：{', '.join(missing)}"
            )

    merged_tasks = kept + new_tasks
    graph = {task["id"]: task.get("depends_on", []) for task in merged_tasks}
    if find_edge_cycle(graph):
        raise WorkspaceError("合并后的任务 DAG 出现依赖环。")

    return {
        "schema_version": (existing or {}).get("schema_version", TASK_SCHEMA_VERSION),
        "task_version": ((existing or {}).get("task_version") or 0) + 1,
        "status": "planned",
        "tasks": merged_tasks,
        "last_error": "",
        **(
            {"orchestration": plan["orchestration"]}
            if isinstance(plan.get("orchestration"), dict)
            else {}
        ),
    }


def normalize_task_plan(raw: Any, module_ids: list[str]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WorkspaceError("Task Agent 返回值必须是 JSON 对象。")
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list):
        raise WorkspaceError("Task Agent 缺少 `tasks` 数组。")

    known_modules = set(module_ids)
    ids: set[str] = set()
    for item in tasks_raw:
        if not isinstance(item, dict):
            raise WorkspaceError("Task Agent 的 `tasks` 元素必须是对象。")
        task_id = slugify(str(item.get("id") or item.get("summary") or ""))
        if not task_id:
            raise WorkspaceError("Task Agent 返回了缺少可用 id 的任务。")
        if task_id in ids:
            raise WorkspaceError(f"Task Agent 返回了重复任务 ID：{task_id}")
        ids.add(task_id)

    tasks: list[dict[str, Any]] = []
    for index, item in enumerate(tasks_raw):
        if not isinstance(item, dict):
            raise WorkspaceError(f"`tasks[{index}]` 必须是对象。")
        task_id = slugify(str(item.get("id") or item.get("summary") or ""))
        module_id = str(item.get("module_id") or "").strip()
        if not module_id:
            if len(known_modules) == 1:
                module_id = next(iter(known_modules))
            else:
                raise WorkspaceError(f"任务 `{task_id}` 缺少 module_id。")
        if module_id not in known_modules:
            raise WorkspaceError(f"任务 `{task_id}` 引用了未知模块：{module_id}")

        target_files: list[str] = []
        for path in normalized_list(item.get("target_files"), f"tasks[{index}].target_files"):
            candidate = Path(str(path).replace("\\", "/"))
            if candidate.is_absolute() or ".." in candidate.parts:
                raise WorkspaceError(f"任务 `{task_id}` 包含不安全的文件路径。")
            target_files.append(str(candidate).replace("\\", "/"))
        if not target_files:
            raise WorkspaceError(f"任务 `{task_id}` 至少需要一个目标文件。")

        depends_on = normalized_list(item.get("depends_on"), f"tasks[{index}].depends_on")
        for dependency in depends_on:
            if dependency not in ids:
                raise WorkspaceError(f"任务 `{task_id}` 依赖未知任务：{dependency}")

        tasks.append({
            "id": task_id,
            "module_id": module_id,
            "summary": normalized_text(item.get("summary"), f"tasks[{index}].summary", True),
            "target_files": target_files,
            "depends_on": depends_on,
            "verification": normalized_list(
                item.get("verification"), f"tasks[{index}].verification"
            ),
            "priority": "medium",
            "status": "pending",
            "patch": [],
            "thinking": "",
            "last_error": "",
        })

    graph = {task["id"]: task["depends_on"] for task in tasks}
    if find_edge_cycle(graph):
        raise WorkspaceError("Task Agent 返回了带环的任务依赖。")

    return {
        "schema_version": TASK_SCHEMA_VERSION,
        "task_version": 1,
        "status": "planned",
        "tasks": tasks,
        "last_error": "",
    }


def sync_work_items_from_architecture(
    project_root: Path,
    architecture: dict[str, Any],
    project: dict[str, Any] | None = None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Derive one work item per architecture module. No task-planning model.

    The architecture graph is the work graph. Each module becomes one sub-agent
    assignment; module dependencies become execution dependencies; module path is
    the sandbox scope. `tasks.json` is just a materialized view for scheduling,
    diff review, and the outline list — the user never has to confirm a separate
    task plan.
    """
    if existing is None:
        existing = read_task_state(project_root)
    modules = architecture.get("modules", []) if isinstance(architecture, dict) else []
    module_ids = [str(module.get("id") or "") for module in modules if isinstance(module, dict)]
    if existing and existing.get("source") != "architecture" and existing.get("tasks"):
        # A pre-existing AI task plan is still a valid work plan; keep it rather
        # than silently replacing reviewed work. New architecture-first states are
        # marked `source: architecture` and always derive from modules.
        return existing
    old_tasks = {
        str(task.get("id") or ""): task
        for task in (existing or {}).get("tasks", [])
        if isinstance(task, dict)
    }

    tasks: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("id") or "")
        if not module_id:
            continue
        previous = old_tasks.get(module_id, {})
        path = str(module.get("path") or "").strip().replace("\\", "/").strip("/")
        explicit_targets = [
            str(item).strip().replace("\\", "/").strip("/")
            for item in (module.get("target_files") or [])
            if str(item).strip()
        ]
        previous_targets = [
            str(item).strip().replace("\\", "/").strip("/")
            for item in (previous.get("target_files") or [])
            if str(item).strip()
        ]
        target_files = explicit_targets or previous_targets or ([path] if path else [])
        if not target_files:
            target_files = []
        verification = [
            str(item).strip()
            for item in (module.get("verification") or previous.get("verification") or [])
            if str(item).strip()
        ]
        status = previous.get("status", "pending")
        if status in {"planned", "generating"}:
            status = "pending"
        tasks.append({
            "id": module_id,
            "module_id": module_id,
            "summary": normalized_text(
                module.get("brief") or module.get("responsibility"),
                f"module:{module_id}",
                True,
            ),
            "target_files": target_files,
            "depends_on": [
                str(item).strip()
                for item in (module.get("depends_on") or [])
                if str(item).strip() in module_ids
            ],
            "verification": verification,
            "priority": previous.get("priority", "medium"),
            "status": status,
            "patch": previous.get("patch", []),
            "thinking": previous.get("thinking", ""),
            "last_error": previous.get("last_error", ""),
            "applied_files": previous.get("applied_files", []),
            "rejected_files": previous.get("rejected_files", []),
            "pending_files": previous.get("pending_files", []),
            "verification_output": previous.get("verification_output"),
            "repair_attempts": previous.get("repair_attempts", 0),
            "execution_mode": previous.get("execution_mode"),
            "sandbox_path": previous.get("sandbox_path"),
            "applied_files": previous.get("applied_files", []),
            "code_changed_files": previous.get("code_changed_files", []),
            "documentation_updated": previous.get("documentation_updated", []),
            "documentation_error": previous.get("documentation_error", ""),
            "documentation_registered": previous.get(
                "documentation_registered", False
            ),
        })

    done_statuses = {"applied", "verified", "rejected"}
    status = "confirmed"
    if tasks and all(task["status"] in done_statuses for task in tasks):
        status = "done"
    state = {
        "schema_version": TASK_SCHEMA_VERSION,
        "task_version": 1,
        "status": status,
        "tasks": tasks,
        "last_error": existing.get("last_error", "") if existing else "",
        "source": "architecture",
        "documentation_task": (
            existing.get("documentation_task")
            if isinstance(existing, dict)
            else None
        ),
        "docs_in_sync": existing.get("docs_in_sync", True) if existing else True,
        "docs_sync_required": (
            existing.get("docs_sync_required", False) if existing else False
        ),
        "delivery_status": (
            existing.get("delivery_status", "blocked") if existing else "blocked"
        ),
        "overall_status": (
            existing.get("overall_status", "blocked") if existing else "blocked"
        ),
    }
    write_task_state(project_root, state)
    return state




def set_task_priority(
    project_root: Path,
    task_id: str,
    priority: str,
) -> dict[str, Any]:
    """Adjust one task's priority, keeping orchestration metadata in sync."""
    priority = str(priority or "").strip().lower()
    if priority not in {"high", "medium", "low"}:
        raise WorkspaceError("任务优先级只能是 high/medium/low。")
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = next(
        (item for item in state.get("tasks", []) if item.get("id") == task_id),
        None,
    )
    if not task:
        raise WorkspaceError(f"找不到任务：{task_id}")
    task["priority"] = priority
    orchestration = state.get("orchestration")
    if isinstance(orchestration, dict) and isinstance(
        orchestration.get("priorities"), dict
    ):
        orchestration["priorities"][task_id] = priority
    write_task_state(project_root, state)
    return state


def plan_tasks(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    module_id: str | None = None,
) -> dict[str, Any]:
    existing = read_task_state(project_root)
    if existing and existing.get("status") in {"confirmed", "generating", "review"}:
        raise WorkspaceError("已有进行中的任务计划，请先完成或重新规划。")
    prompt = TASK_PLAN_PROMPT
    known_module_ids = [module["id"] for module in architecture.get("modules", [])]
    if module_id:
        architecture = _focused_architecture(architecture, module_id)
        prompt = _plan_prompt(module_id)
    with token_usage.usage_scope(
        project_root,
        module_id=module_id or "",
        feature="planning",
    ):
        result = _task_runtime.call_model_json(
            provider,
            prompt,
            {"project": project, "architecture": architecture},
            timeout=240,
        )
    plan = normalize_task_plan(
        result,
        known_module_ids,
    )
    if module_id:
        plan = _merge_planned_module(existing, plan, module_id)
    write_task_state(project_root, plan)
    return plan


def stream_plan_tasks(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    module_id: str | None = None,
):
    """Streaming variant of `plan_tasks`: reasoning/content plus a final done event."""
    existing = read_task_state(project_root)
    if existing and existing.get("status") in {"confirmed", "generating", "review"}:
        raise WorkspaceError("已有进行中的任务计划，请先完成或重新规划。")
    prompt = TASK_PLAN_PROMPT
    known_module_ids = [module["id"] for module in architecture.get("modules", [])]
    if module_id:
        architecture = _focused_architecture(architecture, module_id)
        prompt = _plan_prompt(module_id)

    def emit():
        with token_usage.usage_scope(
            project_root,
            module_id=module_id or "",
            feature="planning",
        ):
            for event in _task_runtime.stream_json_model(
                provider,
                prompt,
                {"project": project, "architecture": architecture},
                timeout=240,
            ):
                if event["type"] == "done":
                    plan = normalize_task_plan(
                        event["result"],
                        known_module_ids,
                    )
                    if module_id:
                        plan = _merge_planned_module(existing, plan, module_id)
                    write_task_state(project_root, plan)
                    yield {"type": "done", "tasks": plan}
                else:
                    yield event

    return emit()


def confirm_tasks(project_root: Path) -> dict[str, Any]:
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    if state["status"] != "planned":
        raise WorkspaceError("任务计划已经确认。")
    state["status"] = "confirmed"
    write_task_state(project_root, state)
    return state
