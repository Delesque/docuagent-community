"""Memory-sedimentation loop: work logs become reviewable candidate updates.

The loop is explicit: the model proposes stable facts for `~/.docuagent/user-profile.md`
or the project `.docuagent/standards.md`, the candidate is stored, and the user accepts,
rejects, or later reverts it. Candidates are never applied automatically.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents import read_registry, read_work_log
from llm_client import ProviderConfig, call_model_json
from core import WorkspaceError, normalized_text
from standards import DEFAULT_STANDARDS_MD
import token_usage
from workspace import (
    atomic_write_json,
    atomic_write_text,
    managed_path,
    read_json,
    utc_now,
)

MEMORY_CANDIDATES_FILE = "memory-candidates.json"
RECIPES_FILE = "recipes.md"
MAX_MEMORY_CANDIDATES = 20

MEMORY_SEDIMENT_PROMPT = """You are the Memory Sediment Agent for DocuAgent.
Read the work logs below and propose stable, reusable facts for the user profile, the
project standards, or the project recipe file. Facts must be true, concise, and worth
remembering across future tasks. Do not compress or delete existing entries.
Return JSON only:
{"candidates":[{"target":"user-profile|standards|recipe","action":"append","title":"...","content":"...","source":"...","reason":"..."}]}
Rules:
- `target` is `user-profile` for global user preferences, or `standards` for project coding standards.
- `target` is `recipe` for a project-level repeatable practice that saved time or kept
  output consistent, phrased so a future sub-agent can follow it.
- `action` is always `append`.
- `title` is a short heading; `content` is one or two concise markdown lines.
- `source` names the work log or task the fact came from.
- `reason` explains why this fact should be kept.
- Return at most 10 candidates; return an empty array when nothing is reusable.
"""


def _user_profile_path() -> Path:
    return Path.home() / ".docuagent" / "user-profile.md"


def read_memory_candidates(project_root: Path) -> list[dict[str, Any]]:
    raw = read_json(managed_path(project_root, MEMORY_CANDIDATES_FILE))
    candidates = raw.get("candidates") if raw else None
    if not isinstance(candidates, list):
        return []
    return [candidate for candidate in candidates if isinstance(candidate, dict)]


def _write_memory_candidates(
    project_root: Path,
    candidates: list[dict[str, Any]],
) -> None:
    atomic_write_json(
        managed_path(project_root, MEMORY_CANDIDATES_FILE),
        {
            "schema_version": 1,
            "candidates": candidates,
        },
    )


def _find_candidate(
    project_root: Path,
    candidate_id: str,
    statuses: set[str] | None = None,
) -> dict[str, Any]:
    for candidate in read_memory_candidates(project_root):
        if candidate.get("id") == candidate_id:
            if statuses and candidate.get("status") not in statuses:
                raise WorkspaceError("候选记忆当前状态不允许该操作。")
            return candidate
    raise WorkspaceError("找不到记忆候选。")


def _read_target_file(target: str, project_root: Path) -> Path:
    if target == "user-profile":
        return _user_profile_path()
    if target == "standards":
        return managed_path(project_root, "standards.md")
    if target == "recipe":
        return managed_path(project_root, RECIPES_FILE)
    raise WorkspaceError("记忆候选 target 无效。")


def _append_memory_entry(
    target_file: Path,
    title: str,
    source: str,
    content: str,
) -> None:
    if not target_file.exists():
        if target_file == _user_profile_path():
            base = "# User Profile\n"
        elif target_file.name == RECIPES_FILE:
            base = "# Project Recipes\n"
        else:
            base = DEFAULT_STANDARDS_MD
        atomic_write_text(target_file, base)
    existing = target_file.read_text(encoding="utf-8").rstrip()
    block = (
        f"\n\n## {title}\n\n"
        f"- source: {source}\n"
        f"- created: {utc_now()}\n\n"
        f"{content.strip()}\n"
    )
    atomic_write_text(target_file, existing + block)


def suggest_memory_updates(
    project_root: Path,
    provider: ProviderConfig,
) -> dict[str, Any]:
    existing = read_memory_candidates(project_root)
    pending = [candidate for candidate in existing if candidate.get("status") == "pending"]
    if len(pending) >= MAX_MEMORY_CANDIDATES:
        raise WorkspaceError("记忆候选已满，请先处理现有候选。")

    work_logs: dict[str, str] = {}
    for entry in read_registry(project_root).get("agents", {}).values():
        module_id = entry.get("module_id") if isinstance(entry, dict) else None
        if module_id:
            work_logs[str(module_id)] = read_work_log(project_root, str(module_id))[-6000:]

    user_profile_path = _user_profile_path()
    standards_path = managed_path(project_root, "standards.md")
    recipes_path = managed_path(project_root, RECIPES_FILE)
    with token_usage.usage_scope(project_root, feature="memory"):
        result = call_model_json(
            provider,
            MEMORY_SEDIMENT_PROMPT,
            {
                "work_logs": work_logs,
                "current_user_profile": (
                    user_profile_path.read_text(encoding="utf-8")
                    if user_profile_path.exists()
                    else ""
                ),
                "current_standards": (
                    standards_path.read_text(encoding="utf-8")
                    if standards_path.exists()
                    else ""
                ),
                "current_recipes": (
                    recipes_path.read_text(encoding="utf-8")
                    if recipes_path.exists()
                    else ""
                ),
            },
            timeout=240,
        )

    raw_candidates = result.get("candidates")
    if not isinstance(raw_candidates, list):
        raise WorkspaceError("Memory Agent 返回值缺少 `candidates` 数组。")
    added: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_candidates[:10]):
        if not isinstance(raw, dict):
            raise WorkspaceError(f"candidates[{index}] 必须是对象。")
        target = str(raw.get("target") or "").strip()
        action = str(raw.get("action") or "append").strip()
        if target not in {"user-profile", "standards", "recipe"}:
            raise WorkspaceError(f"candidates[{index}] 的 target 无效。")
        if action != "append":
            raise WorkspaceError(f"candidates[{index}] 的 action 只支持 append。")
        title = normalized_text(raw.get("title"), f"candidates[{index}].title", True)
        content = normalized_text(
            raw.get("content"), f"candidates[{index}].content", True
        )
        candidate = {
            "id": f"{utc_now().replace(':', '-')}-{len(existing) + len(added) + 1}",
            "target": target,
            "action": action,
            "title": title[:120],
            "content": content[:2000],
            "source": normalized_text(
                raw.get("source"), f"candidates[{index}].source"
            )[:200],
            "reason": normalized_text(
                raw.get("reason"), f"candidates[{index}].reason"
            )[:500],
            "status": "pending",
            "created_at": utc_now(),
        }
        added.append(candidate)

    candidates = existing + added
    _write_memory_candidates(project_root, candidates)
    return {"candidates": candidates}


def apply_memory_candidate(
    project_root: Path,
    candidate_id: str,
) -> dict[str, Any]:
    candidate = _find_candidate(project_root, candidate_id, {"pending"})
    target_file = _read_target_file(candidate["target"], project_root)
    before = target_file.read_text(encoding="utf-8") if target_file.exists() else ""
    _append_memory_entry(
        target_file,
        candidate["title"],
        candidate.get("source", ""),
        candidate["content"],
    )
    candidate["status"] = "applied"
    candidate["applied_at"] = utc_now()
    candidate["before"] = before
    candidates = read_memory_candidates(project_root)
    for item in candidates:
        if item.get("id") == candidate_id:
            item.update(candidate)
    _write_memory_candidates(project_root, candidates)
    return candidate


def reject_memory_candidate(
    project_root: Path,
    candidate_id: str,
) -> dict[str, Any]:
    candidate = _find_candidate(project_root, candidate_id, {"pending"})
    candidate["status"] = "rejected"
    candidate["rejected_at"] = utc_now()
    candidates = read_memory_candidates(project_root)
    for item in candidates:
        if item.get("id") == candidate_id:
            item.update(candidate)
    _write_memory_candidates(project_root, candidates)
    return candidate


def revert_memory_candidate(
    project_root: Path,
    candidate_id: str,
) -> dict[str, Any]:
    candidate = _find_candidate(project_root, candidate_id, {"applied"})
    target_file = _read_target_file(candidate["target"], project_root)
    atomic_write_text(target_file, candidate.get("before", ""))
    candidate["status"] = "reverted"
    candidate["reverted_at"] = utc_now()
    candidates = read_memory_candidates(project_root)
    for item in candidates:
        if item.get("id") == candidate_id:
            item.update(candidate)
    _write_memory_candidates(project_root, candidates)
    return candidate
