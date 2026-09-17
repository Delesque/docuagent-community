"""Token usage and prompt-cache accounting.

Usage is stored in four dimensions:

- global totals, retained for the existing settings panel;
- per-project totals, keyed by a one-way hash of the resolved project path;
- per-module totals for code work;
- per-feature totals such as architecture, agent and documentation.

The local file never stores a raw project path. The frontend sends the path to
query the matching project bucket, and the server hashes it before doing a
lookup.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import telemetry
from workspace import atomic_write_json, read_json

USAGE_FILE = "token-usage.json"
USAGE_SCHEMA_VERSION = 1
_lock = threading.Lock()
_scope: contextvars.ContextVar[tuple[Path | None, str, str]] = contextvars.ContextVar(
    "docuagent_token_scope",
    default=(None, "", ""),
)

_METRIC_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_hit_tokens",
    "cache_miss_tokens",
)


def _usage_path() -> Path:
    override = str(os.environ.get("DOCUAGENT_USAGE_PATH") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".docuagent" / USAGE_FILE


def _project_key(project_root: Path | None) -> str:
    if project_root is None:
        return ""
    resolved = str(Path(project_root).resolve()).casefold()
    return hashlib.sha256(resolved.encode("utf-8")).hexdigest()[:24]


def _empty_metrics() -> dict[str, int]:
    return {key: 0 for key in (*_METRIC_KEYS, "calls")}


def _normalized_metrics(value: Any) -> dict[str, int]:
    metrics = _empty_metrics()
    if isinstance(value, dict):
        for key in metrics:
            metrics[key] = int(value.get(key) or 0)
    return metrics


def _add_metrics(target: dict[str, int], parsed: dict[str, int]) -> None:
    for key in _METRIC_KEYS:
        target[key] = int(target.get(key) or 0) + int(parsed.get(key) or 0)
    target["calls"] = int(target.get("calls") or 0) + 1


def _read_document() -> dict[str, Any]:
    raw = read_json(_usage_path())
    if not isinstance(raw, dict):
        raw = {}
    return {
        "schema_version": USAGE_SCHEMA_VERSION,
        **_normalized_metrics(raw),
        "projects": raw.get("projects") if isinstance(raw.get("projects"), dict) else {},
    }


def _project_bucket(document: dict[str, Any], key: str) -> dict[str, Any]:
    projects = document.setdefault("projects", {})
    bucket = projects.get(key)
    if not isinstance(bucket, dict):
        bucket = {}
        projects[key] = bucket
    totals = _normalized_metrics(bucket.get("totals"))
    modules = bucket.get("modules") if isinstance(bucket.get("modules"), dict) else {}
    features = bucket.get("features") if isinstance(bucket.get("features"), dict) else {}
    bucket.update({"totals": totals, "modules": modules, "features": features})
    return bucket


@contextmanager
def usage_scope(
    project_root: Path | None,
    *,
    module_id: str = "",
    feature: str = "",
) -> Iterator[None]:
    """Attribute nested model calls to a project/module/feature."""
    token = _scope.set((project_root, str(module_id or ""), str(feature or "")))
    try:
        yield
    finally:
        _scope.reset(token)


def parse_usage(usage: Any) -> dict[str, int]:
    """Extract prompt/completion tokens and cache hit/miss from a usage dict."""
    if not isinstance(usage, dict):
        return {}
    prompt = int(usage.get("prompt_tokens") or 0)
    completion = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0) or (prompt + completion)
    cache_hit = int(usage.get("prompt_cache_hit_tokens") or 0)
    cache_miss = int(usage.get("prompt_cache_miss_tokens") or 0)
    if cache_hit == 0 and cache_miss == 0:
        details = usage.get("prompt_tokens_details")
        if isinstance(details, dict):
            cached = int(details.get("cached_tokens") or 0)
            if cached > 0:
                cache_hit = cached
                cache_miss = max(0, prompt - cached)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss,
    }


def record_usage(
    usage: Any,
    *,
    project_root: Path | None = None,
    module_id: str = "",
    feature: str = "",
) -> dict[str, int]:
    """Accumulate one response's usage and return the global totals."""
    parsed = parse_usage(usage)
    if not parsed:
        return {}
    scoped_project, scoped_module, scoped_feature = _scope.get()
    selected_project = project_root if project_root is not None else scoped_project
    selected_module = str(module_id or scoped_module or "")
    selected_feature = str(feature or scoped_feature or "other")

    with _lock:
        document = _read_document()
        _add_metrics(document, parsed)
        key = _project_key(selected_project)
        if key:
            bucket = _project_bucket(document, key)
            _add_metrics(bucket["totals"], parsed)
            if selected_module:
                module = bucket["modules"].setdefault(selected_module, _empty_metrics())
                _add_metrics(module, parsed)
            item = bucket["features"].setdefault(selected_feature, _empty_metrics())
            _add_metrics(item, parsed)
        atomic_write_json(_usage_path(), document)
        totals = _normalized_metrics(document)
    telemetry.record_provider_usage(parsed)
    return totals


def _summary(metrics: dict[str, int]) -> dict[str, Any]:
    hit = int(metrics.get("cache_hit_tokens") or 0)
    miss = int(metrics.get("cache_miss_tokens") or 0)
    total = hit + miss
    return {
        "calls": int(metrics.get("calls") or 0),
        "prompt_tokens": int(metrics.get("prompt_tokens") or 0),
        "completion_tokens": int(metrics.get("completion_tokens") or 0),
        "total_tokens": int(metrics.get("total_tokens") or 0),
        "cache_hit_tokens": hit,
        "cache_miss_tokens": miss,
        "server_hit_rate": round(hit / total, 4) if total else None,
    }


def _breakdown(items: dict[str, Any], key_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item_id, metrics in items.items():
        summary = _summary(_normalized_metrics(metrics))
        rows.append({key_name: str(item_id), **summary})
    rows.sort(key=lambda item: (-int(item["total_tokens"]), str(item[key_name])))
    return rows


def usage_summary(
    project_root: Path | None = None,
    *,
    module_id: str = "",
) -> dict[str, Any]:
    """Return global or project-scoped totals plus module/feature breakdowns."""
    document = _read_document()
    if project_root is None:
        return {
            **_summary(_normalized_metrics(document)),
            "scope": "global",
            "modules": [],
            "features": [],
        }

    bucket = _project_bucket(document, _project_key(project_root))
    modules = _breakdown(bucket["modules"], "module_id")
    features = _breakdown(bucket["features"], "feature")
    if module_id:
        modules = [item for item in modules if item["module_id"] == module_id]
    return {
        **_summary(_normalized_metrics(bucket["totals"])),
        "scope": "project",
        "modules": modules,
        "features": features,
    }
