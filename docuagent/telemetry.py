"""Anonymous local metrics with explicit, batch-only upload consent.

The collector only accepts whitelisted counters and aggregate durations. It never
accepts project paths, prompts, code, model names, API keys or free-form events.
Local recording is enabled by default; network upload is blocked until the user
sets ``consent`` to true and a release build configures an HTTPS endpoint.
"""

from __future__ import annotations

import json
import os
import platform
import re
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import APP_VERSION, WorkspaceError
from workspace import atomic_write_json, read_json, utc_now

TELEMETRY_FILE = "telemetry.json"
TELEMETRY_SCHEMA_VERSION = 1
POLICY_VERSION = 1
AUTO_UPLOAD_INTERVAL_SECONDS = 24 * 60 * 60
UPLOAD_URL_ENV = "DOCUAGENT_TELEMETRY_UPLOAD_URL"
_KIND_RE = re.compile(r"[^a-z0-9._-]+")
_LOCK = threading.RLock()
_AUTO_UPLOAD_RUNNING = False


def _telemetry_path() -> Path:
    override = str(os.environ.get("DOCUAGENT_TELEMETRY_PATH") or "").strip()
    if override:
        return Path(override)
    return Path.home() / ".docuagent" / TELEMETRY_FILE


def _empty_metrics() -> dict[str, Any]:
    return {
        "app_starts": 0,
        "jobs_started": 0,
        "jobs_completed": 0,
        "jobs_failed": 0,
        "jobs_cancelled": 0,
        "jobs_orphaned": 0,
        "job_duration_count": 0,
        "job_duration_total_ms": 0,
        "context_cache_hits": 0,
        "context_cache_misses": 0,
        "provider_calls": 0,
        "provider_prompt_tokens": 0,
        "provider_completion_tokens": 0,
        "provider_cache_hits": 0,
        "provider_cache_misses": 0,
        "verification_failures": 0,
        "documentation_failures": 0,
        "documentation_retries": 0,
        "jobs_by_kind": {},
    }


def _empty_document() -> dict[str, Any]:
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "settings": {
            "consent": False,
            "consent_at": "",
            "policy_version": POLICY_VERSION,
        },
        "lifetime": _empty_metrics(),
        "pending": _empty_metrics(),
        "last_upload_at": "",
        "last_upload_error": "",
        "last_auto_attempt": "",
    }


def _normalize_metrics(value: Any) -> dict[str, Any]:
    target = _empty_metrics()
    if not isinstance(value, dict):
        return target
    for key in target:
        if key == "jobs_by_kind":
            continue
        target[key] = int(value.get(key) or 0)
    raw_kinds = value.get("jobs_by_kind")
    if isinstance(raw_kinds, dict):
        kinds: dict[str, dict[str, int]] = {}
        for raw_kind, raw_counts in raw_kinds.items():
            kind = _normalize_kind(raw_kind)
            if not kind or not isinstance(raw_counts, dict):
                continue
            kinds[kind] = {
                key: int(raw_counts.get(key) or 0)
                for key in ("started", "completed", "failed", "cancelled", "orphaned")
            }
        target["jobs_by_kind"] = kinds
    return target


def _read_document() -> dict[str, Any]:
    raw = read_json(_telemetry_path())
    if not isinstance(raw, dict):
        return _empty_document()
    document = _empty_document()
    raw_settings = raw.get("settings")
    if isinstance(raw_settings, dict):
        document["settings"]["consent"] = raw_settings.get("consent") is True
        document["settings"]["consent_at"] = str(raw_settings.get("consent_at") or "")
        document["settings"]["policy_version"] = int(
            raw_settings.get("policy_version") or POLICY_VERSION
        )
    document["lifetime"] = _normalize_metrics(raw.get("lifetime"))
    document["pending"] = _normalize_metrics(raw.get("pending"))
    for key in ("last_upload_at", "last_upload_error", "last_auto_attempt"):
        document[key] = str(raw.get(key) or "")
    return document


def _normalize_kind(value: Any) -> str:
    kind = _KIND_RE.sub("-", str(value or "").strip().lower()).strip("-")
    return kind[:40]


def _add_metrics(target: dict[str, Any], update: dict[str, Any]) -> None:
    for key in _empty_metrics():
        if key == "jobs_by_kind":
            continue
        target[key] = int(target.get(key) or 0) + int(update.get(key) or 0)
    raw_kinds = update.get("jobs_by_kind")
    if isinstance(raw_kinds, dict):
        kinds = target.setdefault("jobs_by_kind", {})
        for raw_kind, raw_counts in raw_kinds.items():
            kind = _normalize_kind(raw_kind)
            if not kind or not isinstance(raw_counts, dict):
                continue
            counts = kinds.setdefault(
                kind,
                {key: 0 for key in ("started", "completed", "failed", "cancelled", "orphaned")},
            )
            for key in counts:
                counts[key] = int(counts.get(key) or 0) + int(raw_counts.get(key) or 0)


def _subtract_metrics(target: dict[str, Any], sent: dict[str, Any]) -> None:
    for key in _empty_metrics():
        if key == "jobs_by_kind":
            continue
        target[key] = max(0, int(target.get(key) or 0) - int(sent.get(key) or 0))
    target_kinds = target.setdefault("jobs_by_kind", {})
    for raw_kind, sent_counts in (sent.get("jobs_by_kind") or {}).items():
        kind = _normalize_kind(raw_kind)
        counts = target_kinds.get(kind)
        if not counts or not isinstance(sent_counts, dict):
            continue
        for key in counts:
            counts[key] = max(
                0,
                int(counts.get(key) or 0) - int(sent_counts.get(key) or 0),
            )


def _metrics_are_empty(metrics: dict[str, Any]) -> bool:
    if any(
        int(value or 0)
        for key, value in metrics.items()
        if key != "jobs_by_kind"
    ):
        return False
    return not any(
        any(int(value or 0) for value in counts.values())
        for counts in (metrics.get("jobs_by_kind") or {}).values()
        if isinstance(counts, dict)
    )


def _pending_metric_count(metrics: dict[str, Any]) -> int:
    count = sum(
        1
        for key, value in metrics.items()
        if key != "jobs_by_kind" and int(value or 0) > 0
    )
    count += sum(
        1
        for counts in (metrics.get("jobs_by_kind") or {}).values()
        if isinstance(counts, dict)
        and any(int(value or 0) > 0 for value in counts.values())
    )
    return count


def _write_document(document: dict[str, Any]) -> None:
    atomic_write_json(_telemetry_path(), document)


def _record(update: dict[str, Any]) -> dict[str, Any]:
    with _LOCK:
        document = _read_document()
        _add_metrics(document["lifetime"], update)
        _add_metrics(document["pending"], update)
        _write_document(document)
    _schedule_auto_upload()
    return document["lifetime"]


def record_app_start() -> dict[str, Any]:
    return _record({"app_starts": 1})


def record_job_started(kind: str) -> dict[str, Any]:
    normalized = _normalize_kind(kind) or "task"
    return _record({
        "jobs_started": 1,
        "jobs_by_kind": {normalized: {"started": 1}},
    })


def record_job_finished(
    kind: str,
    status: str,
    *,
    duration_ms: int | None = None,
) -> dict[str, Any]:
    normalized = _normalize_kind(kind) or "task"
    terminal = str(status or "").strip().lower()
    if terminal not in {"completed", "failed", "cancelled", "orphaned"}:
        return {}
    update: dict[str, Any] = {
        f"jobs_{terminal}": 1,
        "jobs_by_kind": {normalized: {terminal: 1}},
    }
    if duration_ms is not None and duration_ms >= 0:
        value = int(duration_ms)
        update.update({
            "job_duration_count": 1,
            "job_duration_total_ms": value,
        })
    return _record(update)


def record_context_cache(hits: int, misses: int) -> dict[str, Any]:
    return _record({
        "context_cache_hits": max(0, int(hits)),
        "context_cache_misses": max(0, int(misses)),
    })


def record_provider_usage(parsed: dict[str, int]) -> dict[str, Any]:
    return _record({
        "provider_calls": 1,
        "provider_prompt_tokens": int(parsed.get("prompt_tokens") or 0),
        "provider_completion_tokens": int(parsed.get("completion_tokens") or 0),
        "provider_cache_hits": int(parsed.get("cache_hit_tokens") or 0),
        "provider_cache_misses": int(parsed.get("cache_miss_tokens") or 0),
    })


def record_verification_failure() -> dict[str, Any]:
    return _record({"verification_failures": 1})


def record_documentation_failure(*, retry: bool = False) -> dict[str, Any]:
    return _record({
        "documentation_failures": 1,
        "documentation_retries": 1 if retry else 0,
    })


def upload_url() -> str:
    return str(os.environ.get(UPLOAD_URL_ENV) or "").strip()


def _validated_upload_url() -> str:
    value = upload_url()
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as exc:
        raise WorkspaceError("匿名统计上传地址无效。") from exc
    if parsed.scheme == "https" and parsed.netloc:
        return value
    if (
        parsed.scheme == "http"
        and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        and parsed.netloc
    ):
        return value
    raise WorkspaceError("匿名统计只允许上传到 HTTPS 地址。")


def upload_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    platform_name = {
        "win32": "windows",
        "darwin": "macos",
    }.get(sys.platform, sys.platform)
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "product": "docuagent-community",
        "release_version": APP_VERSION,
        "platform": platform_name,
        "architecture": platform.machine(),
        "batch_id": "batch-" + uuid.uuid4().hex,
        "metrics": metrics,
    }


def _post_payload(url: str, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": f"DocuAgent/{APP_VERSION}",
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if not 200 <= int(response.status) < 300:
            raise WorkspaceError(f"匿名统计上传失败：HTTP {response.status}")


def _run_upload(*, automatic: bool) -> dict[str, Any]:
    global _AUTO_UPLOAD_RUNNING
    with _LOCK:
        document = _read_document()
        if document["settings"]["consent"] is not True:
            if automatic:
                _AUTO_UPLOAD_RUNNING = False
                return {"uploaded": False, "reason": "consent-required"}
            raise WorkspaceError("请先同意匿名统计上传。")
        url = _validated_upload_url()
        if not url:
            if automatic:
                _AUTO_UPLOAD_RUNNING = False
                return {"uploaded": False, "reason": "endpoint-unavailable"}
            raise WorkspaceError("当前版本尚未配置匿名统计上传服务。")
        sent = _normalize_metrics(document["pending"])
        if _metrics_are_empty(sent):
            if automatic:
                _AUTO_UPLOAD_RUNNING = False
            return {"uploaded": False, "reason": "empty"}
        payload = upload_payload(sent)

    try:
        _post_payload(url, payload)
    except Exception as exc:
        message = str(exc)[:240]
        with _LOCK:
            current = _read_document()
            current["last_upload_error"] = message
            current["last_auto_attempt"] = utc_now()
            _write_document(current)
        if automatic:
            _AUTO_UPLOAD_RUNNING = False
            return {"uploaded": False, "reason": "failed", "error": message}
        raise WorkspaceError(f"匿名统计上传失败：{message}") from exc

    with _LOCK:
        current = _read_document()
        _subtract_metrics(current["pending"], sent)
        current["last_upload_at"] = utc_now()
        current["last_upload_error"] = ""
        current["last_auto_attempt"] = current["last_upload_at"]
        _write_document(current)
    _AUTO_UPLOAD_RUNNING = False
    return {"uploaded": True, "metrics": sent, "uploaded_at": current["last_upload_at"]}


def _schedule_auto_upload(*, force: bool = False) -> None:
    global _AUTO_UPLOAD_RUNNING
    if not upload_url():
        return
    with _LOCK:
        if _AUTO_UPLOAD_RUNNING:
            return
        document = _read_document()
        if document["settings"]["consent"] is not True:
            return
        last_attempt = document.get("last_auto_attempt") or document.get("last_upload_at")
        if last_attempt and not force:
            try:
                parsed = datetime.fromisoformat(str(last_attempt))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                elapsed = (datetime.now(timezone.utc) - parsed).total_seconds()
                if elapsed < AUTO_UPLOAD_INTERVAL_SECONDS:
                    return
            except ValueError:
                pass
        _AUTO_UPLOAD_RUNNING = True
        document["last_auto_attempt"] = utc_now()
        _write_document(document)
    threading.Thread(
        target=_run_upload,
        kwargs={"automatic": True},
        name="docuagent-telemetry-upload",
        daemon=True,
    ).start()


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def summary() -> dict[str, Any]:
    with _LOCK:
        document = _read_document()
    lifetime = document["lifetime"]
    pending = document["pending"]
    terminal_jobs = sum(
        int(lifetime.get(key) or 0)
        for key in ("jobs_completed", "jobs_failed", "jobs_cancelled", "jobs_orphaned")
    )
    cache_hits = int(lifetime.get("context_cache_hits") or 0) + int(
        lifetime.get("provider_cache_hits") or 0
    )
    cache_misses = int(lifetime.get("context_cache_misses") or 0) + int(
        lifetime.get("provider_cache_misses") or 0
    )
    duration_count = int(lifetime.get("job_duration_count") or 0)
    return {
        "schema_version": TELEMETRY_SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "local_recording_enabled": True,
        "consent": document["settings"]["consent"] is True,
        "consent_at": document["settings"]["consent_at"],
        "upload_available": bool(upload_url()),
        "auto_upload_interval_hours": 24,
        "pending": pending,
        "lifetime": lifetime,
        "pending_events": _pending_metric_count(pending),
        "failure_rate": _rate(int(lifetime.get("jobs_failed") or 0), terminal_jobs),
        "cancellation_rate": _rate(
            int(lifetime.get("jobs_cancelled") or 0),
            terminal_jobs,
        ),
        "orphan_rate": _rate(
            int(lifetime.get("jobs_orphaned") or 0),
            terminal_jobs,
        ),
        "cache_hit_rate": _rate(cache_hits, cache_hits + cache_misses),
        "average_job_duration_ms": (
            round(
                int(lifetime.get("job_duration_total_ms") or 0) / duration_count
            )
            if duration_count
            else None
        ),
        "last_upload_at": document["last_upload_at"],
        "last_upload_error": document["last_upload_error"],
    }


def configure(payload: dict[str, Any]) -> dict[str, Any]:
    if "consent" not in payload or not isinstance(payload.get("consent"), bool):
        raise WorkspaceError("consent 必须是布尔值。")
    consent = bool(payload["consent"])
    with _LOCK:
        document = _read_document()
        document["settings"]["consent"] = consent
        document["settings"]["policy_version"] = POLICY_VERSION
        if consent:
            document["settings"]["consent_at"] = utc_now()
        _write_document(document)
    if consent:
        _schedule_auto_upload(force=True)
    return summary()


def upload_pending() -> dict[str, Any]:
    return _run_upload(automatic=False)


def clear_local_data() -> dict[str, Any]:
    with _LOCK:
        document = _read_document()
        document["lifetime"] = _empty_metrics()
        document["pending"] = _empty_metrics()
        document["last_upload_at"] = ""
        document["last_upload_error"] = ""
        document["last_auto_attempt"] = ""
        _write_document(document)
    return summary()
