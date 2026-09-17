"""Sub-agent tool bridge for code intelligence (the P2 capability).

This module is the *only* place where code intelligence becomes visible to sub-agents.
It imports ``agent_tools`` (the core) and registers four optional, capability-gated
tools into ``agent_tools'`` external-tool registry at import time. ``agent_tools``
never imports this package back, so the decoupling contract from
CODE_INTELLIGENCE_ROADMAP (§1/§5) holds: the core stays a one-way consumer.

The tools are registered only when the operator explicitly enables code intelligence
(``DOCUAGENT_CODE_INTEL``), matching the "explicit opt-in, fail closed" governance in
capabilities.py. The same env flag makes ``agents.build_agent_context`` grant the
``code_intel`` capability, so a sub-agent only sees these tools when both sides agree.

Why this is a moat: a sub-agent can ``code_goto`` / ``code_references`` / ``code_search``
to learn exactly where a symbol is defined or used, instead of reading whole files into
its (already minimal) context. It reinforces the "sub-agents use concise logs, not chat
history" token-saving design.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from core import WorkspaceError

from capabilities import CODE_INTEL, CODE_INTEL_ENV

import agent_tools
from ._service import SERVICE


# ---- coordinate + path helpers -------------------------------------------
# Sub-agents reason about 1-based line/character (what they see in file content).
# The code-intel service and pyindex speak 0-based (LSP convention) internally.
def _to0(value: Any) -> int:
    try:
        return max(0, int(value) - 1)
    except (TypeError, ValueError):
        return 0


def _to1(value: Any) -> int:
    return int(value) + 1


def _resolve_abs(project_root: Path, raw: Any) -> Path:
    raw = str(raw or "").strip().replace("\\", "/")
    if not raw:
        raise WorkspaceError("代码智能工具需要 path 参数。")
    root = project_root.resolve()
    candidate = (root / raw).resolve()
    if candidate.is_relative_to(root):
        return candidate
    # Allow an absolute path that still lives inside the project.
    alt = Path(raw).resolve()
    if alt.is_relative_to(root):
        return alt
    raise WorkspaceError("代码智能路径必须位于项目内。")


def _loc_dict(loc: Any, project_root: Path) -> dict[str, Any]:
    uri = str(loc.uri)
    if uri.startswith("file://"):
        # urlparse("file:///C:/x").path == "/C:/x" on Windows; strip the leading
        # slash so Path() resolves the drive correctly.
        raw = unquote(urlparse(uri).path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        p = Path(raw)
    else:
        p = Path(uri)
    try:
        rel = p.relative_to(project_root.resolve()).as_posix()
    except ValueError:
        rel = uri
    return {
        "file": rel,
        "line": _to1(loc.line),
        "character": _to1(loc.character),
    }


# ---- tool handlers -------------------------------------------------------
def _handle_goto(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    abs_path = _resolve_abs(project_root, args.get("path"))
    if not abs_path.is_file():
        return {"found": False, "definitions": [],
                "message": f"文件不存在：{args.get('path')}"}
    locs = SERVICE.goto_definition(
        str(project_root), str(abs_path), _to0(args.get("line")), _to0(args.get("character", 1))
    )
    defs = [_loc_dict(loc, project_root) for loc in locs]
    return {
        "found": bool(defs),
        "definitions": defs,
        "message": "找到定义。" if defs else "未找到定义（可能不在索引范围内，或非 Python 文件）。",
    }


def _handle_references(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    abs_path = _resolve_abs(project_root, args.get("path"))
    if not abs_path.is_file():
        return {"found": False, "references": [],
                "message": f"文件不存在：{args.get('path')}"}
    locs = SERVICE.find_references(
        str(project_root), str(abs_path), _to0(args.get("line")), _to0(args.get("character", 1))
    )
    refs = [_loc_dict(loc, project_root) for loc in locs]
    return {
        "found": bool(refs),
        "references": refs,
        "count": len(refs),
        "message": f"找到 {len(refs)} 处引用。" if refs else "未找到引用。",
    }


def _handle_search(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    term = str(args.get("term") or "").strip()
    if not term:
        raise WorkspaceError("code_search 需要 term 参数。")
    try:
        limit = int(args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20
    records = SERVICE.search(str(project_root), term, limit)
    symbols = [
        {
            "name": r.name,
            "kind": r.kind,
            "file": r.file,
            "line": _to1(r.line),
            "signature": r.signature,
            "is_public": r.is_public,
        }
        for r in records
    ]
    return {"term": term, "count": len(symbols), "symbols": symbols}


def _handle_hover(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    abs_path = _resolve_abs(project_root, args.get("path"))
    if not abs_path.is_file():
        return {"found": False, "text": None,
                "message": f"文件不存在：{args.get('path')}"}
    text = SERVICE.hover(
        str(project_root), str(abs_path), _to0(args.get("line")), _to0(args.get("character", 1))
    )
    return {
        "found": bool(text),
        "text": text,
        "message": "找到悬停信息。" if text else "未找到悬停信息。",
    }


# ---- ignore-list handlers (KNOWN_ISSUES #5) -----------------------------
def _handle_ignore_add(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise WorkspaceError("code_ignore_add 需要 pattern 参数。")
    return SERVICE.ignore_add(str(project_root), pattern)


def _handle_ignore_propose(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise WorkspaceError("code_ignore_propose 需要 pattern 参数。")
    return SERVICE.ignore_propose(str(project_root), pattern)


def _handle_ignore_list(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    return SERVICE.ignore_list(str(project_root))


def _handle_ignore_pending(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    return {"pending": SERVICE.ignore_pending(str(project_root))}


def _handle_ignore_approve(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    # Gated by the orchestration layer: only the main agent, after human approval,
    # should invoke this. It writes to the human-owned ignore file.
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise WorkspaceError("code_ignore_approve 需要 pattern 参数。")
    return SERVICE.ignore_approve(str(project_root), pattern)


def _handle_ignore_reject(
    project_root: Path, tool: str, args: dict[str, Any], context: dict[str, Any]
) -> dict[str, Any]:
    pattern = str(args.get("pattern") or "").strip()
    if not pattern:
        raise WorkspaceError("code_ignore_reject 需要 pattern 参数。")
    return SERVICE.ignore_reject(str(project_root), pattern)


# ---- registration --------------------------------------------------------
def register() -> None:
    """Register the code-intel tools into agent_tools' external registry."""
    agent_tools.register_external_tool(
        "code_goto",
        CODE_INTEL,
        _handle_goto,
        {
            "name": "code_goto",
            "description": (
                "跳转到项目文件中某符号的定义。传入项目相对路径与 1-based 行列；"
                "返回定义所在的文件/行列。无需读取整个文件即可定位代码，是比整文件"
                "塞上下文更省 token 的导航方式。"
            ),
            "args": {
                "path": "项目相对路径",
                "line": "1-based 行号",
                "character": "1-based 列号（可选，默认 1）",
            },
        },
        prompt_line="- code_goto(path, line, character): jump to the definition of the symbol at the given 1-based position in a project file, without reading the whole file.",
    )
    agent_tools.register_external_tool(
        "code_references",
        CODE_INTEL,
        _handle_references,
        {
            "name": "code_references",
            "description": (
                "查找项目文件中某符号的全部引用位置。传入项目相对路径与 1-based 行列；"
                "返回所有引用所在的文件/行列列表。用于理解「谁在调用/使用这个符号」，"
                "避免全局 grep 整个项目。"
            ),
            "args": {
                "path": "项目相对路径",
                "line": "1-based 行号",
                "character": "1-based 列号（可选，默认 1）",
            },
        },
        prompt_line="- code_references(path, line, character): list every reference to the symbol at the given 1-based position across the project.",
    )
    agent_tools.register_external_tool(
        "code_search",
        CODE_INTEL,
        _handle_search,
        {
            "name": "code_search",
            "description": (
                "在项目的真实符号索引中按名称/签名模糊检索符号（函数/类/常量），"
                "返回名称、类型、文件、行号、签名、是否公开。用于「谁实现了 X」"
                "「有哪些同名符号」，是 search_reuse（契约注册表检索）在真实代码上的补充。"
            ),
            "args": {
                "term": "要检索的符号名/签名片段",
                "limit": "返回条数上限（可选，默认 20）",
            },
        },
        prompt_line="- code_search(term, limit): fuzzy-search the project's real symbol index (functions/classes/constants) by name or signature.",
    )
    agent_tools.register_external_tool(
        "code_hover",
        CODE_INTEL,
        _handle_hover,
        {
            "name": "code_hover",
            "description": (
                "获取项目文件中某位置的悬停信息（如 def/class 签名）。传入项目相对路径"
                "与 1-based 行列；返回签名文本。无需读取整个文件即可拿到符号签名。"
            ),
            "args": {
                "path": "项目相对路径",
                "line": "1-based 行号",
                "character": "1-based 列号（可选，默认 1）",
            },
        },
        prompt_line="- code_hover(path, line, character): get the hover/signature text at the given 1-based position without opening the file.",
    )
    agent_tools.register_external_tool(
        "code_ignore_list",
        CODE_INTEL,
        _handle_ignore_list,
        {
            "name": "code_ignore_list",
            "description": (
                "查看当前生效的代码智能「文件忽略列表」：返回 persistent（人类持久配置）、"
                "session（本会话 agent 临时添加）、pending（待主 agent+人类审核）三份，以及合并后的 effective 集合。"
                "用于 agent 在添加忽略规则前先确认现状，避免重复或误加。"
            ),
            "args": {},
        },
        prompt_line="- code_ignore_list(): show the currently effective file-ignore list (persistent + session + pending).",
    )
    agent_tools.register_external_tool(
        "code_ignore_add",
        CODE_INTEL,
        _handle_ignore_add,
        {
            "name": "code_ignore_add",
            "description": (
                "为当前会话添加一条文件忽略规则（gitignore 风格 glob，如 **/test_*.py、node_modules）。"
                "立即生效、不落盘：本次分析的索引/对账/架构投影会立刻排除匹配文件，但重启后失效。"
                "适合排除 agent 自己生成的临时文件或探针噪音。"
            ),
            "args": {
                "pattern": "gitignore 风格 glob，如 **/test_*.py、node_modules",
            },
        },
        prompt_line="- code_ignore_add(pattern): exclude files matching `pattern` for this session only (not persisted).",
    )
    agent_tools.register_external_tool(
        "code_ignore_propose",
        CODE_INTEL,
        _handle_ignore_propose,
        {
            "name": "code_ignore_propose",
            "description": (
                "提议一条文件忽略规则持久化：立即在本会话生效（同 code_ignore_add），同时进入 pending 待审队列，"
                "等待主 agent 审核 + 人类批准后才写进人类的项目配置 .docuagent/codeintel.ignore。"
                "agent 只能 add、不能删人类的持久条目；持久化必须经两级审核。"
            ),
            "args": {
                "pattern": "gitignore 风格 glob，如 **/fixtures/*.py",
            },
        },
        prompt_line="- code_ignore_propose(pattern): add `pattern` now AND queue it for persistence (needs main-agent + human approval).",
    )
    agent_tools.register_external_tool(
        "code_ignore_pending",
        CODE_INTEL,
        _handle_ignore_pending,
        {
            "name": "code_ignore_pending",
            "description": (
                "列出待审的忽略规则队列（agent 通过 code_ignore_propose 提交的条目）。"
                "供主 agent 审核：决定批准（code_ignore_approve）或驳回（code_ignore_reject）。"
            ),
            "args": {},
        },
        prompt_line="- code_ignore_pending(): list ignore patterns awaiting main-agent + human approval.",
    )
    agent_tools.register_external_tool(
        "code_ignore_approve",
        CODE_INTEL,
        _handle_ignore_approve,
        {
            "name": "code_ignore_approve",
            "description": (
                "批准一条待审忽略规则，将其写入人类的项目配置 .docuagent/codeintel.ignore 永久生效。"
                "此工具代表已通过主 agent 审核 + 人类批准的动作，不应由子 agent 直接调用。"
            ),
            "args": {
                "pattern": "与 code_ignore_pending 返回一致的 glob",
            },
        },
        prompt_line="- code_ignore_approve(pattern): persist an approved pattern to the human-owned ignore file (main-agent + human approved).",
    )
    agent_tools.register_external_tool(
        "code_ignore_reject",
        CODE_INTEL,
        _handle_ignore_reject,
        {
            "name": "code_ignore_reject",
            "description": (
                "驳回一条待审忽略规则：从 pending 队列移除，并撤销其在本会话的生效。用于主 agent 判定该规则不需保留时。"
            ),
            "args": {
                "pattern": "与 code_ignore_pending 返回一致的 glob",
            },
        },
        prompt_line="- code_ignore_reject(pattern): drop a proposed pattern from the queue and this session.",
    )


# Code intelligence is opt-in: only register when the operator has explicitly
# enabled it. The same flag grants the `code_intel` capability in agent contexts.
if os.environ.get(CODE_INTEL_ENV):
    register()
