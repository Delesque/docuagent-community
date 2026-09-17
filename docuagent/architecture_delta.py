"""Canonical comparison of two architecture documents.

Ported in spirit from Archify's architecture-delta: canonicalize both sides first
(key order, list order), then compare. Every difference classifies as one of:

- ``added``   — a module, edge, or group exists only in the newer document
- ``removed`` — exists only in the older document
- ``changed`` — semantic fields differ (responsibility, edge reason, constraints…)
- ``moved``   — presentation-only fields differ (path, group, target_files); the
  module means the same thing, it just sits somewhere else

The ``moved``/``changed`` split is the whole point: a concurrent or iterative edit
that only relocates files must never surface as a semantic warning. Callers sort
``moved`` into a low-priority channel.

Only whitelisted fields participate. UI-state fields such as an edge's ``accepted``
flag or a document's ``generated_at`` are ignored by construction, so compare output
is stable regardless of viewer state or document envelope.
"""

from __future__ import annotations

from typing import Any

DELTA_SCHEMA_VERSION = 1

DELTA_KINDS = ("added", "removed", "changed", "moved")

# Per-entity semantic fields: a difference in any of these is a real change.
SEMANTIC_MODULE_FIELDS = ("name", "brief", "responsibility", "needs_ui", "verification", "depends_on")
SEMANTIC_EDGE_FIELDS = ("label", "reason")
SEMANTIC_GROUP_FIELDS = ("label", "kind", "members")
SEMANTIC_TOP_FIELDS = (
    "summary", "platform", "language", "runtime", "frameworks", "stack",
    "data", "integrations", "constraints", "verification", "risks", "unresolved",
)
# Presentation-only module fields: differences here are `moved`, not `changed`.
PRESENTATION_MODULE_FIELDS = ("path", "group", "target_files")

FIELD_LABELS = {
    "name": "名称", "brief": "简介", "responsibility": "职责", "needs_ui": "界面标记",
    "verification": "验证", "depends_on": "依赖", "path": "路径", "group": "分组",
    "target_files": "目标文件", "label": "标签", "reason": "原因", "kind": "类型",
    "members": "成员",
}
KIND_LABELS = {"added": "新增", "removed": "移除", "changed": "更新", "moved": "仅位置调整"}


def _canonical(value: Any) -> str:
    """Order-stable serialization used for equality: sorted keys, joined lists."""
    if isinstance(value, list):
        return f"[{','.join(_canonical(item) for item in value)}]"
    if isinstance(value, dict):
        return "{%s}" % ",".join(
            f"{key}:{_canonical(value[key])}" for key in sorted(value)
        )
    return repr(value)


def _sorted_by_key(items: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    return sorted(
        [item for item in items if isinstance(item, dict)],
        key=lambda item: str(item.get(key) or ""),
    )


def _edge_key(edge: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(edge.get("from") or ""),
        str(edge.get("to") or ""),
        str(edge.get("kind") or ""),
    )


def _changed_fields(
    before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...]
) -> list[str]:
    return [
        field for field in fields
        if _canonical(before.get(field)) != _canonical(after.get(field))
    ]


def _change(kind: str, subject: dict[str, Any], fields: list[str],
            message: str) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "kind": kind,
        "subject": subject,
        "message": message,
    }
    if fields:
        entry["fields"] = fields
    return entry


def _field_list_label(fields: list[str]) -> str:
    return "、".join(FIELD_LABELS.get(field, field) for field in fields)


def compare_architectures(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare two architecture documents and return the classified delta."""
    changes: list[dict[str, Any]] = []

    # --- top-level design fields -------------------------------------------
    for field in SEMANTIC_TOP_FIELDS:
        if _canonical(before.get(field)) != _canonical(after.get(field)):
            changes.append(_change(
                "changed",
                {"type": "architecture", "id": field},
                [field],
                f"顶层字段「{FIELD_LABELS.get(field, field)}」有更新。",
            ))

    # --- modules ------------------------------------------------------------
    before_modules = {str(m.get("id") or ""): m for m in before.get("modules", []) if isinstance(m, dict)}
    after_modules = {str(m.get("id") or ""): m for m in after.get("modules", []) if isinstance(m, dict)}
    for module_id in sorted(set(after_modules) - set(before_modules)):
        name = str(after_modules[module_id].get("name") or module_id)
        changes.append(_change("added", {"type": "module", "id": module_id}, [], f"新增模块 {name}。"))
    for module_id in sorted(set(before_modules) - set(after_modules)):
        name = str(before_modules[module_id].get("name") or module_id)
        changes.append(_change("removed", {"type": "module", "id": module_id}, [], f"移除模块 {name}。"))
    for module_id in sorted(set(before_modules) & set(after_modules)):
        before_module = before_modules[module_id]
        after_module = after_modules[module_id]
        name = str(after_module.get("name") or module_id)
        semantic = _changed_fields(before_module, after_module, SEMANTIC_MODULE_FIELDS)
        presentation = _changed_fields(before_module, after_module, PRESENTATION_MODULE_FIELDS)
        if semantic:
            changes.append(_change(
                "changed", {"type": "module", "id": module_id}, semantic,
                f"模块 {name} 更新：{_field_list_label(semantic)}。",
            ))
        elif presentation:
            changes.append(_change(
                "moved", {"type": "module", "id": module_id}, presentation,
                f"模块 {name} 仅位置调整：{_field_list_label(presentation)}。",
            ))

    # --- edges ---------------------------------------------------------------
    before_edges = {_edge_key(e): e for e in before.get("edges", []) if isinstance(e, dict)}
    after_edges = {_edge_key(e): e for e in after.get("edges", []) if isinstance(e, dict)}
    for key in sorted(set(after_edges) - set(before_edges)):
        source, target, kind = key
        changes.append(_change(
            "added", {"type": "edge", "from": source, "to": target, "kind": kind}, [],
            f"新增连线 {source} → {target}（{kind}）。",
        ))
    for key in sorted(set(before_edges) - set(after_edges)):
        source, target, kind = key
        changes.append(_change(
            "removed", {"type": "edge", "from": source, "to": target, "kind": kind}, [],
            f"移除连线 {source} → {target}（{kind}）。",
        ))
    for key in sorted(set(before_edges) & set(after_edges)):
        source, target, kind = key
        fields = _changed_fields(before_edges[key], after_edges[key], SEMANTIC_EDGE_FIELDS)
        if fields:
            changes.append(_change(
                "changed", {"type": "edge", "from": source, "to": target, "kind": kind}, fields,
                f"连线 {source} → {target}（{kind}）更新：{_field_list_label(fields)}。",
            ))

    # --- groups --------------------------------------------------------------
    before_groups = {str(g.get("id") or ""): g for g in before.get("groups", []) if isinstance(g, dict)}
    after_groups = {str(g.get("id") or ""): g for g in after.get("groups", []) if isinstance(g, dict)}
    for group_id in sorted(set(after_groups) - set(before_groups)):
        label = str(after_groups[group_id].get("label") or group_id)
        changes.append(_change("added", {"type": "group", "id": group_id}, [], f"新增分组 {label}。"))
    for group_id in sorted(set(before_groups) - set(after_groups)):
        label = str(before_groups[group_id].get("label") or group_id)
        changes.append(_change("removed", {"type": "group", "id": group_id}, [], f"移除分组 {label}。"))
    for group_id in sorted(set(before_groups) & set(after_groups)):
        fields = _changed_fields(before_groups[group_id], after_groups[group_id], SEMANTIC_GROUP_FIELDS)
        if fields:
            label = str(after_groups[group_id].get("label") or group_id)
            changes.append(_change(
                "changed", {"type": "group", "id": group_id}, fields,
                f"分组 {label} 更新：{_field_list_label(fields)}。",
            ))

    counts = {kind: 0 for kind in DELTA_KINDS}
    for change in changes:
        counts[change["kind"]] += 1
    return {"schema_version": DELTA_SCHEMA_VERSION, "counts": counts, "changes": changes}


def format_delta(delta: dict[str, Any]) -> list[str]:
    """Human-readable Chinese lines, one per change, in stable order."""
    return [str(change.get("message") or "") for change in delta.get("changes", [])]


def format_delta_summary(delta: dict[str, Any]) -> str | None:
    """One counts line, or None when nothing changed at all."""
    counts = delta.get("counts") or {}
    if not any(counts.get(kind) for kind in DELTA_KINDS):
        return None
    return (
        f"新增 {counts.get('added', 0)} · 移除 {counts.get('removed', 0)} · "
        f"更新 {counts.get('changed', 0)} · 仅位置调整 {counts.get('moved', 0)}"
    )
