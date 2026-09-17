"""Short path for the Main Agent to dispatch a small (few-file) micro task.

The Main Agent may decide a request is small enough to skip full planning. The micro
task is auto-confirmed, generated through the existing sandbox tool loop, and stops at
a reviewable diff. Applying it still goes through the normal task apply pipeline.
"""

from __future__ import annotations

import difflib
import time
from pathlib import Path
from typing import Any

import sandbox as _sandbox
import task_jobs as _task_jobs
import token_usage
from agent_tools import AgentLoopCancelled, AGENT_TOOL_LOOP_PROMPT, build_agent_context, execute_tool, run_agent_tool_loop
from llm_client import ProviderConfig, call_model_json
from core import WorkspaceError, normalized_list, normalized_text, slugify
from tasks import normalize_generated_files, validate_generated_files
from workspace import atomic_write_json, managed_path, read_json

MAX_MICRO_FILES = 3

MICRO_TASK_JUDGE_PROMPT = """You are the Main Agent for a local-first AI coding workspace.
The user described a change. Decide whether it is a small change (at most " + str(MAX_MICRO_FILES) + " files) that can
skip full task planning and be handled as one micro task.

Only dispatch when:
- the change touches at most " + str(MAX_MICRO_FILES) + " files in one existing architecture module;
- the change is a small implementation or fix (not a new module, not an architecture change);
- the request is concrete enough to generate code from.

Return JSON only:
{"dispatch": true, "module_id": "...", "summary": "...",
 "target_files": ["src/example.py"], "verification": ["python -m unittest tests.example"]}
When you decide not to dispatch, return:
{"dispatch": false, "module_id": "", "summary": "reason", "target_files": [], "verification": []}
"""


def _append_micro_task(
    project_root: Path,
    module_id: str,
    summary: str,
    target_files: list[str],
    verification: list[str],
    ui_context: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    state = read_json(managed_path(project_root, "tasks.json"))
    if not isinstance(state, dict):
        state = {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [],
            "last_error": "",
        }
    tasks = state.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        state["tasks"] = tasks
    task_id = f"micro-{slugify(module_id)}-{int(time.time() * 1000)}"
    task = {
        "id": task_id,
        "module_id": module_id,
        "summary": summary,
        "target_files": target_files,
        "depends_on": [],
        "verification": verification,
        "priority": "high",
        "status": "pending",
        "patch": [],
        "thinking": "",
        "last_error": "",
        "ui_context": ui_context,
    }
    tasks.append(task)
    state["status"] = "confirmed"
    state["last_error"] = ""
    atomic_write_json(managed_path(project_root, "tasks.json"), state)
    return state, task


def _sandbox_patch_entries(
    project_root: Path,
    sandbox: dict[str, Any],
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for relative, content in _sandbox.sandbox_files(sandbox).items():
        target = project_root / relative
        try:
            before = target.read_text(encoding="utf-8")
        except OSError:
            before = ""
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile=f"a/{relative}",
                tofile=f"b/{relative}",
                n=3,
            )
        )
        if not diff:
            continue
        entries.append({
            "path": relative,
            "before": before,
            "after": content,
            "diff": diff,
        })
    return entries


def generate_micro_task_patch(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task: dict[str, Any],
    cancel_check: Any = None,
) -> dict[str, Any]:
    """Run the sandbox tool loop and stop at a reviewable diff."""
    agent_id = str(task.get("module_id") or task["id"])
    sandbox: dict[str, Any] | None = None
    try:
        sandbox = _sandbox.create_sandbox(project_root)
        task["execution_mode"] = "sandbox"
        task["sandbox_path"] = str(sandbox["path"])
        result = run_agent_tool_loop(
            project_root,
            provider,
            architecture,
            project,
            task,
            sandbox=sandbox,
            system_prompt=AGENT_TOOL_LOOP_PROMPT,
            call_model=call_model_json,
            use_native_tools=True,
            cancelled=cancel_check,
        )
        handoff = str(result.get("needs_handoff") or "").strip()
        if handoff and not (result.get("done") or result.get("files")):
            task["status"] = "blocked"
            task["last_error"] = f"已转接对话流：{handoff[:1000]}"
            return task
        if result.get("files") or result.get("patch"):
            files = normalize_generated_files(result, task["id"])
            validate_generated_files(files, task["id"])
            context = build_agent_context(project_root, agent_id, architecture, task)
            for entry in files:
                execute_tool(
                    project_root,
                    task,
                    {
                        "tool": "write_file",
                        "args": {
                            "path": entry["path"],
                            "content": entry["content"],
                        },
                    },
                    sandbox=sandbox,
                    agent_context=context,
                    architecture=architecture,
                )
        if not result.get("done") and not (result.get("files") or result.get("patch")):
            raise WorkspaceError("微任务模型没有返回完成信号或文件。")
        task["patch"] = _sandbox_patch_entries(project_root, sandbox)
        task["thinking"] = str(result.get("thinking") or "")
        task["status"] = "review"
        task["last_error"] = ""
    except AgentLoopCancelled:
        task["status"] = "pending"
        task["last_error"] = "已停止"
    finally:
        if sandbox:
            _sandbox.destroy_sandbox(sandbox)
    return task


def dispatch_micro_task(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    request: str,
    module_id: str | None = None,
    ui_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Judge whether the request is a micro task, auto-confirm, and generate a diff."""
    request = str(request or "").strip()
    if len(request) < 2:
        raise WorkspaceError("请描述要做的微任务。")
    modules = architecture.get("modules", [])
    if not isinstance(modules, list) or not modules:
        raise WorkspaceError("项目还没有可派发微任务的架构模块。")
    module_index = {
        str(module.get("id")): module
        for module in modules
        if isinstance(module, dict) and module.get("id")
    }
    model_input = {
        "project": {
            "name": project.get("name", ""),
            "slug": project.get("slug", ""),
        },
        "request": request,
        "architecture_modules": [
            {
                "id": module.get("id"),
                "name": module.get("name"),
                "responsibility": module.get("responsibility"),
                "path": module.get("path"),
            }
            for module in modules
            if isinstance(module, dict)
        ],
        "target_module_id": module_id or "",
    }
    with token_usage.usage_scope(project_root, feature="microtask"):
        result = call_model_json(
            provider,
            MICRO_TASK_JUDGE_PROMPT,
            model_input,
            timeout=180,
        )
    if result.get("dispatch") is not True:
        reason = str(result.get("summary") or "不是适合微任务的小改动。")[:500]
        raise WorkspaceError(f"主 Agent 判定不派发微任务：{reason}")

    selected_module_id = module_id or str(result.get("module_id") or "").strip()
    if selected_module_id not in module_index:
        raise WorkspaceError(f"主 Agent 选择了未知模块：{selected_module_id}")
    module_id = selected_module_id
    summary = normalized_text(result.get("summary"), "summary", True)
    raw_files = normalized_list(result.get("target_files"), "target_files")
    target_files: list[str] = []
    for raw in raw_files:
        path = str(raw).strip().replace("\\", "/")
        candidate = Path(path)
        if not path or candidate.is_absolute() or ".." in candidate.parts:
            raise WorkspaceError(f"微任务目标文件路径无效：{raw}")
        target_files.append(path)
    if not target_files or len(target_files) > MAX_MICRO_FILES:
        raise WorkspaceError(f"微任务目标文件必须是 1 到 {MAX_MICRO_FILES} 个。")
    verification = normalized_list(result.get("verification"), "verification")[:3]

    state, task = _append_micro_task(
        project_root,
        module_id,
        summary,
        target_files,
        verification,
        ui_context=ui_context,
    )
    handle = _task_jobs.begin_job(
        project_root,
        [task["id"]],
        kind="micro-task",
        transport="sync",
    )
    try:
        generate_micro_task_patch(
            project_root,
            provider,
            architecture,
            project,
            task,
            cancel_check=lambda: handle.cancelled(task["id"]),
        )
    finally:
        status = task.get("status")
        terminal = (
            "cancelled" if handle.cancel_requested else
            "failed" if status in {"failed", "blocked"} else
            "completed"
        )
        _task_jobs.finish_job(handle, terminal, str(task.get("last_error") or ""))
    for index, item in enumerate(state["tasks"]):
        if item.get("id") == task["id"]:
            state["tasks"][index] = task
            break
    atomic_write_json(managed_path(project_root, "tasks.json"), state)
    return {
        "task": task,
        "tasks": state,
    }
