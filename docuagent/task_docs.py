"""Doc synchronisation: the pipeline step that runs after every task is done.

Split out of tasks.py, which ran the whole pipeline (plan, generate, patch, verify,
docs) from one 2365-line module. This segment was taken first because it is the most
loosely coupled: it calls into the shared task state and the patch guards, and nothing
in the pipeline calls back into it.

Model calls go through task_runtime so patching the seam still reaches this code now
that it no longer lives in tasks.py.
"""

from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any

from agents import append_work_log
from architecture_preflight import require_documented_command_consistency
from core import WorkspaceError
from llm_client import ProviderConfig
from standards import DOC_INTEGRITY_RULES
from workspace import (
    apply_text_files_atomic,
    architecture_document,
    atomic_write_json,
    managed_path,
    read_json,
    read_contracts,
    utc_now,
)
import task_runtime as _task_runtime
import token_usage

from task_state import (
    MAX_DOCUMENTATION_AUTO_RETRIES,
    TASK_DONE_STATUSES,
    _assert_safe_target,
    architecture_fingerprint,
    document_version,
    ensure_documentation_state,
    normalize_generated_files,
    update_delivery_status,
    read_task_state,
    record_documentation_failure,
    write_task_state,
)


def call_model_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Resolve the model call at call time through the shared seam.

    Binding the function object at import time would freeze it before the suite gets
    to patch anything: the test would stay green while issuing a real network call,
    which is what happened when this module was first extracted. task_runtime is the
    seam every stage shares, and it imports nothing from the pipeline, so depending on
    it keeps this module free of a cycle back into tasks.
    """
    return _task_runtime.call_model_json(*args, **kwargs)


DOC_SYNC_PROMPT = """You are the Doc Maintainer. Write exactly one AI_ARCH.md for the
directory named by target.path. This is a semantic navigation document for future coding
agents, not a generated file listing. Use the supplied disk facts as authoritative, the
architecture and contracts for intent and public interfaces, and child_documents for
already-updated child context. Describe the directory's responsibility, every direct
file and subdirectory, important public symbols or commands, dependency constraints,
and useful verification commands. Mention every name in target.entries exactly in
backticks. Do not include placeholders, writing instructions, or claims unsupported by
the supplied facts. For the root document, link every top-level directory to its
AI_ARCH.md and include the exact requires_python value when present. Return JSON only:
{"files":[{"path":"<target.path>","content":"..."}]}
""" + "\n\n" + DOC_INTEGRITY_RULES


# These folders are generated state, dependency trees, or historical copies. They do
# not belong to the project's navigable source-document tree.
_DOCUMENT_TREE_IGNORED_DIRS = {
    ".docuagent",
    ".git",
    "__pycache__",
    ".pytest_cache",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "coverage",
    "archive",
    ".cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    ".idea",
    ".vscode",
    ".backups",
    ".recovery",
    "tmp",
    "temp",
    "out",
    "target",
}

# Project-level opt-out file (same managed-file pattern as apply-mode.json):
# {"schema_version": 1, "ignored_dirs": ["vendor", "generated"], "updated_at": ...}
# Names are single directory levels — no separators, no dot-dot — so a project can
# keep dependency/caches/temp trees out of the navigation-document tree without
# those trees ever growing AI_ARCH.md files.
DOCUMENT_IGNORE_FILE = "doc-ignore.json"


def _valid_ignore_name(name: str) -> bool:
    return bool(name) and "/" not in name and "\\" not in name and name not in {".", ".."}


def read_document_ignored_dirs(project_root: Path) -> set[str]:
    """Built-in ignore list merged with the project's own opt-outs.

    Invalid entries in the project file are skipped on read (a hand-edited file
    must not break the whole documentation tree), while the writer validates.
    """
    ignored = set(_DOCUMENT_TREE_IGNORED_DIRS)
    raw = read_json(managed_path(project_root, DOCUMENT_IGNORE_FILE))
    entries = (raw or {}).get("ignored_dirs")
    if not isinstance(entries, list):
        return ignored
    for entry in entries:
        name = str(entry).strip()
        if _valid_ignore_name(name):
            ignored.add(name)
    return ignored


def write_document_ignored_dirs(project_root: Path, names: list[str]) -> list[str]:
    """Persist the project-level ignore list; returns the cleaned entries."""
    stripped = [str(name).strip() for name in names]
    invalid = [name for name in stripped if name and not _valid_ignore_name(name)]
    if invalid:
        raise WorkspaceError(
            f"忽略目录必须是单级目录名，不能包含路径分隔符：{', '.join(invalid)}"
        )
    cleaned = sorted({name for name in stripped if name})
    atomic_write_json(
        managed_path(project_root, DOCUMENT_IGNORE_FILE),
        {
            "schema_version": 1,
            "ignored_dirs": cleaned,
            "updated_at": utc_now(),
        },
    )
    return cleaned


def _affected_child_directories(
    project_root: Path,
    tasks: list[dict[str, Any]],
) -> list[Path]:
    root = project_root.resolve()
    ignored = read_document_ignored_dirs(project_root)
    directories: dict[str, Path] = {}
    for task in tasks:
        for raw_path in task.get("target_files", []):
            try:
                target = _assert_safe_target(root, raw_path)
            except WorkspaceError:
                continue
            parent = target.parent
            while parent != root and parent.exists():
                relative_parts = parent.relative_to(root).parts
                if any(part in ignored for part in relative_parts):
                    break
                directories[str(parent)] = parent
                parent = parent.parent
    return sorted(directories.values(), key=lambda path: str(path))


def _iter_document_directories(project_root: Path) -> list[Path]:
    """Return every navigable project directory that must have an AI_ARCH.md.

    Empty directories are included. The document records that the directory is
    currently empty, so an AI never has to guess whether a folder was intentionally
    left undocumented.
    """
    root = project_root.resolve()
    if not root.is_dir():
        return []
    ignored = read_document_ignored_dirs(project_root)
    discovered: list[Path] = []
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            children = sorted(directory.iterdir(), key=lambda path: path.name.lower())
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.is_symlink():
                continue
            relative_parts = child.relative_to(root).parts
            if any(part in ignored for part in relative_parts):
                continue
            pending.append(child)
            discovered.append(child)
    return sorted(discovered, key=lambda path: str(path))


def maintain_child_docs(
    project_root: Path,
    tasks: list[dict[str, Any]],
    *,
    all_directories: bool = False,
) -> list[str]:
    """Compatibility gate: report missing docs without generating prose.

    Formal AI_ARCH.md content is owned by the Doc Maintainer model. Callers that need
    an update must use the model-backed document-tree synchronizer.
    """
    directories = (
        _iter_document_directories(project_root)
        if all_directories
        else _affected_child_directories(project_root, tasks)
    )
    missing = [
        (directory / "AI_ARCH.md").relative_to(project_root).as_posix()
        for directory in directories
        if not (directory / "AI_ARCH.md").is_file()
    ]
    if missing:
        raise WorkspaceError("目录文档尚未由文档 Agent 生成：" + ", ".join(missing))
    return []


def document_tree_gaps(project_root: Path) -> list[str]:
    """List navigable directories whose mandatory child document is missing."""
    missing: list[str] = []
    for directory in _iter_document_directories(project_root):
        if not (directory / "AI_ARCH.md").is_file():
            missing.append(
                str(directory.relative_to(project_root)).replace("\\", "/")
                + "/AI_ARCH.md"
            )
    return missing


def maintain_document_tree(
    project_root: Path,
    tasks: list[dict[str, Any]] | None = None,
    *,
    require_root: bool = True,
) -> list[str]:
    """Validate the mandatory directory-document tree without writing prose."""
    del tasks
    missing = document_tree_gaps(project_root)
    if missing:
        raise WorkspaceError(
            "目录文档维护未完成，缺少：" + ", ".join(missing)
        )
    root_doc = project_root / "AI_ARCH.md"
    if require_root and not root_doc.is_file():
        raise WorkspaceError("目录文档维护未完成，缺少根目录 AI_ARCH.md。")
    return []




def sync_architecture_from_tasks(
    project_root: Path,
    architecture: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> list[str]:
    """Write task-refined facts back into the architecture documents.

    Task generation can refine the files and verification commands for a module. This
    closes the loop architecture -> work items -> architecture so the next architecture
    edit or work-item derivation starts from what was actually implemented.
    """
    modules = architecture.get("modules", []) if isinstance(architecture, dict) else []
    by_id = {
        str(module.get("id")): module
        for module in modules
        if isinstance(module, dict) and module.get("id")
    }
    updated: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        if task.get("status") not in {"applied", "verified"}:
            continue
        module = by_id.get(str(task.get("module_id") or ""))
        if not module:
            continue
        target_files = [
            str(item).strip().replace("\\", "/").strip("/")
            for item in (task.get("target_files") or [])
            if str(item).strip()
        ]
        verification = [
            str(item).strip()
            for item in (task.get("verification") or [])
            if str(item).strip()
        ]
        if target_files and module.get("target_files") != target_files:
            module["target_files"] = target_files
            updated.append(module["id"])
        if verification and module.get("verification") != verification:
            module["verification"] = verification
            if module["id"] not in updated:
                updated.append(module["id"])

    if not updated:
        return []

    document = architecture_document(project_root)
    if document and document.get("modules"):
        document_modules = {
            str(module.get("id")): module
            for module in document.get("modules", [])
            if isinstance(module, dict) and module.get("id")
        }
        for module_id in updated:
            source = by_id.get(module_id)
            target = document_modules.get(module_id)
            if not source or not target:
                continue
            target["target_files"] = source.get("target_files", target.get("target_files", []))
            target["verification"] = source.get("verification", target.get("verification", []))
        atomic_write_json(
            managed_path(project_root, "architecture.json"),
            document,
        )

    state = read_json(managed_path(project_root, "bootstrap.json"))
    if state and state.get("architecture", {}).get("modules"):
        state_modules = {
            str(module.get("id")): module
            for module in state["architecture"].get("modules", [])
            if isinstance(module, dict) and module.get("id")
        }
        for module_id in updated:
            source = by_id.get(module_id)
            target = state_modules.get(module_id)
            if not source or not target:
                continue
            target["target_files"] = source.get("target_files", target.get("target_files", []))
            target["verification"] = source.get("verification", target.get("verification", []))
        atomic_write_json(
            managed_path(project_root, "bootstrap.json"),
            state,
        )

    return updated


def _workspace_document_facts(project_root: Path) -> dict[str, Any]:
    """Read disk facts without shipping arbitrary config/secrets to the model."""
    root = project_root.resolve()
    directories = _iter_document_directories(root)
    facts: dict[str, Any] = {
        "top_level_directories": [path.name for path in directories if path.parent == root],
        "directories": [path.relative_to(root).as_posix() for path in directories],
        "files": [], "python_imports": {},
    }
    for directory in [root, *directories]:
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.is_symlink() or path.name.startswith("."):
                continue
            relative = path.relative_to(root).as_posix()
            facts["files"].append(relative)
            if path.suffix == ".py":
                try:
                    tree = ast.parse(path.read_text(encoding="utf-8"))
                    imports = [ast.unparse(node) for node in ast.walk(tree)
                               if isinstance(node, (ast.Import, ast.ImportFrom))]
                    facts["python_imports"][relative] = imports
                except (OSError, UnicodeError, SyntaxError):
                    continue
    config = root / "pyproject.toml"
    if config.is_file() and not config.is_symlink():
        try:
            metadata = tomllib.loads(config.read_text(encoding="utf-8"))
            facts["requires_python"] = metadata.get("project", {}).get("requires-python", "")
        except (OSError, ValueError):
            pass
    return facts


_DOCUMENT_PLACEHOLDER_MARKERS = (
    "待替换",
    "写作指南（阅读后删除）",
    "目录说明（占位",
    "由系统在目录创建时自动生成，是模板",
)
_DOCUMENT_RESULT_ATTEMPTS = 2


def _document_path(project_root: Path, directory: Path) -> str:
    if directory.resolve() == project_root.resolve():
        return "AI_ARCH.md"
    return f"{directory.relative_to(project_root).as_posix()}/AI_ARCH.md"


def _direct_document_entries(project_root: Path, directory: Path) -> list[dict[str, str]]:
    ignored = read_document_ignored_dirs(project_root)
    entries: list[dict[str, str]] = []
    try:
        children = sorted(directory.iterdir(), key=lambda path: path.name.lower())
    except OSError:
        return entries
    for child in children:
        if child.is_symlink() or child.name == "AI_ARCH.md" or child.name.startswith("."):
            continue
        if child.is_dir() and child.name in ignored:
            continue
        entries.append({"name": child.name, "kind": "directory" if child.is_dir() else "file"})
    return entries


def _imports_for_directory(project_root: Path, directory: Path) -> dict[str, list[str]]:
    imports: dict[str, list[str]] = {}
    for entry in _direct_document_entries(project_root, directory):
        if entry["kind"] != "file" or not entry["name"].endswith(".py"):
            continue
        path = directory / entry["name"]
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, SyntaxError):
            continue
        imports[path.relative_to(project_root).as_posix()] = [
            ast.unparse(node)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
        ]
    return imports


def _contract_modules_for_directory(
    project_root: Path,
    directory: Path,
    contracts: dict[str, Any],
) -> list[dict[str, Any]]:
    relative = "" if directory.resolve() == project_root.resolve() else directory.relative_to(project_root).as_posix()
    matched: list[dict[str, Any]] = []
    for module in contracts.get("modules", []) if isinstance(contracts, dict) else []:
        if not isinstance(module, dict):
            continue
        raw_path = str(module.get("path") or "").replace("\\", "/").strip("/")
        owner = raw_path if not Path(raw_path).suffix else str(Path(raw_path).parent).replace("\\", "/")
        owner = "" if owner == "." else owner
        if owner == relative:
            matched.append(module)
    return matched


def _affected_document_directories(
    project_root: Path,
    changed_files: list[str],
    affected_modules: list[str],
    architecture: dict[str, Any],
    *,
    all_directories: bool,
) -> list[Path]:
    root = project_root.resolve()
    all_dirs = _iter_document_directories(root)
    if all_directories:
        return sorted(all_dirs, key=lambda path: (-len(path.relative_to(root).parts), path.as_posix()))

    selected: set[Path] = set()
    candidates = list(changed_files)
    affected = {str(item) for item in affected_modules}
    modules = [
        module for module in architecture.get("modules", [])
        if isinstance(module, dict)
    ] if isinstance(architecture, dict) else []
    impacted = set(affected)
    for module in modules:
        module_id = str(module.get("id") or "")
        dependencies = {str(item) for item in module.get("depends_on", [])}
        if module_id in affected or dependencies & affected:
            impacted.add(module_id)
            impacted.update(dependencies & affected)
    for module in modules:
        if str(module.get("id") or "") in impacted:
            candidates.append(str(module.get("path") or ""))
    for raw in candidates:
        normalized = str(raw).replace("\\", "/").strip("/")
        if not normalized or normalized.startswith(".docuagent/"):
            continue
        candidate = (root / normalized).resolve()
        directory = candidate if candidate.is_dir() else candidate.parent
        while directory != root and directory.is_relative_to(root):
            if directory.is_dir():
                selected.add(directory)
            directory = directory.parent
    for directory in all_dirs:
        if not (directory / "AI_ARCH.md").is_file():
            selected.add(directory)
            parent = directory.parent
            while parent != root and parent.is_relative_to(root):
                selected.add(parent)
                parent = parent.parent
    return sorted(selected, key=lambda path: (-len(path.relative_to(root).parts), path.as_posix()))


def _validated_document_result(
    result: dict[str, Any],
    expected_path: str,
    entries: list[dict[str, str]],
    contract_modules: list[dict[str, Any]],
    *,
    top_level_directories: list[str],
    requires_python: str,
) -> str:
    files = normalize_generated_files(result, f"doc-sync:{expected_path}")
    if len(files) != 1 or files[0]["path"] != expected_path:
        returned = ", ".join(file["path"] for file in files) or "无"
        raise WorkspaceError(
            f"文档 Agent 必须只返回 `{expected_path}`，实际返回：{returned}"
        )
    content = str(files[0].get("content") or "").strip()
    if not content:
        raise WorkspaceError(f"文档 Agent 返回了空文档：{expected_path}")
    marker = next((item for item in _DOCUMENT_PLACEHOLDER_MARKERS if item in content), "")
    if marker:
        raise WorkspaceError(f"文档仍含模板占位内容 `{marker}`：{expected_path}")
    missing_entries = [
        entry["name"] for entry in entries if f"`{entry['name']}`" not in content
    ]
    if missing_entries:
        raise WorkspaceError(
            f"文档 `{expected_path}` 缺少直接条目：" + ", ".join(missing_entries)
        )
    for module in contract_modules:
        symbols = [
            str(export.get("symbol") or "").strip()
            for export in module.get("exports", [])
            if isinstance(export, dict) and str(export.get("symbol") or "").strip()
        ]
        missing_symbols = [symbol for symbol in symbols if symbol not in content]
        if missing_symbols:
            raise WorkspaceError(
                f"文档 `{expected_path}` 缺少契约公开符号：" + ", ".join(missing_symbols)
            )
    if expected_path == "AI_ARCH.md":
        missing_links = [
            name for name in top_level_directories if f"{name}/AI_ARCH.md" not in content
        ]
        if missing_links:
            raise WorkspaceError("根文档缺少实际目录导航：" + ", ".join(missing_links))
        if requires_python and requires_python not in content:
            raise WorkspaceError("根文档缺少实际 requires-python 约束：" + requires_python)
    return content + "\n"


def generate_document_tree(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    *,
    completed_tasks: list[dict[str, Any]] | None = None,
    changed_files: list[str] | None = None,
    affected_modules: list[str] | None = None,
    previous_sync_error: str = "",
    all_directories: bool = False,
) -> list[str]:
    """Ask the Doc Maintainer for one directory at a time, then commit as a batch."""
    root = project_root.resolve()
    facts = _workspace_document_facts(root)
    contracts = read_contracts(root) or {}
    directories = _affected_document_directories(
        root,
        changed_files or [],
        affected_modules or [],
        architecture,
        all_directories=all_directories,
    )
    generated: dict[str, str] = {}
    for directory in [*directories, root]:
        path = _document_path(root, directory)
        entries = _direct_document_entries(root, directory)
        child_documents: list[dict[str, str]] = []
        for entry in entries:
            if entry["kind"] != "directory":
                continue
            child_relative = (directory / entry["name"]).relative_to(root).as_posix()
            child_path = f"{child_relative}/AI_ARCH.md"
            content = generated.get(child_path)
            if content is None:
                child_file = root / child_path
                if child_file.is_file():
                    content = child_file.read_text(encoding="utf-8")
            if content is not None:
                child_documents.append({"path": child_path, "content": content})
        current_file = root / path
        current_document = current_file.read_text(encoding="utf-8") if current_file.is_file() else ""
        contract_modules = _contract_modules_for_directory(root, directory, contracts)
        payload = {
                "target": {
                    "path": path,
                    "directory": "." if directory == root else directory.relative_to(root).as_posix(),
                    "entries": entries,
                },
                "project": project,
                "architecture": architecture,
                "completed_tasks": completed_tasks or [],
                "current_document": current_document,
                "child_documents": child_documents,
                "python_imports": _imports_for_directory(root, directory),
                "contracts": {"modules": contract_modules},
                "workspace_facts": facts if directory == root else {},
                "previous_sync_error": previous_sync_error,
            }
        for attempt in range(_DOCUMENT_RESULT_ATTEMPTS):
            with token_usage.usage_scope(root, feature="documentation"):
                result = call_model_json(
                    provider,
                    DOC_SYNC_PROMPT,
                    payload,
                    timeout=240,
                )
            try:
                generated[path] = _validated_document_result(
                    result,
                    path,
                    entries,
                    contract_modules,
                    top_level_directories=facts["top_level_directories"],
                    requires_python=str(facts.get("requires_python") or ""),
                )
                break
            except WorkspaceError as exc:
                if attempt + 1 >= _DOCUMENT_RESULT_ATTEMPTS:
                    raise
                payload = {
                    **payload,
                    "previous_sync_error": str(exc),
                    "retry_instruction": (
                        f"The previous response for {path} failed validation. "
                        "Correct that exact error and return exactly one file."
                    ),
                }

    expected = {_document_path(root, path) for path in _iter_document_directories(root)} | {"AI_ARCH.md"}
    missing_after = sorted(expected - (set(generated) | {path for path in expected if (root / path).is_file()}))
    if missing_after:
        raise WorkspaceError("目录文档维护未完成，缺少：" + ", ".join(missing_after))
    return apply_text_files_atomic(root, generated)


def _sync_docs_once(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    documentation = state.get("documentation_task", {})
    completed_tasks = [
        {
            "id": task["id"],
            "summary": task["summary"],
            "status": task["status"],
            "verification": task["verification"],
        }
        for task in state["tasks"]
    ]
    updated = generate_document_tree(
        project_root,
        provider,
        architecture,
        project,
        completed_tasks=completed_tasks,
        changed_files=[str(item) for item in documentation.get("changed_files", [])],
        affected_modules=[str(item) for item in documentation.get("affected_modules", [])],
        previous_sync_error=str(documentation.get("last_error") or ""),
        all_directories=bool(document_tree_gaps(project_root)),
    )
    require_documented_command_consistency(project_root)
    state["architecture_synced"] = sync_architecture_from_tasks(
        project_root, architecture, state["tasks"]
    )
    return state, updated


def _finish_documentation_sync(
    project_root: Path,
    state: dict[str, Any],
    architecture: dict[str, Any],
    updated: list[str],
    retry_count: int,
) -> dict[str, Any]:
    ensure_documentation_state(state)
    documentation = state["documentation_task"]
    documentation.update(document_version(project_root))
    documentation["source_fingerprint"] = architecture_fingerprint(architecture)
    documentation["status"] = "synced"
    documentation["last_error"] = ""
    documentation["auto_retry_count"] = retry_count
    documentation["updated_at"] = utc_now()
    state["status"] = "done"
    state["docs_in_sync"] = True
    state["docs_sync_required"] = False
    state["docs_synced_at"] = utc_now()
    state["last_error"] = ""
    update_delivery_status(state)
    state["error_node_events"] = _documentation_resolve_events(project_root)
    _write_state_without_events(project_root, state)
    state["updated"] = list(dict.fromkeys(updated))
    for task in state["tasks"]:
        append_work_log(
            project_root,
            str(task.get("module_id") or task["id"]),
            "文档同步",
            task["id"],
            "同步文件：" + "、".join(state["updated"]),
        )
    return state


def _documentation_owner_node(state: dict[str, Any]) -> str:
    """Pick the graph node a documentation error attaches to."""
    documentation = state["documentation_task"]
    modules = [str(item) for item in documentation.get("affected_modules", []) if str(item).strip()]
    if modules:
        return modules[0]
    code_task_id = str(documentation.get("code_task_id") or "").strip()
    if code_task_id:
        return code_task_id
    return "project"


def _documentation_error_event(
    project_root: Path,
    state: dict[str, Any],
    error: str,
    retry_count: int,
) -> dict[str, Any]:
    """Create or refresh the documentation error node; return the event dict."""
    from error_nodes import DOCUMENTATION_ERROR_ACTIONS, create_or_refresh_error_node

    documentation = state["documentation_task"]
    node, created = create_or_refresh_error_node(
        project_root,
        owner_node_id=_documentation_owner_node(state),
        source="documentation",
        kind="doc-sync",
        severity="critical",
        title="文档同步失败，整体交付已阻塞",
        detail=str(error),
        retry_count=retry_count,
        max_retries=int(documentation.get("max_auto_retries", 0)),
        actions=DOCUMENTATION_ERROR_ACTIONS,
        code_task_id=str(documentation.get("code_task_id") or ""),
        changed_files=[str(path) for path in documentation.get("changed_files", [])],
    )
    return {
        "event": "error_node_created" if created else "error_node_refreshed",
        "node": node,
    }


def _documentation_resolve_events(project_root: Path) -> list[dict[str, Any]]:
    """Resolve documentation error nodes after a successful sync, if any."""
    from error_nodes import resolve_error_nodes

    return [
        {"event": "error_node_resolved", "node": node}
        for node in resolve_error_nodes(project_root, source="documentation", kind="doc-sync")
    ]


def _write_state_without_events(project_root: Path, state: dict[str, Any]) -> None:
    """Error node events ride on return values only; tasks.json stays clean."""
    events = state.pop("error_node_events", None)
    write_task_state(project_root, state)
    if events is not None:
        state["error_node_events"] = events


def sync_docs(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    cancel_check: Any = None,
) -> dict[str, Any]:
    """Run only the independent Doc Maintainer loop.

    A failed documentation attempt is retried twice automatically. No code
    generation, ordinary task retry, or Repair Agent is called from this loop.
    """
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    unfinished = [
        task["id"]
        for task in state["tasks"]
        if task["status"] not in TASK_DONE_STATUSES
    ]
    if unfinished:
        raise WorkspaceError(f"还有未完成的任务：{', '.join(unfinished)}")

    ensure_documentation_state(state)
    documentation = state["documentation_task"]
    documentation["status"] = "pending"
    documentation["last_error"] = ""
    documentation["auto_retry_count"] = 0
    state["docs_in_sync"] = False
    state["docs_sync_required"] = True
    update_delivery_status(state)
    write_task_state(project_root, state)

    for attempt in range(MAX_DOCUMENTATION_AUTO_RETRIES + 1):
        if cancel_check is not None and cancel_check():
            documentation["status"] = "pending"
            documentation["last_error"] = "已停止文档同步。"
            documentation["updated_at"] = utc_now()
            state["last_error"] = documentation["last_error"]
            write_task_state(project_root, state)
            update_delivery_status(state)
            return state
        documentation["status"] = "running" if attempt == 0 else "retrying"
        documentation["auto_retry_count"] = attempt
        documentation["updated_at"] = utc_now()
        write_task_state(project_root, state)
        try:
            state, updated = _sync_docs_once(
                project_root,
                provider,
                architecture,
                project,
            )
            return _finish_documentation_sync(
                project_root,
                state,
                architecture,
                updated,
                attempt,
            )
        except Exception as exc:
            error = str(exc)
            documentation = state["documentation_task"]
            record_documentation_failure(
                state,
                error,
                retry_count=min(attempt + 1, MAX_DOCUMENTATION_AUTO_RETRIES),
            )
            if attempt >= MAX_DOCUMENTATION_AUTO_RETRIES:
                documentation["status"] = "blocked"
                state["status"] = "blocked"
                state["last_error"] = error
                update_delivery_status(state)
                state["error_node_events"] = [
                    _documentation_error_event(
                        project_root,
                        state,
                        error,
                        retry_count=MAX_DOCUMENTATION_AUTO_RETRIES,
                    )
                ]
                _write_state_without_events(project_root, state)
                return state
            documentation["status"] = "retrying"
            write_task_state(project_root, state)

    raise WorkspaceError("文档维护循环意外结束。")


def retry_documentation(
    project_root: Path,
    provider: ProviderConfig,
    architecture: dict[str, Any],
    project: dict[str, Any],
    cancel_check: Any = None,
) -> dict[str, Any]:
    """Retry only the persisted documentation task."""
    state = read_task_state(project_root)
    if not state:
        raise WorkspaceError("还没有任务计划。")
    ensure_documentation_state(state)
    documentation = state["documentation_task"]
    if documentation["status"] not in {"blocked", "retrying", "pending"}:
        raise WorkspaceError("当前没有可重试的文档维护错误。")
    documentation["status"] = "pending"
    documentation["last_error"] = ""
    documentation["auto_retry_count"] = 0
    write_task_state(project_root, state)
    return sync_docs(
        project_root,
        provider,
        architecture,
        project,
        cancel_check=cancel_check,
    )


def ensure_nav_docs_in_tree(work_root: Path, task: dict[str, Any]) -> list[str]:
    """Compatibility no-op; missing navigation docs are handled by ancestor fallback."""
    del work_root, task
    return []
