"""Structured context-block cache-hit tracking.

DocuAgent injects named, stable context blocks into model calls. A block whose
content digest matches the previous call for the same scope is a cache hit.
Statistics are kept globally and per project module so the UI can show both the
project rate and each module's rate.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import telemetry
from workspace import atomic_write_json, managed_path, read_json

BLOCK_KEYS = (
    "user_profile",
    "standards",
    "recipes",
    "project_overview",
    "contract_view",
    "module_contract",
    "unresolved_attachments",
    "error_memory",
    "work_log_tail",
)

STATS_FILE = "cache-stats.json"
STATS_SCHEMA_VERSION = 1
_lock = threading.Lock()


def _serialize(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _block_hash(value: Any) -> str:
    return hashlib.sha256(_serialize(value).encode("utf-8")).hexdigest()


def _stats_path(project_root: Path) -> Path:
    return managed_path(project_root, STATS_FILE)


def _empty_block() -> dict[str, Any]:
    return {"hits": 0, "misses": 0, "digest": None, "chars": 0}


def _normalize_block(value: Any) -> dict[str, Any]:
    block = _empty_block()
    if isinstance(value, dict):
        block["hits"] = int(value.get("hits") or 0)
        block["misses"] = int(value.get("misses") or 0)
        block["digest"] = value.get("digest")
        block["chars"] = int(value.get("chars") or 0)
    return block


def _normalize_blocks(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    return {
        key: _normalize_block(block)
        for key, block in value.items()
        if isinstance(key, str)
    }


def read_cache_stats(project_root: Path) -> dict[str, Any]:
    raw = read_json(_stats_path(project_root))
    if not isinstance(raw, dict):
        return {
            "schema_version": STATS_SCHEMA_VERSION,
            "blocks": {},
            "modules": {},
        }
    if "blocks" in raw or "modules" in raw:
        return {
            "schema_version": STATS_SCHEMA_VERSION,
            "blocks": _normalize_blocks(raw.get("blocks")),
            "modules": {
                str(module_id): _normalize_blocks(blocks)
                for module_id, blocks in (raw.get("modules") or {}).items()
                if isinstance(module_id, str)
            },
        }
    # Legacy files stored block keys at the document root.
    return {
        "schema_version": STATS_SCHEMA_VERSION,
        "blocks": _normalize_blocks(raw),
        "modules": {},
    }


def _record_blocks(
    blocks: dict[str, dict[str, Any]],
    context: dict[str, Any],
) -> tuple[int, int]:
    hits = 0
    misses = 0
    for key in BLOCK_KEYS:
        value = context.get(key)
        if value is None:
            continue
        digest = _block_hash(value)
        block = _normalize_block(blocks.get(key))
        blocks[key] = block
        if block.get("digest") == digest:
            block["hits"] = int(block.get("hits") or 0) + 1
            hits += 1
        else:
            block["misses"] = int(block.get("misses") or 0) + 1
            block["digest"] = digest
            misses += 1
        block["chars"] = len(_serialize(value))
    return hits, misses


def record_context(
    project_root: Path,
    context: dict[str, Any],
    *,
    module_id: str = "",
) -> dict[str, Any]:
    """Record one context assembly and update project and module hit/miss stats."""
    with _lock:
        stats = read_cache_stats(project_root)
        hits, misses = _record_blocks(stats["blocks"], context)
        if module_id:
            module_blocks = stats["modules"].setdefault(module_id, {})
            _record_blocks(module_blocks, context)
        atomic_write_json(_stats_path(project_root), stats)
    telemetry.record_context_cache(hits, misses)
    return stats


def _summarize(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    total_hits = 0
    total_misses = 0
    for key in BLOCK_KEYS:
        block = blocks.get(key)
        if not isinstance(block, dict):
            continue
        hits = int(block.get("hits") or 0)
        misses = int(block.get("misses") or 0)
        total = hits + misses
        rows.append({
            "key": key,
            "hits": hits,
            "misses": misses,
            "hit_rate": round(hits / total, 4) if total else None,
            "chars": int(block.get("chars") or 0),
        })
        total_hits += hits
        total_misses += misses
    overall = total_hits + total_misses
    return {
        "blocks": rows,
        "total_hits": total_hits,
        "total_misses": total_misses,
        "overall_hit_rate": round(total_hits / overall, 4) if overall else None,
    }


def cache_summary(project_root: Path) -> dict[str, Any]:
    """Return project totals, block rows, and per-module summaries."""
    stats = read_cache_stats(project_root)
    summary = _summarize(stats["blocks"])
    modules: list[dict[str, Any]] = []
    for module_id, blocks in stats["modules"].items():
        item = _summarize(blocks)
        modules.append({
            "module_id": module_id,
            "total_hits": item["total_hits"],
            "total_misses": item["total_misses"],
            "overall_hit_rate": item["overall_hit_rate"],
        })
    modules.sort(key=lambda item: str(item["module_id"]))
    summary["modules"] = modules
    if modules:
        summary["total_hits"] = sum(int(item["total_hits"]) for item in modules)
        summary["total_misses"] = sum(
            int(item["total_misses"]) for item in modules
        )
        total = summary["total_hits"] + summary["total_misses"]
        summary["overall_hit_rate"] = (
            round(summary["total_hits"] / total, 4) if total else None
        )
    return summary
