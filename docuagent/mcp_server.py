"""Minimal MCP server exposing DocuAgent capabilities over stdio.

This is a stdlib-only subset of the Model Context Protocol (JSON-RPC 2.0 over
newline-delimited messages on stdin/stdout). It keeps the default runtime free of
third-party dependencies; the official MCP SDK can replace this transport later.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import gitops
import snapshots
import tasks
import workspace
from core import APP_VERSION, WorkspaceError

PROTOCOL_VERSION = "2025-03-26"
SERVER_INFO = {"name": "docuagent-mcp", "version": APP_VERSION}


def _text_result(value: Any) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(value, ensure_ascii=False, indent=2),
            }
        ],
        "isError": False,
    }


def _tool(
    name: str,
    description: str,
    properties: dict[str, dict[str, str]],
    required: list[str],
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
        },
    }


TOOLS = [
    _tool(
        "inspect_workspace",
        "Inspect a DocuAgent workspace: entries, bootstrap state, architecture, and conversation.",
        {"path": {"type": "string", "description": "Absolute project path"}},
        ["path"],
    ),
    _tool(
        "list_tasks",
        "Read the current task plan and task statuses.",
        {"path": {"type": "string", "description": "Absolute project path"}},
        ["path"],
    ),
    _tool(
        "read_architecture",
        "Read the current architecture document.",
        {"path": {"type": "string", "description": "Absolute project path"}},
        ["path"],
    ),
    _tool(
        "read_attachments",
        "Read node attachments for a module.",
        {
            "path": {"type": "string", "description": "Absolute project path"},
            "module_id": {"type": "string", "description": "Architecture module id"},
        },
        ["path"],
    ),
    _tool(
        "list_snapshots",
        "List project snapshots available for rollback.",
        {"path": {"type": "string", "description": "Absolute project path"}},
        ["path"],
    ),
    _tool(
        "git_status",
        "Read the project's git status.",
        {"path": {"type": "string", "description": "Absolute project path"}},
        ["path"],
    ),
    _tool(
        "verify_task",
        "Run verification for an applied task.",
        {
            "path": {"type": "string", "description": "Absolute project path"},
            "task_id": {"type": "string", "description": "Task id"},
        },
        ["path", "task_id"],
    ),
]


def _project_root(raw: str) -> Path:
    return workspace.resolve_project_path(raw)


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "inspect_workspace":
        root = _project_root(str(arguments.get("path") or ""))
        return _text_result(
            workspace.inspect_workspace(str(root), lambda state: state)
        )
    if name == "list_tasks":
        root = _project_root(str(arguments.get("path") or ""))
        return _text_result(tasks.read_task_state(root))
    if name == "read_architecture":
        root = _project_root(str(arguments.get("path") or ""))
        return _text_result(workspace.architecture_document(root))
    if name == "read_attachments":
        root = _project_root(str(arguments.get("path") or ""))
        module_id = str(arguments.get("module_id") or "")
        attachments = workspace.read_node_attachments(root)
        return _text_result(
            attachments.get(module_id, []) if module_id else attachments
        )
    if name == "list_snapshots":
        root = _project_root(str(arguments.get("path") or ""))
        return _text_result(snapshots.list_snapshots(root))
    if name == "git_status":
        root = _project_root(str(arguments.get("path") or ""))
        return _text_result(gitops.git_status(root))
    if name == "verify_task":
        root = _project_root(str(arguments.get("path") or ""))
        task_id = str(arguments.get("task_id") or "")
        return _text_result(tasks.verify_task(root, task_id))
    raise WorkspaceError(f"未知 MCP 工具：{name}")


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = str(message.get("method") or "")
    request_id = message.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        }
    if method == "notifications/initialized":
        return None
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {"tools": TOOLS},
        }
    if method == "tools/call":
        params = message.get("params") or {}
        name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        try:
            result = call_tool(name, arguments)
        except WorkspaceError as exc:
            result = {
                "content": [{"type": "text", "text": str(exc)}],
                "isError": True,
            }
        except Exception as exc:  # defensive: MCP clients need a JSON-RPC response
            result = {
                "content": [{"type": "text", "text": f"工具执行失败：{exc}"}],
                "isError": True,
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"未知方法：{method}"},
    }


def run_mcp_server() -> None:
    """Read newline-delimited JSON-RPC messages from stdin and reply on stdout."""
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            sys.stdout.write(
                json.dumps({
                    "jsonrpc": "2.0",
                    "id": None,
                    "error": {"code": -32700, "message": "无法解析 JSON-RPC 请求"},
                })
                + "\n"
            )
            sys.stdout.flush()
            continue
        response = handle_message(message)
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    run_mcp_server()
