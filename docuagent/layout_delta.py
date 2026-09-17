"""Structured layout intent and deterministic layout diffs.

A ``layout_delta`` describes what should change about a DOM node's flex/grid
container, the node's own sizing/flex constraints, and its grid placement. The
module also turns two ``dom_layout_snapshot`` nodes into a comparable delta, so
the same DOM change always produces the same structured result.
"""

from __future__ import annotations

import re
from typing import Any

from core import WorkspaceError

LAYOUT_DELTA_SCHEMA_VERSION = 1

CONTAINER_FIELDS = (
    "display",
    "direction",
    "gap",
    "align",
    "justify",
    "padding",
    "margin",
)
ELEMENT_FIELDS = (
    "x",
    "y",
    "position",
    "width",
    "height",
    "min_width",
    "max_width",
    "min_height",
    "max_height",
    "flex_grow",
    "flex_shrink",
    "flex_basis",
    "order",
    "align_self",
)
GRID_FIELDS = (
    "template_columns",
    "template_rows",
    "gap",
    "column_span",
    "row_span",
)


def _text(value: Any, fallback: str = "") -> str:
    return str(value or "").strip() or fallback


def _number(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed.is_integer():
        return int(parsed)
    return round(parsed, 2)


def _length(value: Any) -> int | float | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if text in {"auto", "none", "normal", "0px"}:
        return None
    match = re.match(r"^([+-]?(?:\d+\.?\d*|\.\d+))(?:px)?$", text)
    if not match:
        return None
    return _number(match.group(1))


def _span(value: Any) -> int | None:
    if value is None or value == "":
        return None
    parts = re.split(r"\s*/\s*", str(value).strip())
    if len(parts) == 2:
        try:
            return int(parts[1]) - int(parts[0])
        except (TypeError, ValueError):
            return None
    try:
        return int(parts[0])
    except (TypeError, ValueError):
        return None


def _canonical_section(raw: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return {key: raw[key] for key in fields if key in raw}


def validate_layout_delta(payload: Any) -> dict[str, Any]:
    """Validate the stable shape shared by generated and model-supplied deltas."""
    if not isinstance(payload, dict):
        raise WorkspaceError("layout_delta 必须是 JSON 对象。")
    if payload.get("schema_version") != LAYOUT_DELTA_SCHEMA_VERSION:
        raise WorkspaceError(
            f"不支持的 layout_delta 版本：{payload.get('schema_version')!r}"
        )
    if not _text(payload.get("reason")):
        raise WorkspaceError("layout_delta 缺少变更理由 reason。")
    for name in ("container", "element", "grid"):
        if not isinstance(payload.get(name), dict):
            raise WorkspaceError(f"layout_delta 的 {name} 必须是对象。")
    return payload


def normalize_layout_delta(raw: Any, module_id: str = "", reason: str = "") -> dict[str, Any]:
    if not isinstance(raw, dict):
        raw = {}
    payload: dict[str, Any] = {
        "schema_version": LAYOUT_DELTA_SCHEMA_VERSION,
        "module_id": _text(raw.get("module_id"), module_id),
        "element_id": _text(raw.get("element_id")),
        "selector": _text(raw.get("selector")),
        "source_hint": _text(raw.get("source_hint")),
        "reason": _text(raw.get("reason"), reason),
        "container": _canonical_section(raw.get("container"), CONTAINER_FIELDS),
        "element": _canonical_section(raw.get("element"), ELEMENT_FIELDS),
        "grid": _canonical_section(raw.get("grid"), GRID_FIELDS),
    }
    changed = raw.get("changed")
    if isinstance(changed, list):
        payload["changed"] = [str(item) for item in changed if str(item).strip()]
    return validate_layout_delta(payload)


def _changed_values(
    before: dict[str, Any],
    after: dict[str, Any],
    fields: tuple[str, ...],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key in fields:
        if key in after and before.get(key) != after.get(key):
            result[key] = after[key]
    return result


def _node_size(node: dict[str, Any], key: str) -> int | float | None:
    rect = node.get("rect")
    if isinstance(rect, dict) and key in rect:
        return _number(rect[key])
    layout = node.get("layout")
    if isinstance(layout, dict) and key in layout:
        return _number(layout[key])
    return None


def _build_container(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_layout = before.get("layout") or {}
    after_layout = after.get("layout") or {}
    raw: dict[str, Any] = {}
    if before_layout.get("display") != after_layout.get("display"):
        raw["display"] = _text(after_layout.get("display"))
    direction = (
        after_layout.get("flex_direction")
        or after_layout.get("direction")
        or ""
    )
    before_direction = (
        before_layout.get("flex_direction")
        or before_layout.get("direction")
        or ""
    )
    if before_direction != direction:
        raw["direction"] = _text(direction)
    gap = after_layout.get("gap")
    before_gap = before_layout.get("gap")
    if before_gap != gap:
        raw["gap"] = _text(gap)
    align = after_layout.get("align_items")
    before_align = before_layout.get("align_items")
    if before_align != align:
        raw["align"] = _text(align)
    justify = after_layout.get("justify_content")
    before_justify = before_layout.get("justify_content")
    if before_justify != justify:
        raw["justify"] = _text(justify)
    for key in ("padding", "margin"):
        before_value = before_layout.get(key)
        after_value = after_layout.get(key)
        if before_value != after_value and isinstance(after_value, list):
            raw[key] = [_number(item) for item in after_value]
    return raw


def _build_element(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_layout = before.get("layout") or {}
    after_layout = after.get("layout") or {}
    result: dict[str, Any] = {}
    before_rect = before.get("rect")
    after_rect = after.get("rect")
    if isinstance(before_rect, dict) and isinstance(after_rect, dict):
        for key in ("x", "y"):
            before_value = _number(before_rect.get(key))
            after_value = _number(after_rect.get(key))
            if before_value != after_value and after_value is not None:
                result[key] = after_value
    if before_layout.get("position") != after_layout.get("position"):
        position = _text(after_layout.get("position"))
        if position and position != "static":
            result["position"] = position
    for key in ("width", "height"):
        before_value = _node_size(before, key)
        after_value = _node_size(after, key)
        if before_value != after_value and after_value is not None:
            result[key] = after_value
    for key, source in (
        ("min_width", "min_width"),
        ("max_width", "max_width"),
        ("min_height", "min_height"),
        ("max_height", "max_height"),
        ("flex_grow", "flex_grow"),
        ("flex_shrink", "flex_shrink"),
        ("flex_basis", "flex_basis"),
        ("order", "order"),
        ("align_self", "align_self"),
    ):
        before_value = before_layout.get(source)
        after_value = after_layout.get(source)
        if before_value == after_value:
            continue
        if source in {"min_width", "max_width", "min_height", "max_height"}:
            value: Any = _length(after_value)
        elif source in {"flex_grow", "flex_shrink", "order"}:
            value = _number(after_value)
        else:
            value = _text(after_value)
        if value not in (None, ""):
            result[key] = value
    return result


def _build_grid(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_layout = before.get("layout") or {}
    after_layout = after.get("layout") or {}
    result: dict[str, Any] = {}
    for key, source in (
        ("template_columns", "grid_template_columns"),
        ("template_rows", "grid_template_rows"),
        ("gap", "gap"),
    ):
        before_value = before_layout.get(source)
        after_value = after_layout.get(source)
        if before_value != after_value:
            value = _text(after_value)
            if value and value not in {"none", "normal"}:
                result[key] = value
    before_column = _span(before_layout.get("grid_column"))
    after_column = _span(after_layout.get("grid_column"))
    if before_column != after_column and after_column is not None:
        result["column_span"] = after_column
    before_row = _span(before_layout.get("grid_row"))
    after_row = _span(after_layout.get("grid_row"))
    if before_row != after_row and after_row is not None:
        result["row_span"] = after_row
    return result


def build_layout_delta_from_nodes(
    before: dict[str, Any],
    after: dict[str, Any],
    module_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Diff two layout nodes into a deterministic layout_delta."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise WorkspaceError("布局 delta 需要 before/after 节点。")
    container = _build_container(before, after)
    element = _build_element(before, after)
    grid = _build_grid(before, after)
    changed: list[str] = []
    for section, values in (
        ("container", container),
        ("element", element),
        ("grid", grid),
    ):
        changed.extend(f"{section}.{key}" for key in sorted(values))
    delta = normalize_layout_delta(
        {
            "module_id": module_id,
            "element_id": _text(after.get("id")),
            "selector": _text(after.get("selector")),
            "source_hint": _text(after.get("source_hint")),
            "reason": reason or "布局快照发生变化",
            "container": container,
            "element": element,
            "grid": grid,
            "changed": sorted(changed),
        },
        module_id=module_id,
    )
    return delta


def _find_node_path(
    snapshot: dict[str, Any],
    element_id: str | None,
    selector: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    root = snapshot.get("root")
    if not isinstance(root, dict):
        raise WorkspaceError("布局快照缺少 root 节点。")
    queue: list[tuple[list[dict[str, Any]], dict[str, Any]]] = [([], root)]
    while queue:
        path, node = queue.pop(0)
        if element_id and node.get("id") == element_id:
            return path, node
        if selector and node.get("selector") == selector:
            return path, node
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    queue.append((path + [node], child))
    raise WorkspaceError("布局快照中找不到目标元素。")


def _find_node(
    snapshot: dict[str, Any],
    element_id: str | None,
    selector: str | None,
) -> dict[str, Any]:
    return _find_node_path(snapshot, element_id, selector)[1]


def build_layout_delta_from_target(
    element: dict[str, Any],
    targets: dict[str, Any],
    module_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    """Build a layout_delta from explicit target attributes.

    Lets the frontend property panel express a change without requiring a
    before/after snapshot pair: the caller states the target values directly,
    and the section field whitelists keep the result canonical and safe.
    """
    container = targets.get("container") if isinstance(targets.get("container"), dict) else {}
    elem_targets = targets.get("element") if isinstance(targets.get("element"), dict) else {}
    grid = targets.get("grid") if isinstance(targets.get("grid"), dict) else {}
    changed: list[str] = []
    for section, values in (
        ("container", container),
        ("element", elem_targets),
        ("grid", grid),
    ):
        changed.extend(f"{section}.{key}" for key in sorted(values))
    return normalize_layout_delta(
        {
            "module_id": module_id,
            "element_id": _text(element.get("id")),
            "selector": _text(element.get("selector")),
            "source_hint": _text(element.get("source_hint")),
            "reason": reason or "用户指定目标布局属性",
            "container": container,
            "element": elem_targets,
            "grid": grid,
            "changed": sorted(changed),
        },
        module_id=module_id,
    )


def build_layout_delta_from_snapshots(
    before_snapshot: dict[str, Any],
    after_snapshot: dict[str, Any],
    element_id: str | None = None,
    selector: str | None = None,
    module_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    if not isinstance(before_snapshot, dict) or not isinstance(after_snapshot, dict):
        raise WorkspaceError("布局 delta 需要 before/after 快照。")
    before = _find_node(before_snapshot, element_id, selector)
    after = _find_node(after_snapshot, element_id, selector)
    module_id = module_id or _text(after_snapshot.get("module_id"))
    return build_layout_delta_from_nodes(before, after, module_id, reason)


def _compact_node(node: dict[str, Any], max_children: int) -> dict[str, Any]:
    children = node.get("children")
    compact_children: list[dict[str, Any]] = []
    if isinstance(children, list):
        for child in children[:max_children]:
            if isinstance(child, dict):
                item = dict(child)
                item["children"] = []
                compact_children.append(item)
    compact = dict(node)
    compact["children"] = compact_children
    return compact


def layout_context_for_element(
    snapshot: dict[str, Any],
    element_id: str | None = None,
    selector: str | None = None,
    max_children: int = 10,
) -> dict[str, Any]:
    """Build a bounded layout context: ancestors plus the selected element."""
    if not isinstance(snapshot, dict):
        raise WorkspaceError("布局上下文需要布局快照。")
    path, node = _find_node_path(snapshot, element_id, selector)
    meta = snapshot.get("meta")
    return {
        "snapshot_meta": {
            "url": _text(snapshot.get("url")),
            "title": _text(snapshot.get("title")),
            "module_id": _text(snapshot.get("module_id")),
            "captured_at": _text(snapshot.get("captured_at")),
            "viewport": snapshot.get("viewport"),
            "node_count": (meta or {}).get("node_count"),
            "truncated": (meta or {}).get("truncated"),
        },
        "ancestors": [_compact_node(item, 0) for item in path],
        "element": _compact_node(node, max_children),
    }


def layout_delta_to_request_text(
    delta: dict[str, Any],
    annotations: str = "",
) -> str:
    """Render a layout_delta into a stable, model-friendly micro-task request."""
    if not isinstance(delta, dict) or delta.get("schema_version") != LAYOUT_DELTA_SCHEMA_VERSION:
        delta = normalize_layout_delta(delta)
    validate_layout_delta(delta)
    lines = [
        f"针对模块 `{_text(delta.get('module_id'), '未指定')}` 的布局微任务：",
        f"目标元素：{_text(delta.get('selector'), delta.get('element_id') or '未指定')}",
        f"变更理由：{delta.get('reason')}",
    ]
    annotations = _text(annotations)
    if annotations:
        lines.append(f"用户标注：{annotations}")

    sections = (
        ("容器布局", delta.get("container")),
        ("元素约束", delta.get("element")),
        ("网格布局", delta.get("grid")),
    )
    for title, values in sections:
        if not isinstance(values, dict) or not values:
            continue
        lines.append(f"{title}：")
        for key in sorted(values):
            value = values[key]
            if isinstance(value, list):
                value = ", ".join(str(item) for item in value)
            lines.append(f"- {key} = {value}")
    lines.append("请只按上述 layout_delta 修改对应元素/容器，保留其他布局不变。")
    return "\n".join(lines)


def layout_delta_schema() -> dict[str, Any]:
    return {
        "schema_version": LAYOUT_DELTA_SCHEMA_VERSION,
        "module_id": "architecture module id",
        "element_id": "layout node id",
        "selector": "stable selector",
        "source_hint": "source file/component hint",
        "reason": "short natural language change reason",
        "container": {
            "display": "block/flex/grid/...",
            "direction": "row/column/...",
            "gap": "computed gap",
            "align": "align-items",
            "justify": "justify-content",
            "padding": "[top, right, bottom, left]",
            "margin": "[top, right, bottom, left]",
        },
        "element": {
            "width": "number",
            "height": "number",
            "min_width": "number or null",
            "max_width": "number or null",
            "flex_grow": "number",
            "flex_shrink": "number",
            "flex_basis": "auto/number/...",
            "order": "number",
            "align_self": "auto/flex-start/...",
        },
        "grid": {
            "template_columns": "computed grid template columns",
            "template_rows": "computed grid template rows",
            "gap": "computed grid gap",
            "column_span": "number",
            "row_span": "number",
        },
        "changed": ["section.property"],
    }
