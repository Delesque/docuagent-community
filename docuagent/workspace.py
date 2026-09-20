"""Filesystem persistence, UI state, workspace inspection, and migration.

Atomic writes throughout: a torn state file must never be able to block opening a
project. Corrupt UI state degrades to defaults rather than raising, for the same
reason.

Migration deliberately does not run cycle validation. Legacy data that would now be
rejected must still load, otherwise old projects could never be opened again;
validation applies to new model output only.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core import (
    CONTRACTS_FILE,
    MANAGED_DIR,
    MAX_SCALE,
    MIN_SCALE,
    RESERVED_NODE_IDS,
    UI_STATE_VERSION,
    WINDOW_BAR_SLOTS,
    WorkspaceError,
    clamp,
    coerce_float,
    edges_from_modules,
    normalize_brief,
    normalize_contracts,
    normalize_edge_type,
    optional_slug,
)

ARCHITECTURE_HISTORY_MAX = 20


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_node_id(value: str) -> str:
    """Slugify a layout-state key, leaving reserved ids intact.

    `optional_slug` strips leading and trailing separators, which would turn
    `__conversation__` into `conversation` — the key would then no longer match the id
    the canvas uses, and pinning the conversation would silently fail to round-trip.
    """
    stripped = value.strip()
    if stripped in RESERVED_NODE_IDS:
        return stripped
    return optional_slug(stripped)


def resolve_project_path(raw_path: str) -> Path:
    if not raw_path or not raw_path.strip():
        raise WorkspaceError("请输入项目目录。")
    path = Path(raw_path).expanduser()
    try:
        return path.resolve()
    except OSError as exc:
        raise WorkspaceError(f"无法解析项目目录：{exc}") from exc


def managed_path(project_root: Path, *parts: str) -> Path:
    return project_root / MANAGED_DIR / Path(*parts)


def atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        delete=False,
        dir=path.parent,
    ) as handle:
        handle.write(content)
        temp_name = handle.name
    os.replace(temp_name, path)


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_write_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def apply_text_files_atomic(
    project_root: Path,
    changes: dict[str, str],
    expected_before: dict[str, str] | None = None,
) -> list[str]:
    """Apply several text-file writes as one all-or-nothing operation.

    `expected_before` is sparse: paths present in the mapping must still match the
    captured content; paths absent from the mapping keep the historical unguarded
    behavior. A missing file reads as an empty string, matching the task patch
    version-guard semantics.
    """
    root = project_root.resolve()
    prepared: list[tuple[str, Path, str, str | None]] = []
    expected_before = expected_before or {}

    for relative, content in changes.items():
        target = (root / relative).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise WorkspaceError(f"补丁目标超出项目目录：{relative}") from exc
        try:
            current = target.read_text(encoding="utf-8")
        except OSError:
            current = None
        if relative in expected_before and (current or "") != expected_before[relative]:
            raise WorkspaceError(
                f"文件 `{relative}` 在补丁生成后被修改（STALE_VERSION），"
                "请重新生成该任务或人工处理冲突。"
            )
        prepared.append((relative, target, content, current))

    written: list[tuple[Path, str | None]] = []
    try:
        for relative, target, content, current in prepared:
            atomic_write_text(target, content)
            written.append((target, current))
    except (OSError, WorkspaceError) as exc:
        rollback_errors: list[str] = []
        for target, previous in reversed(written):
            try:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_write_text(target, previous)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        if rollback_errors:
            raise WorkspaceError(
                f"应用文件失败且回滚不完整：{exc}；回滚错误：{'；'.join(rollback_errors)}"
            ) from exc
        if isinstance(exc, WorkspaceError):
            raise
        raise WorkspaceError(f"应用文件失败，已回滚：{exc}") from exc

    return [relative for relative, _, _, _ in prepared]


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        # 并发原子替换（os.replace）在 Windows 上可能短暂隐藏文件；
        # 把"文件消失"当作"尚未写入"，而不是报错。
        return None
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"无法读取状态文件 {path}：{exc}") from exc
    if not isinstance(value, dict):
        raise WorkspaceError(f"状态文件格式无效：{path}")
    return value


def contracts_path(project_root: Path) -> Path:
    return managed_path(project_root, CONTRACTS_FILE)


def read_contracts(project_root: Path) -> dict[str, Any] | None:
    """Read the Project Contract Registry, returning None when not yet created."""
    raw = read_json(contracts_path(project_root))
    return normalize_contracts(raw) if isinstance(raw, dict) else None


def write_contracts(project_root: Path, contracts: dict[str, Any]) -> None:
    """Atomically persist the Project Contract Registry after validation."""
    atomic_write_json(contracts_path(project_root), normalize_contracts(contracts))


def default_ui_state() -> dict[str, Any]:
    return {
        "ui_state_version": UI_STATE_VERSION,
        "camera": {"x": 0.0, "y": 0.0, "scale": 1.0},
        "nodes": {},
        "window_bar": [],
        "outline_expanded": [],
        "updated_at": utc_now(),
    }


def normalize_ui_state(value: Any) -> dict[str, Any]:
    """Layout state is separate from architecture so moving a node never bumps
    `architecture_version`. Unknown or corrupt fields fall back to defaults rather
    than raising, because losing a camera position must not block opening a project.
    """
    state = default_ui_state()
    if not isinstance(value, dict):
        return state

    raw_camera = value.get("camera")
    if isinstance(raw_camera, dict):
        state["camera"] = {
            "x": coerce_float(raw_camera.get("x")),
            "y": coerce_float(raw_camera.get("y")),
            "scale": clamp(
                coerce_float(raw_camera.get("scale"), 1.0), MIN_SCALE, MAX_SCALE
            ),
        }

    raw_nodes = value.get("nodes")
    if isinstance(raw_nodes, dict):
        nodes: dict[str, Any] = {}
        for key, raw_node in raw_nodes.items():
            node_id = normalize_node_id(str(key))
            if not node_id or not isinstance(raw_node, dict):
                continue
            nodes[node_id] = {
                "x": coerce_float(raw_node.get("x")),
                "y": coerce_float(raw_node.get("y")),
                "pinned": bool(raw_node.get("pinned")),
            }
        state["nodes"] = nodes

    raw_bar = value.get("window_bar")
    if isinstance(raw_bar, list):
        slots: list[str] = []
        for item in raw_bar:
            node_id = normalize_node_id(str(item or ""))
            if node_id and node_id not in slots:
                slots.append(node_id)
        state["window_bar"] = slots[:WINDOW_BAR_SLOTS]

    raw_expanded = value.get("outline_expanded")
    if isinstance(raw_expanded, list):
        expanded: list[str] = []
        for item in raw_expanded:
            node_id = normalize_node_id(str(item or ""))
            if node_id and node_id not in expanded:
                expanded.append(node_id)
        state["outline_expanded"] = expanded

    return state


def read_ui_state(project_root: Path) -> dict[str, Any]:
    path = managed_path(project_root, "ui-state.json")
    try:
        raw = read_json(path)
    except WorkspaceError:
        return default_ui_state()
    return normalize_ui_state(raw)


def write_ui_state(project_root: Path, value: Any) -> dict[str, Any]:
    state = normalize_ui_state(value)
    state["updated_at"] = utc_now()
    atomic_write_json(managed_path(project_root, "ui-state.json"), state)
    return state


def prune_ui_state(project_root: Path, module_ids: set[str]) -> dict[str, Any]:
    """Drop layout entries for modules that no longer exist.

    Reserved ids survive: the conversation node is not a module, so it never appears in
    `module_ids`, but its pin and window-bar slot must still persist.
    """
    state = read_ui_state(project_root)
    keep = module_ids | RESERVED_NODE_IDS
    state["nodes"] = {
        node_id: node for node_id, node in state["nodes"].items() if node_id in keep
    }
    state["window_bar"] = [item for item in state["window_bar"] if item in keep]
    state["outline_expanded"] = [
        item for item in state["outline_expanded"] if item in keep
    ]
    return state


def read_conversation_history(project_root: Path) -> list[dict[str, Any]]:
    """Read conversation history from `.docuagent/conversation.json`."""
    path = managed_path(project_root, "conversation.json")
    try:
        raw = read_json(path)
    except WorkspaceError:
        return []
    if not raw or not isinstance(raw.get("messages"), list):
        return []
    return normalize_conversation_messages(raw["messages"])


def write_conversation_history(
    project_root: Path, messages: list[dict[str, Any]]
) -> None:
    """Save conversation history to `.docuagent/conversation.json`."""
    messages = normalize_conversation_messages(messages)
    conversation = {
        "schema_version": 1,
        "updated_at": utc_now(),
        "messages": messages,
    }
    atomic_write_json(managed_path(project_root, "conversation.json"), conversation)


def read_stale_modules(project_root: Path) -> set[str]:
    raw = read_json(managed_path(project_root, "stale.json"))
    if not raw or not isinstance(raw.get("module_ids"), list):
        return set()
    return {str(module_id) for module_id in raw["module_ids"]}


def write_stale_modules(project_root: Path, module_ids: set[str]) -> None:
    atomic_write_json(
        managed_path(project_root, "stale.json"),
        {"schema_version": 1, "module_ids": sorted(module_ids)},
    )


def read_node_attachments(project_root: Path) -> dict[str, list[dict[str, Any]]]:
    raw = read_json(managed_path(project_root, "node-attachments.json"))
    if not raw or not isinstance(raw.get("attachments"), dict):
        return {}
    return {
        str(module_id): [
            attachment
            for attachment in items
            if isinstance(attachment, dict)
        ]
        for module_id, items in raw["attachments"].items()
        if isinstance(items, list)
    }


def write_node_attachments(
    project_root: Path,
    attachments: dict[str, list[dict[str, Any]]],
) -> None:
    atomic_write_json(
        managed_path(project_root, "node-attachments.json"),
        {"schema_version": 1, "attachments": attachments},
    )


def add_node_attachment(
    project_root: Path,
    module_id: str,
    attachment_type: str,
    text: str,
) -> dict[str, list[dict[str, Any]]]:
    attachments = read_node_attachments(project_root)
    items = attachments.get(module_id, [])
    attachment_type = (
        attachment_type
        if attachment_type in {"note", "question", "decision", "option", "error_memory", "suggestion"}
        else "note"
    )
    items.append({
        "id": f"{utc_now().replace(':', '-')}-{len(items) + 1}",
        "type": attachment_type,
        "text": text[:1000],
        "created_at": utc_now(),
        "resolved": False,
        "archived": False,
    })
    attachments[module_id] = items
    write_node_attachments(project_root, attachments)
    return attachments


def resolve_node_attachment(
    project_root: Path,
    module_id: str,
    attachment_id: str,
) -> dict[str, list[dict[str, Any]]]:
    attachments = read_node_attachments(project_root)
    for attachment in attachments.get(module_id, []):
        if attachment.get("id") == attachment_id:
            attachment["resolved"] = True
            break
    write_node_attachments(project_root, attachments)
    return attachments


def archive_node_attachment(
    project_root: Path,
    module_id: str,
    attachment_id: str,
) -> dict[str, list[dict[str, Any]]]:
    attachments = read_node_attachments(project_root)
    for attachment in attachments.get(module_id, []):
        if attachment.get("id") == attachment_id:
            attachment["archived"] = True
            attachment["resolved"] = True
            break
    write_node_attachments(project_root, attachments)
    return attachments


def read_architecture_history(project_root: Path) -> list[dict[str, Any]]:
    """Previously accepted architecture documents, oldest first."""
    try:
        raw = read_json(managed_path(project_root, "architecture-history.json"))
    except WorkspaceError:
        return []
    if not raw or not isinstance(raw.get("entries"), list):
        return []
    return [entry for entry in raw["entries"] if isinstance(entry, dict)]


def push_architecture_history(project_root: Path, document: dict[str, Any]) -> None:
    """Record the architecture that is about to be replaced by an edit."""
    entries = read_architecture_history(project_root)
    entries.append(document)
    if len(entries) > ARCHITECTURE_HISTORY_MAX:
        entries = entries[-ARCHITECTURE_HISTORY_MAX:]
    atomic_write_json(
        managed_path(project_root, "architecture-history.json"),
        {"schema_version": 1, "entries": entries},
    )


def pop_architecture_history(project_root: Path) -> dict[str, Any] | None:
    """Remove and return the most recent previous architecture, if any."""
    entries = read_architecture_history(project_root)
    if not entries:
        return None
    previous = entries.pop()
    atomic_write_json(
        managed_path(project_root, "architecture-history.json"),
        {"schema_version": 1, "entries": entries},
    )
    return previous


def _normalize_chips(value: Any) -> list[dict[str, Any]]:
    """Keep persistable message chips, dropping anything that would not render twice.

    Chips are routed by their text, so a stale one is harmless: it either still applies or
    does nothing. The shape is validated here because this file is written by the client and
    read back into the UI.
    """
    if not isinstance(value, list):
        return []
    chips: list[dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        kind = str(raw.get("type") or "").strip()
        if not text or kind not in {"input", "view", "action"}:
            continue
        chip: dict[str, Any] = {"text": text, "type": kind}
        detail = raw.get("detail")
        if isinstance(detail, str) and detail:
            chip["detail"] = detail
        if raw.get("localSummary"):
            chip["localSummary"] = True
        chips.append(chip)
    return chips


def normalize_conversation_messages(messages: Any) -> list[dict[str, Any]]:
    """Clean persisted conversation messages before they reach the UI.

    Older versions could append the previous project's transcript to a newly opened
    one, and the restore notice itself was saved as a message. Both produce lines that
    are not real conversation: consecutive duplicates and synthetic "已恢复 N 条对话
    记录" notices. They are dropped here so a polluted file restores cleanly instead of
    replaying the bug.
    """
    if not isinstance(messages, list):
        return []
    normalized: list[dict[str, Any]] = []
    for raw in messages:
        if not isinstance(raw, dict):
            continue
        role = str(raw.get("role") or "").strip()
        content = str(raw.get("content") or "").strip()
        if role not in {"user", "assistant"} or not content:
            continue
        if content.startswith("已恢复") and "条对话记录" in content:
            continue
        entry: dict[str, Any] = {"role": role, "content": content}
        if raw.get("timestamp"):
            entry["timestamp"] = str(raw["timestamp"])
        chips = _normalize_chips(raw.get("chips"))
        if chips:
            entry["chips"] = chips
        if (
            normalized
            and normalized[-1]["role"] == role
            and normalized[-1]["content"] == content
        ):
            continue
        normalized.append(entry)
    return normalized


def visible_entries(project_root: Path) -> list[Path]:
    if not project_root.exists():
        return []
    ignored = {MANAGED_DIR, ".git", "__pycache__", ".DS_Store"}
    return sorted(
        (entry for entry in project_root.iterdir() if entry.name not in ignored),
        key=lambda entry: (not entry.is_dir(), entry.name.lower()),
    )


def detect_mode(project_root: Path) -> str:
    meaningful = [
        entry
        for entry in visible_entries(project_root)
        if entry.name not in {"AI_ARCH.md", "README.md"}
    ]
    return "imported" if meaningful else "new"


def migrate_architecture_shape(
    architecture: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Bring a pre-graph architecture dict up to the graph schema.

    Legacy shapes seen in the wild: no `edges` at all (generated by
    `finalize_bootstrap`, which wrote bare `depends_on`), and a hand-authored
    `dependencies` array carrying prose types like "loopback JSON API". Both are
    absorbed. Idempotent: re-running on migrated data changes nothing.
    """
    migrated = dict(architecture)
    changed = False

    raw_modules = migrated.get("modules")
    modules = raw_modules if isinstance(raw_modules, list) else []
    normalized_modules: list[dict[str, Any]] = []
    for raw_module in modules:
        if not isinstance(raw_module, dict):
            continue
        module = dict(raw_module)
        module_id = optional_slug(str(module.get("id") or module.get("name") or ""))
        if not module_id:
            continue
        module["id"] = module_id
        if not str(module.get("name") or "").strip():
            # Hand-authored files often omit `name`; without it nodes render blank.
            module["name"] = module_id.replace("-", " ").replace("_", " ").title()
            changed = True
        if "brief" not in module:
            module["brief"] = normalize_brief(
                module.get("responsibility") or module.get("name")
            )
            changed = True
        if "needs_ui" not in module:
            module["needs_ui"] = False
            changed = True
        if "group" not in module:
            module["group"] = None
            changed = True
        if not isinstance(module.get("depends_on"), list):
            module["depends_on"] = []
            changed = True
        normalized_modules.append(module)
    if normalized_modules or modules:
        migrated["modules"] = normalized_modules

    module_ids = {module["id"] for module in normalized_modules}

    if not isinstance(migrated.get("groups"), list):
        migrated["groups"] = []
        changed = True

    if not isinstance(migrated.get("edges"), list):
        legacy = migrated.get("dependencies")
        edges: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        if isinstance(legacy, list):
            for raw_edge in legacy:
                if not isinstance(raw_edge, dict):
                    continue
                source = optional_slug(str(raw_edge.get("from") or ""))
                target = optional_slug(str(raw_edge.get("to") or ""))
                if not source or not target or source == target:
                    continue
                if source not in module_ids or target not in module_ids:
                    continue
                kind = normalize_edge_type(
                    raw_edge.get("kind") or raw_edge.get("type")
                )
                key = (source, target, kind)
                if key in seen:
                    continue
                seen.add(key)
                edges.append(
                    {
                        "from": source,
                        "to": target,
                        "kind": kind,
                        "label": str(raw_edge.get("label") or "").strip()[:80],
                    }
                )
        for edge in edges_from_modules(normalized_modules):
            key = (edge["from"], edge["to"], edge["kind"])
            if edge["to"] in module_ids and key not in seen:
                seen.add(key)
                edges.append(edge)
        migrated["edges"] = edges
        changed = True

    return migrated, changed


def migrate_managed_directory(project_root: Path) -> list[str]:
    """Upgrade an existing `.docuagent/` in place. Returns changed file names."""
    changed: list[str] = []

    bootstrap_path = managed_path(project_root, "bootstrap.json")
    try:
        bootstrap = read_json(bootstrap_path)
    except WorkspaceError:
        bootstrap = None
    if bootstrap and isinstance(bootstrap.get("architecture"), dict):
        migrated, dirty = migrate_architecture_shape(bootstrap["architecture"])
        if dirty:
            bootstrap["architecture"] = migrated
            bootstrap["updated_at"] = utc_now()
            atomic_write_json(bootstrap_path, bootstrap)
            changed.append("bootstrap.json")

    architecture_path = managed_path(project_root, "architecture.json")
    try:
        architecture_doc = read_json(architecture_path)
    except WorkspaceError:
        architecture_doc = None
    if architecture_doc:
        migrated, dirty = migrate_architecture_shape(architecture_doc)
        if dirty:
            atomic_write_json(architecture_path, migrated)
            changed.append("architecture.json")

    ui_state_path = managed_path(project_root, "ui-state.json")
    if (project_root / MANAGED_DIR).exists() and not ui_state_path.exists():
        atomic_write_json(ui_state_path, default_ui_state())
        changed.append("ui-state.json")

    return changed


def architecture_document(project_root: Path) -> dict[str, Any] | None:
    """Read `.docuagent/architecture.json` as a standalone architecture source.

    A finalized project has architecture here but may have no live `bootstrap.json`
    (hand-authored projects never had one, and a migrated project may predate it).
    The graph canvas must still render in that case, so this is a first-class source
    rather than a fallback detail of the interview state.
    """
    document = read_json(managed_path(project_root, "architecture.json"))
    if not document:
        return None
    migrated, _ = migrate_architecture_shape(document)
    return migrated


def inspect_workspace(
    raw_path: str,
    public_state: Callable[[dict[str, Any] | None], dict[str, Any] | None],
) -> dict[str, Any]:
    """Full workspace snapshot for the frontend.

    `public_state` is injected rather than imported: it lives in the HTTP layer, and
    importing it here would make workspace depend on its own caller.

    Architecture resolution order is live `bootstrap.json`, then `architecture.json`,
    then None — finished and hand-authored projects have no bootstrap state but must
    still render.
    """
    project_root = resolve_project_path(raw_path)
    migrate_managed_directory(project_root)
    entries = visible_entries(project_root)
    state = read_json(managed_path(project_root, "bootstrap.json"))
    document = architecture_document(project_root)
    architecture = None
    if state and state.get("architecture", {}).get("modules"):
        architecture = state["architecture"]
    elif document and document.get("modules"):
        architecture = document
    conversation = read_conversation_history(project_root)
    return {
        "path": str(project_root),
        "exists": project_root.exists(),
        "mode": detect_mode(project_root),
        "entries": [
            {"name": entry.name, "kind": "directory" if entry.is_dir() else "file"}
            for entry in entries[:80]
        ],
        "bootstrap": public_state(state) if state else None,
        "architecture": architecture,
        "project_name": (
            (state or {}).get("project", {}).get("name")
            or (document or {}).get("project", {}).get("name")
            or project_root.name
        ),
        "ui_state": read_ui_state(project_root),
        "conversation": conversation,
        "architecture_can_undo": len(read_architecture_history(project_root)) > 0,
        "stale_modules": sorted(read_stale_modules(project_root)),
    }


def ask_directory(initial_path: str = "") -> str:
    """Native folder picker.

    Known risk: tkinter runs on an HTTP worker thread here. It works today, but Tk
    off the main thread is documented as unstable on Windows. If the picker stops
    appearing, dispatch to the main thread via a queue rather than debugging Tk.
    """
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError as exc:
        raise WorkspaceError("当前 Python 环境不支持系统文件夹选择器。") from exc

    initial_directory = (
        Path(initial_path).expanduser() if initial_path else Path.home()
    )
    while (
        not initial_directory.exists()
        and initial_directory != initial_directory.parent
    ):
        initial_directory = initial_directory.parent
    if not initial_directory.is_dir():
        initial_directory = Path.home()

    root = None
    try:
        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        root.update()
        selected = filedialog.askdirectory(
            parent=root,
            title="选择 DocuAgent 项目文件夹",
            initialdir=str(initial_directory),
            mustexist=False,
        )
    except tk.TclError as exc:
        raise WorkspaceError(f"无法打开系统文件夹选择器：{exc}") from exc
    finally:
        if root is not None:
            try:
                root.destroy()
            except tk.TclError:
                pass
    return str(selected or "")
