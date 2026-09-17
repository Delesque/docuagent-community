"""Bounded read-only tool loop for sub-agents.

The model may call a small set of read-only tools to understand the workspace before
returning complete file content. Writes are never allowed here: the final patch still
goes through the existing review/apply/verify gate.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import skills
import plugins
import subprocess
import token_usage
from pathlib import Path
from typing import Any, Callable

from agents import build_agent_context
from command_policy import validate_verification_command
from llm_client import (
    ProviderConfig,
    ToolCallingNotSupported,
    call_model_chat,
    call_model_json,
    openai_tool_schemas,
)
from capabilities import (
    READ_ARCHITECTURE,
    READ_PROJECT,
    RUN_VERIFICATION,
    WRITE_SANDBOX,
    require_capability,
)
from core import WorkspaceError
from gitops import git_status
from sandbox import run_verification as sandbox_run_verification, changed_sandbox_files
from standards import IMPLEMENTATION_SAFETY_RULES
from workspace import atomic_write_json, atomic_write_text, read_contracts, read_json, read_node_attachments

REPEAT_REMINDER_THRESHOLDS = (3, 5, 8)
REPEAT_REMINDER_ARG_PREVIEW = 500
REPEAT_TOOL_HARD_LIMIT = 12
# Tools that count as "writing progress". Anything else a round calls is
# read-only exploration; too many of those in a row means the model is stalling.
WRITE_TOOLS = frozenset({"write_file", "edit_file"})
READ_ONLY_ROUND_THRESHOLDS = (5, 7, 10)
READ_ONLY_ROUND_HARD_LIMIT = 14
NATIVE_DETAIL_TOOL_TURNS = 3
TOOL_HISTORY_DETAIL_TURNS = 2
STEP_LOG_MAX_ITEMS = 40
CHECKPOINT_DETAIL_TURNS = 3
STEP_LOG_MARKER = "操作日志（精简）："
READ_LIMIT = 60_000
RESULT_LIMIT = 60_000
MAX_SEARCH_RESULTS = 500
BLOCKED_PARTS = {".git", ".docuagent"}
SEARCH_EXCLUDED_DIRS = {".git", ".docuagent", "node_modules", "__pycache__", ".venv", ".backups", ".recovery"}

# What happens to sandbox writes once the task finishes. The two modes place
# different obligations on the agent, so they get different words: in auto mode a
# bad change is already in the project by the time anyone looks at it, while in
# review mode the agent is producing a proposal that someone else will check.
APPLY_POLICY_PROMPT = {
    "review": (
        "审阅模式：你的改动会作为补丁上交人工审阅，审阅通过后才会落到真实项目。"
        "你不需要也不能把改动应用出去——把文件写完整、写正确即可。"
    ),
    "auto": (
        "自动应用模式：你的改动在验证通过后会自动落到真实项目，没有人工审阅这一步。"
        "因此收尾前必须先用 run_verification 验证改动完整可运行——"
        "一旦自动落盘，错误就已经在用户的代码里了。"
    ),
}


def agent_tool_loop_prompt(apply_mode: str) -> str:
    """The tool-loop system prompt for one apply mode.

    Callers that know the project's apply mode (task generation, micro tasks)
    must pass it here. The public constant below is the review variant and only
    serves as a safe fallback for callers without a project context.
    """
    policy = APPLY_POLICY_PROMPT.get(apply_mode) or APPLY_POLICY_PROMPT["review"]
    return _AGENT_TOOL_LOOP_PROMPT_TEMPLATE.replace("{apply_policy}", policy)


_AGENT_TOOL_LOOP_PROMPT_TEMPLATE = """You are the Implementation Agent for one bounded task in a project.
Use the architecture's language and runtime, not a hard-coded language assumption.

You receive only the project overview, project standards, project recipes, a user-profile
excerpt, the module contract, unresolved node attachments, your own past work log, and
recent error memory. Chat history is never sent.

You may write your reasoning, questions, and conclusions in natural language. Use the
available tool interface only when you need to read or write files; do not emulate tool
calls in prose.

Available tools:
- read_file(path): read a UTF-8 text file inside the project.
- list_dir(path): list entries under a project directory.
- search_files(pattern, path): find lines containing a literal string under a directory.
- read_ai_arch(path): read the root-to-target-directory AI_ARCH.md chain. This is a
  HARD GATE: read_file / write_file / edit_file in a directory are rejected until
  you have called read_ai_arch for it, so call it first for every directory you
  touch - otherwise your writes are discarded and you burn a turn retrying.
- read_architecture(): read your module plus upstream/downstream neighbors.
- search_reuse(term): search the Project Contract Registry for existing exports, commands,
  shared-kernel symbols, recipes, and vocabulary before defining anything new.
- read_module_interface(module_id): read a dependency module's contract and public files.
- read_attachments(module_id): read node attachments for a module.
- git_status(): read the project's git status.
- task_status(): show the current task and verification commands.
- read_ui_layout(): read the structured DOM layout context attached to this task.
- run_verification(command): run one of the task's declared verification commands.
- write_file(path, content): write a complete file inside the trial sandbox.
- edit_file(path, old_text, new_text): replace exact text inside a sandbox file.

You work in a trial sandbox. `write_file` and `edit_file` only change sandbox files,
never the real project. To finish the task, call `write_file`/`edit_file`
for every file that must be created or changed, then reply in natural language with a
short summary. Do not paste final file contents into prose; the system reads them from
the sandbox.

{apply_policy}

Contract notes:
- `contract_view.expected_public_api` lists symbols YOUR module was declared to provide to
  other modules. Implement exactly those names — other agents are coded against them.
- In contract entries, `status: "proposed"` means a promise that no code implements yet:
  do not rely on it as if it existed. `"active"` means real code already uses it. If a
  declared interface cannot be satisfied, hand off with the discrepancy instead of
  inventing a different name.

If the request leaves this module or would require an architecture change, do not
implement it. Say clearly that it needs a handoff, or use the `needs_handoff` signal.

The system may append `repeat_reminders` after your tool results. They are advisory:
when you see one, stop and read the latest result before repeating the same call.

The system also maintains a compact `step_log` of operation names and outcomes.
It is a reminder, not a replacement for exact file content: use read tools when you
need the current text of a file or a full error trace.
""" + "\n\n" + IMPLEMENTATION_SAFETY_RULES

# Safe fallback for callers that have no project context to read the apply mode
# from. Review is the default everywhere: nothing reaches the project unapplied.
AGENT_TOOL_LOOP_PROMPT = agent_tool_loop_prompt("review")


LEGACY_TOOL_CALL_FORMAT = """
This provider has no native function-calling API. When you need a tool, append exactly
one JSON block at the end of your reply:

```json
{"thinking":"...","tool_calls":[{"tool":"write_file","args":{"path":"src/core.js","content":"..."}}]}
```

A structured handoff still looks like:
{"needs_handoff":"short explanation"}
"""

TOOL_DESCRIPTIONS = [
    {
        "name": "read_file",
        "description": "Read a UTF-8 text file inside the project (max 20000 chars).",
        "args": {"path": "relative project path"},
    },
    {
        "name": "list_dir",
        "description": "List files and directories under a project directory.",
        "args": {"path": "relative project path, empty for root"},
    },
    {
        "name": "search_files",
        "description": "Find lines containing a literal string (max 200 matches) under a directory.",
        "args": {"pattern": "literal substring to find", "path": "relative project dir, empty for root"},
    },
    {
        "name": "read_ai_arch",
        "description": "Read the root-to-target-directory AI_ARCH.md navigation chain.",
        "args": {"path": "relative target file or directory, optional"},
    },
    {
        "name": "read_architecture",
        "description": "Read your module plus upstream/downstream neighbors.",
        "args": {},
    },
    {
        "name": "search_reuse",
        "description": "Search the Project Contract Registry before defining a new symbol, command, constant, or variable.",
        "args": {"term": "symbol/command/term to search"},
    },
    {
        "name": "read_module_interface",
        "description": "Read a dependency module's contract and public files.",
        "args": {"module_id": "architecture module id"},
    },
    {
        "name": "read_attachments",
        "description": "Read node attachments for a module.",
        "args": {"module_id": "architecture module id"},
    },
    {
        "name": "git_status",
        "description": "Read the project's git status.",
        "args": {},
    },
    {
        "name": "task_status",
        "description": "Show the current task summary, target files, and verification commands.",
        "args": {},
    },
    {
        "name": "read_ui_layout",
        "description": "Read the structured UI layout context (selected element, layout delta, ancestors) attached to this task.",
        "args": {},
    },
    {
        "name": "run_verification",
        "description": "Run one of the task's declared verification commands.",
        "args": {"command": "exact verification command"},
    },
    {
        "name": "write_file",
        "description": "Write a file inside the trial sandbox.",
        "args": {"path": "relative project path", "content": "complete file content"},
    },
    {
        "name": "edit_file",
        "description": "Replace exact text inside a sandbox file.",
        "args": {"path": "relative project path", "old_text": "exact existing text", "new_text": "replacement text"},
    },
]


class AgentLoopCancelled(Exception):
    """Raised when the caller cancels the tool loop between model turns."""


def _safe_relative_path(path: str) -> str:
    raw = str(path or "").strip().replace("\\", "/")
    if raw.startswith("/") or ".." in Path(raw).parts:
        raise WorkspaceError("工具路径必须相对项目根目录。")
    return raw


def _resolve_in_project(project_root: Path, path: str) -> Path:
    return _resolve_in_root(project_root, path)


def _resolve_in_root(root: Path, path: str) -> Path:
    raw = _safe_relative_path(path)
    target = (root / raw).resolve()
    root = root.resolve()
    if not target.is_relative_to(root):
        raise WorkspaceError("工具路径超出项目目录。")
    if any(part in BLOCKED_PARTS for part in target.relative_to(root).parts):
        raise WorkspaceError("该路径受保护，不允许读取。")
    if target.name == ".env":
        raise WorkspaceError("密钥文件不允许读取。")
    return target


def _in_allowed_scope(
    raw: str,
    agent_context: dict[str, Any],
    field: str = "allowed_read_scope",
    allow_root_file: bool = False,
    allow_root_dir: bool = False,
) -> bool:
    """Scope check; `allow_root_file` / `allow_root_dir` open READ-ONLY views of
    project-root facts (package.json, README.md, the root listing) that every
    sub-agent may consult — knowing what the project declares is not the same
    as being allowed to write there. Never applies to the write field, and
    dotfiles (.env, .docuagent, …) stay out."""
    path = str(raw or "").strip().replace("\\", "/").strip("/")
    if _in_scope(raw, agent_context, field):
        return True
    if field != "allowed_read_scope":
        return False
    if allow_root_file and path and "/" not in path and not path.startswith("."):
        return True
    if allow_root_dir and path in {"", "."}:
        return True
    return False


def _in_scope(raw: str, agent_context: dict[str, Any], field: str) -> bool:
    path = str(raw or "").strip().replace("\\", "/").strip("/")
    allowed = {
        str(item).strip().replace("\\", "/").strip("/")
        for item in (agent_context.get(field) or [])
        if str(item).strip()
    }
    if path in allowed:
        return True
    return any(
        item and (path == item or path.startswith(item.rstrip("/") + "/"))
        for item in allowed
    )


def _in_write_scope(raw: str, agent_context: dict[str, Any]) -> bool:
    if agent_context.get("allowed_write_scope"):
        return _in_scope(raw, agent_context, "allowed_write_scope")
    return _in_scope(raw, agent_context, "allowed_read_scope")


def _navigation_paths_for(
    raw: str,
    agent_context: dict[str, Any],
) -> list[str]:
    navigation = agent_context.get("navigation_documents")
    if not isinstance(navigation, dict):
        navigation = agent_context.get("directory_documents")
    if not isinstance(navigation, dict):
        return []
    required = navigation.get("required_paths") or []
    target = str(raw or "").replace("\\", "/").strip("/")
    paths: list[str] = []
    for item in required:
        document = str(item or "").replace("\\", "/").strip("/")
        if not document or not document.lower().endswith("/ai_arch.md") and document.lower() != "ai_arch.md":
            continue
        directory = document.rsplit("/", 1)[0] if "/" in document else ""
        if not directory or target == directory or target.startswith(directory + "/"):
            paths.append(document)
    return sorted(set(paths), key=lambda item: (item.count("/"), item))


def _mark_navigation_read(
    agent_context: dict[str, Any],
    paths: list[str],
) -> None:
    navigation = agent_context.get("navigation_documents")
    if not isinstance(navigation, dict):
        navigation = agent_context.get("directory_documents")
    if not isinstance(navigation, dict):
        return
    read_paths = {
        str(item).replace("\\", "/").strip("/")
        for item in (navigation.get("read_paths") or [])
        if str(item).strip()
    }
    read_paths.update(paths)
    navigation["read_paths"] = sorted(read_paths)


def _ensure_navigation_read(
    work_root: Path,
    raw: str,
    agent_context: dict[str, Any],
    operation: str,
) -> list[str]:
    required = _navigation_paths_for(raw, agent_context)
    navigation = agent_context.get("navigation_documents")
    if not isinstance(navigation, dict):
        navigation = agent_context.get("directory_documents")
    if not required or not isinstance(navigation, dict) or not navigation.get("enforced", False):
        return required

    required = [
        path for path in required
        if (_resolve_in_root(work_root, path)).is_file()
    ]
    # A new target directory has no formal document until the Doc Maintainer runs.
    # Existing ancestors remain mandatory; the absent child is not a write deadlock.
    if not required:
        return required
    read_paths = {
        str(item).replace("\\", "/").strip("/")
        for item in (navigation.get("read_paths") or [])
        if str(item).strip()
    }
    unread = [path for path in required if path not in read_paths]
    if unread:
        raise WorkspaceError(
            f"{operation} 前必须先调用 read_ai_arch 读取导航链：{', '.join(unread)}。"
        )
    return required


def _tail_with_notice(text: str, limit: int = RESULT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"[tool output truncated: omitted {omitted} chars; showing the tail]\n{text[-limit:]}"

def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise WorkspaceError(f"读取文件失败：{exc}") from exc
    if len(text) > READ_LIMIT:
        return text[:READ_LIMIT].rstrip() + "\n[truncated]"
    return text


def _module_by_id(
    architecture: dict[str, Any],
    module_id: str,
) -> dict[str, Any]:
    modules = architecture.get("modules", []) if isinstance(architecture, dict) else []
    return next(
        (module for module in modules if isinstance(module, dict) and module.get("id") == module_id),
        {},
    )


def _focused_architecture(
    architecture: dict[str, Any],
    module_id: str,
) -> dict[str, Any]:
    modules = architecture.get("modules", []) if isinstance(architecture, dict) else []
    if not isinstance(modules, list):
        return {"modules": []}
    by_id = {
        str(module.get("id")): module
        for module in modules
        if isinstance(module, dict) and module.get("id")
    }
    selected = {module_id}
    changed = True
    while changed:
        changed = False
        for mid, module in by_id.items():
            if mid not in selected:
                continue
            for dep in (module.get("depends_on") or []):
                if dep in by_id and dep not in selected:
                    selected.add(dep)
                    changed = True
    for mid, module in by_id.items():
        if module_id in (module.get("depends_on") or []):
            selected.add(mid)
    return {
        "modules": [by_id[mid] for mid in selected if mid in by_id],
    }


def _module_interface(
    project_root: Path,
    architecture: dict[str, Any],
    module_id: str,
) -> dict[str, Any]:
    module = _module_by_id(architecture, module_id)
    if not module:
        raise WorkspaceError(f"架构中不存在模块：{module_id}")
    path = str(module.get("path") or "").strip()
    entries: list[dict[str, str]] = []
    if path:
        target = _resolve_in_root(project_root, path)
        if target.is_dir():
            for entry in sorted(target.iterdir(), key=lambda item: item.name):
                entries.append({
                    "name": entry.name,
                    "type": "dir" if entry.is_dir() else "file",
                })
    return {
        "id": module.get("id", module_id),
        "name": module.get("name", module_id),
        "path": path,
        "responsibility": module.get("responsibility", ""),
        "brief": module.get("brief", ""),
        "depends_on": module.get("depends_on", []),
        "needs_ui": bool(module.get("needs_ui")),
        "public_files": entries[:100],
    }


def _search_files(work_root: Path, pattern: str, start_dir: Path) -> dict[str, Any]:
    matches: list[dict[str, Any]] = []
    truncated = False
    for current, dirs, names in os.walk(start_dir):
        dirs[:] = [d for d in sorted(dirs) if d not in SEARCH_EXCLUDED_DIRS]
        current_path = Path(current)
        for name in sorted(names):
            path = current_path / name
            if any(part in SEARCH_EXCLUDED_DIRS for part in path.relative_to(work_root).parts):
                continue
            if len(matches) >= MAX_SEARCH_RESULTS:
                truncated = True
                break
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    matches.append({
                        "path": path.relative_to(work_root).as_posix(),
                        "line": lineno,
                        "text": line.strip()[:200],
                    })
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        truncated = True
                        break
        if truncated:
            break
    return {"pattern": pattern, "matches": matches, "truncated": truncated, "truncated_notice": "结果超过 500 条，当前结果不完整；请继续缩小 path 或 pattern 探索。" if truncated else ""}


def _search_reuse(project_root: Path, args: dict[str, Any]) -> dict[str, Any]:
    """Deterministic Project Contract Registry search.

    Search scope: module exports, commands, shared_kernel, recipes, and vocabulary.
    """
    term = str(args.get("term") or "").strip()
    if not term:
        raise WorkspaceError("search_reuse 需要 term 参数。")
    contracts = read_contracts(project_root) or {}
    term_lower = term.lower()
    tokens = set(re.findall(r"[a-z0-9_]+", term_lower))
    # Prefer literal substrings; fall back to shared tokens.
    def matches(text: str) -> bool:
        text_lower = str(text or "").lower()
        if not text_lower:
            return False
        if term_lower in text_lower or text_lower in term_lower:
            return True
        return bool(tokens & set(re.findall(r"[a-z0-9_]+", text_lower)))

    exports: list[dict[str, Any]] = []
    for module in contracts.get("modules", []):
        if not isinstance(module, dict):
            continue
        module_exports = module.get("exports")
        if not isinstance(module_exports, list):
            continue
        for entry in module_exports:
            if not isinstance(entry, dict):
                continue
            if matches(entry.get("symbol") or "") or matches(entry.get("signature") or ""):
                exports.append({
                    "module_id": module.get("id", ""),
                    **entry,
                })

    commands = [
        entry for entry in contracts.get("commands", [])
        if isinstance(entry, dict)
        and (matches(entry.get("name") or "") or matches(entry.get("verb") or "")
             or matches(entry.get("resource") or ""))
    ]
    shared_kernel = [
        entry for entry in contracts.get("shared_kernel", [])
        if isinstance(entry, dict)
        and (matches(entry.get("symbol") or "") or matches(entry.get("why") or ""))
    ]
    recipes = [
        entry for entry in contracts.get("recipes", [])
        if isinstance(entry, dict)
        and (matches(entry.get("name") or "") or matches(entry.get("problem") or "")
             or matches(entry.get("solution") or ""))
    ]
    vocabulary = [
        entry for entry in contracts.get("vocabulary", [])
        if isinstance(entry, dict)
        and (matches(entry.get("term") or "") or matches(entry.get("forbidden_aliases") or ""))
    ]
    data_schema = [
        entry for entry in contracts.get("data_schema", [])
        if isinstance(entry, dict)
        and (matches(entry.get("name") or "") or matches(entry.get("invariants") or "")
             or matches(entry.get("owner") or ""))
    ]
    config_policy = [
        entry for entry in contracts.get("config_policy", [])
        if isinstance(entry, dict)
        and (matches(entry.get("name") or "") or matches(entry.get("default") or "")
             or matches(entry.get("owner") or ""))
    ]

    return {
        "term": term,
        "exports": exports,
        "commands": commands,
        "shared_kernel": shared_kernel,
        "recipes": recipes,
        "vocabulary": vocabulary,
        "data_schema": data_schema,
        "config_policy": config_policy,
        "total": (
            len(exports) + len(commands) + len(shared_kernel) + len(recipes)
            + len(vocabulary) + len(data_schema) + len(config_policy)
        ),
    }


TOOL_CAPABILITIES = {
    "read_file": READ_PROJECT,
    "list_dir": READ_PROJECT,
    "search_files": READ_PROJECT,
    "read_ai_arch": READ_ARCHITECTURE,
    "read_architecture": READ_ARCHITECTURE,
    "search_reuse": READ_ARCHITECTURE,
    "read_module_interface": READ_ARCHITECTURE,
    "read_attachments": READ_ARCHITECTURE,
    "git_status": READ_PROJECT,
    "task_status": READ_PROJECT,
    "read_ui_layout": READ_PROJECT,
    "write_file": WRITE_SANDBOX,
    "edit_file": WRITE_SANDBOX,
    "run_verification": RUN_VERIFICATION,
}


# ---- Optional, capability-gated tools contributed by isolated packages ------
# Packages such as `codeintel` register tools here at import time. The core never
# imports those packages back, so codeintel stays a fully isolated package (see
# CODE_INTELLIGENCE_ROADMAP §1/§5). A registered tool is only visible to a sub-agent
# when its required capability is present in the agent context (explicit opt-in).
EXTERNAL_TOOL_HANDLERS: dict[str, Callable] = {}
EXTERNAL_TOOL_DESCRIPTIONS: list[dict] = []
EXTERNAL_TOOL_CAPABILITIES: dict[str, str] = {}
EXTERNAL_TOOL_PROMPT: list[dict] = []


def register_external_tool(
    name: str,
    capability: str,
    handler: Callable[[Path, str, dict[str, Any], dict[str, Any]], dict[str, Any]],
    description: dict[str, Any],
    prompt_line: str = "",
) -> None:
    """Register an optional tool provided by an isolated package.

    Called by packages such as codeintel at import time. The core only consults
    these registries; it never imports the provider, keeping the seam one-way.
    Idempotent: a second registration of the same tool name is ignored.
    """
    if name in EXTERNAL_TOOL_HANDLERS:
        return
    EXTERNAL_TOOL_HANDLERS[name] = handler
    EXTERNAL_TOOL_DESCRIPTIONS.append(description)
    EXTERNAL_TOOL_CAPABILITIES[name] = capability
    if prompt_line:
        EXTERNAL_TOOL_PROMPT.append({"capability": capability, "line": prompt_line})


def _visible_external_tools(agent_context: dict[str, Any]) -> list[dict[str, Any]]:
    declared = set(agent_context.get("capabilities") or [])
    return [
        description for description in EXTERNAL_TOOL_DESCRIPTIONS
        if EXTERNAL_TOOL_CAPABILITIES.get(description.get("name", "")) in declared
    ]


def _external_prompt_addendum(agent_context: dict[str, Any]) -> str:
    declared = set(agent_context.get("capabilities") or [])
    lines = [
        item["line"] for item in EXTERNAL_TOOL_PROMPT
        if item.get("capability") in declared and item.get("line")
    ]
    return "\n" + "\n".join(lines) if lines else ""


def execute_tool(
    project_root: Path,
    task: dict[str, Any],
    tool_call: dict[str, Any],
    sandbox: dict[str, Any] | None = None,
    agent_context: dict[str, Any] | None = None,
    architecture: dict[str, Any] | None = None,
) -> dict[str, Any]:
    tool = str(tool_call.get("tool") or "").strip()
    args = tool_call.get("args")
    if not isinstance(args, dict):
        args = {}
    context = agent_context or {}
    arch = architecture or {}
    work_root = Path(sandbox["path"]) if sandbox else project_root.resolve()
    required_capability = TOOL_CAPABILITIES.get(tool) or EXTERNAL_TOOL_CAPABILITIES.get(tool)
    if required_capability:
        require_capability(context, required_capability)

    if tool == "read_file":
        raw = str(args.get("path") or "")
        if not _in_allowed_scope(raw, context, allow_root_file=True):
            raise WorkspaceError("文件不在当前子 Agent 允许读取范围内。")
        path = _resolve_in_root(work_root, raw)
        if not path.is_file():
            raise WorkspaceError(f"文件不存在：{raw}")
        if path.name == "AI_ARCH.md":
            _mark_navigation_read(context, [raw.replace("\\", "/").strip("/")])
        else:
            _ensure_navigation_read(work_root, raw, context, "读取源码")
        return {"path": str(path.relative_to(work_root)).replace("\\", "/"),
                "content": _read_text(path)}

    if tool == "list_dir":
        raw = str(args.get("path") or "").strip()
        if not _in_allowed_scope(raw or ".", context, allow_root_dir=True):
            raise WorkspaceError("目录不在当前子 Agent 允许读取范围内。")
        target = _resolve_in_root(work_root, raw) if raw else work_root.resolve()
        if not target.is_dir():
            raise WorkspaceError(f"目录不存在：{raw}")
        entries = []
        for entry in sorted(target.iterdir(), key=lambda item: item.name):
            entries.append({
                "name": entry.name,
                "type": "dir" if entry.is_dir() else "file",
            })
        truncated = len(entries) > 200
        return {"path": str(target.relative_to(work_root)).replace("\\", "/") or ".",
                "entries": entries[:200],
                "truncated": truncated,
                "truncated_notice": "目录结果不完整，请继续用更窄路径探索。" if truncated else ""}

    if tool == "search_files":
        raw = str(args.get("path") or "").strip()
        pattern = str(args.get("pattern") or "").strip()
        if not pattern:
            raise WorkspaceError("search_files 需要 pattern。")
        if not _in_allowed_scope(raw or ".", context):
            raise WorkspaceError("搜索目录不在当前子 Agent 允许范围内。")
        start = _resolve_in_root(work_root, raw) if raw else work_root.resolve()
        if not start.is_dir():
            raise WorkspaceError(f"目录不存在：{raw}")
        return _search_files(work_root, pattern, start)

    if tool == "read_ai_arch":
        target_hint = str(args.get("path") or "").strip()
        if not target_hint:
            target_hint = str((context.get("module_contract") or {}).get("path") or "")
            if not target_hint:
                targets = task.get("target_files") or []
                target_hint = str(targets[0]) if targets else ""
        paths = [
            path for path in _navigation_paths_for(target_hint, context)
            if _resolve_in_root(work_root, path).is_file()
        ]
        documents: list[dict[str, str]] = []
        for document in paths:
            target = _resolve_in_root(work_root, document)
            documents.append({
                "path": document,
                "content": _read_text(target),
            })
        _mark_navigation_read(context, paths)
        return {
            "path": target_hint or ".",
            "documents": documents,
            "required_paths": paths,
            "content": documents[0]["content"] if documents else "",
        }

    if tool == "read_architecture":
        return _focused_architecture(
            arch,
            str(context.get("module_contract", {}).get("id") or task.get("module_id") or ""),
        )

    if tool == "search_reuse":
        return _search_reuse(project_root, args)

    if tool == "read_module_interface":
        module_id = str(args.get("module_id") or "").strip()
        own = str(context.get("module_contract", {}).get("id") or task.get("module_id") or "")
        own_module = _module_by_id(arch, own)
        allowed = set(own_module.get("depends_on") or [])
        allowed.add(own)
        if module_id not in allowed:
            raise WorkspaceError("只能读取依赖模块的接口。")
        return _module_interface(project_root, arch, module_id)

    if tool == "read_attachments":
        module_id = str(args.get("module_id") or "").strip()
        own = str(context.get("module_contract", {}).get("id") or task.get("module_id") or "")
        if module_id and module_id != own:
            raise WorkspaceError("只能读取当前子 Agent 自己的附件。")
        attachments = read_node_attachments(project_root).get(own, [])
        return {"module_id": own, "attachments": attachments[-20:]}

    if tool == "git_status":
        return git_status(project_root)

    if tool == "task_status":
        return {
            "id": task.get("id", ""),
            "module_id": task.get("module_id", ""),
            "summary": task.get("summary", ""),
            "target_files": task.get("target_files", []),
            "verification": task.get("verification", []),
            "status": task.get("status", ""),
            "has_ui_context": isinstance(task.get("ui_context"), dict),
        }

    if tool == "read_ui_layout":
        ui_context = task.get("ui_context")
        if not isinstance(ui_context, dict):
            raise WorkspaceError("该任务没有 UI 布局上下文。")
        return {"ui_context": ui_context}

    if tool == "write_file":
        if not sandbox:
            raise WorkspaceError("write_file 只能在沙箱内调用。")
        raw = str(args.get("path") or "")
        content = str(args.get("content") or "")
        if not _in_write_scope(raw, context):
            raise WorkspaceError("写入路径不在当前子 Agent 允许范围内。")
        _ensure_navigation_read(work_root, raw, context, "写入文件")
        target = _resolve_in_root(work_root, raw)
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, content)
        return {"path": raw, "written": len(content)}

    if tool == "edit_file":
        if not sandbox:
            raise WorkspaceError("edit_file 只能在沙箱内调用。")
        raw = str(args.get("path") or "")
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        if not old_text:
            raise WorkspaceError("edit_file 需要 old_text。")
        if not _in_write_scope(raw, context):
            raise WorkspaceError("编辑路径不在当前子 Agent 允许范围内。")
        _ensure_navigation_read(work_root, raw, context, "编辑文件")
        target = _resolve_in_root(work_root, raw)
        current = _read_text(target)
        if old_text not in current:
            raise WorkspaceError("old_text 在文件中不存在。")
        atomic_write_text(target, current.replace(old_text, new_text, 1))
        return {"path": raw, "edited": True}

    if tool == "run_verification":
        command = str(args.get("command") or "").strip()
        allowed = {str(item).strip() for item in (task.get("verification") or [])}
        if command not in allowed:
            raise WorkspaceError("只允许运行任务声明的验证命令。")
        # The declared list is model-authored, so membership alone proves nothing about
        # safety: the policy decides what may actually execute.
        argv = validate_verification_command(command)
        try:
            result = subprocess.run(
                argv,
                cwd=str(work_root),
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
                "stderr": str(exc),
                "returncode": -1,
            }
        return {
            "command": command,
            "stdout": _tail_with_notice(result.stdout),
            "stderr": _tail_with_notice(result.stderr),
            "stdout_truncated": len(result.stdout) > RESULT_LIMIT,
            "stderr_truncated": len(result.stderr) > RESULT_LIMIT,
            "returncode": result.returncode,
        }

    # Optional, capability-gated tools contributed by isolated packages (codeintel).
    handler = EXTERNAL_TOOL_HANDLERS.get(tool)
    if handler is not None:
        return handler(project_root, tool, args, context)

    return plugins.call_plugin_tool(project_root, tool, args)
    raise WorkspaceError(f"未知工具：{tool}")


def _model_input(
    project: dict[str, Any],
    task: dict[str, Any],
    agent_context: dict[str, Any],
    history: list[dict[str, Any]],
    extra_context: dict[str, Any] | None = None,
    tool_descriptions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # Stable blocks (three-layer memory / document tree / architecture contract)
    # go BEFORE the per-task block so the server-side prompt-cache prefix stays
    # as long as possible across tasks. Dict key order is the serialization order
    # (json.dumps has no sort_keys), which is exactly what prefix caching keys on.
    payload = {
        "project": {
            "name": project.get("name", ""),
            "slug": project.get("slug", ""),
            "root": project.get("root", ""),
        },
        "agent_context": agent_context,
        "task": {
            "id": task["id"],
            "module_id": task.get("module_id", ""),
            "summary": task.get("summary", ""),
            "target_files": task.get("target_files", []),
            "verification": task.get("verification", []),
            "ui_context": task.get("ui_context"),
        },
        "tools": tool_descriptions if tool_descriptions is not None else TOOL_DESCRIPTIONS,
        "tool_history": history[-TOOL_HISTORY_DETAIL_TURNS:],
    }
    if extra_context:
        payload["extra_context"] = extra_context
    return payload


_WRITE_NUDGE_TEXT = (
    "【必须动笔】你刚才用自然语言结束了本轮，但尚未调用 write_file/edit_file 写入任何文件，"
    "而本任务要求产出实现文件。不要再用文字描述计划或总结——"
    "直接调用 write_file 或 edit_file 完成实现，全部写完并运行验证通过后再收尾。"
)


def _nudge_to_write(
    agent_context: dict[str, Any],
    history: list[dict[str, Any]],
    native_messages: list[dict[str, Any]] | None = None,
    native_turn: int | None = None,
    native_content: str = "",
) -> bool:
    """Give a write-capable agent ONE forced-pen chance before failing it.

    Real-model pilot: the sub-agent ended in natural language with zero writes
    on round 4 — before the 5/7/10 read-only guard even fired. When the task has
    a write scope and no file was ever written, push a direct instruction and
    let the loop continue; only the second offence fails the task.
    """
    if not agent_context.get("allowed_write_scope"):
        return False
    if agent_context.get("_write_nudge_done"):
        return False
    agent_context["_write_nudge_done"] = True
    if native_messages is not None:
        history.append({
            "turn": native_turn,
            "assistant_content": native_content,
            "native_calls": [],
            "tool_calls": [],
            "results": [],
        })
        native_messages.append({"role": "user", "content": _WRITE_NUDGE_TEXT})
    else:
        history.append({"role": "user", "content": _WRITE_NUDGE_TEXT})
    return True


def _checkpoint_path(project_root: Path, task_id: str) -> Path:
    return project_root / ".docuagent" / "checkpoints" / f"{task_id}.json"


def save_loop_checkpoint(
    project_root: Path, task_id: str, history: list[dict[str, Any]],
    sandbox: dict[str, Any] | None = None,
) -> None:
    """Persist a resumable, bounded tool-loop checkpoint.

    The most recent turns retain exact tool results for continuation; the older
    exploration is represented by a bounded operation log and the written-file list.
    """
    path = _checkpoint_path(project_root, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    step_log: list[str] = []
    written_files: list[str] = []
    for turn in history:
        for call, result in zip(turn.get("tool_calls", []), turn.get("results", [])):
            if not isinstance(call, dict) or not isinstance(result, dict):
                continue
            output = result.get("result", result)
            if isinstance(output, dict):
                _turn_step_log(step_log, str(call.get("tool") or ""), output)
                if call.get("tool") in {"write_file", "edit_file"} and output.get("ok"):
                    path_value = str(output.get("path") or call.get("args", {}).get("path") or "")
                    if path_value and path_value not in written_files:
                        written_files.append(path_value)
    previous = read_json(path) or {}
    if not history or history[0].get("turn", 1) <= 1:
        previous = {}
    written_files = list(dict.fromkeys(previous.get("written_files", []) + written_files))
    atomic_write_json(path, {
        "task_id": task_id,
        "history": history[-CHECKPOINT_DETAIL_TURNS:],
        "step_log": (previous.get("step_log", []) + step_log)[-STEP_LOG_MAX_ITEMS:],
        "written_files": written_files[-200:],
        "turn_count": max((turn.get("turn", 0) for turn in history), default=len(history)),
        "sandbox_files": changed_sandbox_files(sandbox) if sandbox else {},
        "base_hashes": sandbox.get("base_hashes", {}) if sandbox else {},
        "updated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
    })


def restore_checkpoint_sandbox(project_root: Path, task_id: str, sandbox: dict[str, Any]) -> None:
    """Restore checkpoint writes only into a fresh sandbox, preserving conflict checks."""
    checkpoint = read_json(_checkpoint_path(project_root, task_id)) or {}
    changes = checkpoint.get("sandbox_files", {})
    baseline = checkpoint.get("base_hashes", {})
    if checkpoint.get("written_files") and not changes:
        raise WorkspaceError("旧检查点未保存沙箱文件，请重新生成任务。")
    # All previously observed source must still match; never continue from stale reads.
    for relative, digest in baseline.items():
        if sandbox.get("base_hashes", {}).get(relative) != digest:
            raise WorkspaceError(f"检查点生成后项目文件已变化：{relative}，请重新生成。")
    pending = []
    root = Path(sandbox["path"]).resolve()
    for relative, content in changes.items():
        target = _resolve_in_root(root, relative)
        current = sandbox.get("base_hashes", {}).get(relative)
        if current != baseline.get(relative):
            raise WorkspaceError(f"检查点目标已变化：{relative}，请重新生成。")
        pending.append((target, content))
    for target, content in pending:
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(target, content)


def load_loop_checkpoint(project_root: Path, task_id: str) -> list[dict[str, Any]]:
    raw = read_json(_checkpoint_path(project_root, task_id))
    history = raw.get("history") if isinstance(raw, dict) else None
    return history if isinstance(history, list) else []


def has_loop_checkpoint(project_root: Path, task_id: str) -> bool:
    return bool(load_loop_checkpoint(project_root, task_id))


def clear_loop_checkpoint(project_root: Path, task_id: str) -> None:
    path = _checkpoint_path(project_root, task_id)
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def _call_loop_model(
    model_call: Callable[..., dict[str, Any]],
    provider: ProviderConfig,
    prompt: str,
    model_input: dict[str, Any],
    timeout: int,
) -> dict[str, Any]:
    """Ask the model for one turn, preferring the prose-tolerant protocol.

    The production model call accepts `allow_prose=True`. Test doubles and older
    callables may not; fall back to the strict call only when that exact keyword is
    rejected, so unrelated TypeErrors still propagate.
    """
    try:
        return model_call(
            provider,
            prompt,
            model_input,
            timeout=timeout,
            allow_prose=True,
        )
    except TypeError as exc:
        if "allow_prose" not in str(exc):
            raise
        return model_call(provider, prompt, model_input, timeout=timeout)


def _canonical_tool_args(args: Any) -> str:
    try:
        return json.dumps(args or {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(args)


def _read_only_round_reminders(history: list[dict[str, Any]]) -> list[str]:
    """Escalate when the agent keeps reading and never writes.

    Real-model pilots showed the sub-agent looping on list_dir/read_file/
    task_status for ten-plus rounds without ever calling a write tool, although
    a direct tool test proved it writes fine. Thresholds 5/7/10 get steadily
    sterner: stop reading, call a write tool, or the round ends failed.
    """
    read_only = _read_only_round_count(history)
    if read_only not in READ_ONLY_ROUND_THRESHOLDS:
        return []
    if read_only == READ_ONLY_ROUND_THRESHOLDS[0]:
        return [(
            f"你已连续 {read_only} 轮只读取、没有写入任何文件。不要再继续读取了："
            "现有信息已经足够实现本任务。请直接开始实现并调用 write_file 或 edit_file。"
        )]
    if read_only == READ_ONLY_ROUND_THRESHOLDS[1]:
        return [(
            f"你已连续 {read_only} 轮只读。现在必须调用 write_file 或 edit_file 写入实现；"
            "如果存在无法自行解决的阻碍，请明确说明原因并结束本轮。"
        )]
    return [(
        f"禁止再调用任何只读工具。你已连续 {read_only} 轮只读，本轮必须调用 "
        "write_file 或 edit_file 完成实现；否则任务将判为失败。"
    )]


def _read_only_round_count(history: list[dict[str, Any]]) -> int:
    """Count only the current suffix of read-only tool rounds."""
    read_only = 0
    for turn in reversed(history):
        calls = [call for call in turn.get("tool_calls", []) if isinstance(call, dict)]
        tools = [str(call.get("tool") or "") for call in calls]
        if not tools or any(tool in WRITE_TOOLS for tool in tools):
            break
        read_only += 1
    return read_only


def _repeat_tool_chain(history: list[dict[str, Any]]) -> tuple[str, str, int]:
    """Return the current suffix of one exactly repeated tool call."""
    chain_tool = ""
    chain_args = ""
    chain_count = 0
    for turn in history:
        calls = turn.get("tool_calls", [])
        results = turn.get("results", [])
        for call, _result in zip(calls, results):
            if not isinstance(call, dict):
                continue
            tool = str(call.get("tool") or "")
            canonical = _canonical_tool_args(call.get("args"))
            if tool == chain_tool and canonical == chain_args:
                chain_count += 1
            else:
                chain_tool = tool
                chain_args = canonical
                chain_count = 1
    return chain_tool, chain_args, chain_count


def _no_progress_violation(history: list[dict[str, Any]]) -> str | None:
    """Return a stop reason when the current execution suffix has no progress."""
    read_only = _read_only_round_count(history)
    if read_only >= READ_ONLY_ROUND_HARD_LIMIT:
        return (
            f"Agent 已连续 {read_only} 轮只读且没有写入、验证或状态推进，"
            "系统已停止本次执行以避免无限循环。检查点已保留，可修正上下文后恢复。"
        )
    tool, args, count = _repeat_tool_chain(history)
    if count >= REPEAT_TOOL_HARD_LIMIT:
        preview = args[:REPEAT_REMINDER_ARG_PREVIEW]
        return (
            f"Agent 连续 {count} 次重复调用 `{tool}` 且参数未变化：{preview}。"
            "系统已停止本次执行以避免继续消耗模型调用。"
        )
    return None


def _repeat_tool_reminders(history: list[dict[str, Any]]) -> list[str]:
    """DSH-style advisory guard: warn, never block, on exact repeated calls.

    DeepSeek Harness does not impose a turn budget; it detects chains of the
    exact same `(tool, canonical args)` call and injects escalating reminders
    at configured thresholds. The model remains free to retry deliberately.
    """
    reminders: list[str] = []
    chain_tool: str | None = None
    chain_args: str | None = None
    chain_count = 0
    for turn in history:
        calls = turn.get("tool_calls", [])
        results = turn.get("results", [])
        for call, result in zip(calls, results):
            if not isinstance(call, dict):
                continue
            tool = str(call.get("tool") or "")
            canonical = _canonical_tool_args(call.get("args"))
            if tool == chain_tool and canonical == chain_args:
                chain_count += 1
            else:
                chain_tool = tool
                chain_args = canonical
                chain_count = 1
            if chain_count not in REPEAT_REMINDER_THRESHOLDS:
                continue
            if chain_count == REPEAT_REMINDER_THRESHOLDS[0]:
                reminders.append(
                    "You are repeating the exact same tool call with identical arguments. "
                    "Carefully analyze the previous result before calling again: if the task "
                    "is not complete, try a different approach or different arguments instead "
                    "of repeating the call."
                )
            else:
                preview = chain_args[:REPEAT_REMINDER_ARG_PREVIEW]
                if len(chain_args) > REPEAT_REMINDER_ARG_PREVIEW:
                    preview += f"… (+{len(chain_args) - REPEAT_REMINDER_ARG_PREVIEW} more chars)"
                reminders.append(
                    f"Repeated tool call detected:\n"
                    f"- tool: {tool}\n"
                    f"- consecutive_calls: {chain_count}\n"
                    f"- arguments: {preview}\n"
                    "The repeated calls are not making progress. Do not call this tool with "
                    "these exact arguments again. Inspect the latest result and choose a "
                    "different action, different arguments, or finish the task if enough "
                    "evidence has been gathered."
                )
    return reminders


def _arg_preview(args: Any) -> str:
    if not isinstance(args, dict):
        return ""
    keys = {key: value for key, value in args.items() if key != "content"}
    text = json.dumps(keys, ensure_ascii=False, sort_keys=True, default=str)
    return text[:240]


def _tool_result_summary(tool: str, result: dict[str, Any]) -> str:
    """Compact per-step context: operation, outcome, and a short error tail.

    This is the context the sub-agent carries forward. Full file contents and
    command output are deliberately excluded; the model can re-read them with
    tools when it needs exact text.
    """
    ok = bool(result.get("ok"))
    if ok:
        if tool == "read_file":
            text = str(result.get("content") or "")
            return f"read_file {_arg_preview({'path': result.get('path')})} → ok（{len(text)} 字符）"
        if tool == "list_dir":
            return f"list_dir → ok（{len(result.get('entries') or [])} 项）"
        if tool == "search_files":
            return f"search_files → ok（{len(result.get('matches') or [])} 条匹配）"
        if tool == "write_file":
            return f"write_file {_arg_preview({'path': result.get('path')})} → ok（写入 {result.get('written', 0)} 字符）"
        if tool == "edit_file":
            return f"edit_file {_arg_preview({'path': result.get('path')})} → ok"
        if tool == "run_verification":
            return f"run_verification → returncode {result.get('returncode', 0)}"
        return f"{tool} → ok"
    error = str(result.get("error") or result.get("stderr") or "失败")[:220]
    return f"{tool} → 失败：{error}"


def _turn_step_log(step_log: list[str], tool: str, result: dict[str, Any]) -> None:
    step_log.append(_tool_result_summary(tool, result))
    if len(step_log) > STEP_LOG_MAX_ITEMS:
        del step_log[:-STEP_LOG_MAX_ITEMS]


def _refresh_native_step_log(
    messages: list[dict[str, Any]],
    step_log: list[str],
) -> None:
    """Keep exactly one compact step-log user message at the tail of native chat."""
    messages[:] = [
        message
        for message in messages
        if not (
            message.get("role") == "user"
            and str(message.get("content") or "").startswith(STEP_LOG_MARKER)
        )
    ]
    if step_log:
        messages.append({
            "role": "user",
            "content": STEP_LOG_MARKER + "\n" + "\n".join(step_log),
        })


def _prune_native_tool_messages(
    messages: list[dict[str, Any]],
    keep_turns: int = NATIVE_DETAIL_TOOL_TURNS,
) -> None:
    """Keep full tool results only for the last `keep_turns` tool turns.

    Older native tool messages are removed from chat. Their operation names and
    outcomes already live in `step_log`, which is injected separately. This is
    what makes compaction unnecessary for our sub-agent: no chat history is
    kept, only compact operation context plus a small recent full window.
    """
    assistant_indices = [
        index
        for index, message in enumerate(messages)
        if message.get("role") == "assistant" and message.get("tool_calls")
    ]
    if len(assistant_indices) <= keep_turns:
        return
    remove_until = assistant_indices[-keep_turns]
    # The prefix carries the task, contracts and scope, not disposable tool history.
    del messages[assistant_indices[0]:remove_until]


def _history_wrote_files(history: list[dict[str, Any]]) -> bool:
    return any(
        call.get("tool") in {"write_file", "edit_file"} and result.get("ok")
        for turn in history
        for call, result in zip(turn.get("tool_calls", []), turn.get("results", []))
    )


def _restore_navigation_reads(
    agent_context: dict[str, Any],
    history: list[dict[str, Any]],
) -> None:
    """Carry successful navigation reads across checkpoint resume."""
    for turn in history:
        calls = turn.get("tool_calls", [])
        results = turn.get("results", [])
        for call, result in zip(calls, results):
            if not isinstance(call, dict) or not isinstance(result, dict):
                continue
            output = result.get("result", result)
            if not isinstance(output, dict) or not output.get("ok", True):
                continue
            tool = str(call.get("tool") or "")
            if tool == "read_ai_arch":
                paths = output.get("required_paths") or []
                _mark_navigation_read(agent_context, [str(path) for path in paths])
            elif tool == "read_file":
                path = str(output.get("path") or call.get("args", {}).get("path") or "")
                if path.replace("\\", "/").lower().endswith("ai_arch.md"):
                    _mark_navigation_read(agent_context, [path])


def _stream_legacy_tool_loop(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task: dict[str, Any],
    sandbox: dict[str, Any] | None,
    max_turns: int | None,
    cancelled: Callable[[], bool] | None,
    call_model: Callable[..., dict[str, Any]] | None,
    system_prompt: str | None,
    extra_context: dict[str, Any] | None,
    resume: bool,
):
    """Prose-tolerant JSON-block protocol used before / without native tools."""
    agent_id = str(task.get("module_id") or task.get("id") or "")
    agent_context = build_agent_context(project_root, agent_id, architecture, task)
    try:
        available_skills = skills.list_skills(project_root)
        if available_skills:
            query = (
                f"{agent_context.get('module_contract', {}).get('responsibility', '')} "
                f"{task.get('summary', '')}"
            )
            agent_context["skills"] = skills.select_skills(
                provider,
                query,
                available_skills,
            )
    except Exception:
        # Keep the deterministic keyword match as a fallback.
        pass
    history: list[dict[str, Any]] = []
    step_log: list[str] = []
    wrote_files = False
    task_id = str(task.get("id") or "")
    if resume and task_id:
        history = load_loop_checkpoint(project_root, task_id)
        checkpoint = read_json(_checkpoint_path(project_root, task_id)) or {}
        wrote_files = bool(checkpoint.get("sandbox_files")) or bool(checkpoint.get("written_files")) or _history_wrote_files(history)
        step_log = list(checkpoint.get("step_log", []))
        _restore_navigation_reads(agent_context, history)
    model_call = call_model or call_model_json
    prompt = (system_prompt or AGENT_TOOL_LOOP_PROMPT) + LEGACY_TOOL_CALL_FORMAT
    addendum = _external_prompt_addendum(agent_context)
    if addendum:
        prompt = prompt + addendum
    tool_descriptions = (
        TOOL_DESCRIPTIONS
        + plugins.list_plugin_tools(project_root)
        + _visible_external_tools(agent_context)
    )

    turn = max((item.get("turn", 0) for item in history), default=0)
    while True:
        turn += 1
        if max_turns is not None and turn > max_turns:
            raise WorkspaceError(f"Agent 工具循环达到调试上限 {max_turns} 轮。")
        if cancelled and cancelled():
            raise AgentLoopCancelled()
        no_progress = _no_progress_violation(history)
        if no_progress:
            raise WorkspaceError(no_progress)
        model_input = _model_input(
            project,
            task,
            agent_context,
            history,
            extra_context,
            tool_descriptions,
        )
        repeat_reminders = _repeat_tool_reminders(history)
        if repeat_reminders or step_log:
            model_input = dict(model_input)
            if step_log:
                model_input["step_log"] = step_log[:]
            if repeat_reminders:
                model_input["repeat_reminders"] = repeat_reminders
        result = _call_loop_model(
            model_call,
            provider,
            prompt,
            model_input,
            300,
        )
        prose = result.get("__prose__")
        if isinstance(prose, str):
            thinking = str(result.get("__reasoning") or prose).strip()
            if thinking:
                yield {"type": "reasoning", "text": thinking[:4000]}
            if not wrote_files:
                if _nudge_to_write(agent_context, history):
                    continue
                raise WorkspaceError(
                    "子 Agent 用自然语言结束了本轮，但没有调用 write_file/edit_file "
                    "写入任何文件。"
                )
            if task_id:
                clear_loop_checkpoint(project_root, task_id)
            yield {
                "type": "done",
                "result": {
                    "done": True,
                    "summary": thinking,
                    "thinking": thinking,
                },
            }
            return

        thinking = str(result.get("thinking") or result.get("__reasoning") or "")
        if thinking:
            yield {"type": "reasoning", "text": thinking}

        calls = result.get("tool_calls")
        if isinstance(calls, list) and calls:
            results = []
            for call in calls:
                tool = str(call.get("tool") or "")
                try:
                    output = execute_tool(
                        project_root,
                        task,
                        call,
                        sandbox=sandbox,
                        agent_context=agent_context,
                        architecture=architecture,
                    )
                    output["ok"] = True
                    if tool in {"write_file", "edit_file"}:
                        wrote_files = True
                except Exception as exc:
                    # Tool failures are model-visible observations. Returning the
                    # error keeps the agent loop alive so the agent can adjust,
                    # retry, or choose a different tool.
                    output = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append({"tool": tool, "result": output})
                _turn_step_log(step_log, tool, output)
                yield {
                    "type": "tool_activity",
                    "text": f"[tool {turn}] {tool}",
                }
            history.append({
                "turn": turn,
                "tool_calls": calls,
                "results": results,
            })
            if task_id:
                save_loop_checkpoint(project_root, task_id, history, sandbox=sandbox)
            continue

        handoff = str(result.get("needs_handoff") or "").strip()
        has_legacy_files = bool(result.get("files") or result.get("patch"))
        if result.get("done") or handoff or has_legacy_files:
            if result.get("done") and not wrote_files and not has_legacy_files:
                if _nudge_to_write(agent_context, history):
                    continue
                raise WorkspaceError(
                    "子 Agent 声称完成，但没有调用 write_file/edit_file，"
                    "也没有返回文件内容。"
                )
            if task_id:
                clear_loop_checkpoint(project_root, task_id)
            yield {"type": "done", "result": result}
            return
        raise WorkspaceError("模型没有返回工具调用、自然语言完成信号或最终文件。")



def _native_assistant_message(
    content: str,
    tool_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": "assistant",
        "tool_calls": [
            {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": call["name"],
                    "arguments": json.dumps(call["args"], ensure_ascii=False),
                },
            }
            for call in tool_calls
        ],
    }
    if content:
        message["content"] = content
    return message


def _native_tool_result_message(
    call_id: str,
    output: dict[str, Any],
) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "content": json.dumps(output, ensure_ascii=False),
    }


def _native_calls_from_history_turn(turn: dict[str, Any]) -> list[dict[str, Any]]:
    raw_calls = turn.get("native_calls")
    if isinstance(raw_calls, list) and raw_calls:
        return [
            {
                "id": str(call.get("id") or f"call_{turn.get('turn', 0)}_{index}"),
                "name": str(call.get("name") or call.get("tool") or ""),
                "args": call.get("args") if isinstance(call.get("args"), dict) else {},
            }
            for index, call in enumerate(raw_calls)
            if isinstance(call, dict)
        ]
    return [
        {
            "id": f"call_{turn.get('turn', 0)}_{index}",
            "name": str(call.get("tool") or ""),
            "args": call.get("args") if isinstance(call.get("args"), dict) else {},
        }
        for index, call in enumerate(turn.get("tool_calls", []))
        if isinstance(call, dict)
    ]


def _native_history_messages(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for turn in history:
        calls = _native_calls_from_history_turn(turn)
        messages.append(_native_assistant_message(
            str(turn.get("assistant_content") or ""),
            calls,
        ))
        for call, result in zip(calls, turn.get("results", [])):
            if not call["name"]:
                continue
            messages.append(_native_tool_result_message(
                call["id"],
                result.get("result", {}) if isinstance(result, dict) else {},
            ))
    return messages


def _stream_native_tool_loop(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task: dict[str, Any],
    sandbox: dict[str, Any] | None,
    max_turns: int | None,
    cancelled: Callable[[], bool] | None,
    call_model: Callable[..., dict[str, Any]] | None,
    system_prompt: str | None,
    extra_context: dict[str, Any] | None,
    resume: bool,
):
    """Preferred OpenAI-native tool-calling loop.

    The model writes natural-language `content` every turn. Structured actions arrive
    as provider-enforced `tool_calls`; we execute them and feed results back as
    standard `role: tool` messages. Providers that reject `tools` fall back to the
    prose-tolerant JSON-block loop.
    """
    agent_id = str(task.get("module_id") or task.get("id") or "")
    agent_context = build_agent_context(project_root, agent_id, architecture, task)
    try:
        available_skills = skills.list_skills(project_root)
        if available_skills:
            query = (
                f"{agent_context.get('module_contract', {}).get('responsibility', '')} "
                f"{task.get('summary', '')}"
            )
            agent_context["skills"] = skills.select_skills(
                provider,
                query,
                available_skills,
            )
    except Exception:
        pass

    history: list[dict[str, Any]] = []
    step_log: list[str] = []
    wrote_files = False
    task_id = str(task.get("id") or "")
    if resume and task_id:
        history = load_loop_checkpoint(project_root, task_id)
        checkpoint = read_json(_checkpoint_path(project_root, task_id)) or {}
        wrote_files = bool(checkpoint.get("sandbox_files")) or bool(checkpoint.get("written_files")) or _history_wrote_files(history)
        step_log = list(checkpoint.get("step_log", []))
        _restore_navigation_reads(agent_context, history)
        for turn in history:
            for call, result in zip(turn.get("tool_calls", []), turn.get("results", [])):
                if isinstance(call, dict) and isinstance(result, dict):
                    _turn_step_log(
                        step_log,
                        str(call.get("tool") or ""),
                        result.get("result", {}),
                    )

    prompt = system_prompt or AGENT_TOOL_LOOP_PROMPT
    addendum = _external_prompt_addendum(agent_context)
    if addendum:
        prompt = prompt + addendum
    tool_descriptions = (
        TOOL_DESCRIPTIONS
        + plugins.list_plugin_tools(project_root)
        + _visible_external_tools(agent_context)
    )
    openai_tools = openai_tool_schemas(tool_descriptions)
    messages = [
        {
            "role": "system",
            "content": prompt,
        },
        {
            "role": "user",
            "content": json.dumps(
                _model_input(
                    project,
                    task,
                    agent_context,
                    [],
                    extra_context,
                    [],
                ),
                ensure_ascii=False,
            ),
        },
    ]
    if history:
        messages.extend(_native_history_messages(history))

    model_call = call_model or call_model_json
    turn = max((item.get("turn", 0) for item in history), default=0)
    while True:
        turn += 1
        if max_turns is not None and turn > max_turns:
            raise WorkspaceError(f"Agent 工具循环达到调试上限 {max_turns} 轮。")
        if cancelled and cancelled():
            raise AgentLoopCancelled()
        no_progress = _no_progress_violation(history)
        if no_progress:
            raise WorkspaceError(no_progress)
        _prune_native_tool_messages(messages)
        _refresh_native_step_log(messages, step_log)
        for reminder in _repeat_tool_reminders(history):
            messages.append({"role": "user", "content": reminder})
        for reminder in _read_only_round_reminders(history):
            messages.append({"role": "user", "content": reminder})
        try:
            response = call_model_chat(
                provider,
                messages,
                tools=openai_tools,
                timeout=300,
            )
        except ToolCallingNotSupported:
            yield from _stream_legacy_tool_loop(
                project_root,
                provider,
                architecture,
                project,
                task,
                sandbox,
                max_turns,
                cancelled,
                model_call,
                system_prompt,
                extra_context,
                bool(history),
            )
            return

        content = str(response.get("content") or "")
        reasoning = str(response.get("reasoning") or content).strip()
        if reasoning:
            yield {"type": "reasoning", "text": reasoning[:4000]}

        calls = response.get("tool_calls")
        if not isinstance(calls, list):
            calls = []
        if calls:
            valid_calls = [call for call in calls if call.get("name")]
            if valid_calls:
                messages.append(_native_assistant_message(content, valid_calls))
            results: list[dict[str, Any]] = []
            for call in valid_calls:
                tool = call["name"]
                output: dict[str, Any]
                try:
                    output = execute_tool(
                        project_root,
                        task,
                        {"tool": tool, "args": call["args"]},
                        sandbox=sandbox,
                        agent_context=agent_context,
                        architecture=architecture,
                    )
                    output["ok"] = True
                    if tool in {"write_file", "edit_file"}:
                        wrote_files = True
                except Exception as exc:
                    # Keep parity with the legacy loop: a failed tool call becomes
                    # a normal tool result instead of aborting the agent turn.
                    output = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                results.append({"tool": tool, "result": output})
                _turn_step_log(step_log, tool, output)
                messages.append(_native_tool_result_message(call["id"], output))
                yield {
                    "type": "tool_activity",
                    "text": f"[tool {turn}] {tool}",
                }
            history.append({
                "turn": turn,
                "assistant_content": content,
                "native_calls": valid_calls,
                "tool_calls": [
                    {"tool": call["name"], "args": call["args"]}
                    for call in valid_calls
                ],
                "results": results,
            })
            if task_id:
                save_loop_checkpoint(project_root, task_id, history, sandbox=sandbox)
            continue

        explicit_handoff: dict[str, Any] | None = None
        try:
            parsed_content = json.loads(content)
            if isinstance(parsed_content, dict) and str(parsed_content.get("needs_handoff") or "").strip():
                explicit_handoff = parsed_content
        except (TypeError, json.JSONDecodeError):
            pass
        if explicit_handoff is not None and not wrote_files:
            if task_id:
                clear_loop_checkpoint(project_root, task_id)
            yield {"type": "done", "result": explicit_handoff}
            return

        if not wrote_files:
            if _nudge_to_write(agent_context, history, messages, turn, content):
                continue
            raise WorkspaceError(
                "子 Agent 用自然语言结束了本轮，但没有调用 write_file/edit_file "
                "写入任何文件。"
            )
        if task_id:
            clear_loop_checkpoint(project_root, task_id)
        yield {
            "type": "done",
            "result": {
                "done": True,
                "summary": content,
                "thinking": content,
            },
        }
        return



def stream_agent_tool_loop(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task: dict[str, Any],
    sandbox: dict[str, Any] | None = None,
    max_turns: int | None = None,
    cancelled: Callable[[], bool] | None = None,
    call_model: Callable[..., dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    extra_context: dict[str, Any] | None = None,
    resume: bool = False,
    use_native_tools: bool = False,
):
    """Yield reasoning/tool events, then one `done` event with the model result.

    With `use_native_tools=True` the loop sends OpenAI-native `tools` schemas and
    standard chat roles. Without it (the default, also used by tests and providers
    without function calling) it uses the prose-tolerant JSON-block protocol.
    """
    module_id = str(task.get("module_id") or task.get("id") or "")
    with token_usage.usage_scope(
        project_root,
        module_id=module_id,
        feature="agent",
    ):
        if use_native_tools:
            yield from _stream_native_tool_loop(
                project_root,
                provider,
                architecture,
                project,
                task,
                sandbox,
                max_turns,
                cancelled,
                call_model,
                system_prompt,
                extra_context,
                resume,
            )
            return
        yield from _stream_legacy_tool_loop(
            project_root,
            provider,
            architecture,
            project,
            task,
            sandbox,
            max_turns,
            cancelled,
            call_model,
            system_prompt,
            extra_context,
            resume,
        )



def run_agent_tool_loop(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    task: dict[str, Any],
    sandbox: dict[str, Any] | None = None,
    max_turns: int | None = None,
    call_model: Callable[..., dict[str, Any]] | None = None,
    system_prompt: str | None = None,
    extra_context: dict[str, Any] | None = None,
    resume: bool = False,
    use_native_tools: bool = False,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Non-streaming wrapper used by synchronous task generation.

    `max_turns` remains an optional test/debug escape hatch. Production callers pass
    the default and the loop has no normal turn ceiling; cancellation and provider
    timeouts remain the termination controls.
    """
    final = None
    for event in stream_agent_tool_loop(
        project_root,
        provider,
        architecture,
        project,
        task,
        sandbox=sandbox,
        max_turns=max_turns,
        cancelled=cancelled,
        call_model=call_model,
        system_prompt=system_prompt,
        extra_context=extra_context,
        resume=resume,
        use_native_tools=use_native_tools,
    ):
        if event["type"] == "done":
            final = event["result"]
    if final is None:
        raise WorkspaceError("Agent 工具循环没有返回结果。")
    return final
