"""Task generation, sandbox execution, and wave scheduling."""
from __future__ import annotations

import difflib
import io
import queue
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from agent_tools import AgentLoopCancelled, execute_tool, stream_agent_tool_loop, restore_checkpoint_sandbox
from agents import (acquire_workspace_locks, append_error_memory, append_work_log, build_agent_context, clear_locks, register_agent, release_workspace_locks, update_agent_status)
import contract_lint
import sandbox as _sandbox
import snapshots as _snapshots
import task_runtime as _task_runtime
import task_jobs as _task_jobs
import telemetry
from core import WorkspaceError
from llm_client import ProviderConfig
from standards import IMPLEMENTATION_SAFETY_RULES
from task_diff import diff_hunks
from task_plan import sync_work_items_from_architecture
from task_state import (
    mark_docs_dirty,
    mark_stale_tasks,
    normalize_generated_files,
    read_apply_mode,
    register_completed_documentation,
    write_task_state,
)
from workspace import add_node_attachment, read_contracts, read_node_attachments


def call_model_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _task_runtime.call_model_json(*args, **kwargs)


def run_agent_tool_loop(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _task_runtime.run_agent_tool_loop(*args, **kwargs)


REPAIR_PROMPT = """You are the Repair Agent for a task whose patch was applied but failed verification.
You receive the same bounded context as the Implementation Agent, plus the verification output
and the currently applied file contents. Fix the files so every verification command passes.

You may explain your diagnosis in natural language. To change files, call `write_file` or
`edit_file` exactly like the Implementation Agent; the writes happen inside the same trial
sandbox and reach the project through the system's apply flow. Call a write tool for every
file that must
change. Never claim a fix is complete without writing corrected files. If a file is already
correct, leave it untouched. When you have written all corrections, finish with a short
natural-language summary.

If the repair is outside this module or requires an architecture change, do not edit files;
say clearly that it needs a handoff, or use the `needs_handoff` signal.
""" + "\n\n" + IMPLEMENTATION_SAFETY_RULES

def next_generatable_task(state: dict[str, Any]) -> dict[str, Any] | None:
    applied = {
        task["id"]
        for task in state["tasks"]
        if task["status"] in {"applied", "verified"}
    }
    for task in state["tasks"]:
        if task["status"] != "pending":
            continue
        if all(dep in applied for dep in task["depends_on"]):
            return task
    return None


def ready_tasks(state: dict[str, Any]) -> list[dict[str, Any]]:
    """All pending tasks whose dependencies are already applied/verified."""
    applied = {
        task["id"]
        for task in state["tasks"]
        if task["status"] in {"applied", "verified"}
    }
    return [
        task
        for task in state["tasks"]
        if task["status"] == "pending"
        and all(dep in applied for dep in task["depends_on"])
    ]


def _task_model_input(
    project: dict[str, Any],
    task: dict[str, Any],
    agent_context: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project": {
            "name": project.get("name", ""),
            "slug": project.get("slug", ""),
            "root": project.get("root", ""),
        },
        "agent_context": agent_context,
        "task": {
            "id": task["id"],
            "module_id": task.get("module_id", ""),
            "summary": task["summary"],
            "target_files": task["target_files"],
            "verification": task["verification"],
        },
    }


def _record_generation_success(
    project_root: Path,
    agent_id: str,
    task: dict[str, Any],
    files: list[dict[str, str]],
) -> None:
    append_work_log(
        project_root,
        agent_id,
        "生成完成",
        task["id"],
        "文件："
        + "、".join(file["path"] for file in files)
        + "\n\n验证命令："
        + "；".join(task["verification"] or [])
        + "\n\n思考尾部："
        + (task["thinking"] or "")[-300:],
    )


def _record_generation_failure(
    project_root: Path,
    agent_id: str,
    task: dict[str, Any],
    error: str,
) -> None:
    append_error_memory(
        project_root,
        agent_id,
        "generation",
        error,
        task_id=task["id"],
        related_files=task.get("target_files", []),
    )
    append_work_log(
        project_root,
        agent_id,
        "生成失败",
        task["id"],
        f"错误：{error}",
    )


def _record_verification_failure(
    project_root: Path,
    agent_id: str,
    task_id: str,
    task: dict[str, Any],
    error: str,
) -> None:
    append_error_memory(
        project_root,
        agent_id,
        "verification",
        error,
        task_id=task_id,
        related_files=task.get("target_files", []),
    )
    append_work_log(
        project_root,
        agent_id,
        "验证失败",
        task_id,
        error,
    )
    telemetry.record_verification_failure()


class TaskCancelled(Exception):
    """Raised inside a worker when its task was cancelled by the user."""


def _register_wave(
    task_ids: list[str],
    project_root: Path | None = None,
    *,
    kind: str = "generate-wave",
    transport: str = "stream",
):
    """Compatibility wrapper around the project-scoped Job registry."""
    return _task_jobs.begin_job(
        project_root,
        task_ids,
        kind=kind,
        transport=transport,
    )


def cancel_tasks(
    task_ids: list[str] | None = None,
    project_root: Path | None = None,
    job_id: str = "",
) -> int:
    """Cancel matching live jobs; legacy callers may omit the project path."""
    return _task_jobs.cancel_jobs(
        task_ids,
        project_root=project_root,
        job_id=job_id,
    )


def _task_cancelled(task_id: str, project_root: Path | None = None) -> bool:
    if project_root is None:
        return _task_jobs.any_task_cancelled(task_id)
    return (
        _task_jobs.task_cancelled(project_root, task_id)
        or _task_jobs.any_task_cancelled(task_id)
    )


def recover_stale_running(
    state: dict[str, Any],
    project_root: Path | None = None,
) -> int:
    """Reset tasks stuck in `running` from an interrupted wave back to `pending`.

    A single-user local session runs one wave at a time (the frontend blocks on the
    stream), so any `running` task seen at the start of a new wave is a zombie left by
    a run that was cut short — browser closed, server restarted — before its final
    state could be written. Without this reset the task is unreachable: `ready_tasks`
    only selects `pending`, so it would never generate again and still blocks
    re-planning. Returns how many tasks were recovered.
    """
    if project_root is not None:
        _task_jobs.reconcile_jobs(project_root)
    live_task_ids = (
        _task_jobs.active_task_ids(project_root)
        if project_root is not None
        else set()
    )
    recovered = 0
    for task in state.get("tasks", []):
        if (
            isinstance(task, dict)
            and task.get("status") == "running"
            and task.get("id") not in live_task_ids
        ):
            task["status"] = "pending"
            task["last_error"] = "上次生成被中断，已重置为待生成。"
            recovered += 1
    return recovered




_PYFLAKES_ISSUE_MARKERS = (
    "undefined name",
    "imported but unused",
    "is assigned to but never used",
    "referenced before assignment",
)


def _pyflakes_issues(entry: dict[str, str]) -> list[str]:
    try:
        from pyflakes.api import check
        from pyflakes.reporter import Reporter
    except ImportError:
        return []
    output = io.StringIO()
    reporter = Reporter(output, output)
    try:
        check(entry["content"], entry["path"], reporter)
    except Exception:
        return []
    return [
        line.strip()
        for line in output.getvalue().splitlines()
        if any(marker in line for marker in _PYFLAKES_ISSUE_MARKERS)
    ]


def validate_generated_files(
    files: list[dict[str, str]],
    task_id: str,
) -> None:
    """Reject generated Python that cannot compile or contains obvious dead names."""
    for entry in files:
        if not entry["path"].endswith(".py"):
            continue
        try:
            compile(entry["content"], entry["path"], "exec")
        except SyntaxError as exc:
            line = exc.lineno or 0
            raise WorkspaceError(
                f"任务 `{task_id}` 生成的 Python 文件语法错误："
                f"{entry['path']}: {exc.msg}（第 {line} 行）"
            ) from exc
        issues = _pyflakes_issues(entry)
        if issues:
            raise WorkspaceError(
                f"任务 `{task_id}` 生成的 Python 文件存在静态问题：\n"
                + "\n".join(issues[:20])
            )


def make_patch_entries(
    project_root: Path, files: list[dict[str, str]]
) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for file in files:
        target = (project_root / file["path"]).resolve()
        before = (
            target.read_text(encoding="utf-8") if target.exists() else ""
        )
        diff = "".join(
            difflib.unified_diff(
                before.splitlines(keepends=True),
                file["content"].splitlines(keepends=True),
                fromfile=f"a/{file['path']}",
                tofile=f"b/{file['path']}",
                n=3,
            )
        )
        entries.append({
            "path": file["path"],
            "before": before,
            "after": file["content"],
            "diff": diff,
            "hunks": diff_hunks(before, file["content"]),
        })
    return entries


def generate_next_task(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
) -> dict[str, Any]:
    """Generate one ready task through the shared execution lifecycle."""
    return _drain_generation_events(
        _generate_wave_events(
            project_root,
            provider,
            architecture,
            project,
            max_workers=1,
            one_task=True,
            kind="generate-next",
            transport="sync",
        )
    )


def _deliver_upstream_change_notice_for_module(
    project_root: Path, module_id: str
) -> None:
    """Task-boundary delivery of contract-change impact. Never mid-run.

    A running agent's context is frozen at start, so interrupting it is off the
    table: the notice lands at the task's completion boundary as an unresolved
    attachment. The user sees it as a note badge on the module node, and the
    module's next agent run picks it up automatically through
    `unresolved_attachments`. Failures degrade silently — a notice must never
    fail the task that just finished. Deduplicated by exact text so repeated
    boundaries do not pile up identical notes.
    """
    try:
        from contract_registry import upstream_changes_for_module

        changes = upstream_changes_for_module(
            read_contracts(project_root) or {}, module_id
        )
        if not changes:
            return
        changed_names = "、".join(
            str(item.get("name") or item.get("id") or "")
            for item in changes.get("changed", [])[:3]
        )
        depth = int(changes.get("depth") or 0)
        hop = "直接依赖" if depth <= 1 else f"{depth} 跳依赖"
        text = (
            f"上游接口已变更（{hop}）：{changed_names}。"
            "继续开发前先读最新契约，必要时重新适配本模块的调用方式。"
        )
        existing = read_node_attachments(project_root).get(module_id, [])
        if any(
            isinstance(item, dict)
            and not item.get("resolved")
            and item.get("text") == text
            for item in existing
        ):
            return
        add_node_attachment(project_root, module_id, "note", text)
    except Exception:
        return


def _settle_review_patch(
    project_root: Path,
    task: dict[str, Any],
    sandbox: dict[str, Any],
) -> None:
    """Review mode: turn the sandbox changes into a patch and stop at review.

    Nothing reaches the project here. The patch carries the same changed-file set
    that auto mode would have applied, so the only difference between the modes
    is who presses the button.
    """
    files = [
        {"path": relative, "content": content}
        for relative, content in sorted(
            _sandbox.changed_sandbox_files(sandbox).items()
        )
    ]
    task["patch"] = make_patch_entries(project_root, files)
    task["status"] = "review"
    task["last_error"] = ""
    lint = contract_lint.check_patch(project_root, task["patch"])
    task["contract_lint_violations"] = lint["violations"]
    task["contract_delta"] = lint["contract_delta"]
    task["contract_lint_skipped"] = lint["skipped"]
    append_work_log(
        project_root,
        str(task.get("module_id") or task["id"]),
        "沙箱转补丁",
        task["id"],
        "审阅模式下沙箱改动转为补丁，等待人工应用。",
    )


def _apply_sandbox_to_main(
    project_root: Path,
    task: dict[str, Any],
    sandbox: dict[str, Any],
) -> bool:
    """Apply sandbox files to the main project and run main verification.

    Auto mode lands changes without a human boundary, so the contract gate must
    run here just like it does on the manual apply route: a violation never
    reaches the project. When the sandbox changes would violate the registry,
    degrade to the same review patch a review-mode task would produce and stop,
    leaving the decision to a human.
    """
    agent_id = str(task.get("module_id") or task["id"])
    _snapshots.create_snapshot(project_root, f"沙箱应用：{task['id']}")

    files = [
        {"path": relative, "content": content}
        for relative, content in sorted(
            _sandbox.changed_sandbox_files(sandbox).items()
        )
    ]
    if files:
        patch = make_patch_entries(project_root, files)
        lint = contract_lint.check_patch(project_root, patch)
        if lint["violations"]:
            task["patch"] = patch
            task["status"] = "review"
            task["last_error"] = ""
            task["contract_lint_violations"] = lint["violations"]
            task["contract_delta"] = lint["contract_delta"]
            task["contract_lint_skipped"] = lint["skipped"]
            append_work_log(
                project_root,
                agent_id,
                "契约门拦截",
                task["id"],
                "自动应用未通过契约检查，已转为补丁等待人工处理："
                + "；".join(lint["violations"][:5]),
            )
            return True

    applied = _sandbox.apply_sandbox(project_root, sandbox)
    task["applied_files"] = applied
    task["status"] = "applied"
    task["code_changed_files"] = list(applied)
    task["documentation_updated"] = []
    append_work_log(
        project_root,
        agent_id,
        "沙箱应用",
        task["id"],
        "已应用文件：" + "、".join(applied) if applied else "无文件变更",
    )
    task["status"] = "verifying"
    verification = _sandbox.run_verification(project_root, task)
    task["verification_output"] = verification
    if verification["passed"]:
        task["status"] = "verified"
        task["last_error"] = ""
        append_work_log(
            project_root,
            agent_id,
            "验证通过",
            task["id"],
            "主工作区验证通过。",
        )
        # Auto mode applies without a human boundary, so the contract-change
        # notice rides on the apply itself; the API apply routes deliver their
        # own after `apply_task_patch`.
        module_id = str(task.get("module_id") or "").strip()
        if module_id:
            _deliver_upstream_change_notice_for_module(project_root, module_id)
        return True
    task["status"] = "failed"
    task["last_error"] = (
        f"主工作区验证失败：{verification.get('command')}\n"
        f"{verification.get('stdout')}\n{verification.get('stderr')}"
    ).strip()
    append_error_memory(
        project_root,
        agent_id,
        "verification",
        task["last_error"],
        task_id=task["id"],
        related_files=task.get("target_files", []),
    )
    return False


def _repair_sandbox_once(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task: dict[str, Any],
    sandbox: dict[str, Any],
    error: dict[str, Any] | str,
) -> tuple[str, dict[str, Any]]:
    """Run one Repair Agent round inside the existing sandbox."""
    agent_id = str(task.get("module_id") or task["id"])
    context = build_agent_context(project_root, agent_id, architecture, task)
    result = run_agent_tool_loop(
        project_root,
        provider,
        architecture,
        project,
        task,
        sandbox=sandbox,
        system_prompt=REPAIR_PROMPT,
        extra_context={
            "verification_output": error,
            "current_files": _sandbox.sandbox_files(sandbox),
        },
        call_model=call_model_json,
        use_native_tools=True,
    )
    handoff = str(result.get("needs_handoff") or "").strip()
    if handoff and not (result.get("done") or result.get("files")):
        return "blocked", result
    if result.get("files"):
        for entry in result["files"]:
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
    return "ok", result


def _complete_sandbox_task(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task: dict[str, Any],
    sandbox: dict[str, Any],
    result: dict[str, Any],
) -> None:
    """Run sandbox verification, apply, and the B -> A fallback loop."""
    agent_id = str(task.get("module_id") or task["id"])
    apply_mode = read_apply_mode(project_root)
    direct_attempts = int(task.get("direct_attempts") or 0)
    sandbox_verification = _sandbox.run_verification(sandbox["path"], task)
    task["sandbox_verification"] = sandbox_verification

    if sandbox_verification["passed"]:
        if apply_mode == "review":
            _settle_review_patch(project_root, task, sandbox)
            return
        if _apply_sandbox_to_main(project_root, task, sandbox):
            return
        error = task["last_error"]
    elif result.get("sandbox_environment_issue") is True:
        if apply_mode == "review":
            # The direct-write fallback bypasses the sandbox entirely, so it
            # must not run in review mode; the patch goes to a human instead.
            _settle_review_patch(project_root, task, sandbox)
            return
        if direct_attempts >= 2:
            task["status"] = "failed"
            task["last_error"] = "沙箱验证失败且已达到直接写入上限。"
            return
        _snapshots.create_snapshot(project_root, f"沙箱降级直接写入：{task['id']}")
        task["execution_mode"] = "direct"
        task["direct_attempts"] = direct_attempts + 1
        if _apply_sandbox_to_main(project_root, task, sandbox):
            return
        error = task["last_error"]
    else:
        task["status"] = "failed"
        task["last_error"] = (
            f"沙箱验证失败：{sandbox_verification.get('command')}\n"
            f"{sandbox_verification.get('stdout')}\n{sandbox_verification.get('stderr')}"
        ).strip()
        error = task["last_error"]

    status, repair_result = _repair_sandbox_once(
        project_root,
        provider,
        architecture,
        project,
        task,
        sandbox,
        error,
    )
    if status == "blocked":
        task["status"] = "blocked"
        task["last_error"] = f"已转接对话流：{str(repair_result.get('needs_handoff') or '')[:1000]}"
        return

    retry_verification = _sandbox.run_verification(sandbox["path"], task)
    task["sandbox_verification"] = retry_verification
    if retry_verification["passed"]:
        if apply_mode == "review":
            _settle_review_patch(project_root, task, sandbox)
            return
        if _apply_sandbox_to_main(project_root, task, sandbox):
            return
    if (
        repair_result.get("sandbox_environment_issue") is True
        and int(task.get("direct_attempts") or 0) < 2
    ):
        if apply_mode == "review":
            _settle_review_patch(project_root, task, sandbox)
            return
        _snapshots.create_snapshot(project_root, f"修复后降级直接写入：{task['id']}")
        task["execution_mode"] = "direct"
        task["direct_attempts"] = int(task.get("direct_attempts") or 0) + 1
        if _apply_sandbox_to_main(project_root, task, sandbox):
            return
    task["status"] = "failed"
    task["last_error"] = (
        task.get("last_error")
        or f"沙箱验证失败：{retry_verification.get('stdout')}\n{retry_verification.get('stderr')}"
    )
    append_work_log(
        project_root,
        agent_id,
        "沙箱失败",
        task["id"],
        task["last_error"],
    )


def generate_one_task(
    project_root: Path,
    task: dict[str, Any],
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    resume: bool = False,
    cancel_check: Any = None,
) -> dict[str, Any]:
    """Generate one task in place. Safe to run concurrently: each worker owns
    its task dict and only reads shared architecture/project data."""
    agent_id = str(task.get("module_id") or task.get("id") or "")
    sandbox: dict[str, Any] | None = None
    try:
        sandbox = _sandbox.create_sandbox(project_root)
        task["execution_mode"] = "sandbox"
        task["sandbox_path"] = str(sandbox["path"])
        if resume:
            restore_checkpoint_sandbox(project_root, task["id"], sandbox)
        result = run_agent_tool_loop(
            project_root,
            provider,
            architecture,
            project,
            task,
            sandbox=sandbox,
            call_model=call_model_json,
            resume=resume,
            use_native_tools=True,
            cancelled=cancel_check,
        )
        handoff = str(result.get("needs_handoff") or "").strip()
        if handoff and not (result.get("files") or result.get("patch") or result.get("done")):
            task["status"] = "blocked"
            task["last_error"] = f"已转接对话流：{handoff[:1000]}"
            append_error_memory(
                project_root,
                agent_id,
                "generation",
                task["last_error"],
                task_id=task["id"],
                related_files=task.get("target_files", []),
            )
            append_work_log(
                project_root,
                agent_id,
                "越界转接",
                task["id"],
                f"转接原因：{handoff[:1000]}",
            )
            return task
        if result.get("files") or result.get("patch"):
            files = normalize_generated_files(result, task["id"])
            validate_generated_files(files, task["id"])
            task["patch"] = make_patch_entries(project_root, files)
            task["thinking"] = str(result.get("thinking") or "")
            task["status"] = "review"
            task["last_error"] = ""
            lint = contract_lint.check_patch(project_root, task["patch"])
            task["contract_lint_violations"] = lint["violations"]
            task["contract_delta"] = lint["contract_delta"]
            task["contract_lint_skipped"] = lint["skipped"]
            _record_generation_success(project_root, agent_id, task, files)
            return task
        if not result.get("done"):
            raise WorkspaceError("模型没有返回 done 或 files。")
        task["thinking"] = str(result.get("thinking") or "")
        _complete_sandbox_task(
            project_root,
            provider,
            architecture,
            project,
            task,
            sandbox,
            result,
        )
    except AgentLoopCancelled:
        task["status"] = "pending"
        task["last_error"] = "已停止"
    except WorkspaceError as exc:
        _record_generation_failure(project_root, agent_id, task, str(exc))
        task["last_error"] = str(exc)
        task["status"] = "failed"
    finally:
        if sandbox:
            _sandbox.destroy_sandbox(sandbox)
    return task


def stream_generate_one_task(
    project_root: Path,
    task: dict[str, Any],
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    cancel_check: Any = None,
):
    """Yield per-task NDJSON events while generating one task's patch.

    The caller owns `task_started`; this generator emits `task_reasoning` deltas
    and one final `task_done`. Cancellation is checked between tool-loop turns so
    a stopped task returns to `pending` and can be regenerated.
    """
    task_id = task["id"]
    agent_id = str(task.get("module_id") or task_id)
    sandbox: dict[str, Any] | None = None
    is_cancelled = cancel_check or (lambda: _task_cancelled(task_id, project_root))
    try:
        if is_cancelled():
            raise TaskCancelled()
        sandbox = _sandbox.create_sandbox(project_root)
        task["execution_mode"] = "sandbox"
        task["sandbox_path"] = str(sandbox["path"])
        yield {
            "type": "task_sandbox_start",
            "task_id": task_id,
            "path": str(sandbox["path"]),
        }
        for event in stream_agent_tool_loop(
            project_root,
            provider,
            architecture,
            project,
            task,
            sandbox=sandbox,
            cancelled=is_cancelled,
            call_model=call_model_json,
            use_native_tools=True,
        ):
            if event["type"] == "reasoning":
                yield {
                    "type": "task_reasoning",
                    "task_id": task_id,
                    "text": event["text"],
                }
            elif event["type"] == "tool_activity":
                yield {
                    "type": "task_reasoning",
                    "task_id": task_id,
                    "text": event["text"],
                }
            elif event["type"] == "done":
                result = event["result"]
                handoff = str(result.get("needs_handoff") or "").strip()
                if handoff and not (result.get("files") or result.get("patch")):
                    task["status"] = "blocked"
                    task["last_error"] = f"已转接对话流：{handoff[:1000]}"
                    append_error_memory(
                        project_root,
                        agent_id,
                        "generation",
                        task["last_error"],
                        task_id=task_id,
                        related_files=task.get("target_files", []),
                    )
                    append_work_log(
                        project_root,
                        agent_id,
                        "越界转接",
                        task_id,
                        f"转接原因：{handoff[:1000]}",
                    )
                    yield {
                        "type": "task_done",
                        "task_id": task_id,
                        "status": "blocked",
                        "error": task["last_error"],
                    }
                    return
                if not (result.get("files") or result.get("patch")) and not result.get("done"):
                    raise WorkspaceError("模型没有返回 done 或 files。")
                task["thinking"] = str(result.get("thinking") or "")
                if result.get("files") or result.get("patch"):
                    files = normalize_generated_files(result, task_id)
                    validate_generated_files(files, task_id)
                    task["patch"] = make_patch_entries(project_root, files)
                    task["status"] = "review"
                    task["last_error"] = ""
                    lint = contract_lint.check_patch(project_root, task["patch"])
                    task["contract_lint_violations"] = lint["violations"]
                    task["contract_delta"] = lint["contract_delta"]
                    task["contract_lint_skipped"] = lint["skipped"]
                    _record_generation_success(project_root, agent_id, task, files)
                    yield {
                        "type": "task_done",
                        "task_id": task_id,
                        "status": "review",
                        "error": "",
                    }
                    return
                yield {
                    "type": "task_sandbox_verify",
                    "task_id": task_id,
                }
                _complete_sandbox_task(
                    project_root,
                    provider,
                    architecture,
                    project,
                    task,
                    sandbox,
                    result,
                )
                if task.get("execution_mode") == "direct":
                    yield {
                        "type": "task_direct_fallback",
                        "task_id": task_id,
                        "reason": str(result.get("sandbox_environment_issue") or ""),
                    }
                yield {
                    "type": "task_done",
                    "task_id": task_id,
                    "status": task["status"],
                    "error": task["last_error"],
                }
                return
    except (AgentLoopCancelled, TaskCancelled):
        task["status"] = "pending"
        task["last_error"] = "已停止"
        append_work_log(
            project_root,
            agent_id,
            "已停止",
            task_id,
            "用户在生成完成前停止了任务。",
        )
        yield {
            "type": "task_done",
            "task_id": task_id,
            "status": "pending",
            "error": "已停止",
        }
    except WorkspaceError as exc:
        _record_generation_failure(project_root, agent_id, task, str(exc))
        task["last_error"] = str(exc)
        task["status"] = "failed"
        yield {
            "type": "task_done",
            "task_id": task_id,
            "status": "failed",
            "error": str(exc),
        }
    except Exception as exc:  # defensive: a worker must never stay stuck in "running"
        _record_generation_failure(
            project_root, agent_id, task, f"生成任务失败：{exc}"
        )
        task["last_error"] = f"生成任务失败：{exc}"
        task["status"] = "failed"
        yield {
            "type": "task_done",
            "task_id": task_id,
            "status": "failed",
            "error": task["last_error"],
        }
    finally:
        if sandbox:
            _sandbox.destroy_sandbox(sandbox)


def _prepare_wave_locks(
    project_root: Path,
    wave: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Register agents and lock workspace ownership for one wave.

    Returns only the tasks that may run now; conflicting tasks stay `pending` and are
    picked up by the next wave after the locks are released.
    """
    clear_locks(project_root)
    assignments = [
        (task["id"], task.get("target_files", []))
        for task in wave
        if task.get("id")
    ]
    granted = set(acquire_workspace_locks(project_root, assignments))
    active: list[dict[str, Any]] = []
    for task in wave:
        if task["id"] in granted:
            register_agent(
                project_root,
                task.get("module_id") or task["id"],
                task["id"],
                task.get("target_files", []),
                "running",
            )
            active.append(task)
        else:
            task["status"] = "pending"
            task["last_error"] = "工作区与其他子 Agent 冲突，等待下一波。"
    return active


def _release_wave_locks(
    project_root: Path,
    active: list[dict[str, Any]],
) -> None:
    release_workspace_locks(
        project_root,
        [task["id"] for task in active if task.get("id")],
    )
    for task in active:
        module_id = task.get("module_id") or task.get("id")
        if task["status"] == "review":
            update_agent_status(project_root, module_id, "review")
        elif task["status"] == "blocked":
            update_agent_status(
                project_root,
                module_id,
                "blocked",
                task.get("last_error", ""),
            )
        elif task["status"] == "failed":
            update_agent_status(
                project_root,
                module_id,
                "blocked",
                task.get("last_error", ""),
            )


def _drain_generation_events(events) -> dict[str, Any]:
    """Run a streaming execution to completion and return its final task state."""
    final_state: dict[str, Any] | None = None
    for event in events:
        if event.get("type") == "done" and isinstance(event.get("tasks"), dict):
            final_state = event["tasks"]
    if final_state is None:
        raise WorkspaceError("任务执行未返回最终状态。")
    return final_state


def _generate_wave_events(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    max_workers: int = 3,
    *,
    one_task: bool = False,
    kind: str = "generate-wave",
    transport: str = "stream",
):
    """Shared execution generator used by sync and streaming wave entry points."""
    state = sync_work_items_from_architecture(project_root, architecture, project)
    mark_stale_tasks(project_root, state)
    recover_stale_running(state, project_root)
    if state["status"] not in {"confirmed", "review"}:
        raise WorkspaceError("架构中还没有可生成的功能模块。")
    wave = ready_tasks(state)
    if one_task:
        wave = wave[:1]
    if not wave:
        state["status"] = "review"
        write_task_state(project_root, state)
        yield {"type": "done", "tasks": state}
        return

    for task in wave:
        task["status"] = "pending"
    active = _prepare_wave_locks(project_root, wave)
    for task in active:
        task["status"] = "running"
    write_task_state(project_root, state)
    if not active:
        state["last_error"] = next(
            (task["last_error"] for task in wave if task["last_error"]), ""
        )
        write_task_state(project_root, state)
        yield {"type": "done", "tasks": state}
        return

    handle = _register_wave(
        [task["id"] for task in active],
        project_root,
        kind=kind,
        transport=transport,
    )
    events: "queue.Queue[dict[str, Any]]" = queue.Queue()

    def run_one(task: dict[str, Any]) -> None:
        events.put({"type": "task_started", "task_id": task["id"]})
        for event in stream_generate_one_task(
            project_root,
            task,
            provider,
            architecture,
            project,
            cancel_check=lambda task_id=task["id"]: handle.cancelled(task_id),
        ):
            events.put(event)

    remaining = len(active)
    workers = max(1, min(max_workers, remaining))
    pool = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = [pool.submit(run_one, task) for task in active]
        while remaining > 0:
            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                continue
            yield event
            if event["type"] == "task_done":
                remaining -= 1
        for future in futures:
            future.result()
    except GeneratorExit:
        handle.cancel()
        _task_jobs.finish_job(handle, "cancelled", "客户端已断开执行流。")
        raise
    except BaseException as exc:
        _task_jobs.finish_job(handle, "failed", str(exc))
        raise
    finally:
        pool.shutdown(wait=True)
        _release_wave_locks(project_root, active)

    register_completed_documentation(state)
    if any(task.get("status") in {"applied", "verified"} for task in active):
        mark_docs_dirty(state)
    state["last_error"] = next(
        (task["last_error"] for task in wave if task["last_error"]), ""
    )
    write_task_state(project_root, state)
    cancelled = handle.cancel_requested or any(
        task.get("status") == "pending" and task.get("last_error") == "已停止"
        for task in active
    )
    failed = any(
        task.get("status") in {"failed", "blocked"}
        for task in active
    )
    _task_jobs.finish_job(
        handle,
        "cancelled" if cancelled else "failed" if failed else "completed",
        state["last_error"],
    )
    yield {"type": "done", "tasks": state}


def generate_wave(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    max_workers: int = 3,
) -> dict[str, Any]:
    """Generate every currently ready task through the shared execution job."""
    return _drain_generation_events(
        _generate_wave_events(
            project_root,
            provider,
            architecture,
            project,
            max_workers=max_workers,
            kind="generate-wave",
            transport="sync",
        )
    )


def stream_generate_wave(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    max_workers: int = 3,
):
    """Yield the shared execution events for a task wave."""
    yield from _generate_wave_events(
        project_root,
        provider,
        architecture,
        project,
        max_workers=max_workers,
        kind="generate-wave",
        transport="stream",
    )
