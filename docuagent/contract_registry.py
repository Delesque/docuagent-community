"""Typed Contract Registry helpers (§3.2 / §3.3 / §3.7).

This module lives in the *contract domain* (alongside ``core`` / ``workspace`` /
``contract_lint``) and must **never** import the ``codeintel`` package. The only
module allowed to wire the contract domain to codeintel is
``main_routes_codeintel.py``; reconciliation is bridged there by flattening the
registry into a typed item list and handing it to ``symbol_index``.

Responsibilities:
- ``flatten_registry``: turn the six typed arrays in ``contracts.json`` into a
  single list of typed items, each carrying ``type`` / ``owner`` / ``id`` /
  ``status`` / ``source_ref`` / ``source_hash`` / ``last_seen``.
- ``apply_reconcile_to_contracts``: pure merge of reconcile output (filled
  ``source_hash`` / ``last_seen`` / ``status``) back into a contracts payload,
  matched by stable ``id``. No file I/O here.
"""

from __future__ import annotations

import copy
import logging
from typing import Any

logger = logging.getLogger(__name__)

# The array name -> registry type discriminator. `public_api` is realized via
# module.exports; `commands_events` via the commands array.
_ARRAY_TO_TYPE = {
    "modules": "public_api",   # only the exports sub-list, see flatten_registry
    "commands": "commands_events",
    "shared_kernel": "shared_kernel",
    "vocabulary": "vocabulary",
    "data_schema": "data_schema",
    "config_policy": "config_policy",
}


def _items_from_array(
    registry: dict[str, Any], array: str, type_name: str
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in registry.get(array, []) or []:
        if not isinstance(item, dict):
            continue
        if array == "modules":
            # For modules we flatten the exports sub-list (public_api), tagged
            # with the owning module id. Exports use `symbol` as the name key, so
            # normalize it to `name` for the reconcile matcher.
            for export in item.get("exports", []) or []:
                if not isinstance(export, dict):
                    logger.warning(
                        "contract_registry: 模块 %r 的 exports 含非对象条目，已跳过。",
                        item.get("id"),
                    )
                    continue
                symbol = export.get("symbol")
                eid = export.get("id")
                if not eid and not symbol:
                    # KNOWN_ISSUES #7：既无 id 也无 symbol 的 export 无法定位，
                    # 告警并丢弃，不再静默跳过。
                    logger.warning(
                        "contract_registry: 模块 %r 的 export 同时缺 `id` 与 `symbol`，"
                        "无法定位，已跳过。",
                        item.get("id"),
                    )
                    continue
                if not eid:
                    # export 以 `symbol` 为身份键；缺 `id` 时按稳定规则推导并告警，
                    # 避免静默丢弃（KNOWN_ISSUES #7）。
                    eid = core.registry_item_id("public_api", item.get("id") or "", symbol)
                    logger.warning(
                        "contract_registry: 模块 %r 的 export %r 缺 `id`，"
                        "已按 symbol 推导 `%s`（建议显式填写 `id`）。",
                        item.get("id"), symbol, eid,
                    )
                flat = {**export, "id": eid, "type": "public_api", "owner": item.get("id")}
                flat.setdefault("name", symbol)
                out.append(flat)
            continue
        flat = dict(item)
        flat["type"] = type_name
        if array in ("commands", "shared_kernel", "vocabulary", "data_schema", "config_policy"):
            flat.setdefault("owner", item.get("owner") or "")
        out.append(flat)
    return out


def flatten_registry(registry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every typed registry entry as a uniform item list.

    Each item keeps its original fields plus a derived ``type`` and (for exports)
    the owning module id. Items already carry ``id`` / ``status`` / ``source_ref``
    / ``source_hash`` / ``last_seen`` from ``core.normalize_contracts``.
    """
    if not isinstance(registry, dict):
        return []
    items: list[dict[str, Any]] = []
    items.extend(_items_from_array(registry, "modules", "public_api"))
    items.extend(_items_from_array(registry, "commands", "commands_events"))
    items.extend(_items_from_array(registry, "shared_kernel", "shared_kernel"))
    items.extend(_items_from_array(registry, "vocabulary", "vocabulary"))
    items.extend(_items_from_array(registry, "data_schema", "data_schema"))
    items.extend(_items_from_array(registry, "config_policy", "config_policy"))
    return items


def projection_graph(registry: dict[str, Any]) -> dict[str, Any]:
    """Aggregate projection graph for the typed registry: nodes, edges, impact.

    This is the contract-domain home of the §3 step3/step4 graph projection so any
    consumer (the codeintel HTTP route, the agent context builder, task completion
    boundaries) can reuse one implementation without touching the codeintel
    package. Shapes:

    - ``nodes``: id / type / owner / name / status / file / line / public
    - ``edges``: ``owns`` (module -> entry), ``depends_on`` (owner A's public_api
      entry -> module B = A depends on B), ``uses`` (shared_kernel entry ->
      consumer module = consumer depends on the entry's owner)
    - ``impact``: :func:`impact_for_roots` seeded with stale entries' owners.
    """
    items = flatten_registry(registry)

    nodes: list[dict[str, Any]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        name = (
            it.get("name")
            or it.get("symbol")
            or it.get("term")
            or it.get("id")
            or ""
        )
        status = it.get("status") or "active"
        nodes.append(
            {
                "id": it.get("id"),
                "type": it.get("type"),
                "owner": it.get("owner") or "",
                "name": name,
                "status": status,
                "file": it.get("source_ref") or "",
                "line": it.get("line") if isinstance(it.get("line"), int) else None,
                "public": status not in ("deprecated", "orphan", "unregistered"),
            }
        )

    edges: list[dict[str, Any]] = []
    # owns: module -> the registry entries it owns.
    for n in nodes:
        if n["owner"]:
            edges.append(
                {
                    "from": n["owner"],
                    "to": n["id"],
                    "kind": "owns",
                    "label": n["type"],
                    "reason": "registry",
                }
            )

    # depends_on: a module's public API entries -> the module it depends on.
    for m in registry.get("modules", []) or []:
        if not isinstance(m, dict):
            continue
        mid = m.get("id")
        if not mid:
            continue
        for dep in m.get("depends_on", []) or []:
            for n in nodes:
                if n["owner"] == mid and n["type"] == "public_api":
                    edges.append(
                        {
                            "from": n["id"],
                            "to": dep,
                            "kind": "depends_on",
                            "label": "依赖",
                            "reason": f"{mid} -> {dep}",
                        }
                    )

    # uses: shared_kernel entry -> consumer module.
    for n in nodes:
        if n["type"] != "shared_kernel":
            continue
        src = next((it for it in items if it.get("id") == n["id"]), None)
        for consumer in (src or {}).get("consumers", []) or []:
            edges.append(
                {
                    "from": n["id"],
                    "to": consumer,
                    "kind": "uses",
                    "label": "消费",
                    "reason": "shared_kernel",
                }
            )

    stale_roots = sorted(
        {n["owner"] for n in nodes if n.get("status") == "stale" and n.get("owner")}
    )
    return {
        "nodes": nodes,
        "edges": edges,
        "impact": impact_for_roots(nodes, edges, stale_roots),
    }


def impact_for_roots(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    roots: list[str],
) -> dict[str, Any]:
    """Propagate changes at ``roots`` to every downstream consumer.

    Walks the *reverse* dependency graph (who depends on me) transitively from each
    root module; every reachable module must re-adapt, directly or through a module
    it depends on. Pure function — callers decide whether to persist, display, or
    inject the result. ``depends_on`` and ``uses`` edges run in opposite directions
    (see :func:`projection_graph`), and both are normalized here into
    ``dependents[target] = {modules that depend on target}``.
    """
    owner_of = {n["id"]: n["owner"] for n in nodes if n.get("id")}
    clean_roots = sorted({root for root in roots if root})

    dependents: dict[str, set[str]] = {}
    for e in edges:
        kind = e.get("kind")
        source_owner = owner_of.get(e.get("from"), "")
        target = e.get("to") or ""
        if not source_owner or not target or source_owner == target:
            continue
        if kind == "depends_on":
            dependents.setdefault(target, set()).add(source_owner)
        elif kind == "uses":
            dependents.setdefault(source_owner, set()).add(target)

    depth: dict[str, int] = {root: 0 for root in clean_roots}
    queue = list(clean_roots)
    while queue:
        current = queue.pop(0)
        for dependent in sorted(dependents.get(current, ())):
            if dependent not in depth:
                depth[dependent] = depth[current] + 1
                queue.append(dependent)

    affected = [
        {"module_id": module_id, "depth": level}
        for module_id, level in sorted(depth.items(), key=lambda kv: (kv[1], kv[0]))
        if level > 0
    ]
    return {"roots": clean_roots, "affected": affected, "count": len(affected)}


def upstream_changes_for_module(registry: dict[str, Any], module_id: str) -> dict[str, Any]:
    """Impact summary for one module: what changed upstream, how far away.

    Empty dict when the module neither owns a stale entry nor sits downstream of
    one. Callers inject this into agent context so an agent that is about to build
    against a moved interface learns it before writing code, not after.
    """
    own = str(module_id or "")
    if not own:
        return {}
    graph = projection_graph(registry)
    impact = graph["impact"]
    own_stale = [
        n for n in graph["nodes"] if n.get("owner") == own and n.get("status") == "stale"
    ]
    affected = next(
        (a for a in impact["affected"] if a["module_id"] == own), None
    )
    if not own_stale and affected is None:
        return {}
    relevant_roots = sorted(set(impact["roots"]) | ({own} if own_stale else set()))
    changed = [
        {
            "id": n["id"],
            "type": n["type"],
            "name": n["name"],
            "owner": n["owner"],
            "file": n["file"],
            "line": n["line"],
        }
        for n in graph["nodes"]
        if n.get("status") == "stale" and n.get("owner") in relevant_roots
    ]
    return {
        "depth": affected["depth"] if affected else 0,
        "roots": relevant_roots,
        "changed": changed[:10],
    }


# Presentation-only entry fields: a difference here means the contract means the
# same thing but sits somewhere else (file moved, hash refreshed) — `moved`, the
# low-priority channel, never a semantic warning. Mirrors architecture_delta.
_CONTRACT_PRESENTATION_FIELDS = ("source_ref", "line", "source_hash", "last_seen")

# Key and derived fields: `name` mirrors symbol/term, and id/type/owner ARE the
# entry's key — they can never differ for the same entry id.
_CONTRACT_DERIVED_FIELDS = frozenset({"name", "id", "type", "owner"})

# Module-level fields compared as whole lists: a dependency added or removed is
# semantic; the module record itself has no presentation-only fields.
_MODULE_SEMANTIC_FIELDS = ("depends_on", "consumes", "path")

DELTA_SCHEMA_VERSION = 1
DELTA_KINDS = ("added", "removed", "changed", "moved")

_FIELD_LABELS = {
    "symbol": "符号", "kind": "类别", "signature": "签名", "summary": "摘要",
    "description": "说明", "term": "术语", "definition": "定义", "status": "状态",
    "consumers": "消费方", "value": "取值", "default": "默认值", "source_ref": "位置",
    "depends_on": "依赖", "consumes": "消费", "path": "路径", "owner": "归属",
    "type": "类型",
}


def _canonical(value: Any) -> str:
    """Order-stable serialization for equality: sorted keys, joined lists."""
    if isinstance(value, list):
        return f"[{','.join(_canonical(item) for item in value)}]"
    if isinstance(value, dict):
        return "{%s}" % ",".join(f"{k}:{_canonical(value[k])}" for k in sorted(value))
    return repr(value)


def _changed_fields(
    before: dict[str, Any], after: dict[str, Any], fields: tuple[str, ...] | list[str]
) -> list[str]:
    return [
        field for field in fields
        if _canonical(before.get(field)) != _canonical(after.get(field))
    ]


def _entry_name(item: dict[str, Any]) -> str:
    return str(item.get("name") or item.get("symbol") or item.get("term") or item.get("id") or "")


def _module_semantic_fields(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Module-level records keyed by id, carrying their semantic fields only."""
    out: dict[str, dict[str, Any]] = {}
    for m in registry.get("modules", []) or []:
        if not isinstance(m, dict) or not m.get("id"):
            continue
        out[str(m["id"])] = {field: m.get(field) for field in _MODULE_SEMANTIC_FIELDS}
    return out


def contracts_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Classified diff of two contract registries (architecture_delta for contracts).

    Same four-way classification and the same critical property: an entry that only
    moved files (source_ref / hash / last_seen) is `moved`, never a semantic
    warning — so a concurrent reconcile that re-locates an unchanged contract does
    not flood the change channel. Entries are keyed by their stable registry id;
    module-level records (depends_on / consumers / path) are compared separately.
    Output shape mirrors architecture_delta: {schema_version, counts, changes}
    with ready-to-display Chinese messages.
    """
    changes: list[dict[str, Any]] = []

    def _change(kind: str, subject: dict[str, Any], fields: list[str], message: str) -> None:
        entry: dict[str, Any] = {"kind": kind, "subject": subject, "message": message}
        if fields:
            entry["fields"] = fields
        changes.append(entry)

    def _label(fields: list[str]) -> str:
        return "、".join(_FIELD_LABELS.get(f, f) for f in fields)

    before_items = {
        str(item.get("id") or ""): item
        for item in flatten_registry(before)
        if isinstance(item, dict) and item.get("id")
    }
    after_items = {
        str(item.get("id") or ""): item
        for item in flatten_registry(after)
        if isinstance(item, dict) and item.get("id")
    }

    semantic_fields: list[str] = []
    for sample in list(before_items.values()) + list(after_items.values()):
        for field in sample:
            if (
                field not in _CONTRACT_PRESENTATION_FIELDS
                and field not in _CONTRACT_DERIVED_FIELDS
                and field not in semantic_fields
            ):
                semantic_fields.append(field)

    for entry_id in sorted(set(after_items) - set(before_items)):
        item = after_items[entry_id]
        _change(
            "added", {"type": "contract", "id": entry_id, "owner": item.get("owner") or ""},
            [], f"新增契约 {item.get('type') or ''}:{_entry_name(item)}。",
        )
    for entry_id in sorted(set(before_items) - set(after_items)):
        item = before_items[entry_id]
        _change(
            "removed", {"type": "contract", "id": entry_id, "owner": item.get("owner") or ""},
            [], f"移除契约 {item.get('type') or ''}:{_entry_name(item)}。",
        )
    for entry_id in sorted(set(before_items) & set(after_items)):
        before_item = before_items[entry_id]
        after_item = after_items[entry_id]
        semantic = _changed_fields(before_item, after_item, semantic_fields)
        presentation = _changed_fields(before_item, after_item, _CONTRACT_PRESENTATION_FIELDS)
        name = _entry_name(after_item)
        kind_label = str(after_item.get("type") or "")
        if semantic:
            _change(
                "changed",
                {"type": "contract", "id": entry_id, "owner": after_item.get("owner") or ""},
                semantic,
                f"契约 {kind_label}:{name} 更新：{_label(semantic)}。",
            )
        elif presentation:
            _change(
                "moved",
                {"type": "contract", "id": entry_id, "owner": after_item.get("owner") or ""},
                presentation,
                f"契约 {kind_label}:{name} 仅位置调整：{_label(presentation)}。",
            )

    # Module-level records: depends_on / consumes / path.
    before_modules = _module_semantic_fields(before)
    after_modules = _module_semantic_fields(after)
    for module_id in sorted(set(after_modules) - set(before_modules)):
        _change("added", {"type": "module", "id": module_id}, [], f"新增模块记录 {module_id}。")
    for module_id in sorted(set(before_modules) - set(after_modules)):
        _change("removed", {"type": "module", "id": module_id}, [], f"移除模块记录 {module_id}。")
    for module_id in sorted(set(before_modules) & set(after_modules)):
        fields = _changed_fields(before_modules[module_id], after_modules[module_id], _MODULE_SEMANTIC_FIELDS)
        if fields:
            _change(
                "changed", {"type": "module", "id": module_id}, fields,
                f"模块 {module_id} 更新：{_label(fields)}。",
            )

    counts = {kind: 0 for kind in DELTA_KINDS}
    for change in changes:
        counts[change["kind"]] += 1
    return {"schema_version": DELTA_SCHEMA_VERSION, "counts": counts, "changes": changes}


def format_contracts_delta(delta: dict[str, Any]) -> list[str]:
    """One Chinese line per change, in stable order."""
    return [str(change.get("message") or "") for change in delta.get("changes", [])]


def apply_reconcile_to_contracts(
    registry: dict[str, Any], enriched: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return a new registry with reconcile output merged back by stable ``id``.

    Only ``source_hash`` / ``last_seen`` / ``status`` are written. Pure function:
    the caller decides whether (and how) to persist the result.
    """
    if not enriched:
        return registry
    by_id = {
        item["id"]: item
        for item in enriched
        if isinstance(item, dict) and item.get("id")
    }
    if not by_id:
        return registry
    updated = copy.deepcopy(registry)

    def _apply(arr: Any) -> None:
        if not isinstance(arr, list):
            return
        for entry in arr:
            if not isinstance(entry, dict) or entry.get("id") not in by_id:
                continue
            src = by_id[entry["id"]]
            if "source_hash" in src:
                entry["source_hash"] = src["source_hash"]
            if "last_seen" in src:
                entry["last_seen"] = src["last_seen"]
            if src.get("status"):
                entry["status"] = src["status"]

    for module in updated.get("modules", []) or []:
        if isinstance(module, dict):
            _apply(module.get("exports", []))
    _apply(updated.get("commands", []))
    _apply(updated.get("shared_kernel", []))
    _apply(updated.get("vocabulary", []))
    _apply(updated.get("data_schema", []))
    _apply(updated.get("config_policy", []))
    return updated


# --- §3 step4: multi-agent atomic delta (lock + optimistic concurrency) -------
#
# A single typed registry entry is owned by one module. When several sub-agents
# run in the same wave they each propose a *contract delta*; applying those
# concurrently must (a) never lose another agent's write, and (b) surface a
# conflict instead of silently overwriting when an agent's view of the registry
# has gone stale. The merge is pure (`apply_contract_delta`); the lock + version
# check live only in `apply_deltas_atomically` so the merge logic stays testable
# without any file I/O.

import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import core

# (registry type -> (top-level array name, identity key in that array)).
# `public_api` is special: it lives in `modules[].exports`, keyed by `symbol`.
_TYPE_TO_ARRAY: dict[str, tuple[str, str]] = {
    "public_api": ("modules", "symbol"),
    "data_schema": ("data_schema", "name"),
    "commands_events": ("commands", "name"),
    "config_policy": ("config_policy", "name"),
    "shared_kernel": ("shared_kernel", "symbol"),
    "vocabulary": ("vocabulary", "term"),
}


class ContractConflict(Exception):
    """Raised when a delta cannot be applied atomically.

    Reasons: the ``expected_hash`` no longer matches the on-disk registry
    (another agent wrote first), or the delta targets an entry that does not
    exist for an ``update``/``remove`` op, or the merged result fails
    ``core.normalize_contracts`` validation.
    """


def _minimal_contracts() -> dict[str, Any]:
    return {
        "schema_version": core.CONTRACTS_SCHEMA_VERSION,
        "project": {"name": "", "language": "", "runtime": ""},
        "modules": [],
        "vocabulary": [],
        "shared_kernel": [],
        "commands": [],
        "data_schema": [],
        "config_policy": [],
    }


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def contracts_hash(registry: dict[str, Any]) -> str:
    """Deterministic content hash used for optimistic-concurrency checks."""
    text = json.dumps(registry, ensure_ascii=False, sort_keys=True)
    return _sha256(text)


def _read_contracts(path: str | os.PathLike[str]) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        return _minimal_contracts()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _minimal_contracts()
    if not isinstance(data, dict):
        return _minimal_contracts()
    return data


def _atomic_write_json(path: str | os.PathLike[str], registry: dict[str, Any]) -> str:
    """Write via temp file + atomic os.replace. Returns the new content hash."""
    text = json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True)
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".delta.tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return _sha256(text)


def _ensure_module(registry: dict[str, Any], owner: str) -> None:
    """Create a stub module for ``owner`` if it is missing (agents may register
    contracts for a module before its architecture entry is finalized)."""
    if not owner or owner == "shared":
        return
    modules = registry.setdefault("modules", [])
    if not isinstance(modules, list):
        modules = []
        registry["modules"] = modules
    if not any(isinstance(m, dict) and m.get("id") == owner for m in modules):
        modules.append(
            {"id": owner, "path": "", "depends_on": [], "exports": [], "consumes": []}
        )


def _resolve_parent(
    registry: dict[str, Any], type_name: str, owner: str
) -> list[dict[str, Any]] | None:
    """Return the array a typed entry lives in (mutating ``registry``)."""
    if type_name == "public_api":
        for module in registry.get("modules", []) or []:
            if isinstance(module, dict) and module.get("id") == owner:
                parent = module.setdefault("exports", [])
                return parent if isinstance(parent, list) else []
        return None
    array_name, _ = _TYPE_TO_ARRAY[type_name]
    parent = registry.setdefault(array_name, [])
    if not isinstance(parent, list):
        parent = []
        registry[array_name] = parent
    return parent


def apply_contract_delta(registry: dict[str, Any], delta: dict[str, Any]) -> dict[str, Any]:
    """Pure: apply one delta to a registry copy and return the normalized result.

    ``delta`` shape::

        {"op": "add" | "update" | "remove",
         "type": <REGISTRY_TYPE>,
         "owner": <module id>,
         "name": <symbol | term | name>,
         "data": { ...fields... }}     # for add / update

    ``add`` is idempotent (re-adds merge into the existing entry); ``update``
    fails with :class:`ContractConflict` if the entry is absent; ``remove`` is a
    silent no-op when already gone. Ownership/bookkeeping fields (``id``,
    ``source_ref``, ``source_hash``, ``last_seen``) are preserved on merge.
    """
    if not isinstance(delta, dict):
        return registry
    op = str(delta.get("op") or "add").lower()
    type_name = str(delta.get("type") or "").strip()
    owner = str(delta.get("owner") or "").strip()
    name = str(
        delta.get("name") or delta.get("symbol") or delta.get("term") or ""
    ).strip()
    data = delta.get("data") or {}
    if not isinstance(data, dict):
        data = {}

    if type_name not in _TYPE_TO_ARRAY:
        raise core.WorkspaceError(f"未知契约类型：{type_name}")
    if not name:
        raise core.WorkspaceError("contract delta 缺少 name / symbol / term。")

    updated = copy.deepcopy(registry)
    _ensure_module(updated, owner)
    parent = _resolve_parent(updated, type_name, owner)
    if parent is None:
        raise core.WorkspaceError(f"找不到 owner 模块 `{owner}`，无法登记 public_api。")
    _, name_key = _TYPE_TO_ARRAY[type_name]

    idx = -1
    for i, entry in enumerate(parent):
        if isinstance(entry, dict) and str(entry.get(name_key) or "") == name:
            idx = i
            break

    if op == "remove":
        if idx >= 0:
            parent.pop(idx)
        return core.normalize_contracts(updated)

    if op == "update" and idx < 0:
        raise ContractConflict(
            f"update 目标条目不存在：{type_name}:{owner}.{name}"
        )

    # Build the patch, keeping bookkeeping fields out of the way for new entries
    # and preserving them when merging into an existing entry.
    patch: dict[str, Any] = {name_key: name, "owner": owner}
    for key, value in data.items():
        if key in ("id", "source_ref", "source_hash", "last_seen"):
            patch[key] = value  # caller may carry reconcile bookkeeping forward
        else:
            patch[key] = value

    if idx >= 0:
        existing = dict(parent[idx])
        for key, value in patch.items():
            if key in ("id", "source_ref", "source_hash", "last_seen") and key in existing:
                continue  # never clobber reconcile bookkeeping of an existing entry
            existing[key] = value
        parent[idx] = existing
    else:
        parent.append(patch)

    return core.normalize_contracts(updated)


@contextmanager
def _acquire_lock(contracts_path: str | os.PathLike[str], extra_lock: Any):
    """Cross-process mutex via an exclusive-create lock file, plus an optional
    in-process ``threading.Lock`` for deterministic test ordering."""
    if extra_lock is not None:
        extra_lock.acquire()
    lock_path = f"{os.path.abspath(contracts_path)}.delta.lock"
    deadline = time.monotonic() + 30
    fd: int | None = None
    try:
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
                break
            except (FileExistsError, PermissionError) as exc:
                # Windows can report access denied while another writer deletes
                # its lock. Retry within the same bounded acquisition window.
                if isinstance(exc, PermissionError) and os.name != "nt":
                    raise
                # Reap a lock left behind by a crashed writer (older than 60s).
                try:
                    if isinstance(exc, FileExistsError) and time.time() - os.path.getmtime(lock_path) > 60:
                        os.remove(lock_path)
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise ContractConflict("无法获取契约锁（超时）。") from exc
                time.sleep(0.01)
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                os.remove(lock_path)
            except OSError:
                pass
        if extra_lock is not None:
            extra_lock.release()


def apply_deltas_atomically(
    contracts_path: str | os.PathLike[str],
    deltas: dict[str, Any] | list[dict[str, Any]],
    *,
    expected_hash: str | None = None,
    lock: Any = None,
) -> dict[str, Any]:
    """Apply one or many deltas to ``contracts.json`` as a single atomic,
    concurrency-safe operation.

    - A file lock serializes writers across processes; ``lock`` adds an
      in-process ``threading.Lock`` for deterministic multi-thread tests.
    - When ``expected_hash`` is provided, the on-disk registry must still hash to
      that value (optimistic concurrency). A mismatch means another writer
      committed first and raises :class:`ContractConflict` — the caller must
      re-read and retry, never blindly overwrite.
    - Returns ``{"ok": True, "hash": <new content hash>, "applied": <count>}``.
    """
    if isinstance(deltas, dict):
        deltas = [deltas]
    deltas = [d for d in deltas if isinstance(d, dict)]
    if not deltas:
        return {"ok": True, "hash": contracts_hash(_read_contracts(contracts_path)), "applied": 0}

    with _acquire_lock(contracts_path, lock):
        current = _read_contracts(contracts_path)
        if expected_hash is not None and contracts_hash(current) != expected_hash:
            raise ContractConflict(
                "契约已被并发修改（expected_hash 过期），乐观合并失败，请重新读取后重试。"
            )
        try:
            updated = current
            for delta in deltas:
                updated = apply_contract_delta(updated, delta)
        except core.WorkspaceError as exc:
            raise ContractConflict(f"contract delta 被校验拒绝：{exc}")
        new_hash = _atomic_write_json(contracts_path, updated)
    return {"ok": True, "hash": new_hash, "applied": len(deltas)}


def promote_proposed_exports(
    project_root,
    payload: dict[str, Any],
    extra_texts: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Flip proposed exports to active once a real production file uses them.

    Declared exports start `proposed` — the architecture model's promise that a
    module will provide a symbol. A symbol turns `active` only when a production
    source file OUTSIDE the owning module (and outside tests) actually mentions
    it. Test-only references are not real consumers and must not promote
    anything; the owner defining its own promise is not a consumer either.

    `extra_texts` maps relative paths to their PENDING content (the patch being
    applied, not yet on disk). The bootstrap-time import scan cannot know about
    files sub-agents create later — a greenfield project scans empty — so the
    patch's own production files are the authoritative first consumers.
    """
    import re as _re
    from pathlib import Path as _Path

    modules = payload.get("modules")
    if not isinstance(modules, list):
        return payload

    proposed: list[tuple[str, str]] = []  # (module_id, symbol)
    for module in modules:
        if not isinstance(module, dict):
            continue
        mod_id = str(module.get("id") or "")
        for item in module.get("exports") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") == "proposed":
                symbol = str(item.get("symbol") or "").strip()
                if symbol:
                    proposed.append((mod_id, symbol))
    if not proposed:
        return payload

    import importscan as _importscan

    scan = _importscan.read_scan(project_root)

    is_test = _importscan._is_test_path
    prod_files: list[str] = []
    if scan:
        prod_files = [
            str(entry.get("path") or "")
            for entry in scan.get("files", [])
            if entry.get("kind") == "code" and not is_test(str(entry.get("path") or ""))
        ]
    # Patch files count as consumer candidates even when the scan is stale
    # (greenfield: the scan ran on an empty project). Docs and managed files
    # never count — their text routinely names the symbols without consuming
    # anything.
    for rel, _text in (extra_texts or {}).items():
        normalized = str(rel).replace("\\", "/").strip()
        if not normalized or normalized in prod_files:
            continue
        if normalized.startswith(".docuagent/") or is_test(normalized):
            continue
        if normalized.lower().endswith((".md", ".txt", ".json")):
            continue
        prod_files.append(normalized)
    if not prod_files:
        return payload

    scope_of: dict[str, list[str]] = {}
    for module in modules:
        if not isinstance(module, dict):
            continue
        mod_id = str(module.get("id") or "")
        scope = [str(module.get("path") or "").strip()]
        scope.extend(str(t) for t in (module.get("target_files") or []))
        scope_of[mod_id] = [item for item in scope if item]

    def _owned_by(rel: str) -> str | None:
        for mod_id, prefixes in scope_of.items():
            for prefix in prefixes:
                if not prefix:
                    continue
                if rel == prefix or rel.startswith(prefix.rstrip("/") + "/"):
                    return mod_id
        return None

    # Modules with an inbound dependency can only turn active through a real
    # production consumer. A module nobody depends on AND whose path is an
    # entry shape (bin/, main/cli/__main__) faces the outside world instead:
    # the CLI is invoked by users, not required by sibling modules. For those,
    # implementing the promised symbol in its own files is the only evidence
    # that can ever exist, so owner files count as consumers.
    _depended_on: set[str] = set()
    for module in modules:
        if not isinstance(module, dict):
            continue
        for dep in module.get("depends_on") or []:
            if isinstance(dep, str) and dep:
                _depended_on.add(dep)

    def _is_entry_module(mod_id: str, path_text: str) -> bool:
        if mod_id in _depended_on:
            return False
        parts = [part for part in path_text.replace("\\", "/").split("/") if part]
        if not parts:
            return False
        if parts[0] == "bin":
            return True
        stem = parts[-1].rsplit(".", 1)[0].lower()
        return stem in {"main", "cli", "__main__", "cmd", "app"}

    regexes = {
        symbol: _re.compile(r"\b" + _re.escape(symbol) + r"\b")
        for _, symbol in proposed
    }
    text_cache: dict[str, str] = {}
    root = _Path(project_root) if project_root else _Path()

    def _contains(rel: str, symbol: str) -> bool:
        if rel not in text_cache:
            if extra_texts and rel in extra_texts:
                text_cache[rel] = str(extra_texts[rel])
            else:
                try:
                    text_cache[rel] = (root / rel).read_text(
                        encoding="utf-8", errors="replace"
                    )
                except OSError:
                    text_cache[rel] = ""
        return regexes[symbol].search(text_cache[rel]) is not None

    def _referenced(mod_id: str, symbol: str) -> bool:
        entry = _is_entry_module(mod_id, str(scope_of.get(mod_id, [""])[0]))
        for rel in prod_files:
            owner = _owned_by(rel)
            if owner == mod_id and not entry:
                continue
            if _contains(rel, symbol):
                return True
        return False

    referenced: set[str] = set()
    for mod_id, symbol in proposed:
        if _referenced(mod_id, symbol):
            referenced.add(f"{mod_id}\x00{symbol}")

    for module in modules:
        if not isinstance(module, dict):
            continue
        mod_id = str(module.get("id") or "")
        for item in module.get("exports") or []:
            if not isinstance(item, dict):
                continue
            if str(item.get("status") or "") != "proposed":
                continue
            symbol = str(item.get("symbol") or "").strip()
            if f"{mod_id}\x00{symbol}" in referenced:
                item["status"] = "active"
    return payload
