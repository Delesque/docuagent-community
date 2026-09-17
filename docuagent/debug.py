"""Minimal breakpoint debugger.

Runs a Python script under pdb, stops at a chosen line, and captures the stack
plus local variables. This is the smallest useful slice of a debugger
(breakpoint -> inspect locals), not a full DAP client.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

from core import WorkspaceError


def debug_file(
    project_root: Path,
    path: str,
    breakpoint_line: int,
) -> dict[str, Any]:
    root = project_root.resolve()
    target = (root / path).resolve()
    if not target.is_relative_to(root):
        raise WorkspaceError("文件路径超出项目目录。")
    if not target.is_file():
        raise WorkspaceError(f"文件不存在：{path}")
    try:
        line = int(breakpoint_line)
    except (TypeError, ValueError):
        raise WorkspaceError("断点行号无效。")
    if line < 1:
        raise WorkspaceError("断点行号无效。")

    args = [sys.executable, "-m", "pdb"]
    for command in (f"break {line}", "continue", "p locals()", "where", "quit"):
        args.extend(["-c", command])
    args.append(str(target))
    try:
        result = subprocess.run(
            args,
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WorkspaceError(f"调试执行失败：{exc}") from exc
    return {
        "path": path,
        "breakpoint_line": line,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "returncode": result.returncode,
    }
