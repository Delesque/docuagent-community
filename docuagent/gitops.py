"""Minimal local Git integration for status, diff, and commit.

Every command runs inside the selected project root. Commits are explicit user actions:
the route snapshots the project first, then stages and commits with a single `-m`
argument so no shell interpolation reaches Git.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from core import WorkspaceError

GIT_TIMEOUT = 30
DIFF_LIMIT = 200_000


def _run_git(
    project_root: Path,
    args: list[str],
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(project_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(f"Git 命令执行失败：{exc}") from exc
    if check and result.returncode != 0:
        message = (result.stderr or result.stdout or "git 命令失败").strip()
        raise WorkspaceError(f"Git 命令失败：{message[:1000]}")
    return result


def _safe_relative_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if not raw:
        return ""
    candidate = Path(raw)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise WorkspaceError("Git 文件路径必须相对项目根目录。")
    return raw


def git_status(project_root: Path) -> dict[str, Any]:
    """Return branch, short head, and porcelain status entries."""
    try:
        inside = _run_git(
            project_root, ["rev-parse", "--is-inside-work-tree"], check=False
        )
    except WorkspaceError:
        return {
            "repo": False,
            "branch": "",
            "head": "",
            "changes": [],
            "dirty": False,
        }
    if inside.returncode != 0:
        return {
            "repo": False,
            "branch": "",
            "head": "",
            "changes": [],
            "dirty": False,
        }

    result = _run_git(
        project_root,
        ["status", "--porcelain=v1", "--untracked-files=normal"],
    )
    changes: list[dict[str, Any]] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        changes.append({
            "path": path,
            "status": code,
            "staged": code[0] != " ",
            "untracked": code == "??",
        })
    branch_result = _run_git(
        project_root, ["branch", "--show-current"], check=False
    )
    head_result = _run_git(
        project_root, ["rev-parse", "--short", "HEAD"], check=False
    )
    return {
        "repo": True,
        "branch": branch_result.stdout.strip(),
        "head": (
            head_result.stdout.strip() if head_result.returncode == 0 else ""
        ),
        "changes": changes,
        "dirty": bool(changes),
    }


def git_diff(
    project_root: Path,
    path: str = "",
) -> dict[str, str]:
    """Return working-tree and staged unified diffs, capped to a safe size."""
    safe_path = _safe_relative_path(path)
    scope = ["--", safe_path] if safe_path else []
    working = _run_git(
        project_root,
        ["diff", "--no-ext-diff", "--unified=3", *scope],
    )
    staged = _run_git(
        project_root,
        ["diff", "--cached", "--no-ext-diff", "--unified=3", *scope],
    )
    return {
        "working": working.stdout[-DIFF_LIMIT:],
        "staged": staged.stdout[-DIFF_LIMIT:],
    }


def git_commit(project_root: Path, message: str) -> dict[str, Any]:
    """Stage all changes and commit with one explicit message argument."""
    message = str(message or "").strip()
    if not message:
        raise WorkspaceError("提交信息不能为空。")
    _run_git(project_root, ["add", "-A"])
    _run_git(project_root, ["commit", "-m", message])
    head = _run_git(project_root, ["rev-parse", "--short", "HEAD"])
    return {
        "committed": True,
        "head": head.stdout.strip(),
        "status": git_status(project_root),
    }
