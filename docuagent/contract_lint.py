"""Deterministic contract lint for patch review and verification.

`check_patch` inspects proposed file contents before they are applied;
`check_working_files` re-checks files that already landed in the project. Both
return Chinese, file-located messages so a violation can be shown next to the
code diff and the author can act on it without reading the registry.

Scope: Python first, using the stdlib `ast` module. JS/TS and other languages
are recorded as `skipped` for now, never as violations.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

from workspace import read_contracts, utc_now


def _norm_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip("/")


def _contract_modules(contracts: dict[str, Any]) -> list[dict[str, Any]]:
    modules = contracts.get("modules", [])
    return [m for m in modules if isinstance(m, dict) and m.get("id")] if isinstance(modules, list) else []


def _module_for_file(contracts: dict[str, Any], file_path: str) -> dict[str, Any] | None:
    """Find the narrowest contract module path owning `file_path`."""
    file_path = _norm_path(file_path)
    best: dict[str, Any] | None = None
    best_len = -1
    for module in _contract_modules(contracts):
        path = _norm_path(module.get("path"))
        if not path:
            continue
        if file_path == path or file_path.startswith(path + "/"):
            if len(path) > best_len:
                best = module
                best_len = len(path)
    return best


def _module_for_import(contracts: dict[str, Any], import_module: str) -> dict[str, Any] | None:
    """Resolve an import's first path segment to a contract module."""
    first = (import_module or "").split(".")[0].strip()
    if not first:
        return None
    for module in _contract_modules(contracts):
        if module.get("id") == first:
            return module
    for module in _contract_modules(contracts):
        path = _norm_path(module.get("path"))
        if path and path.split("/")[-1] == first:
            return module
    return None


def _export_symbols(module: dict[str, Any]) -> set[str]:
    exports = module.get("exports")
    if not isinstance(exports, list):
        return set()
    return {
        str(entry.get("symbol")).strip()
        for entry in exports
        if isinstance(entry, dict) and str(entry.get("symbol") or "").strip()
    }


def _top_level_definitions(tree: ast.AST) -> list[dict[str, Any]]:
    definitions: list[dict[str, Any]] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            definitions.append({"name": node.name, "kind": "function", "lineno": node.lineno})
        elif isinstance(node, ast.ClassDef):
            definitions.append({"name": node.name, "kind": "class", "lineno": node.lineno})
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    definitions.append({"name": target.id, "kind": "constant", "lineno": node.lineno})
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            definitions.append({"name": node.target.id, "kind": "constant", "lineno": node.lineno})
    return definitions


def _public_names(tree: ast.AST, definitions: list[dict[str, Any]]) -> set[str]:
    """Public names are top-level non-private definitions; `__all__` narrows them."""
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    value = node.value
                    if isinstance(value, (ast.Tuple, ast.List)):
                        names = {
                            str(elt.value).strip()
                            for elt in value.elts
                            if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                        }
                        return names
    return {d["name"] for d in definitions if not d["name"].startswith("_")}


def _python_imports(tree: ast.AST) -> list[dict[str, Any]]:
    imports: list[dict[str, Any]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append({
                    "type": "import",
                    "module": alias.name or "",
                    "names": [],
                    "lineno": node.lineno,
                    "level": 0,
                })
        elif isinstance(node, ast.ImportFrom):
            imports.append({
                "type": "from",
                "module": node.module or "",
                "names": [str(alias.name or "") for alias in node.names],
                "lineno": node.lineno,
                "level": node.level,
            })
    return imports


def _registered_command_names(tree: ast.AST) -> set[str]:
    """Extract names from command-registration calls/decorators only.

    Searching raw source made comments, docstrings, and ordinary user-facing
    strings look like duplicate registrations. AST inspection keeps the check
    tied to executable registration syntax and deliberately ignores prose.
    """
    names: set[str] = set()
    registration_terms = {
        "command", "register", "register_command", "add_command", "register_subcommand",
        "add_parser",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        terminal = function.id if isinstance(function, ast.Name) else (
            function.attr if isinstance(function, ast.Attribute) else ""
        )
        terminal = str(terminal or "").strip().lower()
        if terminal not in registration_terms and not terminal.endswith("_command"):
            continue
        candidate: ast.AST | None = None
        for keyword in node.keywords:
            if keyword.arg == "name":
                candidate = keyword.value
                break
        if candidate is None and node.args:
            candidate = node.args[0]
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            value = candidate.value.strip()
            if value:
                names.add(value)
    return names


def _check_python_file(
    contracts: dict[str, Any],
    file_path: str,
    content: str,
) -> dict[str, Any]:
    violations: list[str] = []
    contract_delta: list[dict[str, Any]] = []
    skipped: list[str] = []
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError as exc:
        skipped.append(f"{file_path}: Python 语法无法解析（{exc.msg}），本轮跳过。")
        return {"violations": violations, "contract_delta": contract_delta, "skipped": skipped}

    module = _module_for_file(contracts, file_path)
    own_id = str(module.get("id") or "") if module else ""
    depends_on = set(module.get("depends_on") or []) if module else set()

    definitions = _top_level_definitions(tree)
    public_names = _public_names(tree, definitions)

    # 1. Dependency boundary, symbol boundary, and private protection.
    for imp in _python_imports(tree):
        import_module = imp["module"]
        if not import_module:
            continue
        if imp["level"] and imp["level"] > 0:
            skipped.append(f"{file_path}:{imp['lineno']}: 相对导入 `{import_module}` 暂不参与契约检查。")
            continue
        target = _module_for_import(contracts, import_module)
        if target is None:
            continue
        target_id = str(target.get("id") or "")
        if target_id == own_id:
            continue
        if target_id not in depends_on:
            violations.append(
                f"{file_path}:{imp['lineno']}: 模块 `{own_id or '?'}` 不能 import `{import_module}`，"
                f"因为 `{target_id}` 不在其 depends_on 中。"
            )
            continue
        # Allowed dependency: enforce symbol boundary and private protection.
        exports = _export_symbols(target)
        for name in imp["names"]:
            if name == "*":
                skipped.append(f"{file_path}:{imp['lineno']}: `from {import_module} import *` 不参与符号级检查。")
                continue
            if name.startswith("_"):
                violations.append(
                    f"{file_path}:{imp['lineno']}: 不能 import 依赖模块 `{target_id}` 的私有符号 `{name}`。"
                )
                continue
            if exports and name not in exports:
                violations.append(
                    f"{file_path}:{imp['lineno']}: `{name}` 不在依赖模块 `{target_id}` 的 exports 中。"
                )

    # 2. Duplicate definition.
    vocabulary = contracts.get("vocabulary", [])
    if not isinstance(vocabulary, list):
        vocabulary = []
    for definition in definitions:
        name = definition["name"]
        for contract_module in _contract_modules(contracts):
            contract_id = str(contract_module.get("id") or "")
            if contract_id != own_id and name in _export_symbols(contract_module):
                violations.append(
                    f"{file_path}:{definition['lineno']}: 不能定义 `{name}`，"
                    f"它已经是模块 `{contract_id}` 的 export。"
                )
        for entry in vocabulary:
            if not isinstance(entry, dict):
                continue
            if name in {str(item).strip() for item in (entry.get("forbidden_aliases") or []) if str(item).strip()}:
                violations.append(
                    f"{file_path}:{definition['lineno']}: 不能定义 `{name}`，"
                    f"它是 vocabulary 中规范名 `{entry.get('term')}` 的禁止别名。"
                )
            elif name == str(entry.get("term") or "").strip() and str(entry.get("owner") or "").strip() != own_id:
                violations.append(
                    f"{file_path}:{definition['lineno']}: 不能定义 `{name}`，"
                    f"该规范名由模块 `{entry.get('owner')}` 拥有。"
                )

    # 3. Shared-kernel admission: new public symbols under a shared path need an
    #    owner and at least two consumers in the registry.
    shared_kernel = contracts.get("shared_kernel", [])
    if not isinstance(shared_kernel, list):
        shared_kernel = []
    if "/shared/" in _norm_path(file_path) or _norm_path(file_path).startswith("shared/"):
        for definition in definitions:
            if definition["name"].startswith("_"):
                continue
            entry = next(
                (item for item in shared_kernel if isinstance(item, dict) and item.get("symbol") == definition["name"]),
                None,
            )
            if entry is None:
                violations.append(
                    f"{file_path}:{definition['lineno']}: 新共享符号 `{definition['name']}` "
                    "必须先在 contracts.json 的 shared_kernel 中声明 owner 和至少两个消费者。"
                )
            elif len(entry.get("consumers") or []) < 2 or not str(entry.get("owner") or "").strip():
                violations.append(
                    f"{file_path}:{definition['lineno']}: 共享符号 `{definition['name']}` "
                    "必须声明 owner 和至少两个消费者。"
                )

    # 4. Command conflict: inspect actual registration calls, not raw text.
    commands = contracts.get("commands", [])
    if isinstance(commands, list):
        registered_names = _registered_command_names(tree)
        for command in commands:
            if not isinstance(command, dict):
                continue
            command_name = str(command.get("name") or "").strip()
            owner = str(command.get("owner") or "").strip()
            if command_name and own_id and owner and owner != own_id and command_name in registered_names:
                violations.append(
                    f"{file_path}: 模块 `{own_id}` 出现了已注册命令 `{command_name}`，"
                    f"该命令由模块 `{owner}` 拥有，不能重复注册。"
                )

    # 5. Contract sync: any new public symbol not declared in this module's exports
    #    becomes a contract_delta item for human review (not a hard violation yet).
    if module is not None and own_id:
        own_exports = _export_symbols(module)
        for name in sorted(public_names):
            if name not in own_exports:
                contract_delta.append({
                    "module_id": own_id,
                    "symbol": name,
                    "kind": next(
                        (d["kind"] for d in definitions if d["name"] == name), "function"
                    ),
                    "file": file_path,
                    "reason": "patch 中出现新的公共符号，但 contracts.json 未声明该 export。",
                })

    return {"violations": violations, "contract_delta": contract_delta, "skipped": skipped}


def check_patch(project_root: Path, patch_entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Lint proposed patch entries before they are applied.

    Returns `{"violations": [...], "contract_delta": [...], "skipped": [...]}`.
    """
    contracts = read_contracts(project_root) or {}
    violations: list[str] = []
    contract_delta: list[dict[str, Any]] = []
    skipped: list[str] = []
    for entry in patch_entries:
        if not isinstance(entry, dict):
            continue
        path = _norm_path(entry.get("path"))
        content = str(entry.get("after") or "")
        if path.endswith(".py"):
            result = _check_python_file(contracts, path, content)
            violations.extend(result["violations"])
            contract_delta.extend(result["contract_delta"])
            skipped.extend(result["skipped"])
        else:
            skipped.append(f"{path}: 非 Python 文件暂不参与契约 lint。")
    return {
        "violations": violations,
        "contract_delta": contract_delta,
        "skipped": skipped,
    }


def check_working_files(project_root: Path, paths: list[str]) -> dict[str, Any]:
    """Lint files that already exist in the working tree (verification stage)."""
    contracts = read_contracts(project_root) or {}
    violations: list[str] = []
    contract_delta: list[dict[str, Any]] = []
    skipped: list[str] = []
    for raw_path in paths:
        path = _norm_path(raw_path)
        if not path:
            continue
        if not path.endswith(".py"):
            skipped.append(f"{path}: 非 Python 文件暂不参与契约 lint。")
            continue
        target = project_root / path
        if not target.is_file():
            skipped.append(f"{path}: 文件不存在，跳过契约 lint。")
            continue
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            skipped.append(f"{path}: 文件无法读取（{exc}），跳过契约 lint。")
            continue
        result = _check_python_file(contracts, path, content)
        violations.extend(result["violations"])
        contract_delta.extend(result["contract_delta"])
        skipped.extend(result["skipped"])
    return {
        "violations": violations,
        "contract_delta": contract_delta,
        "skipped": skipped,
    }


def contracts_with_delta(
    project_root: Path,
    contract_delta: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Return a new contracts.json payload with the reviewed delta applied.

    This is a pure merge: no file is written here. Callers can put the returned
    payload in the same atomic apply operation as the code patch, so code and
    contract changes land together or not at all.
    """
    if not contract_delta:
        return None
    contracts = read_contracts(project_root)
    if not isinstance(contracts, dict):
        return None
    updated = copy.deepcopy(contracts)
    modules = updated.setdefault("modules", [])
    if not isinstance(modules, list):
        modules = []
        updated["modules"] = modules
    for delta in contract_delta:
        if not isinstance(delta, dict):
            continue
        module_id = str(delta.get("module_id") or "").strip()
        symbol = str(delta.get("symbol") or "").strip()
        kind = str(delta.get("kind") or "function").strip()
        if not module_id or not symbol:
            continue
        module = next((item for item in modules if isinstance(item, dict) and item.get("id") == module_id), None)
        if module is None:
            module = {
                "id": module_id,
                "path": "",
                "depends_on": [],
                "exports": [],
                "consumes": [],
            }
            modules.append(module)
        exports = module.setdefault("exports", [])
        if not isinstance(exports, list):
            exports = []
            module["exports"] = exports
        if not any(
            isinstance(entry, dict) and entry.get("symbol") == symbol
            for entry in exports
        ):
            exports.append({
                "symbol": symbol,
                "kind": kind,
                "signature": str(delta.get("signature") or ""),
                "since": str(delta.get("since") or "0.1.0"),
            })
    updated["updated_at"] = utc_now()
    return updated


def format_violations(result: dict[str, Any]) -> str:
    """Human-readable Chinese summary for review panels and error messages."""
    lines: list[str] = []
    if result.get("violations"):
        lines.append("契约违规：")
        lines.extend(f"- {item}" for item in result["violations"])
    if result.get("contract_delta"):
        lines.append("契约变更待审阅（contract_delta）：")
        lines.extend(
            f"- {item.get('module_id')}:{item.get('symbol')} ({item.get('file')})"
            for item in result["contract_delta"]
        )
    if result.get("skipped"):
        lines.append("跳过：")
        lines.extend(f"- {item}" for item in result["skipped"])
    return "\n".join(lines)
