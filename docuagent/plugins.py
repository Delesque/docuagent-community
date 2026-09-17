"""Plugin marketplace and runtime integration for DocuAgent.

Plugins are executable capability packs. Each plugin lives in
`~/.docuagent/plugins/<name>/` or `.docuagent/plugins/<name>/`, has a `plugin.json`
manifest, and may register tools and prompt fragments. Python entry modules only run
when the plugin is explicitly enabled.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
from datetime import datetime, timezone
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from core import WorkspaceError
from skills import marketplace_entries as _marketplace_entries
from workspace import atomic_write_json, managed_path

PLUGIN_JSON = "plugin.json"


def _plugin_dirs(project_root: Path) -> list[Path]:
    return [
        project_root / ".docuagent" / "plugins",
        Path.home() / ".docuagent" / "plugins",
    ]


def _read_plugin(plugin_dir: Path) -> dict[str, Any] | None:
    manifest_path = plugin_dir / PLUGIN_JSON
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(manifest, dict) or not str(manifest.get("name") or "").strip():
        return None
    return {
        "name": str(manifest.get("name") or "").strip(),
        "version": str(manifest.get("version") or "0.0.0").strip(),
        "description": str(manifest.get("description") or "").strip(),
        "entry": str(manifest.get("entry") or "").strip(),
        "tools": manifest.get("tools") if isinstance(manifest.get("tools"), list) else [],
        "prompts": manifest.get("prompts") if isinstance(manifest.get("prompts"), list) else [],
        "source": str(plugin_dir),
    }


def _read_enabled_state(project_root: Path) -> dict[str, bool]:
    state: dict[str, bool] = {}
    for path in (
        Path.home() / ".docuagent" / "plugins.json",
        managed_path(project_root, "plugins.json"),
    ):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        plugins = raw.get("plugins", {}) if isinstance(raw, dict) else {}
        if isinstance(plugins, dict):
            for name, value in plugins.items():
                state[str(name)] = bool(value.get("enabled") if isinstance(value, dict) else value)
    return state


def _plugin_dir_hash(path: Path) -> str:
    digest = hashlib.sha256()
    for file_path in sorted(path.rglob("*")):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_path.read_bytes())
    return digest.hexdigest()


def _read_records(project_root: Path) -> dict[str, dict[str, Any]]:
    path = managed_path(project_root, "plugins.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    records = raw.get("plugins", {}) if isinstance(raw, dict) else {}
    return {
        str(name): dict(value)
        for name, value in records.items()
        if isinstance(value, dict)
    }


def _write_record(
    project_root: Path,
    name: str,
    *,
    enabled: bool,
    source: str,
    version: str,
    sha256: str,
) -> None:
    records = _read_records(project_root)
    records[name] = {
        **(records.get(name) or {}),
        "enabled": enabled,
        "source": source,
        "version": version,
        "sha256": sha256,
        "installed_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(managed_path(project_root, "plugins.json"), {"plugins": records})


def _write_enabled(project_root: Path, name: str, enabled: bool) -> None:
    path = managed_path(project_root, "plugins.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    plugins = raw.get("plugins", {}) if isinstance(raw, dict) else {}
    if not isinstance(plugins, dict):
        plugins = {}
    current = plugins.get(name)
    plugins[name] = {
        **(current if isinstance(current, dict) else {}),
        "enabled": enabled,
    }
    atomic_write_json(path, {"plugins": plugins})


def list_plugins(project_root: Path) -> list[dict[str, Any]]:
    enabled = _read_enabled_state(project_root)
    plugins: dict[str, dict[str, Any]] = {}
    records = _read_records(project_root)
    for base in _plugin_dirs(project_root):
        if not base.is_dir():
            continue
        for plugin_dir in sorted(base.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin = _read_plugin(plugin_dir)
            if plugin:
                plugin["enabled"] = enabled.get(plugin["name"], False)
                record = records.get(plugin["name"])
                if record:
                    plugin["installed_from"] = record.get("source", "")
                    plugin["sha256"] = record.get("sha256", "")
                plugins[plugin["name"]] = plugin
    return list(plugins.values())


def _resolve_source(source: str) -> Path:
    if source.startswith(("http://", "https://")) or source.lower().endswith(".zip"):
        temp_root = Path(tempfile.mkdtemp(prefix="docuagent-plugin-"))
        archive = temp_root / "plugin.zip"
        try:
            with urllib.request.urlopen(source, timeout=60) as response:
                archive.write_bytes(response.read())
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(temp_root)
        except Exception as exc:
            raise WorkspaceError(f"无法下载插件：{exc}") from exc
        for candidate in sorted(temp_root.rglob(PLUGIN_JSON)):
            return candidate.parent
        raise WorkspaceError("下载的插件压缩包中没有 plugin.json。")
    return Path(source).expanduser().resolve()


def install_plugin(project_root: Path, source: str) -> dict[str, Any]:
    source_path = _resolve_source(source)
    plugin = _read_plugin(source_path)
    if not plugin:
        raise WorkspaceError(f"不是有效的插件目录：{source}")
    target = project_root / ".docuagent" / "plugins" / plugin["name"]
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source_path, target)
    installed = _read_plugin(target)
    if not installed:
        raise WorkspaceError("插件安装后无法读取。")
    source_hash = _plugin_dir_hash(target)
    _write_record(
        project_root,
        installed["name"],
        enabled=False,
        source=str(source_path),
        version=str(installed.get("version") or "0.0.0"),
        sha256=source_hash,
    )
    installed["enabled"] = False
    installed["installed_from"] = str(source_path)
    installed["sha256"] = source_hash
    return installed


def uninstall_plugin(project_root: Path, name: str) -> dict[str, Any]:
    target = project_root / ".docuagent" / "plugins" / name
    if not target.is_dir():
        raise WorkspaceError(f"插件不存在：{name}")
    shutil.rmtree(target)
    return {"name": name, "uninstalled": True}


def toggle_plugin(project_root: Path, name: str, enabled: bool) -> dict[str, Any]:
    plugin = next(
        (item for item in list_plugins(project_root) if item["name"] == name),
        None,
    )
    if not plugin:
        raise WorkspaceError(f"插件不存在：{name}")
    _write_enabled(project_root, name, bool(enabled))
    plugin["enabled"] = bool(enabled)
    return plugin


def marketplace_entries(marketplace: str) -> list[dict[str, Any]]:
    return [
        entry
        for entry in _marketplace_entries(marketplace)
        if entry.get("type") == "plugin"
    ]


def install_from_marketplace(
    project_root: Path,
    marketplace: str,
    name: str,
) -> dict[str, Any]:
    entry = next(
        (item for item in marketplace_entries(marketplace) if item["name"] == name),
        None,
    )
    if not entry:
        raise WorkspaceError(f"市场中没有插件：{name}")
    return install_plugin(project_root, entry["source"])


def list_plugin_tools(project_root: Path) -> list[dict[str, Any]]:
    tools: list[dict[str, Any]] = []
    for plugin in list_plugins(project_root):
        if not plugin["enabled"]:
            continue
        for tool in plugin["tools"]:
            if isinstance(tool, dict) and str(tool.get("name") or "").strip():
                tools.append({
                    "name": str(tool["name"]).strip(),
                    "description": str(tool.get("description") or "").strip(),
                    "args": tool.get("args") if isinstance(tool.get("args"), dict) else {},
                })
    return tools


def list_plugin_prompts(project_root: Path, target: str) -> list[str]:
    prompts: list[str] = []
    for plugin in list_plugins(project_root):
        if not plugin["enabled"]:
            continue
        for prompt in plugin["prompts"]:
            if not isinstance(prompt, dict):
                continue
            if str(prompt.get("target") or "subagent") != target:
                continue
            content = str(prompt.get("content") or "").strip()
            if content:
                prompts.append(content)
    return prompts


def call_plugin_tool(
    project_root: Path,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    for plugin in list_plugins(project_root):
        if not plugin["enabled"]:
            continue
        if not any(str(tool.get("name")) == name for tool in plugin["tools"]):
            continue
        entry = plugin["entry"]
        if not entry:
            raise WorkspaceError(f"插件 `{plugin['name']}` 的 `{name}` 没有可执行入口。")
        module_path = Path(plugin["source"]) / entry
        spec = importlib.util.spec_from_file_location(
            f"docuagent_plugin_{plugin['name']}",
            module_path,
        )
        if not spec or not spec.loader:
            raise WorkspaceError(f"无法加载插件入口：{module_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        handler = getattr(module, f"tool_{name}", None)
        if handler is None:
            handler = getattr(module, "handle_tool", None)
        if handler is None:
            raise WorkspaceError(f"插件 `{plugin['name']}` 没有 `tool_{name}` 或 `handle_tool`。")
        try:
            if name in handler.__code__.co_varnames:
                result = handler(name, arguments, {"project_root": project_root})
            else:
                result = handler(project_root, arguments)
        except Exception as exc:
            raise WorkspaceError(f"插件工具执行失败：{exc}") from exc
        return result if isinstance(result, dict) else {"content": str(result)}
    raise WorkspaceError(f"未知插件工具：{name}")
