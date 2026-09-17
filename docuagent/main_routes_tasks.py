"""Task, onboarding, orchestration, terminal, and UI-feedback route handlers.

Everything here is a route boundary around modules that own the real logic. None of
these handlers call the model-call seams that tests patch through `main`, so they can
live outside `main.py`; model-calling bootstrap/provider handlers stay in `main`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import agents as _agents
from architecture_preflight import require_architecture_preflight
import bootstrap as _bootstrap
import command_policy as _command_policy
import context_cache as _context_cache
import debug as _debug
import importscan as _importscan
import layout_delta as _layout_delta
import microtask as _microtask
import onboard as _onboard
import orchestrate as _orchestrate
import snapshots as _snapshots
import tasks as _tasks
import token_usage as _token_usage
import ui_layout as _ui_layout
import workspace as _workspace
from bootstrap import (
    apply_architecture_model_result,
    normalize_interview_mode,
    refresh_progress,
)
from core import SCHEMA_VERSION, WorkspaceError, slugify
from llm_client import ProviderConfig
from main_routes_bootstrap import _stale_after_edit, public_state
from workspace import (
    add_node_attachment,
    atomic_write_json,
    detect_mode,
    managed_path,
    prune_ui_state,
    read_contracts,
    read_json,
    read_node_attachments,
    resolve_project_path,
    utc_now,
    write_ui_state,
)

GLOBAL_CONFIG_PATH = Path.home() / ".docuagent" / "config.json"


def _saved_interview_mode() -> str:
    """Read the globally persisted interview mode, defaulting to guided."""
    try:
        raw = read_json(GLOBAL_CONFIG_PATH)
    except WorkspaceError:
        return "guided"
    if isinstance(raw, dict):
        return normalize_interview_mode(raw.get("interview_mode"))
    return "guided"


def task_context(payload: dict[str, Any]) -> tuple[Path, dict[str, Any], dict[str, Any]]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    document = _workspace.architecture_document(project_root)
    state = read_json(managed_path(project_root, "bootstrap.json"))
    if state and state.get("architecture", {}).get("modules"):
        return project_root, state["architecture"], state.get("project") or {}
    if document and document.get("modules"):
        return project_root, document, document.get("project") or {}
    raise WorkspaceError("这个项目还没有架构，无法规划任务。")


def plan_tasks(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("规划任务需要配置 AI 模型。")
    module_id = str(payload.get("module_id") or "").strip() or None
    result = _tasks.plan_tasks(project_root, provider, architecture, project, module_id)
    _snapshots.create_snapshot(project_root, f"规划任务：{module_id or '全部'}")
    return result


def confirm_tasks(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    result = _tasks.confirm_tasks(project_root)
    _snapshots.create_snapshot(project_root, "确认任务")
    return result


def set_task_priority_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    priority = str(payload.get("priority") or "").strip().lower()
    result = _tasks.set_task_priority(project_root, task_id, priority)
    _snapshots.create_snapshot(project_root, f"调整任务优先级：{task_id}")
    return result


def doc_ignore_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the project's documentation-ignore list, or set it when provided.

    One endpoint for both, the same way the apply-mode switch works: omit
    `ignored_dirs` to read, send it to replace the project-level opt-out list.
    The reply always carries `defaults` (built-in list) and `ignored_dirs`
    (the merged view actually in force), so the caller can tell which entries
    came from the project. Names must be single directory levels. The
    navigation-document tree stops creating AI_ARCH.md in ignored directories
    immediately.
    """
    import task_docs as _task_docs

    project_root = resolve_project_path(str(payload.get("path", "")))
    ignored_dirs = payload.get("ignored_dirs")
    if isinstance(ignored_dirs, list):
        _task_docs.write_document_ignored_dirs(
            project_root, [str(name) for name in ignored_dirs]
        )
    return {
        "defaults": sorted(_task_docs._DOCUMENT_TREE_IGNORED_DIRS),
        "ignored_dirs": sorted(_task_docs.read_document_ignored_dirs(project_root)),
    }


def apply_mode_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Read the project's apply mode, or set it when `mode` is provided.

    - review (default): a finished sandbox becomes a patch and the task stops
      at review; nothing reaches the project until a human applies it.
    - auto: a verified sandbox is applied to the project immediately.
    """
    project_root = resolve_project_path(str(payload.get("path", "")))
    mode = str(payload.get("mode") or "").strip().lower()
    if mode:
        _tasks.write_apply_mode(project_root, mode)
    return {
        "mode": _tasks.read_apply_mode(project_root),
        "modes": list(_tasks.APPLY_MODES),
    }


def generate_next_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload, role="subagent")
    if not provider:
        raise WorkspaceError("生成任务需要配置 AI 模型。")
    result = _tasks.generate_next_task(project_root, provider, architecture, project)
    _snapshots.create_snapshot(project_root, "生成任务")
    return result


def generate_wave(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload, role="subagent")
    if not provider:
        raise WorkspaceError("生成任务需要配置 AI 模型。")
    result = _tasks.generate_wave(project_root, provider, architecture, project)
    _snapshots.create_snapshot(project_root, "并行生成任务波次")
    return result


def stream_task_wave_events(payload: dict[str, Any]):
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload, role="subagent")
    if not provider:
        raise WorkspaceError("生成任务需要配置 AI 模型。")
    return _tasks.stream_generate_wave(project_root, provider, architecture, project)


def stream_task_plan_events(payload: dict[str, Any]):
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("规划任务需要配置 AI 模型。")
    module_id = str(payload.get("module_id") or "").strip() or None

    def emit():
        for event in _tasks.stream_plan_tasks(
            project_root, provider, architecture, project, module_id
        ):
            if event["type"] == "done":
                _snapshots.create_snapshot(
                    project_root, f"规划任务：{module_id or '全部'}"
                )
            yield event

    return emit()


def orchestrate_tasks(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("高级编排需要配置 AI 模型。")
    module_id = str(payload.get("module_id") or "").strip() or None
    instruction = str(payload.get("instruction") or "").strip()
    result = _orchestrate.orchestrate_tasks(
        project_root,
        provider,
        architecture,
        project,
        module_id=module_id,
        instruction=instruction,
    )
    _snapshots.create_snapshot(
        project_root, f"高级编排：{module_id or '全部'}"
    )
    return result


def stream_orchestrate_tasks_events(payload: dict[str, Any]):
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("高级编排需要配置 AI 模型。")
    module_id = str(payload.get("module_id") or "").strip() or None
    instruction = str(payload.get("instruction") or "").strip()

    def emit():
        for event in _orchestrate.stream_orchestrate_tasks(
            project_root,
            provider,
            architecture,
            project,
            module_id=module_id,
            instruction=instruction,
        ):
            if event["type"] == "done":
                _snapshots.create_snapshot(
                    project_root, f"高级编排：{module_id or '全部'}"
                )
            yield event

    return emit()


def cancel_tasks_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    raw = payload.get("task_ids")
    task_ids = None
    if isinstance(raw, list):
        task_ids = [str(task_id) for task_id in raw if str(task_id)]
    job_id = str(payload.get("job_id") or "").strip()
    return {
        "cancelled": _tasks.cancel_tasks(
            task_ids,
            project_root=project_root,
            job_id=job_id,
        )
    }


def stream_bootstrap_start_events(payload: dict[str, Any]):
    project_root = resolve_project_path(str(payload.get("path", "")))
    project_root.mkdir(parents=True, exist_ok=True)
    project_name = str(
        payload.get("name") or project_root.name or "Untitled Project"
    ).strip()
    description = str(payload.get("description") or "").strip()
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("需要配置 AI 模型才能初始化项目。")

    answers: dict[str, str] = {}
    if description:
        answers["goal"] = description
    state = {
        "schema_version": SCHEMA_VERSION,
        "status": "interviewing",
        "project": {
            "name": project_name,
            "slug": slugify(project_name),
            "root": str(project_root),
            "mode": detect_mode(project_root),
            # The architecture agent must see the real file system before
            # designing; otherwise it fabricates module files and verification
            # commands that have no counterpart in the project.
            "scan_summary": _importscan.architect_summary(
                _importscan.ensure_scan(project_root)
            ),
        },
        "answers": answers,
        "user_profile": {},
        "interview_mode": normalize_interview_mode(
            payload.get("interview_mode") or _saved_interview_mode()
        ),
        "architecture": {},
        "current_question": None,
        "progress": 0,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "model_notice": "",
        "agent_mode": "ai",
        "model_name": provider.model,
        "agent_turns": 0,
    }
    latest = {
        "question_id": "project-start",
        "answer": description
        or f"Start architecture design for {project_name}.",
    }
    for event in _bootstrap.stream_architecture_model(state, latest, provider):
        if event["type"] == "done":
            apply_architecture_model_result(state, event["result"], provider)
            refresh_progress(state)
            state["updated_at"] = utc_now()
            atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
            _snapshots.create_snapshot(project_root, "开始项目访谈")
            yield {"type": "done", "state": public_state(state) or {}}
        else:
            yield event


def stream_bootstrap_answer_events(payload: dict[str, Any]):
    project_root = resolve_project_path(str(payload.get("path", "")))
    state_path = managed_path(project_root, "bootstrap.json")
    state = read_json(state_path)
    if not state:
        raise WorkspaceError("项目尚未开始初始化。")
    if state.get("status") == "initialized":
        yield {"type": "done", "state": public_state(state) or {}}
        return
    if state.get("status") in {"review", "ready"}:
        raise WorkspaceError("架构正在等待确认；修改请走架构修订接口。")
    current = state.get("current_question")
    if not isinstance(current, dict):
        raise WorkspaceError("当前没有待回答的问题。")
    answer = str(payload.get("answer") or "").strip()
    if len(answer) < 2:
        raise WorkspaceError("请提供更完整的回答。")
    question_id = str(current.get("id") or f"turn-{len(state.get('answers', {})) + 1}")
    state.setdefault("answers", {})[question_id] = answer
    latest = {"question_id": question_id, "answer": answer}
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("需要配置 AI 模型才能进行架构设计。")

    for event in _bootstrap.stream_architecture_model(state, latest, provider):
        if event["type"] == "done":
            apply_architecture_model_result(state, event["result"], provider)
            refresh_progress(state)
            state["updated_at"] = utc_now()
            atomic_write_json(state_path, state)
            _snapshots.create_snapshot(project_root, f"回答：{question_id}")
            yield {"type": "done", "state": public_state(state) or {}}
        else:
            yield event


def scan_import_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Run the read-only M1 scan and persist it, returning the reduced summary."""
    _onboard.require_available()
    project_root = resolve_project_path(str(payload.get("path", "")))
    scan = _importscan.scan_project(project_root)
    _importscan.write_scan(project_root, scan)
    return _importscan.scan_summary(scan)


def start_onboard_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Non-streaming onboarding entry (tests + fallback)."""
    _onboard.require_available()
    project_root = resolve_project_path(str(payload.get("path", "")))
    project_root.mkdir(parents=True, exist_ok=True)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("接入分析需要配置 AI 模型。")
    project_name = str(
        payload.get("name") or project_root.name or "Untitled Project"
    ).strip()
    description = str(payload.get("description") or "").strip()

    scan = _importscan.read_scan(project_root) or _importscan.scan_project(project_root)
    _importscan.write_scan(project_root, scan)
    project = {
        "name": project_name,
        "slug": slugify(project_name),
        "root": str(project_root),
        "mode": "imported",
    }
    model_input = _onboard.build_onboard_model_input(project_root, scan, project, description)
    try:
        result = _onboard.call_onboard(provider, model_input)
    except WorkspaceError as exc:
        raise WorkspaceError(f"接入分析失败：{exc}") from exc
    state = _onboard.build_onboard_state(
        project_root, project_name, provider, description, result
    )
    atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
    _snapshots.create_snapshot(project_root, "项目接入分析")
    return public_state(state) or {}


def stream_onboard_events(payload: dict[str, Any]):
    _onboard.require_available()
    project_root = resolve_project_path(str(payload.get("path", "")))
    project_root.mkdir(parents=True, exist_ok=True)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("接入分析需要配置 AI 模型。")
    project_name = str(
        payload.get("name") or project_root.name or "Untitled Project"
    ).strip()
    description = str(payload.get("description") or "").strip()

    scan = _importscan.read_scan(project_root) or _importscan.scan_project(project_root)
    _importscan.write_scan(project_root, scan)
    project = {
        "name": project_name,
        "slug": slugify(project_name),
        "root": str(project_root),
        "mode": "imported",
    }

    # Adaptive path: small projects get the deterministic single-shot turn; large ones
    # get a read-only tool loop so the agent explores on demand instead of losing the
    # inventory tail to truncation. Both emit the same event shapes, ending in `done`.
    if _onboard.onboard_requires_loop(scan):
        events = _onboard.stream_onboard_loop(
            project_root, provider, project, description, scan
        )
    else:
        model_input = _onboard.build_onboard_model_input(
            project_root, scan, project, description
        )
        events = _onboard.stream_onboard(provider, model_input)

    for event in events:
        if event["type"] == "done":
            result = event["result"]
            state = _onboard.build_onboard_state(
                project_root, project_name, provider, description, result
            )
            atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
            _snapshots.create_snapshot(project_root, "项目接入分析")
            yield {"type": "done", "state": public_state(state) or {}}
        else:
            yield event


def accept_suggestion_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    attachment_id = str(payload.get("attachment_id") or "").strip()
    if not module_id or not attachment_id:
        raise WorkspaceError("缺少模块或建议 ID。")
    result = _onboard.accept_suggestion(project_root, module_id, attachment_id)
    _snapshots.create_snapshot(project_root, f"采纳建议：{module_id}")
    return result


def reject_suggestion_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    attachment_id = str(payload.get("attachment_id") or "").strip()
    if not module_id or not attachment_id:
        raise WorkspaceError("缺少模块或建议 ID。")
    result = _onboard.reject_suggestion(project_root, module_id, attachment_id)
    _snapshots.create_snapshot(project_root, f"拒绝建议：{module_id}")
    return result


def stream_architecture_edit_events(payload: dict[str, Any]):
    project_root = resolve_project_path(str(payload.get("path", "")))
    request = str(payload.get("request") or "").strip()
    if len(request) < 2:
        raise WorkspaceError("请描述需要修改的地方。")
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("修改架构需要配置 AI 模型。")
    document = _workspace.architecture_document(project_root)
    state = read_json(managed_path(project_root, "bootstrap.json"))
    if state and state.get("architecture", {}).get("modules"):
        current = state["architecture"]
    elif document and document.get("modules"):
        current = document
    else:
        raise WorkspaceError("这个项目还没有架构可以修改。")
    project = (document or {}).get("project") or (state or {}).get("project") or {}

    for event in _bootstrap.stream_architecture_edit_model(
        current, request, project, provider
    ):
        if event["type"] == "done":
            result = event["result"]
            architecture = result["architecture"]
            require_architecture_preflight(project_root, architecture)
            _workspace.write_stale_modules(
                project_root,
                _stale_after_edit(current, architecture),
            )
            previous_version = int((document or {}).get("architecture_version") or 0)
            previous_doc = document
            if not previous_doc and state and state.get("architecture", {}).get("modules"):
                previous_doc = {
                    "schema_version": SCHEMA_VERSION,
                    "architecture_version": 0,
                    "generated_at": state.get("updated_at") or utc_now(),
                    "project": project,
                    **state["architecture"],
                }
            architecture_doc = {
                "schema_version": SCHEMA_VERSION,
                "architecture_version": previous_version + 1,
                "generated_at": utc_now(),
                "project": project,
                **architecture,
            }
            if previous_doc:
                _workspace.push_architecture_history(project_root, previous_doc)
            atomic_write_json(
                managed_path(project_root, "architecture.json"), architecture_doc
            )
            if state:
                state["architecture"] = architecture
                state["architecture_version"] = architecture_doc["architecture_version"]
                state["thinking"] = result["thinking"]
                state["updated_at"] = utc_now()
                atomic_write_json(
                    managed_path(project_root, "bootstrap.json"), state
                )
            _tasks.sync_work_items_from_architecture(
                project_root, architecture, project
            )
            _tasks.mark_architecture_docs_dirty(project_root, architecture)
            module_ids = {module["id"] for module in architecture.get("modules", [])}
            write_ui_state(project_root, prune_ui_state(project_root, module_ids))
            _snapshots.create_snapshot(project_root, "修改架构")
            yield {
                "type": "done",
                "result": {
                    "architecture": architecture,
                    "architecture_version": architecture_doc["architecture_version"],
                    "thinking": result["thinking"],
                    "changes": result["changes"],
                    "history_remaining": len(
                        _workspace.read_architecture_history(project_root)
                    ),
                    "work_state": _tasks.read_task_state(project_root),
                },
            }
        else:
            yield event


def _deliver_upstream_change_notice(project_root: Path, task_id: str) -> None:
    """Task-boundary delivery of contract-change impact. Never mid-run.

    Route-boundary wrapper: resolve the module for `task_id`, then hand over to
    the shared core in `task_generate` (the auto-apply path calls the same core
    directly after applying). Failures degrade silently — a notice must never
    fail the task that just finished.
    """
    try:
        state = _tasks.read_task_state(project_root)
        task = next(
            (
                item for item in (state or {}).get("tasks", [])
                if isinstance(item, dict) and str(item.get("id") or "") == task_id
            ),
            None,
        )
        module_id = str((task or {}).get("module_id") or "").strip()
        if not module_id:
            return
        _tasks._deliver_upstream_change_notice_for_module(project_root, module_id)
    except Exception:
        return


def apply_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    result = _tasks.apply_task_patch(project_root, task_id)
    _deliver_upstream_change_notice(project_root, task_id)
    _snapshots.create_snapshot(project_root, f"应用任务：{task_id}")
    return result


def apply_task_partial(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    file_decisions = payload.get("file_decisions")
    if not isinstance(file_decisions, dict):
        raise WorkspaceError("缺少 file_decisions 参数。")
    result = _tasks.apply_task_partial(project_root, task_id, file_decisions)
    _deliver_upstream_change_notice(project_root, task_id)
    _snapshots.create_snapshot(project_root, f"部分应用任务：{task_id}")
    return result


def edit_task_patch_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    file_path = str(payload.get("file") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少 task_id。")
    if not file_path:
        raise WorkspaceError("缺少 file。")
    content = str(payload.get("content") or "")
    base_after_raw = payload.get("base_after")
    base_after = None if base_after_raw is None else str(base_after_raw)
    result = _tasks.edit_task_patch(
        project_root, task_id, file_path, content, base_after=base_after
    )
    _snapshots.create_snapshot(project_root, f"编辑补丁：{task_id}/{file_path}")
    return result


def _validate_command(command: str) -> list[str]:
    return _command_policy.validate_command(command, context="终端")


def _resolve_cwd(project_root: Path, raw_cwd: str) -> Path:
    raw_cwd = raw_cwd.strip()
    cwd = project_root
    if raw_cwd:
        candidate = (project_root / raw_cwd).resolve()
        try:
            candidate.relative_to(project_root.resolve())
        except ValueError as exc:
            raise WorkspaceError("工作目录必须在项目内。") from exc
        if not candidate.is_dir():
            raise WorkspaceError("工作目录不存在。")
        cwd = candidate
    return cwd


def terminal_exec(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    command = str(payload.get("command") or "").strip()
    parts = _validate_command(command)
    cwd = _resolve_cwd(project_root, str(payload.get("cwd") or ""))

    try:
        result = subprocess.run(
            parts,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "command": command,
            "stdout": "",
            "stderr": f"命令执行失败：{exc}",
            "returncode": -1,
        }
    limit = 50_000
    return {
        "command": command,
        "stdout": result.stdout[-limit:],
        "stderr": result.stderr[-limit:],
        "returncode": result.returncode,
    }


def token_usage_route(payload: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(payload.get("path") or "").strip()
    project_root = resolve_project_path(raw_path) if raw_path else None
    summary = _token_usage.usage_summary(project_root)
    if project_root is not None:
        names = _module_names(project_root)
        for item in summary.get("modules", []):
            module_id = str(item.get("module_id") or "")
            item["name"] = names.get(module_id, module_id)
    return summary


def context_cache_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    summary = _context_cache.cache_summary(project_root)
    names = _module_names(project_root)
    for item in summary.get("modules", []):
        module_id = str(item.get("module_id") or "")
        item["name"] = names.get(module_id, module_id)
    return summary


def _module_names(project_root: Path) -> dict[str, str]:
    document = _workspace.architecture_document(project_root) or {}
    return {
        str(module.get("id")): str(module.get("name") or module.get("id"))
        for module in document.get("modules", [])
        if isinstance(module, dict) and module.get("id")
    }


def debug_file_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    file_path = str(payload.get("file") or "").strip()
    if not file_path:
        raise WorkspaceError("缺少 file。")
    line = payload.get("line")
    return _debug.debug_file(project_root, file_path, int(line) if line else 0)


def diagnose_file_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    file_path = str(payload.get("file") or "").strip()
    if not file_path:
        raise WorkspaceError("缺少 file。")
    return _tasks.diagnose_file(project_root, file_path)


def get_task_hunks_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    file_path = str(payload.get("file") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少 task_id。")
    if not file_path:
        raise WorkspaceError("缺少 file。")
    return {"hunks": _tasks.task_hunks(project_root, task_id, file_path)}


def apply_task_hunks_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    file_path = str(payload.get("file") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少 task_id。")
    if not file_path:
        raise WorkspaceError("缺少 file。")
    hunk_ids = payload.get("hunk_ids")
    if not isinstance(hunk_ids, list):
        raise WorkspaceError("缺少 hunk_ids。")
    result = _tasks.apply_task_hunks(
        project_root, task_id, file_path, [int(item) for item in hunk_ids]
    )
    _snapshots.create_snapshot(project_root, f"部分应用 hunks：{task_id}/{file_path}")
    return result


def latest_triage_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path") or ""))
    errors = payload.get("errors")
    if not isinstance(errors, list):
        raise WorkspaceError("缺少 errors 列表。")
    return {"result": _orchestrate.latest_triage(project_root, errors)}


def triage_errors_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("错误汇总需要配置 AI 模型。")
    errors = payload.get("errors")
    if not isinstance(errors, list):
        raise WorkspaceError("缺少 errors 列表。")
    return _orchestrate.triage_errors(project_root, provider, errors)


def reject_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    result = _tasks.reject_task(project_root, task_id)
    _snapshots.create_snapshot(project_root, f"拒绝任务：{task_id}")
    return result


def verify_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    handle = _tasks.begin_job(
        project_root,
        [task_id],
        kind="verify",
        transport="sync",
    )
    try:
        result = _tasks.verify_task(
            project_root,
            task_id,
            cancel_check=lambda: handle.cancelled(task_id),
        )
    except BaseException:
        _tasks.finish_job(handle, "failed")
        raise
    task = next(item for item in result["tasks"] if item["id"] == task_id)
    status = task.get("status")
    terminal = (
        "cancelled" if handle.cancel_requested else
        "failed" if status == "failed" else
        "completed"
    )
    _tasks.finish_job(handle, terminal, str(task.get("last_error") or ""))
    _deliver_upstream_change_notice(project_root, task_id)
    _snapshots.create_snapshot(project_root, f"验证任务：{task_id}")
    return result


def stream_verify_task_events(payload: dict[str, Any]):
    project_root = resolve_project_path(str(payload.get("path", "")))
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    handle = _tasks.begin_job(
        project_root,
        [task_id],
        kind="verify",
        transport="stream",
    )

    def emit():
        final_status = "completed"
        try:
            for event in _tasks.stream_verify_task(
                project_root,
                task_id,
                cancel_check=lambda: handle.cancelled(task_id),
            ):
                if event.get("type") == "done":
                    task_state = (event.get("state") or {}).get("tasks", [])
                    task = next(
                        (item for item in task_state if item.get("id") == task_id),
                        {},
                    )
                    status = task.get("status")
                    final_status = (
                        "cancelled" if handle.cancel_requested else
                        "failed" if status == "failed" else
                        "completed"
                    )
                yield event
        except BaseException:
            _tasks.finish_job(handle, "failed")
            raise
        finally:
            if handle.status == "running":
                _tasks.finish_job(handle, final_status)

    return emit()


def retry_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload, role="subagent")
    if not provider:
        raise WorkspaceError("重新生成需要配置 AI 模型。")
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    result = _tasks.retry_task(
        project_root, provider, architecture, project, task_id
    )
    _snapshots.create_snapshot(project_root, f"对话流裁决重试：{task_id}")
    return result


def repair_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload, role="subagent")
    if not provider:
        raise WorkspaceError("自动修复需要配置 AI 模型。")
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    result = _tasks.repair_task(
        project_root, provider, architecture, project, task_id
    )
    _snapshots.create_snapshot(project_root, f"验证修复：{task_id}")
    return result


def resume_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload, role="subagent")
    if not provider:
        raise WorkspaceError("断点恢复需要配置 AI 模型。")
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    result = _tasks.resume_task(
        project_root, provider, architecture, project, task_id
    )
    _snapshots.create_snapshot(project_root, f"断点恢复：{task_id}")
    return result


def dispatch_micro_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("微任务需要配置 AI 模型。")
    request = str(payload.get("request") or "").strip()
    if len(request) < 2:
        raise WorkspaceError("请描述要做的微任务。")
    result = _microtask.dispatch_micro_task(
        project_root,
        provider,
        architecture,
        project,
        request,
    )
    _snapshots.create_snapshot(project_root, f"派发微任务：{result['task']['id']}")
    return result


def apply_micro_task(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("应用微任务需要配置 AI 模型。")
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise WorkspaceError("缺少任务 ID。")
    state = _tasks.read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = next(
        (item for item in state.get("tasks", []) if item.get("id") == task_id),
        None,
    )
    if not task:
        raise WorkspaceError(f"找不到微任务：{task_id}")

    granted = _agents.acquire_workspace_locks(
        project_root,
        [(task_id, task.get("target_files", []))],
    )
    if task_id not in granted:
        raise WorkspaceError("微任务目标文件被其他任务占用。")
    try:
        applied = _tasks.apply_task_patch(project_root, task_id)
    finally:
        _agents.release_workspace_locks(project_root, [task_id])
    _snapshots.create_snapshot(project_root, f"应用微任务：{task_id}")

    docs = list(
        dict.fromkeys(
            path
            for item in applied.get("tasks", [])
            for path in (item.get("documentation_updated") or [])
        )
    )
    if all(
        item.get("status") in {"applied", "verified", "rejected"}
        for item in applied.get("tasks", [])
    ):
        docs = _tasks.sync_docs(project_root, provider, architecture, project)
    return {
        "tasks": applied,
        "docs": docs,
    }


def sync_docs(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("文档同步需要配置 AI 模型。")
    state = _tasks.read_task_state(project_root) or {}
    documentation = state.get("documentation_task") or {}
    task_ids = [
        str(item)
        for item in (documentation.get("code_task_ids") or [])
        if str(item).strip()
    ]
    if not task_ids and documentation.get("code_task_id"):
        task_ids = [str(documentation["code_task_id"])]
    handle = _tasks.begin_job(
        project_root,
        task_ids or ["documentation"],
        kind="sync-docs",
        transport="sync",
    )
    result: dict[str, Any] | None = None
    try:
        result = _tasks.sync_docs(
            project_root,
            provider,
            architecture,
            project,
            cancel_check=lambda: handle.cancelled(),
        )
    finally:
        doc_status = ((result or {}).get("documentation_task") or {}).get("status")
        terminal = (
            "cancelled" if handle.cancel_requested else
            "failed" if doc_status == "blocked" else
            "completed"
        )
        _tasks.finish_job(handle, terminal, str((result or {}).get("last_error") or ""))
    assert result is not None
    _snapshots.create_snapshot(project_root, "同步文档")
    return result


def retry_documentation(payload: dict[str, Any]) -> dict[str, Any]:
    project_root, architecture, project = task_context(payload)
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("重试文档需要配置 AI 模型。")
    state = _tasks.read_task_state(project_root) or {}
    documentation = state.get("documentation_task") or {}
    task_ids = [
        str(item)
        for item in (documentation.get("code_task_ids") or [])
        if str(item).strip()
    ]
    handle = _tasks.begin_job(
        project_root,
        task_ids or ["documentation"],
        kind="retry-documentation",
        transport="sync",
    )
    result: dict[str, Any] | None = None
    try:
        result = _tasks.retry_documentation(
            project_root,
            provider,
            architecture,
            project,
            cancel_check=lambda: handle.cancelled(),
        )
    finally:
        doc_status = ((result or {}).get("documentation_task") or {}).get("status")
        terminal = (
            "cancelled" if handle.cancel_requested else
            "failed" if doc_status == "blocked" else
            "completed"
        )
        _tasks.finish_job(handle, terminal, str((result or {}).get("last_error") or ""))
    assert result is not None
    _snapshots.create_snapshot(project_root, "重试文档维护")
    return result
