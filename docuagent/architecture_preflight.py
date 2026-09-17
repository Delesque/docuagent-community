"""Filesystem-aware checks for a confirmed architecture.

The architecture model reasons about the future project, but its verification
commands still have to run in the scaffold that actually exists. This module
keeps that distinction explicit: schema readiness belongs to ``core``; command
and ownership preflight belongs here.
"""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from command_policy import validate_verification_command
from core import WorkspaceError

_COMMAND_NAMES = {
    "git",
    "node",
    "npm",
    "npm.cmd",
    "npx",
    "npx.cmd",
    "pip",
    "pip3",
    "pytest",
    "python",
    "python3",
}


def _commands(architecture: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for item in architecture.get("verification", []):
        text = str(item).split("——", 1)[0].strip()
        if text:
            values.append(text)
    for module in architecture.get("modules", []):
        if not isinstance(module, dict):
            continue
        for item in module.get("verification", []):
            text = str(item).strip()
            if text:
                values.append(text)
    return list(dict.fromkeys(values))


def _ownership_issues(architecture: dict[str, Any]) -> list[str]:
    owners: dict[str, str] = {}
    issues: list[str] = []
    for module in architecture.get("modules", []):
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("id") or "")
        for raw_path in module.get("target_files", []):
            path = str(raw_path).replace("\\", "/").strip("/")
            if not path:
                continue
            previous = owners.get(path)
            if previous and previous != module_id:
                issues.append(
                    f"目标文件 `{path}` 同时由模块 `{previous}` 和 `{module_id}` 拥有。"
                )
            else:
                owners[path] = module_id
    return issues


def _unittest_issues(
    project_root: Path,
    architecture: dict[str, Any],
    command: str,
) -> list[str]:
    try:
        parts = shlex.split(command, posix=False)
    except ValueError as exc:
        return [f"验证命令无法解析：`{command}`：{exc}"]
    normalized = [part.strip('"') for part in parts]
    if "unittest" not in normalized or "discover" not in normalized:
        return []
    if "-t" not in normalized:
        return []
    top_index = normalized.index("-t")
    if top_index + 1 >= len(normalized) or normalized[top_index + 1] not in {".", "./"}:
        return []
    planned = {
        str(path).replace("\\", "/").strip("/")
        for module in architecture.get("modules", [])
        if isinstance(module, dict)
        for path in module.get("target_files", [])
    }
    if (project_root / "tests" / "__init__.py").exists() or "tests/__init__.py" in planned:
        return []
    return [
        "验证命令使用 `python -m unittest discover -s tests -t .`，但当前脚手架没有 "
        "`tests/__init__.py`；请删除 `-t .`，或把测试包初始化文件纳入架构 target_files。"
    ]


def architecture_preflight_issues(
    project_root: Path,
    architecture: dict[str, Any],
) -> list[str]:
    """Return all blocking architecture/scaffold integration issues."""
    issues = _ownership_issues(architecture)
    for command in _commands(architecture):
        try:
            parts = shlex.split(command, posix=False)
        except ValueError:
            parts = []
        if not parts or Path(parts[0].strip('"')).name.lower() not in _COMMAND_NAMES:
            # Architecture verification fields are also used for human-readable
            # acceptance notes. Only command-shaped values get executable checks.
            continue
        try:
            validate_verification_command(command)
        except WorkspaceError as exc:
            issues.append(f"验证命令 `{command}` 不被允许：{exc}")
            continue
        issues.extend(_unittest_issues(project_root, architecture, command))
    return list(dict.fromkeys(issues))


def require_architecture_preflight(
    project_root: Path,
    architecture: dict[str, Any],
) -> None:
    """Raise one actionable error when the architecture cannot be executed."""
    issues = architecture_preflight_issues(project_root, architecture)
    if issues:
        raise WorkspaceError(
            "架构与当前脚手架不一致，请先修订后再确认：\n- "
            + "\n- ".join(issues[:12])
        )


def documented_command_issues(project_root: Path) -> list[str]:
    """Return stale executable commands left in README or navigation documents."""
    if (project_root / "tests" / "__init__.py").exists():
        return []
    issues: list[str] = []
    candidates = [project_root / "README.md"]
    candidates.extend(
        path
        for path in project_root.rglob("AI_ARCH.md")
        if ".docuagent" not in path.parts
    )
    for path in candidates:
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for number, line in enumerate(lines, start=1):
            if (
                "unittest" in line
                and "discover" in line
                and "-t ." in line
            ):
                issues.append(
                    f"`{path.relative_to(project_root).as_posix()}` 第 {number} 行仍使用 "
                    "`-t .`，但项目没有 `tests/__init__.py`。"
                )
    return issues


def require_documented_command_consistency(project_root: Path) -> None:
    """Reject a completed docs pass that leaves an unexecutable command."""
    issues = documented_command_issues(project_root)
    if issues:
        raise WorkspaceError(
            "文档中的验证命令不可执行：\n- " + "\n- ".join(issues[:12])
        )
