"""Project snapshots for rolling a workspace back to an earlier conversation moment.

Every state-changing operation (interview turn, architecture edit, task apply, doc
sync) takes a lightweight snapshot of the managed state plus the project's text files.
The frontend lists those snapshots and can restore one; after a restore the page
reloads from the restored files.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from workspace import (
    apply_text_files_atomic,
    atomic_write_json,
    managed_path,
    read_json,
    utc_now,
)

SNAPSHOT_MAX = 30
SNAPSHOT_STATE_FILE = "snapshots.json"
_EXCLUDED_DIRS = {
    ".git",
    ".backups",
    ".recovery",
    "node_modules",
    "__pycache__",
    "web-next",
}
_MAX_FILES = 300
_MAX_FILE_BYTES = 200_000
_SECRET_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
}
_SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx"}


def _is_secret_file(name: str) -> bool:
    lowered = name.lower()
    return lowered in _SECRET_FILE_NAMES or Path(lowered).suffix in _SECRET_SUFFIXES


def _collect_files(project_root: Path) -> tuple[dict[str, str], dict[str, Any]]:
    """Collect restorable text files plus explicit completeness metadata.

    The snapshot store itself is intentionally excluded. Including
    `.docuagent/snapshots.json` would make every new snapshot embed all previous
    snapshots, growing the store quadratically.
    """
    files: dict[str, str] = {}
    root = project_root.resolve()
    total_files = 0
    skipped_large: list[str] = []
    skipped_binary: list[str] = []
    skipped_secret_count = 0
    skipped_over_limit = 0

    for current, dirs, names in os.walk(project_root):
        dirs[:] = [d for d in sorted(dirs) if d not in _EXCLUDED_DIRS]
        current_path = Path(current)
        for name in sorted(names):
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative == f".docuagent/{SNAPSHOT_STATE_FILE}":
                continue
            if _is_secret_file(name):
                skipped_secret_count += 1
                continue
            total_files += 1
            if len(files) >= _MAX_FILES:
                skipped_over_limit += 1
                continue
            try:
                if path.stat().st_size > _MAX_FILE_BYTES:
                    skipped_large.append(relative)
                    continue
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                skipped_binary.append(relative)
                continue
            files[relative] = content

    skipped_count = (
        len(skipped_large)
        + len(skipped_binary)
        + skipped_secret_count
        + skipped_over_limit
    )
    meta = {
        "total_files": total_files,
        "truncated": skipped_count > 0,
        "skipped_count": skipped_count,
        "skipped_large_files": skipped_large[:50],
        "skipped_binary_files": skipped_binary[:50],
        "skipped_secret_count": skipped_secret_count,
        "skipped_over_limit": skipped_over_limit,
    }
    return files, meta


def read_snapshots(project_root: Path) -> list[dict[str, Any]]:
    raw = read_json(managed_path(project_root, SNAPSHOT_STATE_FILE))
    if not raw or not isinstance(raw.get("entries"), list):
        return []
    return [entry for entry in raw["entries"] if isinstance(entry, dict)]


def create_snapshot(project_root: Path, reason: str) -> dict[str, Any]:
    entries = read_snapshots(project_root)
    snapshot_id = f"{utc_now().replace(':', '-')}-{len(entries) + 1}"
    files, completeness = _collect_files(project_root)
    entry = {
        "id": snapshot_id,
        "created_at": utc_now(),
        "reason": reason[:200],
        "file_count": len(files),
        "total_files": completeness["total_files"],
        "truncated": completeness["truncated"],
        "skipped_count": completeness["skipped_count"],
        "skipped_large_files": completeness["skipped_large_files"],
        "skipped_binary_files": completeness["skipped_binary_files"],
        "skipped_secret_count": completeness["skipped_secret_count"],
        "skipped_over_limit": completeness["skipped_over_limit"],
        "files": files,
    }
    entries.append(entry)
    if len(entries) > SNAPSHOT_MAX:
        entries = entries[-SNAPSHOT_MAX:]
    atomic_write_json(
        managed_path(project_root, SNAPSHOT_STATE_FILE),
        {"schema_version": 1, "entries": entries},
    )
    return {
        "id": entry["id"],
        "created_at": entry["created_at"],
        "reason": entry["reason"],
        "file_count": entry["file_count"],
        "total_files": entry["total_files"],
        "truncated": entry["truncated"],
        "skipped_count": entry["skipped_count"],
    }


def list_snapshots(project_root: Path) -> list[dict[str, Any]]:
    entries = read_snapshots(project_root)
    return [
        {
            "id": entry["id"],
            "created_at": entry.get("created_at", ""),
            "reason": entry.get("reason", ""),
            "file_count": int(entry.get("file_count") or len(entry.get("files", {}))),
            "total_files": int(entry.get("total_files") or len(entry.get("files", {}))),
            "truncated": bool(entry.get("truncated")),
            "skipped_count": int(entry.get("skipped_count") or 0),
        }
        for entry in reversed(entries)
    ]


def restore_snapshot(project_root: Path, snapshot_id: str) -> dict[str, Any]:
    entries = read_snapshots(project_root)
    target = next((entry for entry in entries if entry.get("id") == snapshot_id), None)
    if not target:
        raise LookupError(f"找不到快照：{snapshot_id}")
    files = target.get("files")
    if not isinstance(files, dict):
        raise LookupError(f"快照 `{snapshot_id}` 没有文件内容。")

    current_files, _ = _collect_files(project_root)
    extra_files = sorted(set(current_files) - set(files))

    restored = apply_text_files_atomic(
        project_root,
        {relative: str(content) for relative, content in files.items()},
    )
    return {
        "id": target["id"],
        "created_at": target.get("created_at", ""),
        "reason": target.get("reason", ""),
        "restored_files": len(restored),
        "truncated": bool(target.get("truncated")),
        "skipped_count": int(target.get("skipped_count") or 0),
        "extra_files": extra_files[:100],
        "extra_file_count": len(extra_files),
    }
