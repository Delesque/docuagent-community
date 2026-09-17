"""Task diagnostics, verification, retry, resume, and repair orchestration."""
from __future__ import annotations

import subprocess
import threading
import queue
from pathlib import Path
from typing import Any

from agent_tools import AgentLoopCancelled, execute_tool, has_loop_checkpoint
from agents import append_error_memory, append_work_log, build_agent_context, acquire_workspace_locks, release_workspace_locks, register_agent
from command_policy import validate_verification_command
import contract_lint
from core import WorkspaceError
from llm_client import ProviderConfig
import sandbox as _sandbox
import task_runtime as _task_runtime
import task_jobs as _task_jobs
from task_generate import (
    REPAIR_PROMPT,
    _complete_sandbox_task,
    _pyflakes_issues,
    _record_generation_failure,
    _record_verification_failure,
    generate_one_task,
    recover_stale_running,
    validate_generated_files,
)
from task_state import (
    MAX_REPAIR_ATTEMPTS,
    _task_by_id,
    ensure_documentation_state,
    normalize_generated_files,
    record_documentation_failure,
    register_completed_documentation,
    register_documentation_task,
    read_task_state,
    update_delivery_status,
    write_task_state,
)


def call_model_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _task_runtime.call_model_json(*args, **kwargs)


def run_agent_tool_loop(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _task_runtime.run_agent_tool_loop(*args, **kwargs)


def _finish_job_for_task(handle: _task_jobs.JobHandle, task: dict[str, Any]) -> None:
    status = task.get("status")
    if handle.cancel_requested or (
        status == "pending" and task.get("last_error") == "已停止"
    ):
        terminal = "cancelled"
    elif status in {"failed", "blocked"}:
        terminal = "failed"
    else:
        terminal = "completed"
    _task_jobs.finish_job(handle, terminal, str(task.get("last_error") or ""))


def _start_verification_process(
    argv: list[str],
    project_root: Path,
) -> tuple[subprocess.Popen[str], threading.Timer]:
    process = subprocess.Popen(
        argv,
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    timer = threading.Timer(300, process.kill)
    timer.start()
    return process, timer


def _iter_process_output(
    process: subprocess.Popen[str],
    cancel_check: Any = None,
):
    """Yield process output while polling cancellation and the 300s kill timer."""
    lines: "queue.Queue[str | None]" = queue.Queue()

    def reader() -> None:
        try:
            assert process.stdout is not None
            for line in iter(process.stdout.readline, ""):
                lines.put(line)
        finally:
            lines.put(None)

    reader_thread = threading.Thread(target=reader, daemon=True)
    reader_thread.start()
    while True:
        if cancel_check is not None and cancel_check():
            process.kill()
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            if process.poll() is not None and not reader_thread.is_alive():
                break
            continue
        if line is None:
            break
        yield line.rstrip("\n")
    process.wait(timeout=5)
    if process.stdout is not None:
        process.stdout.close()


def _parse_pyflakes_issue(issue: str) -> dict[str, Any] | None:
    # Format: "<path>:<line>:<col>: <message>"
    if ":" not in issue:
        return None
    _, rest = issue.split(":", 1)
    if ":" not in rest:
        return None
    line_part, rest2 = rest.split(":", 1)
    if ":" not in rest2:
        return None
    col_part, message = rest2.split(":", 1)
    try:
        line = int(line_part.strip())
        column = int(col_part.strip())
    except ValueError:
        return None
    return {
        "line": line,
        "column": column,
        "severity": "warning",
        "message": message.strip(),
        "source": "pyflakes",
    }


def diagnose_file(project_root: Path, path: str) -> dict[str, Any]:
    """Return syntax + static-analysis diagnostics for one project file.

    Mirrors the diagnostics half of an LSP: line/column/severity/message, which
    the frontend renders as inline issues next to the code.
    """
    root = project_root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise WorkspaceError("文件路径超出项目目录。")
    if not target.is_file():
        raise WorkspaceError(f"文件不存在：{path}")
    try:
        content = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise WorkspaceError(f"读取文件失败：{exc}") from exc

    diagnostics: list[dict[str, Any]] = []
    if not path.endswith(".py"):
        return {"path": path, "diagnostics": diagnostics}

    try:
        compile(content, path, "exec")
    except SyntaxError as exc:
        return {
            "path": path,
            "diagnostics": [{
                "line": exc.lineno or 1,
                "column": exc.offset or 1,
                "severity": "error",
                "message": exc.msg,
                "source": "syntax",
            }],
        }

    for issue in _pyflakes_issues({"path": path, "content": content}):
        parsed = _parse_pyflakes_issue(issue)
        if parsed:
            diagnostics.append(parsed)
    return {"path": path, "diagnostics": diagnostics}


from task_diff import apply_hunks, diff_hunks  # noqa: F401  (compat re-export)
from task_patch import (  # noqa: F401  (re-exported for existing callers)
    _patch_apply_payload,
    apply_task_hunks,
    apply_task_partial,
    apply_task_patch,
    edit_task_patch,
    ensure_file_unchanged,
    reject_task,
    task_hunks,
)


def _contract_lint_failed(task: dict[str, Any], lint: dict[str, Any]) -> bool:
    """Record contract lint result and return True when the task must fail."""
    task["contract_lint_violations"] = lint.get("violations", [])
    task["contract_delta"] = lint.get("contract_delta", [])
    task["contract_lint_skipped"] = lint.get("skipped", [])
    if not lint.get("violations"):
        return False
    task["last_error"] = "契约校验未通过：\n" + "\n".join(
        f"- {item}" for item in lint["violations"]
    )
    return True


def verify_task(
    project_root: Path,
    task_id: str,
    cancel_check: Any = None,
) -> dict[str, Any]:
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = _task_by_id(state, task_id)
    agent_id = str(task.get("module_id") or task_id)
    if task["status"] != "applied":
        raise WorkspaceError(f"任务 `{task_id}` 还没有应用补丁。")

    # Documentation is generated only by the independent Doc Maintainer pass.
    task["documentation_updated"] = []
    ensure_documentation_state(state)
    changed_files = [
        str(path)
        for path in (task.get("code_changed_files") or [])
        if str(path).strip()
    ]
    if (
        changed_files
        and state["documentation_task"]["status"] in {"not_required", "synced"}
    ):
        register_documentation_task(
            state,
            task_id,
            changed_files,
            agent_id,
        )
    task["status"] = "verifying"
    update_delivery_status(state)
    write_task_state(project_root, state)

    # 初始化验证输出容器
    verification_output = {
        "stdout": "",
        "stderr": "",
        "returncode": 0,
        "command": "",
        "timestamp": "",
    }

    if not task["verification"]:
        lint = contract_lint.check_working_files(project_root, task.get("target_files", []))
        if _contract_lint_failed(task, lint):
            task["status"] = "failed"
            task["verification_output"] = verification_output
            _record_verification_failure(project_root, agent_id, task_id, task, task["last_error"])
            update_delivery_status(state)
            write_task_state(project_root, state)
            return state
        task["status"] = "verified"
        task["verification_output"] = verification_output
        append_work_log(
            project_root,
            agent_id,
            "验证通过",
            task_id,
            "没有配置验证命令，标记为已验证。",
        )
        write_task_state(project_root, state)
        update_delivery_status(state)
        return state

    # 收集所有命令的输出
    all_stdout = []
    all_stderr = []

    for command in task["verification"]:
        if cancel_check is not None and cancel_check():
            task["status"] = "applied"
            task["last_error"] = "已停止验证。"
            write_task_state(project_root, state)
            update_delivery_status(state)
            return state
        try:
            argv = validate_verification_command(command)
        except WorkspaceError as exc:
            task["last_error"] = str(exc)
            task["status"] = "failed"
            task["verification_output"] = {
                "stdout": "\n\n".join(all_stdout),
                "stderr": str(exc),
                "returncode": -1,
                "command": command,
                "timestamp": "",
            }
            _record_verification_failure(
                project_root, agent_id, task_id, task, task["last_error"]
            )
            write_task_state(project_root, state)
            update_delivery_status(state)
            return state
        process: subprocess.Popen[str] | None = None
        timer: threading.Timer | None = None
        try:
            process, timer = _start_verification_process(argv, project_root)
            command_output = list(_iter_process_output(process, cancel_check))
            if cancel_check is not None and cancel_check():
                task["status"] = "applied"
                task["last_error"] = "已停止验证。"
                write_task_state(project_root, state)
                update_delivery_status(state)
                return state
            stdout_text = "\n".join(command_output)
            all_stdout.append(f"$ {command}\n{stdout_text}")
        except (OSError, subprocess.TimeoutExpired) as exc:
            error_msg = f"验证命令执行失败：{exc}"
            task["last_error"] = error_msg
            task["status"] = "failed"
            task["verification_output"] = {
                "stdout": "\n\n".join(all_stdout),
                "stderr": error_msg,
                "returncode": -1,
                "command": command,
                "timestamp": "",
            }
            _record_verification_failure(
                project_root, agent_id, task_id, task, task["last_error"]
            )
            write_task_state(project_root, state)
            update_delivery_status(state)
            return state
        finally:
            if timer is not None:
                timer.cancel()
        assert process is not None

        if process.returncode != 0:
            # 保留最近 5000 字符避免日志爆炸
            stdout_tail = stdout_text[-5000:]
            stderr_tail = ""

            task["last_error"] = (
                f"验证失败：{command}\n{stdout_tail}\n{stderr_tail}"
            ).strip()
            task["status"] = "failed"
            task["verification_output"] = {
                "stdout": "\n\n".join(all_stdout),
                "stderr": "\n\n".join(all_stderr),
                "returncode": process.returncode,
                "command": command,
                "timestamp": "",
            }
            _record_verification_failure(
                project_root, agent_id, task_id, task, task["last_error"]
            )
            update_delivery_status(state)
            write_task_state(project_root, state)
            return state

    # 所有验证通过
    lint = contract_lint.check_working_files(project_root, task.get("target_files", []))
    if _contract_lint_failed(task, lint):
        task["status"] = "failed"
        task["verification_output"] = {
            "stdout": "\n\n".join(all_stdout),
            "stderr": task["last_error"],
            "returncode": -1,
            "command": "; ".join(task["verification"]),
            "timestamp": "",
        }
        _record_verification_failure(project_root, agent_id, task_id, task, task["last_error"])
        write_task_state(project_root, state)
        update_delivery_status(state)
        return state

    task["status"] = "verified"
    task["last_error"] = ""
    task["verification_output"] = {
        "stdout": "\n\n".join(all_stdout),
        "stderr": "\n\n".join(all_stderr),
        "returncode": 0,
        "command": "; ".join(task["verification"]),
        "timestamp": "",
    }
    append_work_log(
        project_root,
        agent_id,
        "验证通过",
        task_id,
        "命令：\n" + "\n".join(f"- {command}" for command in task["verification"]),
    )
    update_delivery_status(state)
    write_task_state(project_root, state)
    return state


def stream_verify_task(
    project_root: Path,
    task_id: str,
    cancel_check: Any = None,
):
    """Run verification commands and yield NDJSON output events in real time."""
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = _task_by_id(state, task_id)
    agent_id = str(task.get("module_id") or task_id)
    if task["status"] != "applied":
        raise WorkspaceError(f"任务 `{task_id}` 还没有应用补丁。")

    task["documentation_updated"] = []
    ensure_documentation_state(state)
    changed_files = [
        str(path)
        for path in (task.get("code_changed_files") or [])
        if str(path).strip()
    ]
    if (
        changed_files
        and state["documentation_task"]["status"] in {"not_required", "synced"}
    ):
        register_documentation_task(
            state,
            task_id,
            changed_files,
            agent_id,
        )
    task["status"] = "verifying"
    update_delivery_status(state)
    write_task_state(project_root, state)

    all_stdout: list[str] = []
    all_stderr: list[str] = []

    def finish(status: str, error: str, returncode: int, command: str) -> dict[str, Any]:
        if status == "verified":
            lint = contract_lint.check_working_files(project_root, task.get("target_files", []))
            if _contract_lint_failed(task, lint):
                status = "failed"
                error = task["last_error"]
                returncode = -1
        task["status"] = status
        task["last_error"] = error
        task["verification_output"] = {
            "stdout": "\n\n".join(all_stdout),
            "stderr": "\n\n".join(all_stderr),
            "returncode": returncode,
            "command": command,
            "timestamp": "",
        }
        if status == "failed":
            _record_verification_failure(project_root, agent_id, task_id, task, error)
        elif status == "verified":
            append_work_log(
                project_root,
                agent_id,
                "验证通过",
                task_id,
                "命令：" + "\n".join(f"- {item}" for item in task["verification"]),
            )
        update_delivery_status(state)
        write_task_state(project_root, state)
        return state

    if not task["verification"]:
        yield {"type": "output", "task_id": task_id, "line": "没有配置验证命令。", "stream": "stdout"}
        yield {"type": "done", "task_id": task_id, "state": finish("verified", "", 0, "")}
        return

    for command in task["verification"]:
        yield {"type": "command_start", "task_id": task_id, "command": command}
        try:
            parts = validate_verification_command(command)
        except WorkspaceError as exc:
            all_stderr.append(str(exc))
            yield {"type": "done", "task_id": task_id, "state": finish("failed", str(exc), -1, command)}
            return
        process: subprocess.Popen[str] | None = None
        timer: threading.Timer | None = None
        try:
            process, timer = _start_verification_process(parts, project_root)
        except (OSError, ValueError) as exc:
            all_stderr.append(str(exc))
            yield {"type": "done", "task_id": task_id, "state": finish("failed", str(exc), -1, command)}
            return

        command_output: list[str] = []
        try:
            assert process.stdout is not None
            for line in _iter_process_output(process, cancel_check):
                command_output.append(line)
                all_stdout.append(line)
                yield {
                    "type": "output",
                    "task_id": task_id,
                    "line": line,
                    "stream": "stdout",
                }
        finally:
            timer.cancel()

        if cancel_check is not None and cancel_check():
            task["status"] = "applied"
            task["last_error"] = "已停止验证。"
            yield {
                "type": "done",
                "task_id": task_id,
                "state": finish("applied", "已停止验证。", -9, command),
            }
            return

        all_stdout.append(f"$ {command}")
        returncode = process.returncode
        yield {
            "type": "command_done",
            "task_id": task_id,
            "command": command,
            "returncode": returncode,
        }
        if returncode != 0:
            error = (
                f"验证失败：{command}\n"
                + "\n".join(command_output[-5000:])
            ).strip()
            yield {"type": "done", "task_id": task_id, "state": finish("failed", error, returncode, command)}
            return

    yield {"type": "done", "task_id": task_id, "state": finish("verified", "", 0, "; ".join(task["verification"]))}


def retry_task(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Retry a task after the conversation flow makes a decision.

    Blocked handoff tasks are regenerated in place; failed tasks are either re-verified
    when a patch already landed, or regenerated when generation itself failed.
    """
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = _task_by_id(state, task_id)
    retry_generation = task["status"] in {"failed", "running"} or (
        task["status"] == "pending"
        and str(task.get("last_error") or "").startswith("上次生成被中断")
    )
    if recover_stale_running(state, project_root):
        write_task_state(project_root, state)
    handle: _task_jobs.JobHandle | None = None
    if retry_generation:
        if task["status"] == "failed" and task.get("patch") and task.get("verification_output"):
            handle = _task_jobs.begin_job(
                project_root,
                [task_id],
                kind="retry-verify",
                transport="sync",
            )
            append_work_log(
                project_root,
                str(task.get("module_id") or task_id),
                "错误决策重试",
                task_id,
                "用户选择重新验证已应用的补丁。",
            )
            task["status"] = "applied"
            task["last_error"] = ""
            update_delivery_status(state)
            write_task_state(project_root, state)
            try:
                verified = verify_task(
                    project_root,
                    task_id,
                    cancel_check=lambda: handle.cancelled(task_id),
                )
                _finish_job_for_task(handle, _task_by_id(verified, task_id))
                return verified
            except BaseException as exc:
                _task_jobs.finish_job(handle, "failed", str(exc))
                raise
            finally:
                if handle.status == "running":
                    _finish_job_for_task(handle, task)
        handle = _task_jobs.begin_job(
            project_root,
            [task_id],
            kind="retry-generate",
            transport="sync",
        )
        append_work_log(
            project_root,
            str(task.get("module_id") or task_id),
            "错误决策重试",
            task_id,
            "用户选择重新生成失败或中断任务。",
        )
        task["status"] = "running"
        task["last_error"] = ""
        write_task_state(project_root, state)
        try:
            generate_one_task(
                project_root,
                task,
                provider,
                architecture,
                project,
                cancel_check=lambda: handle.cancelled(task_id),
            )
        except Exception as exc:
            _record_generation_failure(
                project_root,
                str(task.get("module_id") or task_id),
                task,
                f"重试生成失败：{exc}",
            )
            task["last_error"] = f"重试生成失败：{exc}"
            task["status"] = "failed"
        register_completed_documentation(state)
        update_delivery_status(state)
        write_task_state(project_root, state)
        _finish_job_for_task(handle, task)
        return state

    if task["status"] != "blocked" or not task.get("last_error", "").startswith(
        "已转接对话流"
    ):
        raise WorkspaceError(f"任务 `{task_id}` 不是等待裁决的越界转接任务。")

    handle = _task_jobs.begin_job(
        project_root,
        [task_id],
        kind="retry-handoff",
        transport="sync",
    )
    task["status"] = "running"
    task["last_error"] = ""
    append_work_log(
        project_root,
        str(task.get("module_id") or task_id),
        "对话流裁决",
        task_id,
        "用户允许重新生成，已清空转接状态。",
    )
    write_task_state(project_root, state)
    try:
        generate_one_task(
            project_root,
            task,
            provider,
            architecture,
            project,
            cancel_check=lambda: handle.cancelled(task_id),
        )
    except Exception as exc:
        _record_generation_failure(
            project_root,
            str(task.get("module_id") or task_id),
            task,
            f"重试生成失败：{exc}",
        )
        task["last_error"] = f"重试生成失败：{exc}"
        task["status"] = "failed"
    register_completed_documentation(state)
    update_delivery_status(state)
    write_task_state(project_root, state)
    _finish_job_for_task(handle, task)
    return state


def resume_task(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Resume stopped or failed generation without discarding sandbox progress."""
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    if recover_stale_running(state, project_root):
        write_task_state(project_root, state)
    task = _task_by_id(state, task_id)
    if task["status"] not in {"failed", "pending"}:
        raise WorkspaceError(f"任务 `{task_id}` 不是失败或停止状态，无法恢复。")
    if not has_loop_checkpoint(project_root, task_id):
        raise WorkspaceError(f"任务 `{task_id}` 没有有效检查点，请重新生成。")
    completed = {item["id"] for item in state["tasks"] if item["status"] in {"applied", "verified"}}
    if any(dep not in completed for dep in task.get("depends_on", [])):
        raise WorkspaceError("依赖任务尚未完成，无法恢复。")
    if task_id not in acquire_workspace_locks(project_root, [(task_id, task.get("target_files", []))]):
        raise WorkspaceError("工作区正被其他任务占用，无法恢复。")
    register_agent(project_root, str(task.get("module_id") or task_id), task_id, task.get("target_files", []))
    handle = _task_jobs.begin_job(
        project_root,
        [task_id],
        kind="resume",
        transport="sync",
    )
    append_work_log(
        project_root,
        str(task.get("module_id") or task_id),
        "断点恢复",
        task_id,
        "从工具循环检查点继续生成。",
    )
    task["status"] = "running"
    task["last_error"] = ""
    write_task_state(project_root, state)
    try:
        generate_one_task(
            project_root,
            task,
            provider,
            architecture,
            project,
            resume=True,
            cancel_check=lambda: handle.cancelled(task_id),
        )
    except Exception as exc:
        _record_generation_failure(
            project_root,
            str(task.get("module_id") or task_id),
            task,
            f"断点恢复失败：{exc}",
        )
        task["last_error"] = f"断点恢复失败：{exc}"
        task["status"] = "failed"
    finally:
        release_workspace_locks(project_root, [task_id])
        _finish_job_for_task(handle, task)
    register_completed_documentation(state)
    update_delivery_status(state)
    write_task_state(project_root, state)
    return state


def repair_task(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task_id: str,
) -> dict[str, Any]:
    """Ask the Repair Agent to fix an applied patch that failed verification.

    The repair loop keeps the same patch gate as generation: the model proposes complete
    file contents, the user reviews the new diff, then applies and verifies again.
    """
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    task = _task_by_id(state, task_id)
    agent_id = str(task.get("module_id") or task_id)
    if task["status"] != "failed":
        raise WorkspaceError(f"任务 `{task_id}` 不是失败状态，无法自动修复。")
    output = task.get("verification_output")
    if not isinstance(output, dict) or not task.get("patch"):
        raise WorkspaceError(f"任务 `{task_id}` 没有可修复的验证补丁。")

    attempts = int(task.get("repair_attempts") or 0)
    if attempts >= MAX_REPAIR_ATTEMPTS:
        raise WorkspaceError(f"任务 `{task_id}` 已达 {MAX_REPAIR_ATTEMPTS} 次自动修复上限，请人工检查。")
    task["repair_attempts"] = attempts + 1
    task["status"] = "running"
    task["last_error"] = ""
    write_task_state(project_root, state)

    handle = _task_jobs.begin_job(
        project_root,
        [task_id],
        kind="repair",
        transport="sync",
    )
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
            system_prompt=REPAIR_PROMPT,
            extra_context={
                "verification_output": {
                    "command": str(output.get("command") or ""),
                    "stdout": str(output.get("stdout") or "")[-8000:],
                    "stderr": str(output.get("stderr") or "")[-8000:],
                    "returncode": output.get("returncode", -1),
                },
                "current_files": [
                    {"path": entry["path"], "content": entry["after"]}
                    for entry in task["patch"]
                ],
            },
            call_model=call_model_json,
            use_native_tools=True,
            cancelled=lambda: handle.cancelled(task_id),
        )
        handoff = str(result.get("needs_handoff") or "").strip()
        if handoff and not (result.get("files") or result.get("patch")):
            task["status"] = "blocked"
            task["last_error"] = f"已转接对话流：{handoff[:1000]}"
            append_error_memory(
                project_root,
                agent_id,
                "repair",
                task["last_error"],
                task_id=task_id,
                related_files=task.get("target_files", []),
            )
            append_work_log(
                project_root,
                agent_id,
                "修复越界转接",
                task_id,
                f"转接原因：{handoff[:1000]}",
            )
            return state

        if result.get("files") or result.get("patch"):
            files = normalize_generated_files(result, task_id)
            validate_generated_files(files, task_id)
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
        register_completed_documentation(state)
        summary = str(result.get("summary") or "已生成修复补丁。")[:500]
        append_work_log(
            project_root,
            agent_id,
            "验证修复",
            task_id,
            f"第 {attempts + 1} 次修复：{summary}\n"
            f"验证命令：{'; '.join(task['verification'] or [])}",
        )
    except AgentLoopCancelled:
        task["status"] = "pending"
        task["last_error"] = "已停止"
    except Exception as exc:
        task["status"] = "failed"
        task["last_error"] = str(exc)
        append_error_memory(
            project_root,
            agent_id,
            "repair",
            str(exc),
            task_id=task_id,
            related_files=task.get("target_files", []),
        )
        append_work_log(
            project_root,
            agent_id,
            "修复失败",
            task_id,
            str(exc),
        )
    finally:
        if sandbox:
            _sandbox.destroy_sandbox(sandbox)
        _finish_job_for_task(handle, task)
    register_completed_documentation(state)
    update_delivery_status(state)
    write_task_state(project_root, state)
    return state
