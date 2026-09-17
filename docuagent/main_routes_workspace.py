"""Thin HTTP route handlers for workspace extensions.

These routes are the JSON boundary around modules that already own their logic:
git, MCP, skills, plugins, UI preview/layout, snapshots, node attachments, and
sub-agent messaging. Keeping them out of `main.py` lets the server module stay
focused on transport, security, and provider/config concerns.
"""

from __future__ import annotations

from typing import Any

import agents as _agents
import gitops as _gitops
import layout_delta as _layout_delta
import mcp_client as _mcp_client
import plugins as _plugins
import skills as _skills
import snapshots as _snapshots
import ui_layout as _ui_layout
import workspace as _workspace
from core import WorkspaceError
from workspace import resolve_project_path


def git_status_route(raw_path: str) -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return _gitops.git_status(project_root)


def list_mcp_servers_route(raw_path: str) -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return {"servers": _mcp_client.configured_servers(project_root)}


def save_mcp_servers_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    servers = payload.get("servers")
    if not isinstance(servers, list):
        raise WorkspaceError("servers 必须是列表。")
    return {"servers": _mcp_client.save_mcp_servers(project_root, servers)}


def test_mcp_server_route(payload: dict[str, Any]) -> dict[str, Any]:
    command = str(payload.get("command") or "").strip()
    if not command:
        raise WorkspaceError("缺少 MCP server 命令。")
    args = payload.get("args")
    if not isinstance(args, list):
        args = []
    return {"tools": _mcp_client.test_server_entry(command, args)}


def approve_mcp_server_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    server = str(payload.get("server") or "").strip()
    if not server:
        raise WorkspaceError("缺少 MCP server 名称。")
    return {"servers": _mcp_client.approve_mcp_server(project_root, server)}


def list_mcp_tools_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    server = str(payload.get("server") or "").strip()
    return {"tools": _mcp_client.list_server_tools(project_root, server)}


def call_mcp_tool_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    server = str(payload.get("server") or "").strip()
    tool = str(payload.get("tool") or "").strip()
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    return _mcp_client.call_server_tool(project_root, server, tool, arguments)


def git_diff_route(raw_path: str, path: str = "") -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return _gitops.git_diff(project_root, path)


def list_skills_route(raw_path: str) -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return {"skills": _skills.list_skills(project_root)}


def import_skill_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    source = str(payload.get("source") or "").strip()
    if not source:
        raise WorkspaceError("缺少 Skill 来源。")
    return {"skill": _skills.import_skill(project_root, source)}


def marketplace_entries_route(payload: dict[str, Any]) -> dict[str, Any]:
    marketplace = str(payload.get("marketplace") or "").strip()
    if not marketplace:
        raise WorkspaceError("缺少 marketplace 地址。")
    return {"entries": _skills.marketplace_entries(marketplace)}


def install_skill_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    marketplace = str(payload.get("marketplace") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not marketplace or not name:
        raise WorkspaceError("缺少 marketplace 或 Skill 名称。")
    return {"skill": _skills.install_skill(project_root, marketplace, name)}


def list_plugins_route(raw_path: str) -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return {"plugins": _plugins.list_plugins(project_root)}


def install_plugin_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    source = str(payload.get("source") or "").strip()
    if not source:
        raise WorkspaceError("缺少插件来源。")
    return {"plugin": _plugins.install_plugin(project_root, source)}


def uninstall_plugin_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    name = str(payload.get("name") or "").strip()
    return _plugins.uninstall_plugin(project_root, name)


def toggle_plugin_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    name = str(payload.get("name") or "").strip()
    enabled = bool(payload.get("enabled"))
    return {"plugin": _plugins.toggle_plugin(project_root, name, enabled)}


def plugin_marketplace_route(payload: dict[str, Any]) -> dict[str, Any]:
    marketplace = str(payload.get("marketplace") or "").strip()
    if not marketplace:
        raise WorkspaceError("缺少 marketplace 地址。")
    return {"entries": _plugins.marketplace_entries(marketplace)}


def install_market_plugin_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    marketplace = str(payload.get("marketplace") or "").strip()
    name = str(payload.get("name") or "").strip()
    if not marketplace or not name:
        raise WorkspaceError("缺少 marketplace 或插件名称。")
    return {"plugin": _plugins.install_from_marketplace(project_root, marketplace, name)}

def build_layout_delta_target_route(payload: dict[str, Any]) -> dict[str, Any]:
    module_id = str(payload.get("module_id") or "").strip()
    element = payload.get("element")
    targets = payload.get("targets")
    if not isinstance(element, dict) or not isinstance(targets, dict):
        raise WorkspaceError("缺少 element 或 targets。")
    reason = str(payload.get("reason") or "").strip()
    annotations = str(payload.get("annotations") or "").strip()
    delta = _layout_delta.build_layout_delta_from_target(
        element, targets, module_id=module_id, reason=reason
    )
    return {
        "delta": delta,
        "request": _layout_delta.layout_delta_to_request_text(delta, annotations),
    }


def build_layout_delta_route(payload: dict[str, Any]) -> dict[str, Any]:
    before = payload.get("before")
    after = payload.get("after")
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise WorkspaceError("布局 delta 需要 before/after 快照。")
    element_id = payload.get("element_id")
    if element_id is not None:
        element_id = str(element_id).strip() or None
    selector = payload.get("selector")
    if selector is not None:
        selector = str(selector).strip() or None
    module_id = str(payload.get("module_id") or "").strip()
    reason = str(payload.get("reason") or "").strip()
    annotations = str(payload.get("annotations") or "").strip()
    delta = _layout_delta.build_layout_delta_from_snapshots(
        before,
        after,
        element_id=element_id,
        selector=selector,
        module_id=module_id,
        reason=reason,
    )
    return {
        "delta": delta,
        "request": _layout_delta.layout_delta_to_request_text(delta, annotations),
    }


def capture_ui_layout_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    url = str(payload.get("url") or "").strip()
    module_id = str(payload.get("module_id") or "").strip()
    try:
        max_nodes = int(payload.get("max_nodes") or 800)
        max_text = int(payload.get("max_text") or 120)
    except (TypeError, ValueError) as exc:
        raise WorkspaceError("max_nodes/max_text 必须是整数。") from exc
    return _ui_layout.capture_layout_snapshot(
        project_root,
        url,
        module_id,
        max_nodes=max_nodes,
        max_text=max_text,
    )

def git_commit_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    message = str(payload.get("message") or "").strip()
    if not message:
        raise WorkspaceError("提交信息不能为空。")
    _snapshots.create_snapshot(project_root, f"Git 提交：{message[:80]}")
    return _gitops.git_commit(project_root, message)

def list_snapshots_route(raw_path: str) -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return {"snapshots": _snapshots.list_snapshots(project_root)}


def restore_snapshot_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    snapshot_id = str(payload.get("snapshot_id") or "").strip()
    if not snapshot_id:
        raise WorkspaceError("缺少快照 ID。")
    try:
        result = _snapshots.restore_snapshot(project_root, snapshot_id)
    except LookupError as exc:
        raise WorkspaceError(str(exc)) from exc
    _snapshots.create_snapshot(project_root, "回滚快照")
    return result


def list_attachments_route(raw_path: str) -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return {"attachments": _workspace.read_node_attachments(project_root)}


def add_attachment_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    text = str(payload.get("text") or "").strip()
    if not module_id:
        raise WorkspaceError("缺少模块 ID。")
    if not text:
        raise WorkspaceError("备注内容不能为空。")
    attachment_type = str(payload.get("type") or "note").strip()
    return {
        "attachments": _workspace.add_node_attachment(
            project_root, module_id, attachment_type, text
        )
    }


def resolve_attachment_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    attachment_id = str(payload.get("attachment_id") or "").strip()
    if not module_id or not attachment_id:
        raise WorkspaceError("缺少模块 ID 或附件 ID。")
    return {
        "attachments": _workspace.resolve_node_attachment(
            project_root, module_id, attachment_id
        )
    }


def archive_attachment_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    attachment_id = str(payload.get("attachment_id") or "").strip()
    if not module_id or not attachment_id:
        raise WorkspaceError("缺少模块 ID 或附件 ID。")
    return {
        "attachments": _workspace.archive_node_attachment(
            project_root, module_id, attachment_id
        )
    }


def clear_agent_errors_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    if not module_id:
        raise WorkspaceError("缺少模块 ID。")
    return {
        "error_memory": _agents.clear_error_memory(project_root, module_id)
    }


def send_agent_message_route(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    text = str(payload.get("message") or "").strip()
    if not module_id:
        raise WorkspaceError("缺少模块 ID。")
    if not text:
        raise WorkspaceError("消息内容不能为空。")
    return _agents.send_agent_message(project_root, module_id, text)
