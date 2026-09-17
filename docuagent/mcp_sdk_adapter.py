"""Optional official MCP SDK adapter.

DocuAgent keeps a stdlib-only MCP server by default. When the official `mcp` package
is installed (`pip install mcp`), this adapter exposes the same tools through the
standard FastMCP server.
"""

from __future__ import annotations

import json
from typing import Any

from core import WorkspaceError
from mcp_server import call_tool as _call_tool


def _result(name: str, arguments: dict[str, Any]) -> str:
    response = _call_tool(name, arguments)
    text = response["content"][0]["text"]
    return json.dumps(json.loads(text), ensure_ascii=False, indent=2)


def run_sdk_server() -> None:
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise WorkspaceError(
            "官方 MCP SDK 未安装；请先执行 `pip install mcp`，或继续使用轻量版 `docuagent_mcp.py`。"
        ) from exc

    mcp = FastMCP("docuagent")

    @mcp.tool()
    def inspect_workspace(path: str) -> str:
        return _result("inspect_workspace", {"path": path})

    @mcp.tool()
    def list_tasks(path: str) -> str:
        return _result("list_tasks", {"path": path})

    @mcp.tool()
    def read_architecture(path: str) -> str:
        return _result("read_architecture", {"path": path})

    @mcp.tool()
    def read_attachments(path: str, module_id: str = "") -> str:
        return _result("read_attachments", {"path": path, "module_id": module_id})

    @mcp.tool()
    def list_snapshots(path: str) -> str:
        return _result("list_snapshots", {"path": path})

    @mcp.tool()
    def git_status(path: str) -> str:
        return _result("git_status", {"path": path})

    @mcp.tool()
    def verify_task(path: str, task_id: str) -> str:
        return _result("verify_task", {"path": path, "task_id": task_id})

    mcp.run()
