"""Sub-agent registry and workspace ownership.

Phase 3's addressing layer: every sub-agent is keyed by its `module_id`, the same
stable id as its architecture node. The registry is the only place the conversation
flow needs to look to find an agent's work area, session, work log, and error memory.

Workspace locks are wave-scoped: they exist to stop two agents in the same parallel
wave from writing the same file. Dependents run in later waves after the lock is
released, which is enough to keep generation conflict-free without locking a task's
patch through the whole review cycle.
"""

from __future__ import annotations

import os
import re
import threading
from pathlib import Path, PurePosixPath
from typing import Any

from capabilities import CODE_INTEL, CODE_INTEL_ENV, DEFAULT_SUBAGENT_CAPABILITIES
from core import WorkspaceError
import context_cache
import plugins
import skills
from standards import DEFAULT_STANDARDS_MD
from workspace import (
    atomic_write_json,
    atomic_write_text,
    managed_path,
    read_contracts,
    read_json,
    read_node_attachments,
    utc_now,
)

AGENTS_DIR = "agents"
REGISTRY_FILE = f"{AGENTS_DIR}/registry.json"
LOCK_FILE = f"{AGENTS_DIR}/locks.json"

AGENT_STATUSES = {"idle", "running", "review", "blocked", "archived"}


def _enabled_subagent_capabilities() -> frozenset:
    """Capabilities every sub-agent is granted by default, plus any opt-in ones.

    Code intelligence is opt-in: it is only added when the operator explicitly sets
    DOCUAGENT_CODE_INTEL, so the capability (and therefore the codeintel agent tools)
    stay off unless consciously enabled. Fails closed by default.
    """
    caps = set(DEFAULT_SUBAGENT_CAPABILITIES)
    if os.environ.get(CODE_INTEL_ENV):
        caps.add(CODE_INTEL)
    return frozenset(caps)
ERROR_MEMORY_MAX = 30
WORK_LOG_TAIL_CHARS = 8000
PROJECT_OVERVIEW_MAX_CHARS = 8000
STANDARDS_MAX_CHARS = 6000
RECIPES_MAX_CHARS = 6000
USER_PROFILE_MAX_CHARS = 3000
SESSION_TAIL_CHARS = 2000
_MEMORY_WRITE_LOCK = threading.Lock()


def _safe_module_id(module_id: str) -> str:
    candidate = str(module_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", candidate):
        raise WorkspaceError("无效的子 Agent 地址。")
    return candidate


def _ensure_agent_memory_files(project_root: Path, module_id: str) -> None:
    """Create the registry-declared memory files so paths never point at nothing."""
    slug = _safe_module_id(module_id)
    work_log = managed_path(project_root, AGENTS_DIR, slug, "work_log.md")
    error_memory = managed_path(project_root, AGENTS_DIR, slug, "error_memory.json")
    session = managed_path(project_root, AGENTS_DIR, slug, "session.md")
    if not work_log.exists():
        atomic_write_text(work_log, "")
    if not error_memory.exists():
        atomic_write_json(error_memory, {"schema_version": 1, "entries": []})
    if not session.exists():
        atomic_write_text(session, "")


def _registry_path(project_root: Path) -> Path:
    return managed_path(project_root, REGISTRY_FILE)


def _lock_path(project_root: Path) -> Path:
    return managed_path(project_root, LOCK_FILE)


def read_registry(project_root: Path) -> dict[str, Any]:
    raw = read_json(_registry_path(project_root))
    return raw if isinstance(raw, dict) else {"schema_version": 1, "agents": {}}


def write_registry(project_root: Path, state: dict[str, Any]) -> None:
    atomic_write_json(_registry_path(project_root), state)


def list_agents(project_root: Path) -> list[dict[str, Any]]:
    state = read_registry(project_root)
    agents = state.get("agents", {})
    if not isinstance(agents, dict):
        return []
    return [
        entry
        for entry in agents.values()
        if isinstance(entry, dict) and entry.get("module_id")
    ]


def register_agent(
    project_root: Path,
    module_id: str,
    task_id: str,
    work_area: list[str],
    status: str = "running",
) -> dict[str, Any]:
    if status not in AGENT_STATUSES:
        status = "running"
    _ensure_agent_memory_files(project_root, module_id)
    state = read_registry(project_root)
    agents = state.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        state["agents"] = agents
    entry = agents.get(module_id)
    if not isinstance(entry, dict):
        entry = {
            "module_id": module_id,
            "work_log": f".docuagent/{AGENTS_DIR}/{module_id}/work_log.md",
            "error_memory": f".docuagent/{AGENTS_DIR}/{module_id}/error_memory.json",
            "session": f".docuagent/{AGENTS_DIR}/{module_id}/session.md",
            "created_at": utc_now(),
        }
    entry.update({
        "task_id": task_id,
        "work_area": list(work_area),
        "status": status,
        "updated_at": utc_now(),
    })
    agents[module_id] = entry
    write_registry(project_root, state)
    return entry


def update_agent_status(
    project_root: Path,
    module_id: str,
    status: str,
    error: str = "",
) -> dict[str, Any] | None:
    if status not in AGENT_STATUSES:
        return None
    state = read_registry(project_root)
    agents = state.get("agents", {})
    entry = agents.get(module_id)
    if not isinstance(entry, dict):
        return None
    entry["status"] = status
    if error:
        entry["last_error"] = error
    entry["updated_at"] = utc_now()
    write_registry(project_root, state)
    return entry


def read_work_log(project_root: Path, module_id: str) -> str:
    """Return the agent's markdown work log, newest entries at the end."""
    path = managed_path(project_root, AGENTS_DIR, _safe_module_id(module_id), "work_log.md")
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def append_work_log(
    project_root: Path,
    module_id: str,
    kind: str,
    task_id: str,
    details: str,
) -> str:
    """Append one structured entry to an agent's work log."""
    with _MEMORY_WRITE_LOCK:
        slug = _safe_module_id(module_id)
        path = managed_path(project_root, AGENTS_DIR, slug, "work_log.md")
        existing = read_work_log(project_root, slug).strip()
        timestamp = utc_now()
        entry = (
            f"## {timestamp} · {kind}\n\n"
            f"- task: {task_id}\n\n"
            f"{str(details or '').strip()}\n"
        )
        content = f"{existing}\n\n{entry}" if existing else entry
        atomic_write_text(path, content)
        return read_work_log(project_root, slug)


def read_error_memory(project_root: Path, module_id: str) -> list[dict[str, Any]]:
    """Return structured error entries, newest last, bounded to the latest 30."""
    path = managed_path(
        project_root, AGENTS_DIR, _safe_module_id(module_id), "error_memory.json"
    )
    raw = read_json(path)
    entries = raw.get("entries") if raw else None
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)][-ERROR_MEMORY_MAX:]


def append_error_memory(
    project_root: Path,
    module_id: str,
    source: str,
    error: str,
    task_id: str = "",
    related_files: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Persist one failure so later tasks can avoid repeating it."""
    with _MEMORY_WRITE_LOCK:
        slug = _safe_module_id(module_id)
        path = managed_path(project_root, AGENTS_DIR, slug, "error_memory.json")
        entries = read_error_memory(project_root, slug)
        entries.append({
            "id": f"{utc_now().replace(':', '-')}-{len(entries) + 1}",
            "created_at": utc_now(),
            "source": (
                source
                if source in {"generation", "verification", "review"}
                else "generation"
            ),
            "task_id": str(task_id or ""),
            "error": str(error or "")[:2000],
            "related_files": [
                str(path) for path in (related_files or []) if str(path)
            ][:20],
        })
        atomic_write_json(
            path,
            {
                "schema_version": 1,
                "entries": entries[-ERROR_MEMORY_MAX:],
            },
        )
        return read_error_memory(project_root, slug)


def clear_error_memory(project_root: Path, module_id: str) -> list[dict[str, Any]]:
    """Clear an agent's persisted error memory after the user reviews it."""
    slug = _safe_module_id(module_id)
    path = managed_path(project_root, AGENTS_DIR, slug, "error_memory.json")
    atomic_write_json(path, {"schema_version": 1, "entries": []})
    return []


def send_agent_message(
    project_root: Path,
    module_id: str,
    text: str,
) -> dict[str, Any]:
    """Forward a user message to an agent's session file for later consumption."""
    slug = _safe_module_id(module_id)
    session = managed_path(project_root, AGENTS_DIR, slug, "session.md")
    if not session.exists():
        atomic_write_text(session, "")
    existing = session.read_text(encoding="utf-8").rstrip()
    block = (
        f"\n\n## {utc_now()} · user message\n\n"
        f"{text.strip()}\n"
    )
    atomic_write_text(session, f"{existing}{block}" if existing else block)
    return {
        "module_id": slug,
        "session_path": f".docuagent/{AGENTS_DIR}/{slug}/session.md",
        "content": session.read_text(encoding="utf-8"),
    }


def agent_detail(project_root: Path, module_id: str) -> dict[str, Any]:
    """A supervision-channel read: work log and error memory for one agent."""
    slug = _safe_module_id(module_id)
    entry = read_registry(project_root).get("agents", {}).get(slug)
    if not isinstance(entry, dict):
        raise WorkspaceError("找不到子 Agent。")
    work_log_path = managed_path(project_root, AGENTS_DIR, slug, "work_log.md")
    error_memory_path = managed_path(
        project_root, AGENTS_DIR, slug, "error_memory.json"
    )
    session_path = managed_path(project_root, AGENTS_DIR, slug, "session.md")
    return {
        "module_id": slug,
        "work_log": read_work_log(project_root, slug),
        "error_memory": read_error_memory(project_root, slug),
        "session": (
            session_path.read_text(encoding="utf-8")
            if session_path.exists()
            else ""
        ),
        "work_log_exists": work_log_path.exists(),
        "error_memory_exists": error_memory_path.exists(),
        "session_path": f".docuagent/{AGENTS_DIR}/{slug}/session.md",
        "work_log_path": entry.get("work_log", ""),
        "error_memory_path": entry.get("error_memory", ""),
    }


def _read_optional_text(path: Path, max_chars: int) -> str:
    if not path.exists():
        return ""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + "\n[context truncated]"


def _contract_query(module: dict[str, Any], task: dict[str, Any] | None) -> str:
    return " ".join(
        str(item)
        for item in (
            module.get("id", ""),
            module.get("name", ""),
            module.get("responsibility", ""),
            module.get("brief", ""),
            (task or {}).get("summary", ""),
        )
        if str(item or "").strip()
    )


def _contract_related(query: str, text: str) -> bool:
    """Deterministic keyword match for search_reuse / contract_view.

    No vector database: first try direct substring both ways (works for Chinese
    phrases and English identifiers like `invoice` vs `invoice_id`), then fall back
    to shared alnum tokens.
    """
    query = (query or "").strip().lower()
    text = (text or "").strip().lower()
    if not query:
        return True
    if not text:
        return False
    if query in text or text in query:
        return True
    query_tokens = set(re.findall(r"[a-z0-9_]+", query))
    text_tokens = set(re.findall(r"[a-z0-9_]+", text))
    return bool(query_tokens & text_tokens)


def _upstream_changes(project_root: Path, module_id: str) -> dict[str, Any]:
    """Contract-change impact summary for this module, or {} when unaffected.

    Answers "did an interface this module builds against move?" at context-build
    time, so an agent whose upstream changed learns it before writing code. Lives
    in the contract domain (``contract_registry``): agents never import the
    codeintel package. Every failure degrades to {} — impact data must never
    block an agent from working.
    """
    try:
        contracts = read_contracts(project_root) or {}
        if not contracts:
            return {}
        from contract_registry import upstream_changes_for_module

        return upstream_changes_for_module(contracts, module_id)
    except Exception:
        return {}


def _contract_view(
    project_root: Path,
    module: dict[str, Any],
    task: dict[str, Any] | None,
) -> dict[str, Any]:
    """The module-level stable view injected before `module_contract`.

    It contains only the registry entries relevant to this agent: related global
    vocabulary, the module's own exports, exports of declared dependencies, plus
    matching shared_kernel and recipe entries. The full contracts.json is never
    injected.
    """
    contracts = read_contracts(project_root) or {}
    own_id = str(module.get("id") or "")
    query = _contract_query(module, task)

    vocabulary: list[dict[str, Any]] = []
    for entry in contracts.get("vocabulary", []):
        if not isinstance(entry, dict):
            continue
        text = " ".join(
            str(entry.get(key) or "")
            for key in ("term", "owner", "kind", "type", "format")
        )
        if _contract_related(query, text):
            vocabulary.append(entry)

    own_exports: list[dict[str, Any]] = []
    dependency_exports: list[dict[str, Any]] = []
    for contract_module in contracts.get("modules", []):
        if not isinstance(contract_module, dict):
            continue
        exports = contract_module.get("exports")
        if not isinstance(exports, list):
            exports = []
        if contract_module.get("id") == own_id:
            own_exports = exports
        elif contract_module.get("id") in (module.get("depends_on") or []):
            dependency_exports.append({
                "module_id": contract_module.get("id", ""),
                "exports": exports,
            })

    # Symbols the architecture declared this module will provide. They are
    # promises (`proposed` in the registry) until real code references them.
    expected_public_api: list[str] = []
    for export in own_exports:
        if not isinstance(export, dict):
            continue
        if str(export.get("status") or "") == "proposed":
            symbol = str(export.get("symbol") or "").strip()
            if symbol:
                expected_public_api.append(symbol)

    shared_kernel: list[dict[str, Any]] = []
    for entry in contracts.get("shared_kernel", []):
        if not isinstance(entry, dict):
            continue
        consumers = entry.get("consumers") or []
        if own_id in consumers or _contract_related(
            query,
            " ".join(str(entry.get(key) or "") for key in ("symbol", "why", "owner")),
        ):
            shared_kernel.append(entry)

    recipes: list[dict[str, Any]] = []
    for entry in contracts.get("recipes", []):
        if not isinstance(entry, dict):
            continue
        used_by = entry.get("used_by") or []
        if own_id in used_by or _contract_related(
            query,
            " ".join(str(entry.get(key) or "") for key in ("name", "problem", "solution")),
        ):
            recipes.append(entry)

    return {
        "vocabulary": vocabulary,
        "own_exports": own_exports,
        "dependency_exports": dependency_exports,
        "expected_public_api": expected_public_api,
        "shared_kernel": shared_kernel,
        "recipes": recipes,
    }


def build_agent_context(
    project_root: Path,
    module_id: str,
    architecture: dict[str, Any],
    task: dict[str, Any] | None = None,
    role: str = "implementation",
) -> dict[str, Any]:
    """Assemble the bounded context a sub-agent is allowed to see.

    The contract is deliberately narrow: stable project overview, project standards,
    project recipes, a user-profile excerpt, the module's own contract and unresolved
    attachments, plus the agent's own past errors and work log. Chat history and
    unrelated modules are never injected.
    """
    slug = _safe_module_id(module_id)
    modules = architecture.get("modules", []) if isinstance(architecture, dict) else []
    module = next(
        (item for item in modules if isinstance(item, dict) and item.get("id") == slug),
        {},
    )
    attachments = read_node_attachments(project_root).get(slug, [])
    unresolved = [
        attachment
        for attachment in attachments
        if isinstance(attachment, dict) and not attachment.get("resolved")
    ]
    read_scope: set[str] = set()
    write_scope: set[str] = set()
    if module.get("path"):
        path = str(module["path"])
        read_scope.add(path)
        write_scope.add(path)
    for raw_path in (task or {}).get("target_files", []):
        if raw_path:
            path = str(raw_path)
            read_scope.add(path)
            write_scope.add(path)
    read_scope.add("AI_ARCH.md")
    # Reading is deliberately wider than writing.
    #
    # Write stays at "my module only" — writing through someone else's module
    # would break ownership boundaries and is what the sandbox is for. But
    # *reading* is how an agent reuses what already exists instead of
    # reinventing it, so the read scope also covers:
    #   - the module's parent directory, so the agent can see its siblings
    #     exist (a plain `list_dir("src")` used to be rejected outright);
    #   - the architecture document, the structure it is working inside;
    #   - the contract registry, which is where the reusable global surface
    #     lives — public API, shared kernel, vocabulary, data schemas. The
    #     `search_reuse` tool reads this same file, but an agent also needs to
    #     be able to open it when a search term is not enough.
    for raw_path in [str(module.get("path") or ""), *((task or {}).get("target_files") or [])]:
        if not raw_path:
            continue
        parent = str(PurePosixPath(str(raw_path).replace("\\", "/")).parent)
        if parent and parent not in (".", "/"):
            read_scope.add(parent)
    read_scope.add(".docuagent/architecture.json")
    read_scope.add(".docuagent/contracts.json")
    # The directory document tree is part of the bounded context. Agents may read
    # only existing navigation documents on the path to their own files. Fresh
    # directories inherit their nearest documented ancestors until the independent
    # Doc Maintainer pass writes the new directory document.
    document_paths: list[str] = []
    document_contents: list[dict[str, str]] = []
    root_document = project_root / "AI_ARCH.md"
    if root_document.is_file():
        document_paths.append("AI_ARCH.md")
        document_contents.append({
            "path": "AI_ARCH.md",
            "content": _read_optional_text(root_document, STANDARDS_MAX_CHARS),
        })
    scope_roots: list[Path] = []
    if module.get("path"):
        scope_roots.append(project_root / str(module["path"]))
    for raw_path in (task or {}).get("target_files", []):
        if raw_path:
            scope_roots.append(project_root / str(raw_path))
    for candidate in scope_roots:
        directory = candidate if candidate.is_dir() else candidate.parent
        if candidate.suffix and not candidate.is_dir():
            directory = candidate.parent
        while directory != project_root and directory.is_relative_to(project_root):
            relative = directory.relative_to(project_root).as_posix()
            doc_path = f"{relative}/AI_ARCH.md"
            doc_file = directory / "AI_ARCH.md"
            if doc_file.is_file() and doc_path not in document_paths:
                document_paths.append(doc_path)
                read_scope.add(doc_path)
                if doc_file.is_file():
                    document_contents.append({
                        "path": doc_path,
                        "content": _read_optional_text(doc_file, STANDARDS_MAX_CHARS),
                    })
            directory = directory.parent
    document_paths.sort()
    document_contents.sort(key=lambda item: item["path"])
    # Adjacent interfaces: an agent may read the paths of modules it depends on, but
    # those paths stay out of the write scope. Reading is how a caller learns an
    # interface; writing through a dependency would break ownership boundaries.
    for dependency_id in (module.get("depends_on") or []):
        dependency = next(
            (item for item in modules if item.get("id") == dependency_id),
            {},
        )
        if dependency.get("path"):
            read_scope.add(str(dependency["path"]))

    # Stable-prefix order matters: dict insertion order becomes JSON key order
    # (json.dumps is called without sort_keys), and OpenAI-compatible providers
    # key their prompt cache on the longest common prefix. Global stable blocks
    # (user profile / standards / recipes / project overview) must come before the
    # first module-specific block (module_contract). Do not insert task-level or
    # module-level volatile fields above this block without updating
    # ARCHITECTURE_CONTRACT_SPEC.md section 3 and the ordering tests.
    navigation_documents = {
        "required_paths": document_paths,
        "loaded": document_contents,
        "read_paths": [],
        "enforced": True,
        "rule": (
            "AI_ARCH.md is the authoritative directory navigation layer. Read the "
            "root-to-target chain before reading or editing implementation files."
        ),
    }
    context = {
        "user_profile": _read_optional_text(
            Path.home() / ".docuagent" / "user-profile.md", USER_PROFILE_MAX_CHARS
        ),
        "standards": _read_optional_text(
            managed_path(project_root, "standards.md"), STANDARDS_MAX_CHARS
        ) or DEFAULT_STANDARDS_MD,
        "recipes": _read_optional_text(
            managed_path(project_root, "recipes.md"), RECIPES_MAX_CHARS
        ),
        "project_overview": _read_optional_text(
            managed_path(project_root, "project.md"), PROJECT_OVERVIEW_MAX_CHARS
        ),
        "contract_view": _contract_view(project_root, module, task),
        "module_contract": {
            "id": module.get("id", slug),
            "name": module.get("name", slug),
            "path": module.get("path", ""),
            "responsibility": module.get("responsibility", ""),
            "brief": module.get("brief", ""),
            "needs_ui": bool(module.get("needs_ui")),
            "depends_on": module.get("depends_on", []),
        },
        # Volatile module-specific block: MUST stay after module_contract (see the
        # stable-prefix comment above) — it changes whenever a contract flips stale.
        "upstream_changes": _upstream_changes(project_root, slug),
        "directory_documents": navigation_documents,
        "navigation_documents": navigation_documents,
        "unresolved_attachments": unresolved[-10:],
        "error_memory": read_error_memory(project_root, slug)[-5:],
        "work_log_tail": read_work_log(project_root, slug)[-WORK_LOG_TAIL_CHARS:],
        "session_tail": _read_optional_text(
            managed_path(project_root, AGENTS_DIR, slug, "session.md"),
            SESSION_TAIL_CHARS,
        ),
        "allowed_read_scope": sorted(read_scope),
        "allowed_write_scope": sorted(write_scope),
        "capabilities": sorted(_enabled_subagent_capabilities()),
        "role": role,
        "ui_context": (task or {}).get("ui_context"),
        "skills": skills.matching_skills(
            project_root,
            f"{module.get('responsibility', '')} {module.get('brief', '')} {task.get('summary', '')}",
        ),
        "plugin_prompts": plugins.list_plugin_prompts(project_root, "subagent"),
        "write_gate": "File writes are allowed only through write_file/edit_file inside "
        "the trial sandbox. They become patch proposals and are never applied to the "
        "real project until after user/conversation-flow review.",
    }
    context_cache.record_context(project_root, context, module_id=slug)
    return context

def read_locks(project_root: Path) -> dict[str, Any]:
    raw = read_json(_lock_path(project_root))
    return raw if isinstance(raw, dict) else {"schema_version": 1, "held": {}}


def write_locks(project_root: Path, state: dict[str, Any]) -> None:
    atomic_write_json(_lock_path(project_root), state)


def clear_locks(project_root: Path) -> None:
    write_locks(project_root, {"schema_version": 1, "held": {}})


def _lock_keys(paths: list[str]) -> set[str]:
    keys: set[str] = set()
    for raw in paths:
        path = str(raw or "").replace("\\", "/").strip("/")
        if not path:
            continue
        keys.add(path)
    return keys


def acquire_workspace_locks(
    project_root: Path,
    assignments: list[tuple[str, list[str]]],
) -> list[str]:
    """Grant non-overlapping workspace ownership to tasks, in assignment order."""
    state = read_locks(project_root)
    held_raw = state.get("held", {})
    held = {
        task_id: set(keys)
        for task_id, keys in held_raw.items()
        if isinstance(task_id, str) and isinstance(keys, list)
    }
    granted: list[str] = []
    for task_id, paths in assignments:
        keys = _lock_keys(paths)
        if not keys:
            granted.append(task_id)
            continue
        if any(keys & other for other in held.values()):
            continue
        held[task_id] = keys
        granted.append(task_id)
    state["held"] = {
        task_id: sorted(keys)
        for task_id, keys in held.items()
    }
    write_locks(project_root, state)
    return granted


def release_workspace_locks(
    project_root: Path,
    task_ids: list[str],
) -> None:
    state = read_locks(project_root)
    held = state.get("held", {})
    if not isinstance(held, dict):
        return
    for task_id in task_ids:
        held.pop(task_id, None)
    state["held"] = held
    write_locks(project_root, state)
