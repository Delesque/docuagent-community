"""Lightweight stdio MCP client for external tool servers.

External MCP servers are configured in `~/.docuagent/mcp.json` and optionally
`.docuagent/mcp.json` inside a project. The client starts the configured command,
performs the MCP handshake, lists tools, and invokes tools over newline-delimited
JSON-RPC. No third-party dependency is required.
"""

from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any

from core import APP_VERSION, WorkspaceError
from workspace import atomic_write_json


def load_mcp_servers(project_root: Path | None = None) -> dict[str, Any]:
    """Load global and project MCP server configs; project entries override global."""
    servers: dict[str, dict[str, Any]] = {}
    paths = [Path.home() / ".docuagent" / "mcp.json"]
    if project_root:
        paths.append(project_root / ".docuagent" / "mcp.json")
    for path in paths:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for item in raw.get("servers", []) if isinstance(raw, dict) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            command = str(item.get("command") or "").strip()
            if name and command:
                servers[name] = {
                    "command": command,
                    "args": [str(arg) for arg in (item.get("args") or [])],
                    "approved": bool(item.get("approved")),
                }
    return {"servers": servers}


class McpClient:
    """A single external MCP server process speaking JSON-RPC over stdio."""

    def __init__(self, command: str, args: list[str] | None = None) -> None:
        self.command = command
        self.args = args or []
        self._process: subprocess.Popen[str] | None = None
        self._next_request_id = 1
        self._lock = threading.Lock()

    def start(self) -> None:
        try:
            self._process = subprocess.Popen(
                [self.command, *self.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise WorkspaceError(f"无法启动 MCP server：{exc}") from exc
        if not self._process.stdin or not self._process.stdout:
            raise WorkspaceError("MCP server 没有可用的 stdio 管道。")
        self._send(
            {
                "jsonrpc": "2.0",
                "id": self._new_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {
                        "name": "docuagent-mcp-client",
                        "version": APP_VERSION,
                    },
                },
            }
        )
        self._send(
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            }
        )

    def _new_id(self) -> int:
        value = self._next_request_id
        self._next_request_id += 1
        return value

    def _send(self, request: dict[str, Any]) -> dict[str, Any]:
        process = self._process
        if not process or not process.stdin or not process.stdout:
            raise WorkspaceError("MCP client 尚未启动。")
        request_id = request.get("id")
        with self._lock:
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()
            if request_id is None:
                return {}
            while True:
                line = process.stdout.readline()
                if not line:
                    raise WorkspaceError("MCP server 已退出，未返回响应。")
                try:
                    response = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if response.get("id") == request_id:
                    if "error" in response:
                        raise WorkspaceError(str(response["error"].get("message")))
                    return response

    def list_tools(self) -> list[dict[str, Any]]:
        response = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._new_id(),
                "method": "tools/list",
            }
        )
        tools = response.get("result", {}).get("tools", [])
        return tools if isinstance(tools, list) else []

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        response = self._send(
            {
                "jsonrpc": "2.0",
                "id": self._new_id(),
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
        )
        return response.get("result", {})

    def stop(self) -> None:
        process = self._process
        self._process = None
        if not process:
            return
        try:
            if process.stdin:
                process.stdin.close()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
            try:
                process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()


def list_configured_servers(project_root: Path) -> list[str]:
    return sorted(load_mcp_servers(project_root).get("servers", {}))


def configured_servers(project_root: Path) -> list[dict[str, Any]]:
    servers = load_mcp_servers(project_root).get("servers", {})
    return [
        {
            "name": name,
            "command": config["command"],
            "args": config["args"],
            "approved": bool(config.get("approved")),
        }
        for name, config in sorted(servers.items())
    ]


def save_mcp_servers(
    project_root: Path,
    servers: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous = load_mcp_servers(project_root).get("servers", {})
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in servers:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        command = str(item.get("command") or "").strip()
        if not name or not command or name in seen:
            continue
        seen.add(name)
        args = [str(arg) for arg in (item.get("args") or []) if str(arg).strip()]
        old = previous.get(name, {})
        same_command = old.get("command") == command and old.get("args") == args
        cleaned.append({
            "name": name,
            "command": command,
            "args": args,
            "approved": bool(old.get("approved")) if same_command else False,
        })
    target_dir = project_root / ".docuagent"
    target_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(
        target_dir / "mcp.json",
        {"servers": cleaned},
    )
    return configured_servers(project_root)


def approve_mcp_server(project_root: Path, server_name: str) -> list[dict[str, Any]]:
    """Record explicit user confirmation for a configured server command."""
    servers = load_mcp_servers(project_root).get("servers", {})
    server = servers.get(server_name)
    if not server:
        raise WorkspaceError(f"未配置 MCP server：{server_name}")
    server["approved"] = True
    cleaned = []
    for name, config in sorted(servers.items()):
        cleaned.append({
            "name": name,
            "command": config["command"],
            "args": config["args"],
            "approved": bool(config.get("approved")),
        })
    target_dir = project_root / ".docuagent"
    target_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(target_dir / "mcp.json", {"servers": cleaned})
    return configured_servers(project_root)


def test_server_entry(
    command: str,
    args: list[str] | None = None,
) -> list[dict[str, Any]]:
    client = McpClient(command, args or [])
    try:
        client.start()
        return client.list_tools()
    finally:
        client.stop()


def list_server_tools(project_root: Path, server_name: str) -> list[dict[str, Any]]:
    server = _server_config(project_root, server_name)
    client = McpClient(server["command"], server["args"])
    try:
        client.start()
        return client.list_tools()
    finally:
        client.stop()


def call_server_tool(
    project_root: Path,
    server_name: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    server = _server_config(project_root, server_name)
    client = McpClient(server["command"], server["args"])
    try:
        client.start()
        return client.call_tool(tool_name, arguments)
    finally:
        client.stop()


def _server_config(project_root: Path, server_name: str) -> dict[str, Any]:
    servers = load_mcp_servers(project_root).get("servers", {})
    server = servers.get(server_name)
    if not server:
        raise WorkspaceError(f"未配置 MCP server：{server_name}")
    if not server.get("approved"):
        raise WorkspaceError(
            f"MCP server「{server_name}」尚未确认启动命令：{server.get('command', '')}"
        )
    return server
