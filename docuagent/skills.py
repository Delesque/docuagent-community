"""Lightweight skill system for DocuAgent.

Skills live in `~/.docuagent/skills/<name>/` (global) and
`.docuagent/skills/<name>/` (project). Each skill has `skill.json` metadata plus a
`SKILL.md` body that agents can inject into bounded context.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from llm_client import ProviderConfig, call_model_json
from core import WorkspaceError

SKILL_JSON = "skill.json"
SKILL_MD = "SKILL.md"
MAX_SKILL_CHARS = 50_000

SKILL_SELECT_PROMPT = """You are selecting skills for one bounded implementation task.
Read the task summary and available skill metadata below. Choose at most 5 skills that
are directly useful. Prefer skills whose rules change how the code should be written.
Return JSON only:
{"skill_names":["skill-a","skill-b"]}
"""


def _skill_dirs(project_root: Path) -> list[Path]:
    return [
        project_root / ".docuagent" / "skills",
        Path.home() / ".docuagent" / "skills",
    ]


def _read_skill(skill_dir: Path) -> dict[str, Any] | None:
    meta_path = skill_dir / SKILL_JSON
    body_path = skill_dir / SKILL_MD
    if not meta_path.exists() or not body_path.exists():
        return None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(meta, dict) or not str(meta.get("name") or "").strip():
        return None
    content = body_path.read_text(encoding="utf-8", errors="replace")
    warning = ""
    if len(content) > MAX_SKILL_CHARS:
        warning = (
            f"技能正文超过 {MAX_SKILL_CHARS} 字符，"
            "可能导致上下文过长并影响输出质量，建议精简。"
        )
    return {
        "name": str(meta.get("name") or "").strip(),
        "description": str(meta.get("description") or "").strip(),
        "tags": [str(tag).strip() for tag in (meta.get("tags") or []) if str(tag).strip()],
        "content": content,
        "warning": warning,
        "source": str(skill_dir),
    }


def list_skills(project_root: Path) -> list[dict[str, Any]]:
    skills: dict[str, dict[str, Any]] = {}
    for base in _skill_dirs(project_root):
        if not base.is_dir():
            continue
        for skill_dir in sorted(base.iterdir()):
            if not skill_dir.is_dir():
                continue
            skill = _read_skill(skill_dir)
            if skill:
                skills[skill["name"]] = skill
    return list(skills.values())


def matching_skills(
    project_root: Path,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    query = (query or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for skill in list_skills(project_root):
        score = 0
        for token in query.split():
            if token in skill["name"].lower():
                score += 3
            if token in skill["description"].lower():
                score += 2
            for tag in skill["tags"]:
                if token in tag.lower():
                    score += 1
        if score > 0:
            scored.append((score, skill))
    scored.sort(key=lambda item: (-item[0], item[1]["name"]))
    return [
        {
            "name": skill["name"],
            "description": skill["description"],
            "tags": skill["tags"],
            "content": skill["content"],
        }
        for _, skill in scored[:limit]
    ]


def import_skill(project_root: Path, source: str) -> dict[str, Any]:
    if source.startswith(("http://", "https://")) or source.lower().endswith(".zip"):
        source_path = _download_skill_archive(source)
    else:
        source_path = Path(source).expanduser().resolve()
    skill = _read_skill(source_path)
    if not skill:
        raise WorkspaceError(f"不是有效的 Skill 目录：{source}")
    target = project_root / ".docuagent" / "skills" / skill["name"]
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source_path, target)
    imported = _read_skill(target)
    if not imported:
        raise WorkspaceError("Skill 导入后无法读取。")
    return {
        "name": imported["name"],
        "description": imported["description"],
        "tags": imported["tags"],
        "warning": imported.get("warning", ""),
    }


def select_skills(
    provider: ProviderConfig,
    query: str,
    available: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Let the model choose relevant skills from metadata, without truncating bodies."""
    if not available:
        return []
    result = call_model_json(
        provider,
        SKILL_SELECT_PROMPT,
        {
            "task_summary": query,
            "skills": [
                {
                    "name": skill["name"],
                    "description": skill["description"],
                    "tags": skill["tags"],
                }
                for skill in available
            ],
        },
        timeout=120,
    )
    names = result.get("skill_names") if isinstance(result, dict) else None
    if not isinstance(names, list):
        raise WorkspaceError("技能选择结果缺少 skill_names。")
    selected = [str(name).strip() for name in names if str(name).strip()]
    by_name = {skill["name"]: skill for skill in available}
    return [by_name[name] for name in selected if name in by_name][:5]


def _download_skill_archive(source: str) -> Path:
    temp_root = Path(tempfile.mkdtemp(prefix="docuagent-skill-"))
    archive = temp_root / "skill.zip"
    try:
        with urllib.request.urlopen(source, timeout=60) as response:
            archive.write_bytes(response.read())
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(temp_root)
    except Exception as exc:
        raise WorkspaceError(f"无法下载 Skill：{exc}") from exc
    for candidate in sorted(temp_root.rglob(SKILL_JSON)):
        return candidate.parent
    raise WorkspaceError("下载的 Skill 压缩包中没有 skill.json。")


def marketplace_entries(marketplace: str) -> list[dict[str, Any]]:
    raw = _read_text_source(marketplace)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkspaceError(f"市场文件不是有效 JSON：{exc}") from exc
    entries = data.get("skills", []) if isinstance(data, dict) else []
    cleaned = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        source = str(entry.get("source") or "").strip()
        if name and source:
            cleaned.append({
                "name": name,
                "description": str(entry.get("description") or "").strip(),
                "tags": [str(tag).strip() for tag in (entry.get("tags") or []) if str(tag).strip()],
                "source": source,
                "type": str(entry.get("type") or "skill").strip(),
            })
    return cleaned


def install_skill(project_root: Path, marketplace: str, name: str) -> dict[str, Any]:
    entry = next(
        (item for item in marketplace_entries(marketplace) if item["name"] == name),
        None,
    )
    if not entry:
        raise WorkspaceError(f"市场中没有 Skill：{name}")
    return import_skill(project_root, entry["source"])


def _read_text_source(source: str) -> str:
    if source.startswith(("http://", "https://")):
        try:
            with urllib.request.urlopen(source, timeout=30) as response:
                return response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise WorkspaceError(f"无法读取市场：{exc}") from exc
    path = Path(source).expanduser()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkspaceError(f"无法读取市场文件：{exc}") from exc
