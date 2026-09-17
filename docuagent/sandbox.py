"""Trial sandboxes for sub-agent writes.

Git repositories get a detached worktree; non-Git projects get a lightweight copy.
All sub-agent writes happen in the sandbox. A passing sandbox verification can be
applied back to the main project through the normal backend safety checks.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from command_policy import validate_verification_command
from core import WorkspaceError
from workspace import apply_text_files_atomic

EXCLUDED_DIRS = {
    ".git",
    ".docuagent",
    "node_modules",
    "__pycache__",
    "web-next",
    ".backups",
    ".recovery",
}
EXCLUDED_FILES = {".env"}
LINK_CANDIDATES = ("node_modules", ".venv")


def _run(
    args: list[str],
    cwd: Path,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            cwd=str(cwd),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(f"沙箱命令执行失败：{exc}") from exc


def _git_repo_root(project_root: Path) -> Path | None:
    result = _run(
        ["git", "rev-parse", "--show-toplevel"],
        project_root,
    )
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _link_runtime_dirs(source: Path, target: Path) -> None:
    for name in LINK_CANDIDATES:
        src = source / name
        if not src.is_dir():
            continue
        dst = target / name
        if dst.exists():
            continue
        try:
            os.symlink(src, dst, target_is_directory=True)
        except OSError:
            continue


def _text_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _hash_sandbox_files(work_path: Path) -> dict[str, str]:
    return {
        relative: _text_hash(content)
        for relative, content in sandbox_files({"path": work_path}).items()
    }



def create_sandbox(project_root: Path) -> dict[str, Any]:
    """Create a temporary sandbox and return its metadata."""
    root = project_root.resolve()
    if not root.is_dir():
        raise WorkspaceError("项目目录不存在。")
    temp_root = Path(tempfile.mkdtemp(prefix="docuagent-sandbox-"))
    work_path = temp_root / "work"

    repo_root = _git_repo_root(root)
    if repo_root and repo_root == root:
        work_path.mkdir()
        result = _run(
            ["git", "worktree", "add", "--detach", str(work_path), "HEAD"],
            root,
        )
        if result.returncode != 0:
            shutil.rmtree(temp_root, ignore_errors=True)
            raise WorkspaceError(
                f"创建 Git worktree 失败：{(result.stderr or result.stdout).strip()[:500]}"
            )
        kind = "git"
        _link_runtime_dirs(root, work_path)
    else:
        shutil.copytree(
            root,
            work_path,
            ignore=shutil.ignore_patterns(*EXCLUDED_DIRS, *EXCLUDED_FILES),
        )
        kind = "copy"
        _link_runtime_dirs(root, work_path)

    return {
        "path": work_path,
        "temp_root": temp_root,
        "kind": kind,
        "project_root": root,
        "base_hashes": _hash_sandbox_files(work_path),
    }


def destroy_sandbox(sandbox: dict[str, Any]) -> None:
    """Remove a worktree (if used) and delete the temporary root."""
    work_path = Path(sandbox.get("path") or "")
    temp_root = Path(sandbox.get("temp_root") or "")
    project_root = Path(sandbox.get("project_root") or "")
    if sandbox.get("kind") == "git" and work_path.is_dir():
        result = _run(
            ["git", "worktree", "remove", "--force", str(work_path)],
            project_root,
        )
        if result.returncode != 0:
            shutil.rmtree(work_path, ignore_errors=True)
    if temp_root.is_dir():
        shutil.rmtree(temp_root, ignore_errors=True)


def _safe_relative_path(project_root: Path, raw: str) -> Path:
    path = str(raw or "").strip().replace("\\", "/")
    if not path or Path(path).is_absolute() or ".." in Path(path).parts:
        raise WorkspaceError(f"沙箱文件路径无效：{raw}")
    target = (project_root / path).resolve()
    if not target.is_relative_to(project_root.resolve()):
        raise WorkspaceError("沙箱文件路径超出沙箱目录。")
    return target


def sandbox_files(sandbox: dict[str, Any]) -> dict[str, str]:
    """Return text files inside the sandbox work tree, keyed by relative path."""
    work_path = Path(sandbox["path"])
    files: dict[str, str] = {}
    for current, dirs, names in os.walk(work_path):
        dirs[:] = [d for d in sorted(dirs) if d not in EXCLUDED_DIRS]
        current_path = Path(current)
        for name in sorted(names):
            if name in EXCLUDED_FILES:
                continue
            path = current_path / name
            if any(part in EXCLUDED_DIRS for part in path.relative_to(work_path).parts):
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            relative = path.relative_to(work_path).as_posix()
            files[relative] = content
    return files


def changed_sandbox_files(sandbox: dict[str, Any]) -> dict[str, str]:
    """Sandbox files whose content differs from the base captured at creation.

    Shared by the auto-apply write-back and the review-mode patch builder so both
    paths see exactly the same set of agent changes.
    """
    base_hashes = sandbox.get("base_hashes")
    if not isinstance(base_hashes, dict):
        return {}
    changed: dict[str, str] = {}
    for relative, content in sandbox_files(sandbox).items():
        if base_hashes.get(relative) == _text_hash(content):
            continue
        changed[relative] = content
    return changed


def apply_sandbox(project_root: Path, sandbox: dict[str, Any]) -> list[str]:
    """Apply agent-changed sandbox files atomically with conflict checks.

    `base_hashes` is captured when the sandbox is created. Only files whose sandbox
    content differs from that base are candidates for write-back; if the main
    project moved away from the same base, applying would overwrite user work and
    is rejected before any file is written.
    """
    root = project_root.resolve()
    base_hashes = sandbox.get("base_hashes")
    if not isinstance(base_hashes, dict):
        # Sandboxes are transient, but keep a defensive fallback for older in-flight
        # metadata: without a base we cannot prove write-back safety.
        raise WorkspaceError("沙箱缺少基线信息，无法安全回写。")

    changes: dict[str, str] = {}
    for relative, content in changed_sandbox_files(sandbox).items():
        base_hash = base_hashes.get(relative)
        target = _safe_relative_path(root, relative)
        try:
            current = target.read_text(encoding="utf-8")
        except OSError:
            current = None

        if current == content:
            continue
        if base_hash is None:
            if current is not None:
                raise WorkspaceError(f"沙箱新增文件已在主项目中存在：{relative}")
        elif current is None:
            raise WorkspaceError(f"主项目文件在沙箱生成后被删除：{relative}")
        elif _text_hash(current) != str(base_hash):
            raise WorkspaceError(
                f"主项目文件 `{relative}` 在沙箱生成后被修改，已停止回写。"
            )
        changes[relative] = content

    return apply_text_files_atomic(root, changes)


def run_verification(
    root: Path,
    task: dict[str, Any],
) -> dict[str, Any]:
    """Run every declared verification command inside `root`."""
    outputs: list[str] = []
    for command in task.get("verification") or []:
        try:
            argv = validate_verification_command(command)
        except WorkspaceError as exc:
            return {
                "passed": False,
                "stdout": "\n".join(outputs),
                "stderr": str(exc),
                "returncode": -1,
                "command": command,
            }
        try:
            result = subprocess.run(
                argv,
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "passed": False,
                "stdout": "\n".join(outputs),
                "stderr": str(exc),
                "returncode": -1,
                "command": command,
            }
        outputs.append(f"$ {command}\n{result.stdout}")
        if result.returncode != 0:
            return {
                "passed": False,
                "stdout": "\n".join(outputs),
                "stderr": result.stderr,
                "returncode": result.returncode,
                "command": command,
            }
    return {
        "passed": True,
        "stdout": "\n".join(outputs),
        "stderr": "",
        "returncode": 0,
        "command": "; ".join(task.get("verification") or []),
    }
