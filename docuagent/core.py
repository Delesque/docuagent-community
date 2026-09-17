"""Schema constants, validation, and normalization.

Recovered from the pre-refactor `docuagent.py`. Zero third-party dependencies.

Two invariants worth knowing before editing:
- `event` edges are excluded from cycle detection on purpose: a publish/subscribe
  cycle between two modules is legitimate design, not a model error.
- `normalize_edge_type` degrades unknown prose to `uses` rather than raising, so
  legacy `.docuagent/` directories stay readable.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


MANAGED_DIR = ".docuagent"
SCHEMA_VERSION = 1
APP_VERSION = "0.1.0"

# Graph workbench schema. See .docuagent/features/graph-workbench.md.
UI_STATE_VERSION = 1
BRIEF_MAX_CHARS = 140

# Reasoning text shown when a "看看" chip is expanded. Reasoning models emit far more
# than a person reads in a chip, and the whole state file is rewritten on every turn,
# so this is capped rather than stored in full.
THINKING_MAX_CHARS = 600

WINDOW_BAR_SLOTS = 8
MIN_SCALE = 0.30
MAX_SCALE = 2.00

# The conversation is rendered as a node on the graph canvas, but it is NOT a module:
# it has no source path, is never generated, and is deliberately absent from
# `normalize_architecture` output — the architecture dict is echoed back to the model
# every turn, and a synthetic module there would read as one the agent had designed.
#
# The id is reserved here for one reason: layout state (pin, window-bar slot, outline
# expansion) is keyed by node id, and `prune_ui_state` drops keys that are not modules.
# Without this, pinning the conversation would silently fail to persist.
# See frontend/src/graph/conversationNode.ts.
CONVERSATION_NODE_ID = "__conversation__"
RESERVED_NODE_IDS = frozenset({CONVERSATION_NODE_ID})

# Closed set of dependency types. Each is redundantly encoded in the UI by color,
# stroke pattern, and hover label, so no type is distinguishable by color alone.
EDGE_TYPES = ("uses", "data", "event", "extends", "blocks")
DEFAULT_EDGE_TYPE = "uses"

# `event` is deliberately excluded: a publish/subscribe cycle between two modules is
# legitimate design, not a model error. Only these types constrain build order and
# task scheduling waves.
BUILD_ORDER_EDGE_TYPES = frozenset({"uses", "data", "extends", "blocks"})

# Stage 3: the first version is a small, discussable map. A ready draft must land in
# this range; an oversized draft is rejected immediately so the model merges helpers
# instead of accumulating one-function modules.
MIN_FIRST_VERSION_MODULES = 3
MAX_FIRST_VERSION_MODULES = 8

EDGE_TYPE_ALIASES = {
    "use": "uses",
    "call": "uses",
    "calls": "uses",
    "in-process-call": "uses",
    "depends": "uses",
    "depends-on": "uses",
    "import": "uses",
    "imports": "uses",
    "api": "uses",
    "rpc": "uses",
    "http": "uses",
    "shares-data": "data",
    "shared-data": "data",
    "reads": "data",
    "writes": "data",
    "storage": "data",
    "database": "data",
    "db": "data",
    "events": "event",
    "publish": "event",
    "subscribe": "event",
    "pubsub": "event",
    "message": "event",
    "queue": "event",
    "async": "event",
    "extend": "extends",
    "framework": "extends",
    "inherits": "extends",
    "implements": "extends",
    "contract": "extends",
    "block": "blocks",
    "build-order": "blocks",
    "after": "blocks",
    "order": "blocks",
}

GROUP_KINDS = ("framework", "layer")

# Project Contract Registry (.docuagent/contracts.json). See
# ARCHITECTURE_CONTRACT_SPEC.md for the design contract.
CONTRACTS_SCHEMA_VERSION = 1
CONTRACTS_FILE = "contracts.json"
CONTRACT_EXPORT_KINDS = ("function", "class", "method", "interface", "type", "constant", "other")

# §3.2 typed Registry node categories. `public_api` is realized via module.exports;
# `commands_events` via the `commands` array; the rest are top-level arrays.
# `data_schema` and `config_policy` were added in the §3 typed-registry extension
# (still schema_version 1 — purely additive). The discriminator is derived from
# which array an entry lives in (see contract_registry.flatten_registry) because
# some entry shapes already use a `type` field for their own purpose (e.g.
# vocabulary entries carry a format `type`), so we never overwrite it.
REGISTRY_TYPES = (
    "public_api",
    "data_schema",
    "commands_events",
    "config_policy",
    "shared_kernel",
    "vocabulary",
)
# "stale" is written by symbol_index reconcile (declared interface no longer
# matches reality). It MUST persist through normalize_contracts — losing it here
# would silently erase the trigger for contract-impact propagation.
REGISTRY_STATUSES = ("planned", "proposed", "active", "stale", "deprecated")


def registry_item_id(type_name: str, owner: str, name: str) -> str:
    """Stable identifier for a typed registry entry (§3.3)."""
    owner_part = (owner or "").strip() or "_"
    return f"{type_name}:{owner_part}.{name}"


def normalize_contracts(value: Any) -> dict[str, Any]:
    """Validate/normalize a Project Contract Registry payload.

    Returns a normalized copy with every list field present and empty. The goal is
    deterministic, machine-readable validation: malformed contracts must fail loudly
    rather than let a sub-agent read a half-written registry.
    """
    if not isinstance(value, dict):
        raise WorkspaceError("项目契约注册表必须是对象。")
    version = value.get("schema_version")
    if version is None:
        # KNOWN_ISSUES #7：缺 schema_version 时容错按当前版本处理并告警，不再直接抛错
        # （子 Agent / 手写 contracts.json 常漏填该字段）。
        logger.warning(
            "normalize_contracts: contracts.json 缺少 `schema_version`，"
            "已按 %s 处理；建议显式填写 `\"schema_version\": %s`。",
            CONTRACTS_SCHEMA_VERSION, CONTRACTS_SCHEMA_VERSION,
        )
    elif version != CONTRACTS_SCHEMA_VERSION:
        raise WorkspaceError(
            f"项目契约注册表 schema_version 应为 {CONTRACTS_SCHEMA_VERSION}，"
            f"实际为 {version!r}。请先升级或修正 contracts.json 后再试。"
        )

    raw_project = value.get("project")
    project = raw_project if isinstance(raw_project, dict) else {}
    project = {
        "name": str(project.get("name") or "").strip(),
        "language": str(project.get("language") or "").strip(),
        "runtime": str(project.get("runtime") or "").strip(),
    }

    def _registry_meta(raw: Any, type_name: str, owner: str, name: str) -> dict[str, Any]:
        """Attach §3.3 lifecycle metadata to a registry entry.

        `id` is stable (generated when missing), `status` validates against
        REGISTRY_STATUSES, and `source_ref` / `source_hash` / `last_seen` are the
        reconciliation bookkeeping filled later by symbol_index.reconcile_registry.
        """
        raw_dict = raw if isinstance(raw, dict) else {}
        raw_id = str(raw_dict.get("id") or "").strip()
        rid = raw_id or registry_item_id(type_name, owner, name)
        status = str(raw_dict.get("status") or "active").strip().lower()
        if status not in REGISTRY_STATUSES:
            status = "active"
        ref = raw_dict.get("source_ref")
        if not isinstance(ref, dict):
            ref = {}
        return {
            "id": rid,
            "status": status,
            "source_ref": {
                "file": str(ref.get("file") or "").strip(),
                "line": int(ref.get("line") or 0) or 0,
            },
            "source_hash": str(raw_dict.get("source_hash") or "").strip(),
            "last_seen": str(raw_dict.get("last_seen") or "").strip(),
        }

    def _string_list(value: Any, field: str) -> list[str]:
        if value is None:
            return []
        if not isinstance(value, list):
            raise WorkspaceError(f"项目契约字段 `{field}` 必须是数组。")
        result: list[str] = []
        for item in value:
            text = str(item or "").strip()
            if text and text not in result:
                result.append(text)
        return result

    vocabulary = value.get("vocabulary")
    if vocabulary is None:
        vocabulary = []
    if not isinstance(vocabulary, list):
        raise WorkspaceError("项目契约字段 `vocabulary` 必须是数组。")
    normalized_vocabulary: list[dict[str, Any]] = []
    for index, raw in enumerate(vocabulary):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"`vocabulary[{index}]` 必须是对象。")
        term = str(raw.get("term") or "").strip()
        if not term:
            raise WorkspaceError(f"`vocabulary[{index}].term` 不能为空。")
        owner = str(raw.get("owner") or "").strip()
        normalized_vocabulary.append({
            **_registry_meta(raw, "vocabulary", owner, term),
            "term": term,
            "owner": owner,
            "kind": str(raw.get("kind") or "identifier").strip(),
            "type": str(raw.get("type") or "").strip(),
            "format": str(raw.get("format") or "").strip(),
            "forbidden_aliases": _string_list(
                raw.get("forbidden_aliases"), f"vocabulary[{index}].forbidden_aliases"
            ),
        })

    shared_kernel = value.get("shared_kernel")
    if shared_kernel is None:
        shared_kernel = []
    if not isinstance(shared_kernel, list):
        raise WorkspaceError("项目契约字段 `shared_kernel` 必须是数组。")
    normalized_shared: list[dict[str, Any]] = []
    for index, raw in enumerate(shared_kernel):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"`shared_kernel[{index}]` 必须是对象。")
        symbol = str(raw.get("symbol") or "").strip()
        if not symbol:
            raise WorkspaceError(f"`shared_kernel[{index}].symbol` 不能为空。")
        owner = str(raw.get("owner") or "shared").strip()
        normalized_shared.append({
            **_registry_meta(raw, "shared_kernel", owner, symbol),
            "symbol": symbol,
            "kind": str(raw.get("kind") or "function").strip(),
            "owner": owner,
            "path": str(raw.get("path") or "").strip(),
            "why": str(raw.get("why") or "").strip(),
            "consumers": _string_list(
                raw.get("consumers"), f"shared_kernel[{index}].consumers"
            ),
        })

    commands = value.get("commands")
    if commands is None:
        commands = []
    if not isinstance(commands, list):
        raise WorkspaceError("项目契约字段 `commands` 必须是数组。")
    normalized_commands: list[dict[str, Any]] = []
    for index, raw in enumerate(commands):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"`commands[{index}]` 必须是对象。")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise WorkspaceError(f"`commands[{index}].name` 不能为空。")
        owner = str(raw.get("owner") or "").strip()
        normalized_commands.append({
            **_registry_meta(raw, "commands_events", owner, name),
            "name": name,
            "verb": str(raw.get("verb") or "").strip(),
            "resource": str(raw.get("resource") or "").strip(),
            "owner": owner,
            "handler": str(raw.get("handler") or "").strip(),
            "args": _string_list(raw.get("args"), f"commands[{index}].args"),
        })

    modules = value.get("modules")
    if modules is None:
        modules = []
    if not isinstance(modules, list):
        raise WorkspaceError("项目契约字段 `modules` 必须是数组。")
    normalized_modules: list[dict[str, Any]] = []
    for index, raw in enumerate(modules):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"`modules[{index}]` 必须是对象。")
        module_id = str(raw.get("id") or "").strip()
        if not module_id:
            raise WorkspaceError(f"`modules[{index}].id` 不能为空。")
        exports = raw.get("exports")
        if exports is None:
            exports = []
        if not isinstance(exports, list):
            raise WorkspaceError(f"`modules[{index}].exports` 必须是数组。")
        normalized_exports: list[dict[str, Any]] = []
        for export_index, raw_export in enumerate(exports):
            if not isinstance(raw_export, dict):
                raise WorkspaceError(f"`modules[{index}].exports[{export_index}]` 必须是对象。")
            symbol = str(raw_export.get("symbol") or "").strip()
            if not symbol:
                raise WorkspaceError(
                    f"`modules[{index}].exports[{export_index}].symbol` 不能为空。"
                )
            normalized_exports.append({
                **_registry_meta(raw_export, "public_api", module_id, symbol),
                "symbol": symbol,
                "kind": str(raw_export.get("kind") or "function").strip(),
                "signature": str(raw_export.get("signature") or "").strip(),
                "since": str(raw_export.get("since") or "0.1.0").strip(),
            })
        consumes = raw.get("consumes")
        if consumes is None:
            consumes = []
        if not isinstance(consumes, list):
            raise WorkspaceError(f"`modules[{index}].consumes` 必须是数组。")
        normalized_consumes: list[dict[str, Any]] = []
        for consume_index, raw_consume in enumerate(consumes):
            if not isinstance(raw_consume, dict):
                raise WorkspaceError(
                    f"`modules[{index}].consumes[{consume_index}]` 必须是对象。"
                )
            from_module = str(raw_consume.get("from") or "").strip()
            if not from_module:
                raise WorkspaceError(
                    f"`modules[{index}].consumes[{consume_index}].from` 不能为空。"
                )
            normalized_consumes.append({
                "from": from_module,
                "symbols": _string_list(
                    raw_consume.get("symbols"),
                    f"modules[{index}].consumes[{consume_index}].symbols",
                ),
                "purpose": str(raw_consume.get("purpose") or "").strip(),
            })
        normalized_modules.append({
            "id": module_id,
            "path": str(raw.get("path") or "").strip(),
            "depends_on": _string_list(raw.get("depends_on"), f"modules[{index}].depends_on"),
            "exports": normalized_exports,
            "consumes": normalized_consumes,
        })

    module_ids = [module["id"] for module in normalized_modules]
    module_id_set = set(module_ids)

    # §3.2 typed Registry: data_schema (domain entities / DTOs) and config_policy
    # (config items, env vars, feature flags, security boundaries). Both are owned
    # by a module (or "shared") and receive the same §3.3 lifecycle metadata.
    data_schema = value.get("data_schema")
    if data_schema is None:
        data_schema = []
    if not isinstance(data_schema, list):
        raise WorkspaceError("项目契约字段 `data_schema` 必须是数组。")
    normalized_data: list[dict[str, Any]] = []
    for index, raw in enumerate(data_schema):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"`data_schema[{index}]` 必须是对象。")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise WorkspaceError(f"`data_schema[{index}].name` 不能为空。")
        owner = str(raw.get("owner") or "").strip()
        if owner and owner not in module_id_set and owner != "shared":
            raise WorkspaceError(f"`data_schema[{index}].owner` 引用了未知模块 `{owner}`。")
        fields = raw.get("fields")
        normalized_fields: list[dict[str, Any]] = []
        if isinstance(fields, list):
            for field in fields:
                if isinstance(field, dict):
                    normalized_fields.append({
                        "name": str(field.get("name") or "").strip(),
                        "type": str(field.get("type") or "").strip(),
                        "required": bool(field.get("required")),
                    })
        normalized_data.append({
            **_registry_meta(raw, "data_schema", owner, name),
            "name": name,
            "owner": owner,
            "kind": str(raw.get("kind") or "entity").strip(),
            "fields": normalized_fields,
            "invariants": str(raw.get("invariants") or "").strip(),
        })

    config_policy = value.get("config_policy")
    if config_policy is None:
        config_policy = []
    if not isinstance(config_policy, list):
        raise WorkspaceError("项目契约字段 `config_policy` 必须是数组。")
    normalized_policy: list[dict[str, Any]] = []
    for index, raw in enumerate(config_policy):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"`config_policy[{index}]` 必须是对象。")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise WorkspaceError(f"`config_policy[{index}].name` 不能为空。")
        owner = str(raw.get("owner") or "").strip()
        if owner and owner not in module_id_set and owner != "shared":
            raise WorkspaceError(f"`config_policy[{index}].owner` 引用了未知模块 `{owner}`。")
        kind = str(raw.get("kind") or "config").strip()
        if kind not in ("env", "flag", "config"):
            kind = "config"
        normalized_policy.append({
            **_registry_meta(raw, "config_policy", owner, name),
            "name": name,
            "owner": owner,
            "kind": kind,
            "default": str(raw.get("default") or "").strip(),
            "allowed": _string_list(raw.get("allowed"), f"config_policy[{index}].allowed"),
            "security_boundary": bool(raw.get("security_boundary")),
        })

    def _reject_duplicates(values: list[str], label: str) -> None:
        seen: set[str] = set()
        duplicates: list[str] = []
        for item in values:
            if item in seen and item not in duplicates:
                duplicates.append(item)
            seen.add(item)
        if duplicates:
            raise WorkspaceError(f"项目契约字段 `{label}` 存在重复项：{', '.join(duplicates)}")

    _reject_duplicates(module_ids, "modules.id")

    # Cross-entry references are part of the registry contract. Rejecting them at
    # persistence time keeps lint ownership resolution deterministic.
    for index, module in enumerate(normalized_modules):
        for dependency in module["depends_on"]:
            if dependency not in module_id_set:
                raise WorkspaceError(
                    f"`modules[{index}].depends_on` 引用了未知模块 `{dependency}`。"
                )
            if dependency == module["id"]:
                raise WorkspaceError(f"`modules[{index}].depends_on` 不能引用自身。")
        for consume_index, consume in enumerate(module["consumes"]):
            source = consume["from"]
            if source not in module_id_set and source != "shared":
                raise WorkspaceError(
                    f"`modules[{index}].consumes[{consume_index}].from` 引用了未知模块 `{source}`。"
                )

    export_symbols: list[str] = []
    exports_by_module = {
        module["id"]: {item["symbol"] for item in module["exports"]}
        for module in normalized_modules
    }
    shared_symbols = {item["symbol"] for item in normalized_shared}
    for module in normalized_modules:
        export_symbols.extend(item["symbol"] for item in module["exports"])
        for consume in module["consumes"]:
            available = shared_symbols if consume["from"] == "shared" else exports_by_module.get(consume["from"], set())
            unknown_symbols = [symbol for symbol in consume["symbols"] if symbol not in available]
            if unknown_symbols:
                raise WorkspaceError(
                    f"模块 `{module['id']}` 消费 `{consume['from']}` 的未知符号：{', '.join(unknown_symbols)}"
                )
    _reject_duplicates(export_symbols, "modules.exports.symbol")
    _reject_duplicates(export_symbols + list(shared_symbols), "symbols")
    _reject_duplicates([item["term"] for item in normalized_vocabulary], "vocabulary.term")
    _reject_duplicates([item["symbol"] for item in normalized_shared], "shared_kernel.symbol")
    _reject_duplicates([item["name"] for item in normalized_commands], "commands.name")
    _reject_duplicates([item["id"] for item in normalized_data], "data_schema.id")
    _reject_duplicates([item["id"] for item in normalized_policy], "config_policy.id")
    # Every typed registry entry (across all six categories) must have a unique
    # stable id, otherwise the reconcile write-back cannot target a single entry.
    _reject_duplicates(
        [item["id"] for item in normalized_vocabulary]
        + [item["id"] for item in normalized_shared]
        + [item["id"] for item in normalized_commands]
        + [item["id"] for m in normalized_modules for item in m["exports"]]
        + [item["id"] for item in normalized_data]
        + [item["id"] for item in normalized_policy],
        "registry_entry.id",
    )

    for index, entry in enumerate(normalized_vocabulary):
        owner = entry["owner"]
        if not owner:
            raise WorkspaceError(f"`vocabulary[{index}].owner` 不能为空。")
        if owner not in module_id_set and owner != "shared":
            raise WorkspaceError(f"`vocabulary[{index}].owner` 引用了未知模块 `{owner}`。")
    for index, entry in enumerate(normalized_shared):
        owner = entry["owner"]
        if owner not in module_id_set and owner != "shared":
            raise WorkspaceError(f"`shared_kernel[{index}].owner` 引用了未知模块 `{owner}`。")
        unknown = [item for item in entry["consumers"] if item not in module_id_set]
        if unknown:
            raise WorkspaceError(
                f"`shared_kernel[{index}].consumers` 引用了未知模块：{', '.join(unknown)}"
            )
    for index, entry in enumerate(normalized_commands):
        owner = entry["owner"]
        if not owner:
            raise WorkspaceError(f"`commands[{index}].owner` 不能为空。")
        if owner not in module_id_set:
            raise WorkspaceError(f"`commands[{index}].owner` 引用了未知模块 `{owner}`。")
    recipes = value.get("recipes")
    if recipes is None:
        recipes = []
    if not isinstance(recipes, list):
        raise WorkspaceError("项目契约字段 `recipes` 必须是数组。")
    normalized_recipes: list[dict[str, Any]] = []
    for index, raw in enumerate(recipes):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"`recipes[{index}]` 必须是对象。")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise WorkspaceError(f"`recipes[{index}].name` 不能为空。")
        normalized_recipes.append({
            "name": name,
            "problem": str(raw.get("problem") or "").strip(),
            "solution": str(raw.get("solution") or "").strip(),
            "used_by": _string_list(raw.get("used_by"), f"recipes[{index}].used_by"),
        })

    _reject_duplicates([item["name"] for item in normalized_recipes], "recipes.name")
    for index, entry in enumerate(normalized_recipes):
        unknown = [item for item in entry["used_by"] if item not in module_id_set]
        if unknown:
            raise WorkspaceError(
                f"`recipes[{index}].used_by` 引用了未知模块：{', '.join(unknown)}"
            )

    return {
        "schema_version": CONTRACTS_SCHEMA_VERSION,
        "updated_at": str(value.get("updated_at") or "").strip(),
        "project": project,
        "vocabulary": normalized_vocabulary,
        "shared_kernel": normalized_shared,
        "commands": normalized_commands,
        "data_schema": normalized_data,
        "config_policy": normalized_policy,
        "modules": normalized_modules,
        "recipes": normalized_recipes,
    }


class WorkspaceError(RuntimeError):
    """Workspace-facing failure.

    `payload` optionally carries a structured diagnostics receipt
    ({"schema_version": 1, "diagnostics": [...]}) so agents and the HTTP layer can
    consume failures without parsing the human-readable Chinese message. Plain
    `WorkspaceError("text")` raises keep working; payload stays None for them.
    """

    def __init__(self, message: str, payload: dict[str, Any] | None = None):
        super().__init__(message)
        self.payload = payload


def diagnostics_receipt(diagnostics: list[dict[str, Any]]) -> dict[str, Any]:
    """Wrap structured diagnostics in the stable receipt envelope.

    Same shape philosophy as Archify's repair receipts: additive schemaVersion, stable
    rule codes, exact subjects, measured evidence — a feedback protocol, not auto-fix.
    """
    return {"schema_version": 1, "diagnostics": diagnostics}


def slugify(value: str) -> str:
    return optional_slug(value) or "app"


def optional_slug(value: str) -> str:
    """Slugify without the "app" fallback, for fields where absent must stay absent."""
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value.strip()).strip("-_").lower()


def clamp(value: float, low: float, high: float) -> float:
    return low if value < low else high if value > high else value


def coerce_float(value: Any, fallback: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return fallback
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        return fallback
    return number


def normalized_text(value: Any, field: str, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise WorkspaceError(f"Architecture Agent 缺少字段：{field}")
    return text


def normalized_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WorkspaceError(f"Architecture Agent 字段 `{field}` 必须是数组。")
    result: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text and text not in result:
            result.append(text)
    return result


def normalize_flag(value: Any, field: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise WorkspaceError(f"Architecture Agent 字段 `{field}` 必须是布尔值。")


def normalize_brief(value: Any, fallback: str = "") -> str:
    """One sentence, length-capped. Nodes must stay readable at graph zoom."""
    text = str(value or "").strip() or str(fallback or "").strip()
    if not text:
        return ""
    text = re.split(r"(?<=[。！？.!?])\s*", text)[0].strip() or text
    if len(text) > BRIEF_MAX_CHARS:
        text = text[: BRIEF_MAX_CHARS - 1].rstrip() + "…"
    return text


def normalize_groups(value: Any, module_ids: set[str]) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WorkspaceError("Architecture Agent 字段 `groups` 必须是数组。")
    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw_group in enumerate(value):
        if not isinstance(raw_group, dict):
            raise WorkspaceError(f"`groups[{index}]` 必须是对象。")
        label = normalized_text(raw_group.get("label"), f"groups[{index}].label", True)
        group_id = slugify(str(raw_group.get("id") or label))
        if group_id in seen:
            raise WorkspaceError(f"Architecture Agent 返回了重复分组 ID：{group_id}")
        seen.add(group_id)
        kind = str(raw_group.get("kind") or "framework").strip().lower()
        if kind not in GROUP_KINDS:
            kind = "framework"
        members = [
            member
            for member in normalized_list(
                raw_group.get("members", []),
                f"groups[{index}].members",
            )
            if member in module_ids
        ]
        groups.append(
            {
                "id": group_id,
                "label": label[:80],
                "kind": kind,
                "members": members,
            }
        )
    return groups


def validate_module_path(value: Any) -> str:
    raw_path = str(value or "").strip().replace("\\", "/")
    if not raw_path:
        return ""
    candidate = Path(raw_path)
    if (
        raw_path.startswith("/")
        or candidate.is_absolute()
        or bool(candidate.drive)
        or ".." in candidate.parts
    ):
        raise WorkspaceError("Architecture Agent 返回了不安全的模块路径。")
    return raw_path.strip("/")


def normalize_edge_type(value: Any) -> str:
    """Map a free-text dependency type onto the closed edge-type set.

    Legacy projects and models emit prose like "loopback JSON API". Unrecognized
    values degrade to `uses` rather than failing, because rejecting them would make
    older `.docuagent/` directories unreadable.
    """
    raw = str(value or "").strip().lower()
    if not raw:
        return DEFAULT_EDGE_TYPE
    if raw in EDGE_TYPES:
        return raw
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    if slug in EDGE_TYPES:
        return slug
    if slug in EDGE_TYPE_ALIASES:
        return EDGE_TYPE_ALIASES[slug]
    for token in slug.split("-"):
        if token in EDGE_TYPES:
            return token
        if token in EDGE_TYPE_ALIASES:
            return EDGE_TYPE_ALIASES[token]
    return DEFAULT_EDGE_TYPE


def normalize_edges(
    value: Any, module_ids: set[str], field: str = "edges"
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise WorkspaceError(f"Architecture Agent 字段 `{field}` 必须是数组。")
    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for index, raw_edge in enumerate(value):
        if not isinstance(raw_edge, dict):
            raise WorkspaceError(f"`{field}[{index}]` 必须是对象。")
        source = optional_slug(str(raw_edge.get("from") or ""))
        target = optional_slug(str(raw_edge.get("to") or ""))
        if not source or not target:
            raise WorkspaceError(f"`{field}[{index}]` 缺少 from 或 to。")
        unknown = [node for node in (source, target) if node not in module_ids]
        if unknown:
            raise WorkspaceError(
                f"`{field}[{index}]` 引用未知模块：{', '.join(unknown)}"
            )
        if source == target:
            # A module cannot depend on itself; this is model noise, not a decision.
            # Dropping it keeps the interview moving instead of blocking a whole turn.
            continue
        edge_type = normalize_edge_type(raw_edge.get("kind") or raw_edge.get("type"))
        key = (source, target, edge_type)
        if key in seen:
            continue
        seen.add(key)
        edges.append(
            {
                "from": source,
                "to": target,
                "kind": edge_type,
                "label": normalized_text(
                    raw_edge.get("label"), f"{field}[{index}].label"
                )[:80],
                "reason": normalized_text(
                    raw_edge.get("reason"), f"{field}[{index}].reason"
                )[:200],
                "accepted": normalize_flag(
                    raw_edge.get("accepted"), f"{field}[{index}].accepted"
                ),
            }
        )
    return edges


def edges_from_modules(modules: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive `uses` edges from legacy `depends_on` lists."""
    edges: list[dict[str, Any]] = []
    for module in modules:
        for dependency in module.get("depends_on", []):
            edges.append(
                {
                    "from": module["id"],
                    "to": dependency,
                    "kind": DEFAULT_EDGE_TYPE,
                    "label": "",
                    "reason": f"模块 {module['id']} 声明依赖模块 {dependency}。",
                    "accepted": False,
                }
            )
    return edges


EDGE_KIND_PRIORITY = {
    "uses": 0,
    "extends": 1,
    "data": 2,
    "blocks": 3,
    "event": 4,
}

_ENTRY_NODE_RE = re.compile(r"(?:^|[-_\s])(index|entry|shell|html)(?:$|[-_\s])", re.IGNORECASE)
_COMPOSER_NODE_RE = re.compile(
    r"(?:^|[-_\s])(main|app|bootstrap|assembler|composition[-_]?root|entrypoint)(?:$|[-_\s])",
    re.IGNORECASE,
)
_LOAD_LABEL_RE = re.compile(r"加载|脚本|script|load|import|include", re.IGNORECASE)


def _is_entry_node(module: dict[str, Any]) -> bool:
    name = " ".join([str(module.get("id") or ""), str(module.get("name") or "")])
    path = str(module.get("path") or "")
    return bool(_ENTRY_NODE_RE.search(name)) or path.endswith(".html")


def _is_composer_node(module: dict[str, Any]) -> bool:
    name = " ".join([str(module.get("id") or ""), str(module.get("name") or "")])
    return bool(_COMPOSER_NODE_RE.search(name))


def simplify_architecture_edges(
    modules: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply engineering-graph hygiene to AI-produced edges.

    This pass intentionally does not perform a full transitive reduction of `uses`
    edges: a module may legitimately call both a dependency and that dependency's
    dependency directly. It only removes machine-visible noise:

    - duplicate (from, to) pairs, keeping the most informative edge kind;
    - entry-shell fan-out edges that merely duplicate a composition root's path
      (e.g. `index.html -> every script` when `index.html -> main -> ...` already
      exists). These are load-order details and belong in constraints, not in the
      dependency DAG.
    """
    module_ids = {module["id"] for module in modules}
    best: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in edges:
        source = edge.get("from")
        target = edge.get("to")
        if source not in module_ids or target not in module_ids or source == target:
            continue
        key = (source, target)
        current = best.get(key)
        if current is None or EDGE_KIND_PRIORITY.get(
            edge.get("kind"), 99
        ) < EDGE_KIND_PRIORITY.get(current.get("kind"), 99):
            best[key] = edge

    simplified = list(best.values())
    if not simplified:
        return simplified

    entry_ids = {
        module["id"] for module in modules if _is_entry_node(module)
    }
    composer_ids = {
        module["id"] for module in modules if _is_composer_node(module)
    }
    if not entry_ids or not composer_ids:
        return simplified

    by_source: dict[str, list[dict[str, Any]]] = {}
    for edge in simplified:
        by_source.setdefault(edge["from"], []).append(edge["to"])

    build_graph = build_order_graph(module_ids, simplified)

    def reaches(start: str, goal: str) -> bool:
        if start == goal:
            return True
        pending = list(build_graph.get(start, []))
        seen = {start}
        while pending:
            node = pending.pop()
            if node == goal:
                return True
            if node in seen:
                continue
            seen.add(node)
            pending.extend(build_graph.get(node, []))
        return False

    kept: list[dict[str, Any]] = []
    for edge in simplified:
        drop = False
        if edge["from"] in entry_ids:
            for composer in composer_ids:
                if edge["to"] == composer:
                    break
                entry_reaches_composer = (
                    composer in by_source.get(edge["from"], [])
                    or reaches(edge["from"], composer)
                )
                composer_reaches_target = reaches(composer, edge["to"])
                load_like = bool(
                    _LOAD_LABEL_RE.search(str(edge.get("label") or ""))
                )
                if (
                    entry_reaches_composer
                    and composer_reaches_target
                    and (edge.get("kind") == "blocks" or load_like)
                ):
                    drop = True
                    break
        if not drop:
            kept.append(edge)
    return kept


def architecture_graph_diagnostics(
    architecture: dict[str, Any],
) -> list[dict[str, Any]]:
    """Structured counterpart of the graph-quality review.

    Same soft engineering rules as before, but machine-readable: every entry carries a
    stable rule code, the exact subject, measured evidence, and the human message.
    Diagnostics, not gates — agents and the UI consume them without parsing prose.
    """
    modules = architecture.get("modules", [])
    edges = architecture.get("edges", [])
    if not isinstance(modules, list) or not isinstance(edges, list):
        return [
            {
                "rule": "graph/shape-invalid",
                "severity": "warning",
                "subject": {"surface": "architecture.graph"},
                "evidence": {},
                "message": "架构模块或边格式不正确。",
            }
        ]
    out_degree: dict[str, int] = {}
    in_degree: dict[str, int] = {}
    for edge in edges:
        source = str(edge.get("from") or "")
        target = str(edge.get("to") or "")
        out_degree[source] = out_degree.get(source, 0) + 1
        in_degree[target] = in_degree.get(target, 0) + 1
    composer_ids = {
        module["id"] for module in modules if isinstance(module, dict) and _is_composer_node(module)
    }
    entry_ids = {
        module["id"] for module in modules if isinstance(module, dict) and _is_entry_node(module)
    }

    def diagnostic(rule: str, subject: dict[str, Any], evidence: dict[str, Any], message: str) -> dict[str, Any]:
        return {
            "rule": rule,
            "severity": "warning",
            "subject": {"surface": "architecture.graph", **subject},
            "evidence": evidence,
            "message": message,
        }

    diagnostics: list[dict[str, Any]] = []
    for module in modules:
        if not isinstance(module, dict):
            continue
        module_id = str(module.get("id") or "")
        degree = out_degree.get(module_id, 0)
        if degree > 8 and module_id not in composer_ids:
            diagnostics.append(
                diagnostic(
                    "fanout/out-degree-high",
                    {"type": "module", "id": module_id},
                    {"out_degree": degree},
                    f"模块 `{module_id}` 出边 {degree} 条，疑似把加载/装配边混入了领域依赖。",
                )
            )
        if degree > 2 and module_id in entry_ids:
            diagnostics.append(
                diagnostic(
                    "fanout/entry-excess",
                    {"type": "module", "id": module_id},
                    {"out_degree": degree},
                    f"入口模块 `{module_id}` 出边 {degree} 条；入口通常只应连接组合根和直接绑定的 DOM 模块。",
                )
            )
        if (
            out_degree.get(module_id, 0) == 0
            and in_degree.get(module_id, 0) > 0
            and len(str(module.get("responsibility") or "").strip()) <= 24
        ):
            diagnostics.append(
                diagnostic(
                    "module/thin-leaf",
                    {"type": "module", "id": module_id},
                    {"in_degree": in_degree.get(module_id, 0)},
                    f"模块 `{module_id}` 是职责很短的叶子节点，疑似可合并进消费方。",
                )
            )
    module_count = len(modules)
    if module_count > 3:
        ratio = len(edges) / module_count
        if ratio > 2.0:
            diagnostics.append(
                diagnostic(
                    "density/ratio-high",
                    {"type": "graph"},
                    {"edges": len(edges), "modules": module_count, "ratio": round(ratio, 2)},
                    f"图密度偏高：{len(edges)} 条边 / {module_count} 个模块 = {ratio:.1f}；"
                    "检查是否存在可经组合根推导的重复边。",
                )
            )
    return diagnostics[:6]


def architecture_graph_quality_issues(
    architecture: dict[str, Any],
) -> list[str]:
    """Soft engineering review for a generated architecture graph.

    These are diagnostics, not validation gates. They are reported alongside the
    architecture so the user can see when the graph is denser or more fragmented
    than a hand-drawn engineering diagram would be.
    """
    return [item["message"] for item in architecture_graph_diagnostics(architecture)]


def build_order_graph(
    module_ids: set[str],
    edges: list[dict[str, Any]],
) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {module_id: [] for module_id in module_ids}
    for edge in edges:
        if edge["kind"] in BUILD_ORDER_EDGE_TYPES:
            graph.setdefault(edge["from"], []).append(edge["to"])
    return graph


def find_edge_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    """Return one cycle as a node path, or None. Iterative to survive deep graphs."""
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(start: str) -> list[str] | None:
        frames: list[tuple[str, int]] = [(start, 0)]
        visiting.add(start)
        stack.append(start)
        while frames:
            node, cursor = frames[-1]
            neighbors = graph.get(node, [])
            if cursor < len(neighbors):
                frames[-1] = (node, cursor + 1)
                neighbor = neighbors[cursor]
                if neighbor in visiting:
                    return stack[stack.index(neighbor):] + [neighbor]
                if neighbor in visited:
                    continue
                visiting.add(neighbor)
                stack.append(neighbor)
                frames.append((neighbor, 0))
                continue
            frames.pop()
            visiting.discard(node)
            visited.add(node)
            if stack:
                stack.pop()
        return None

    for node in graph:
        if node in visited:
            continue
        cycle = visit(node)
        if cycle:
            return cycle
    return None


def ensure_acyclic_edges(module_ids: set[str], edges: list[dict[str, Any]]) -> None:
    """Reject cycles over build-order edges only.

    `event` edges are excluded on purpose so publish/subscribe cycles stay legal.
    """
    cycle = find_edge_cycle(build_order_graph(module_ids, edges))
    if cycle:
        raise WorkspaceError(
            "Architecture Agent 返回的模块依赖存在环：" + " -> ".join(cycle)
        )


def _would_create_cycle(
    graph: dict[str, list[str]],
    source: str,
    target: str,
) -> bool:
    """True when adding `source -> target` would close a directed cycle."""
    if source not in graph or target not in graph:
        return False
    stack = [target]
    seen: set[str] = set()
    while stack:
        node = stack.pop()
        if node == source:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return False


def break_build_order_cycles(
    module_ids: set[str],
    edges: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a maximal acyclic edge set, dropping the edge that closes each cycle.

    Iterates edges in deterministic order and only keeps an edge when adding it cannot
    close a cycle. `event` edges are excluded from build order and always kept.
    """
    graph: dict[str, list[str]] = {module_id: [] for module_id in module_ids}
    kept: list[dict[str, Any]] = []
    ordered = sorted(
        edges,
        key=lambda edge: (
            edge["from"],
            edge["to"],
            edge["kind"],
            edge.get("label", ""),
        ),
    )
    for edge in ordered:
        if edge["kind"] not in BUILD_ORDER_EDGE_TYPES:
            kept.append(edge)
            continue
        if _would_create_cycle(graph, edge["from"], edge["to"]):
            continue
        graph.setdefault(edge["from"], []).append(edge["to"])
        kept.append(edge)
    return kept


def ensure_acyclic_modules(modules: list[dict[str, Any]]) -> None:
    """Back-compatible wrapper over the typed edge check."""
    module_ids = {module["id"] for module in modules}
    ensure_acyclic_edges(module_ids, edges_from_modules(modules))


def normalize_architecture(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkspaceError("Architecture Agent 缺少 architecture 对象。")

    raw_modules = value.get("modules", [])
    if not isinstance(raw_modules, list):
        raise WorkspaceError("Architecture Agent 字段 `modules` 必须是数组。")
    modules: list[dict[str, Any]] = []
    module_ids: set[str] = set()
    for index, raw_module in enumerate(raw_modules):
        if not isinstance(raw_module, dict):
            raise WorkspaceError("Architecture Agent 的模块条目必须是对象。")
        name = normalized_text(raw_module.get("name"), f"modules[{index}].name", True)
        module_id = slugify(str(raw_module.get("id") or name))
        if module_id in module_ids:
            raise WorkspaceError(f"Architecture Agent 返回了重复模块 ID：{module_id}")
        module_ids.add(module_id)
        responsibility = normalized_text(
            raw_module.get("responsibility"),
            f"modules[{index}].responsibility",
            True,
        )
        modules.append(
            {
                "id": module_id,
                "name": name,
                "responsibility": responsibility,
                "brief": normalize_brief(raw_module.get("brief"), responsibility),
                "path": validate_module_path(raw_module.get("path")),
                "depends_on": normalized_list(
                    raw_module.get("depends_on", []),
                    f"modules[{index}].depends_on",
                ),
                "needs_ui": normalize_flag(
                    raw_module.get("needs_ui"),
                    f"modules[{index}].needs_ui",
                ),
                "group": optional_slug(str(raw_module.get("group") or "")) or None,
                "verification": normalized_list(
                    raw_module.get("verification", []),
                    f"modules[{index}].verification",
                ),
                "target_files": normalized_list(
                    raw_module.get("target_files", []),
                    f"modules[{index}].target_files",
                ),
                "declared_exports": normalized_list(
                    raw_module.get("declared_exports", []),
                    f"modules[{index}].declared_exports",
                ),
                "uncertain": normalize_flag(
                    raw_module.get("uncertain"),
                    f"modules[{index}].uncertain",
                ),
                "uncertain_reason": normalized_text(
                    raw_module.get("uncertain_reason"),
                    f"modules[{index}].uncertain_reason",
                )[:200],
            }
        )

    for module in modules:
        unknown = [
            dependency
            for dependency in module["depends_on"]
            if dependency not in module_ids
        ]
        if unknown:
            raise WorkspaceError(
                f"模块 `{module['id']}` 依赖未知模块：{', '.join(unknown)}"
            )
        module["depends_on"] = [
            dependency
            for dependency in module["depends_on"]
            if dependency != module["id"]
        ]

    groups = normalize_groups(value.get("groups"), module_ids)
    group_ids = {group["id"] for group in groups}
    for module in modules:
        if module["group"] and module["group"] not in group_ids:
            raise WorkspaceError(
                f"模块 `{module['id']}` 引用未知分组：{module['group']}"
            )

    # Typed edges are authoritative when present; otherwise derive from depends_on.
    raw_edges = value.get("edges")
    if raw_edges is None:
        raw_edges = value.get("dependencies")
    if raw_edges is None:
        edges = edges_from_modules(modules)
    else:
        edges = normalize_edges(raw_edges, module_ids)
        declared = {(edge["from"], edge["to"]) for edge in edges}
        for edge in edges_from_modules(modules):
            if (edge["from"], edge["to"]) not in declared:
                edges.append(edge)
    edges = break_build_order_cycles(module_ids, edges)

    return {
        "summary": normalized_text(value.get("summary"), "summary"),
        "platform": normalized_text(value.get("platform"), "platform"),
        "language": normalized_text(value.get("language"), "language"),
        "runtime": normalized_text(value.get("runtime"), "runtime"),
        "frameworks": normalized_list(value.get("frameworks", []), "frameworks"),
        "stack": normalized_list(value.get("stack", []), "stack"),
        "modules": modules,
        "groups": groups,
        "edges": edges,
        "data": normalized_list(value.get("data", []), "data"),
        "integrations": normalized_list(
            value.get("integrations", []),
            "integrations",
        ),
        "constraints": normalized_list(value.get("constraints", []), "constraints"),
        "verification": normalized_list(
            value.get("verification", []),
            "verification",
        ),
        "risks": normalized_list(value.get("risks", []), "risks"),
        "unresolved": normalized_list(value.get("unresolved", []), "unresolved"),
    }


def architecture_edge_reason_diagnostics(architecture: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured form of the missing-edge-reason review.

    One diagnostic per graph (not per edge): the subject names the rule, evidence
    carries the full missing list preview so a repair can target exact edges.
    """
    missing: list[str] = []
    for edge in architecture.get("edges", []):
        if not isinstance(edge, dict):
            continue
        if not str(edge.get("reason") or "").strip():
            missing.append(f"{edge.get('from', '?')} → {edge.get('to', '?')}")
    if not missing:
        return []
    preview = "、".join(missing[:4])
    if len(missing) > 4:
        preview += f" 等 {len(missing)} 条"
    return [
        {
            "rule": "edge/reason-missing",
            "severity": "error",
            "subject": {"surface": "architecture.graph", "type": "edges"},
            "evidence": {"missing_total": len(missing), "preview": missing[:6]},
            "message": f"连线缺少原因：{preview}",
        }
    ]


def architecture_edge_reason_issues(architecture: dict[str, Any]) -> list[str]:
    """Every typed edge must explain why it exists.

    Edges derived from `depends_on` carry a generated reason; model-authored edges must
    provide their own. Missing reasons block `ready` and architecture revisions, but
    legacy documents stay readable because normalization alone never raises for them.
    """
    return [item["message"] for item in architecture_edge_reason_diagnostics(architecture)]


REQUIRED_ARCHITECTURE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("summary", "项目目标", "required/summary"),
    ("language+stack", "语言与技术栈", "required/stack"),
    ("runtime+platform", "运行环境", "required/runtime"),
    ("modules", "功能模块", "required/modules"),
    ("constraints", "工程约束", "required/constraints"),
    ("verification", "验证方式", "required/verification"),
)


def architecture_readiness_diagnostics(architecture: dict[str, Any]) -> list[dict[str, Any]]:
    """Structured form of the ready-gate review: what still blocks a finished design."""
    diagnostics: list[dict[str, Any]] = []

    def missing_field(rule: str, label: str) -> dict[str, Any]:
        return {
            "rule": rule,
            "severity": "error",
            "subject": {"surface": "bootstrap.architecture", "type": "required-field", "rule": rule},
            "evidence": {},
            "message": label,
        }

    if not architecture.get("summary"):
        diagnostics.append(missing_field("required/summary", "项目目标"))
    if not architecture.get("language") and not architecture.get("stack"):
        diagnostics.append(missing_field("required/stack", "语言与技术栈"))
    if not architecture.get("runtime") and not architecture.get("platform"):
        diagnostics.append(missing_field("required/runtime", "运行环境"))
    if not architecture.get("modules"):
        diagnostics.append(missing_field("required/modules", "功能模块"))
    if not architecture.get("constraints"):
        diagnostics.append(missing_field("required/constraints", "工程约束"))
    if not architecture.get("verification"):
        diagnostics.append(missing_field("required/verification", "验证方式"))
    for item in architecture.get("unresolved") or []:
        if isinstance(item, dict):
            text = str(item.get("text") or item.get("question") or "").strip()
            identifier = str(item.get("id") or "").strip()
        else:
            text = str(item).strip()
            identifier = ""
        if not text:
            continue
        subject: dict[str, Any] = {"surface": "bootstrap.architecture", "type": "unresolved-decision"}
        if identifier:
            subject["id"] = identifier
        diagnostics.append(
            {
                "rule": "readiness/unresolved-decision",
                "severity": "error",
                "subject": subject,
                "evidence": {},
                "message": text,
            }
        )
    seen: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []
    for item in diagnostics:
        key = (item["rule"], str(item["message"]))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def architecture_readiness_issues(architecture: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not architecture.get("summary"):
        issues.append("项目目标")
    if not architecture.get("language") and not architecture.get("stack"):
        issues.append("语言与技术栈")
    if not architecture.get("runtime") and not architecture.get("platform"):
        issues.append("运行环境")
    if not architecture.get("modules"):
        issues.append("功能模块")
    if not architecture.get("constraints"):
        issues.append("工程约束")
    if not architecture.get("verification"):
        issues.append("验证方式")
    if architecture.get("unresolved"):
        issues.extend(str(item) for item in architecture["unresolved"])
    return list(dict.fromkeys(issues))
