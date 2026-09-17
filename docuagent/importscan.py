"""Read-only project scan + file signature collection (existing-project onboarding, M1).

This is the "skim all files" layer of the three-tier onboarding read: it walks the
project, records a bounded *signature* for every source file (purpose, top-level
symbols, imports, TODO/FIXME markers), reads manifests in full (bounded), detects
entry points, and reports the dominant language/stack. No model call happens here —
the onboarding Agent consumes the resulting snapshot.

Runtime stays dependency-free, matching the rest of the backend. All reads are
bounded; binary and oversized files are recorded by metadata only, never decoded.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from core import CONTRACTS_SCHEMA_VERSION, WorkspaceError
from workspace import atomic_write_json, managed_path, read_json, utc_now, write_contracts

SCAN_VERSION = 1
SCAN_FILE = "import-scan.json"

IGNORED_DIRS = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".docuagent",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "env",
    ".env",
    "dist",
    "build",
    ".next",
    ".nuxt",
    ".output",
    "target",
    ".idea",
    ".vscode",
    ".DS_Store",
    "coverage",
    "htmlcov",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".eggs",
    "vendor",
    "bower_components",
})

# Directories that are almost always vendored/binary noise; only skip when nested.
IGNORED_BASENAMES = frozenset({
    ".gitignore",
    ".gitattributes",
    ".npmignore",
})

# Files whose full (bounded) content the onboarding Agent needs to see.
MANIFEST_NAMES = frozenset({
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "requirements-dev.txt",
    "setup.py",
    "setup.cfg",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "Cargo.toml",
    "go.mod",
    "go.sum",
    "Gemfile",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "composer.json",
    "tsconfig.json",
    "vite.config.js",
    "vite.config.ts",
    "webpack.config.js",
    "rollup.config.js",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".gitignore",
    ".dockerignore",
})

CODE_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    ".go", ".rs", ".java", ".kt", ".kts", ".rb", ".cs", ".php",
    ".vue", ".svelte", ".swift", ".scala", ".sh", ".bash", ".zsh",
    ".c", ".h", ".cpp", ".hpp", ".cc", ".sql", ".proto", ".graphql",
})

DOC_EXTENSIONS = frozenset({".md", ".markdown", ".rst", ".txt", ".adoc", ".tex"})

DATA_EXTENSIONS = frozenset({
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".csv",
    ".lock", ".svg", ".css", ".scss", ".less", ".html", ".htm",
})

# Cap how much of any single file we decode and how many symbols we keep.
MAX_FILE_BYTES = 512 * 1024
MAX_SYMBOLS = 40
MAX_IMPORTS = 30
MAX_FILES = 4000
MAX_PROMPT_CHARS = 60_000

_ENTRY_BASENAMES = frozenset({
    "main", "app", "index", "server", "cli", "run", "manage", "wsgi", "asgi",
    "bootstrap", "start",
})


def is_binary(data: bytes) -> bool:
    """Heuristic: a NUL byte in the first chunk is enough to call a file binary."""
    sample = data[:4096]
    return b"\x00" in sample


def read_bounded_text(path: Path) -> str | None:
    """Decode a file as UTF-8 (bounded); return None for binary/undecodable files."""
    try:
        data = path.read_bytes()[:MAX_FILE_BYTES]
    except OSError:
        return None
    if is_binary(data):
        return None
    for encoding in ("utf-8", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


# --- Symbol / purpose extraction -------------------------------------------------

_PY_DEF_RE = re.compile(r"^\s*(async\s+def|def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")
_PY_IMPORT_RE = re.compile(r"^\s*(from\s+\S+|import\s+\S+)")
# `__all__ = ["a", "b"]` is the Python file's declared export surface: when it
# is present only those names are exports — the author said so explicitly.
_PY_ALL_RE = re.compile(r"__all__\s*=\s*\[(.*?)\]", re.DOTALL)


def _python_symbols(text: str) -> tuple[list[str], list[str]]:
    """Python public names: an explicit `__all__` if present, else top-level defs.

    Import lines are consumption, never symbols.
    """
    symbols: list[str] = []
    imports: list[str] = []
    for line in text.splitlines():
        match = _PY_IMPORT_RE.match(line)
        if match:
            if match.group(1) not in imports:
                imports.append(match.group(1))
            continue
        match = _PY_DEF_RE.match(line)
        if match and not match.group(2).startswith("_"):
            entry = f"{match.group(1)} {match.group(2)}"
            if entry not in symbols:
                symbols.append(entry)
        if len(symbols) >= MAX_SYMBOLS and len(imports) >= MAX_IMPORTS:
            break
    all_match = _PY_ALL_RE.search(text)
    if all_match:
        kinds = {}
        for entry in symbols:
            parts = entry.split()
            if parts:
                kinds[parts[-1]] = entry
        listed = re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]*)", all_match.group(1))
        symbols = [
            kinds[name] if name in kinds else name
            for name in listed
            if not name.startswith("_")
        ]
    return symbols[:MAX_SYMBOLS], imports[:MAX_IMPORTS]


_JS_DEF_RE = re.compile(
    r"^\s*(export\s+)?(default\s+)?(async\s+)?(function|class)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_CONST_RE = re.compile(
    r"^\s*(export\s+(const|let|var)|const|let|var)\s+([A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_IMPORT_RE = re.compile(r"^\s*(import\s+.+|const\s+\S+\s*=\s*require\(.+|require\(.+)")
# `const x = require(...)` assigns an IMPORT to x: consumption, not a symbol.
_JS_REQUIRE_ASSIGN_RE = re.compile(r"^\s*const\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*=\s*require\(")
# CommonJS named-assignment export: `exports.foo = ...` / `module.exports.foo = ...`.
_JS_EXPORTS_DOT_RE = re.compile(r"^\s*(?:module\.)?exports\.([A-Za-z_$][A-Za-z0-9_$]*)\s*=")
# Single-expression export: `module.exports = runCli` (no object literal).
_JS_EXPORTS_ASSIGN_RE = re.compile(
    r"module\.exports\s*=\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*[;]?\s*(?://.*)?$", re.MULTILINE
)
# ESM export list: `export { a, b as c }`.
_JS_EXPORT_LIST_RE = re.compile(r"export\s*\{([^}]*)\}")
# Entry head inside a CJS object literal: a key (`foo: ...`), a shorthand member
# (`foo,`) or a method (`foo() { ... }`).
_JS_MEMBER_HEAD_RE = re.compile(r"^\s*([A-Za-z_$][A-Za-z0-9_$]*)\s*(?:\(|:|,|$)")


def _js_export_block_names(text: str) -> list[str]:
    """Names listed in a `module.exports = { ... }` object literal.

    Lexical and deliberately shallow (no parser dependency): from the first
    `module.exports = {` it brace-counts to the matching close and reads each
    entry's head. Nested bodies may contribute stray names, so results are
    best-effort — this feeds a review draft, not a compiler.
    """
    opener = re.search(r"module\.exports\s*=\s*\{", text)
    if not opener:
        return []
    names: list[str] = []
    depth = 0
    start = opener.end()
    end = -1
    for index in range(start, min(len(text), start + 8000)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth <= 0:
                end = index
                break
    if end < 0:
        return []
    for line in text[start:end].splitlines():
        stripped = line.strip().lstrip("{")
        if not stripped or stripped.startswith(("}", "//", "/*", "*")):
            continue
        if "(" in stripped or ")" in stripped:
            # Methods / arrows: one head per line; their internal commas must
            # not spawn phantom members.
            match = _JS_MEMBER_HEAD_RE.match(stripped)
            if match and match.group(1) not in names:
                names.append(match.group(1))
            continue
        # Shorthand / keyed entries can share a line (`{ a, b: c }`).
        for segment in stripped.split(","):
            match = _JS_MEMBER_HEAD_RE.match(segment.strip())
            if match and match.group(1) not in names:
                names.append(match.group(1))
    return names


def _js_symbols(text: str) -> tuple[list[str], list[str]]:
    """JS/TS public names: the file's EXPLICIT export surface.

    A file offers what it puts on `module.exports` / `exports.x` / `export`.
    Guessing every top-level name (the old behaviour) registered `const fs =
    require('fs')` as "module exports fs" — consumption read as production.
    Files with no export statement at all are treated as offering nothing.
    """
    imports: list[str] = []
    for line in text.splitlines():
        if _JS_IMPORT_RE.match(line) and line.strip() not in imports:
            imports.append(line.strip())

    kinds: dict[str, str] = {}
    for line in text.splitlines():
        match = _JS_DEF_RE.match(line)
        if match and match.group(5):
            kinds[match.group(5)] = "class" if match.group(4) == "class" else "function"
        else:
            match = _JS_CONST_RE.match(line)
            if match and not _JS_REQUIRE_ASSIGN_RE.match(line):
                kinds[match.group(3)] = "constant"

    export_names: list[str] = []
    for line in text.splitlines():
        match = _JS_EXPORTS_DOT_RE.match(line)
        if match and match.group(1) not in export_names:
            export_names.append(match.group(1))
    for match in _JS_EXPORT_LIST_RE.finditer(text):
        for raw in match.group(1).split(","):
            raw = raw.strip()
            if not raw:
                continue
            alias = re.match(
                r"[A-Za-z_$][A-Za-z0-9_$]*\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)", raw
            )
            name = alias.group(1) if alias else raw.split()[0]
            if name and name not in export_names and not name.startswith("_"):
                export_names.append(name)
    for name in _js_export_block_names(text):
        if name not in export_names:
            export_names.append(name)
    assign_match = _JS_EXPORTS_ASSIGN_RE.search(text)
    if assign_match:
        name = assign_match.group(1)
        if name not in export_names:
            export_names.append(name)

    symbols = [
        f"{kinds[name]} {name}" if name in kinds else name
        for name in export_names
        if not name.startswith("_")
    ][:MAX_SYMBOLS]
    if symbols:
        return symbols, imports[:MAX_IMPORTS]

    # Fallback (no explicit export statement): top-level public definitions
    # only — require assignments are consumption and stay out.
    for line in text.splitlines():
        if _JS_REQUIRE_ASSIGN_RE.match(line):
            continue
        match = _JS_DEF_RE.match(line)
        if match and match.group(5) and not match.group(5).startswith("_"):
            kind = "class" if match.group(4) == "class" else "function"
            entry = f"{kind} {match.group(5)}"
        else:
            match = _JS_CONST_RE.match(line)
            if not match or match.group(3).startswith("_"):
                continue
            entry = f"const {match.group(3)}"  # type: ignore[union-attr]
        if entry and entry not in symbols:
            symbols.append(entry)
        if len(symbols) >= MAX_SYMBOLS:
            break
    return symbols[:MAX_SYMBOLS], imports[:MAX_IMPORTS]


def _symbols_for(path: Path, text: str) -> tuple[list[str], list[str]]:
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _python_symbols(text)
    if suffix in {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte"}:
        return _js_symbols(text)
    return [], []


_PURPOSE_STRIP_RE = re.compile(r"^[\s#/*\"'<>-]+")


def _purpose(text: str) -> str:
    """First meaningful docstring/comment line, used as the file's one-line purpose."""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Skip shebang and boilerplate markers that carry no meaning.
        if stripped.startswith(("#!/", "<?", "<!DOCTYPE", "package ", "use ", "//go:")):
            continue
        stripped = _PURPOSE_STRIP_RE.sub("", stripped)
        if len(stripped) >= 2:
            return stripped[:200]
    return ""


_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b", re.IGNORECASE)


def _file_kind(path: Path) -> str:
    if path.name in MANIFEST_NAMES or path.name.lower().startswith(("dockerfile", "readme")):
        return "manifest"
    suffix = path.suffix.lower()
    if suffix in CODE_EXTENSIONS:
        return "code"
    if suffix in DOC_EXTENSIONS:
        return "doc"
    if suffix in DATA_EXTENSIONS:
        return "data"
    return "other"


def _language_hint(ext: str) -> str | None:
    mapping = {
        ".py": "python",
        ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
        ".ts": "typescript", ".tsx": "typescript",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".kts": "kotlin",
        ".rb": "ruby", ".cs": "csharp", ".php": "php", ".vue": "vue", ".svelte": "svelte",
        ".swift": "swift", ".scala": "scala", ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp",
        ".cc": "cpp", ".sql": "sql", ".sh": "shell", ".bash": "shell",
    }
    return mapping.get(ext)


# --- Manifest facts --------------------------------------------------------------

def _manifest_facts(name: str, text: str) -> list[str]:
    """Best-effort, dependency-free extraction of key facts from a manifest."""
    facts: list[str] = []
    if name == "package.json":
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            return facts
        if isinstance(data, dict):
            if data.get("name"):
                facts.append(f"name={data['name']}")
            if isinstance(data.get("scripts"), dict) and data["scripts"]:
                facts.append("scripts=" + ", ".join(sorted(data["scripts"].keys())[:10]))
            deps = data.get("dependencies") or {}
            if isinstance(deps, dict) and deps:
                facts.append("dependencies=" + ", ".join(sorted(deps.keys())[:20]))
    elif name in {"requirements.txt", "requirements-dev.txt"}:
        lines = [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]
        if lines:
            facts.append("packages=" + ", ".join(lines[:20]))
    return facts


def _entry_candidate(rel: str, name: str) -> bool:
    stem = Path(name).stem.lower()
    if stem in _ENTRY_BASENAMES:
        return True
    parts = Path(rel).parts
    # A main/app/index directly under src/ or a single top-level package dir counts.
    if stem in _ENTRY_BASENAMES and parts and parts[0] in {"src", "app", "lib", "cmd"}:
        return True
    return False


# --- Scan ------------------------------------------------------------------------

def _iter_business_files(project_root: Path):
    """Yield business files (Path) in deterministic scan order.

    Single source of traversal truth: both the full scan and the cheap staleness
    probe consume this generator, so the fingerprint recorded at scan time can
    never drift from what a later freshness check sees. Applies the same ignore
    rules and MAX_FILES cap as the historical in-place walk.
    """
    files_seen = 0

    def walk(directory: Path):
        nonlocal files_seen
        try:
            entries = sorted(directory.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for entry in entries:
            if entry.name in IGNORED_DIRS and entry.is_dir():
                continue
            if entry.is_dir():
                if entry.name in IGNORED_BASENAMES:
                    continue
                yield from walk(entry)
                continue
            if files_seen >= MAX_FILES:
                return
            files_seen += 1
            yield entry

    yield from walk(project_root)


def tree_fingerprint(project_root: Path) -> str:
    """Cheap staleness digest over the business file tree (stat only, no reads).

    Every recorded file contributes (rel path, size, mtime) in scan order; any
    add/remove/edit of a business file changes the digest. Costs one stat walk —
    far cheaper than a full content rescan, cheap enough for bootstrap restarts.
    """
    digest = hashlib.sha256()
    for path in _iter_business_files(project_root):
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(project_root).as_posix()
        digest.update(f"{rel}\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode("utf-8"))
    return digest.hexdigest()


def scan_project(project_root: Path) -> dict[str, Any]:
    """Walk the project read-only and build the signature snapshot."""
    if not project_root.is_dir():
        raise WorkspaceError("项目目录不存在。")

    files: list[dict[str, Any]] = []
    directories: dict[str, int] = {}
    by_ext: dict[str, int] = {}
    totals = {"files": 0, "code": 0, "manifest": 0, "data": 0, "doc": 0, "other": 0, "lines": 0}
    entry_points: list[str] = []
    manifests: list[dict[str, Any]] = []

    for path in _iter_business_files(project_root):
        try:
            stat = path.stat()
        except OSError:
            continue
        rel = path.relative_to(project_root).as_posix()
        suffix = path.suffix.lower()
        kind = _file_kind(path)
        totals["files"] += 1
        totals[kind] += 1
        by_ext[suffix] = by_ext.get(suffix, 0) + 1
        for parent in path.relative_to(project_root).parents:
            directories[parent.as_posix()] = directories.get(parent.as_posix(), 0) + 1

        record: dict[str, Any] = {
            "path": rel,
            "ext": suffix,
            "size": stat.st_size,
            "kind": kind,
        }
        if _entry_candidate(rel, path.name):
            entry_points.append(rel)

        if kind in {"code", "manifest", "doc"}:
            text = read_bounded_text(path)
            if text is not None:
                lines = text.count("\n") + 1
                record["lines"] = lines
                totals["lines"] += lines
                record["purpose"] = _purpose(text)
                if kind == "code":
                    symbols, imports = _symbols_for(path, text)
                    if symbols:
                        record["symbols"] = symbols
                    if imports:
                        record["imports"] = imports
                    todos = len(_TODO_RE.findall(text))
                    if todos:
                        record["todos"] = todos
                if kind == "manifest":
                    facts = _manifest_facts(path.name, text)
                    manifests.append({
                        "path": rel,
                        "name": path.name,
                        "facts": facts,
                    })
        files.append(record)

    # Dominant language from extension counts.
    lang_counts: dict[str, int] = {}
    for ext, count in by_ext.items():
        hint = _language_hint(ext)
        if hint:
            lang_counts[hint] = lang_counts.get(hint, 0) + count
    language = max(lang_counts, key=lang_counts.get) if lang_counts else "unknown"

    return {
        "schema_version": SCAN_VERSION,
        "scanned_at": utc_now(),
        "root": str(project_root),
        "fingerprint": tree_fingerprint(project_root),
        "totals": totals,
        "language": language,
        "by_ext": dict(sorted(by_ext.items(), key=lambda kv: -kv[1])),
        "directories": sorted(directories.keys()),
        "entry_points": sorted(set(entry_points)),
        "manifests": manifests,
        "files": files,
    }


def write_scan(project_root: Path, scan: dict[str, Any]) -> None:
    atomic_write_json(managed_path(project_root, SCAN_FILE), scan)


def read_scan(project_root: Path) -> dict[str, Any] | None:
    raw = read_json(managed_path(project_root, SCAN_FILE))
    return raw if isinstance(raw, dict) else None


def ensure_scan(project_root: Path, *, refresh: bool = False) -> dict[str, Any]:
    """Return the project signature scan, rescanning when the tree has changed.

    The snapshot is reused while the recorded `fingerprint` still matches the
    current file tree; any add/remove/edit of a business file (or a legacy
    snapshot without a fingerprint) triggers a rescan. `refresh=True` forces a
    rescan regardless — callers that need the freshest state after their own
    writes use it instead of reading a possibly-stale cached snapshot.
    """
    existing = read_scan(project_root)
    if not refresh and existing:
        stored = str(existing.get("fingerprint") or "")
        if stored and stored == tree_fingerprint(project_root):
            return existing
    scan = scan_project(project_root)
    write_scan(project_root, scan)
    return scan


def _is_test_path(rel: str) -> bool:
    lowered = str(rel).lower().replace("\\", "/")
    parts = set(lowered.split("/"))
    if parts & {"test", "tests", "__tests__", "spec"}:
        return True
    return ".test." in lowered or ".spec." in lowered


def architect_summary(scan: dict[str, Any] | None, max_code_files: int = 80) -> str:
    """Concise, token-bounded file snapshot for the architecture model.

    The architecture agent must see what actually exists before designing;
    otherwise it fabricates verification commands and module files with no real
    counterpart (a real-model pilot produced `node --test test/parser.test.js`
    for a project that had no test directory at all). An empty project must be
    stated explicitly — that is as much a fact the model needs as a populated one.
    """
    if not scan:
        return "项目文件快照：尚未完成扫描。"
    totals = scan.get("totals") or {}
    total_files = int(totals.get("files") or 0)
    language = str(scan.get("language") or "unknown")
    lines = int(totals.get("lines") or 0)
    doc = int(totals.get("doc") or 0)
    manifest_count = int(totals.get("manifest") or 0)
    other = int(totals.get("other") or 0)

    prod_files: list[dict[str, Any]] = []
    test_files: list[dict[str, Any]] = []
    entry_points = set(scan.get("entry_points") or [])
    for record in scan.get("files", []):
        if record.get("kind") != "code":
            continue
        rel = str(record.get("path") or "")
        (test_files if _is_test_path(rel) else prod_files).append(record)

    def _entry_line(record: dict[str, Any]) -> str:
        rel = str(record.get("path") or "")
        if record.get("lines"):
            size = f"{int(record['lines'])} 行"
        else:
            size = f"{int(record.get('size') or 0)} B"
        marker = "（入口）" if rel in entry_points else ""
        return f"- {rel}{marker}  {size}"

    out = [f"【项目文件快照】共 {total_files} 个文件、约 {lines} 行代码，主导语言：{language}。"]
    if total_files == 0:
        out.append("该项目当前没有任何文件——所有模块文件都等待子 Agent 生成。")
        return "\n".join(out)
    out.append(
        f"分类：源码 {len(prod_files)} 个、测试 {len(test_files)} 个、"
        f"文档 {doc} 个、清单 {manifest_count} 个、其他 {other} 个。"
    )
    if prod_files:
        shown = prod_files[:max_code_files]
        out.append(f"现有源码文件（{len(prod_files)} 个，展示前 {len(shown)} 个）：")
        out.extend(_entry_line(record) for record in shown)
        if len(prod_files) > len(shown):
            out.append("……（其余省略）")
    if test_files:
        out.append(f"测试文件（{len(test_files)} 个）：")
        out.extend(f"- {record.get('path')}" for record in test_files[:20])
    for manifest in scan.get("manifests") or []:
        facts = manifest.get("facts")
        if not isinstance(facts, dict) or not facts:
            continue  # plain READMEs count as docs, not manifests worth listing
        path = str(manifest.get("path") or manifest.get("name") or "")
        snippet = "、".join(
            f"{key}={json.dumps(value, ensure_ascii=False)[:60]}"
            for key, value in list(facts.items())[:5]
        )
        out.append(f"清单：{path}（{snippet}）")
    return "\n".join(out)


# --- Contract Registry draft ------------------------------------------------------

_EXPORT_MAX_PER_MODULE = 12


def _symbol_kind(symbol: str) -> str:
    """Map an importscan symbol entry like `class User` to a contract kind."""
    lowered = symbol.lower()
    if lowered.startswith("class "):
        return "class"
    if lowered.startswith("def "):
        return "function"
    if lowered.startswith("async def "):
        return "function"
    if lowered.startswith(("const ", "let ", "var ", "export const ")):
        return "constant"
    if lowered.startswith(("function ", "export function ")):
        return "function"
    if lowered.startswith(("interface ", "type ")):
        return "type"
    return "other"


def _symbol_name(symbol: str) -> str:
    """`def create_invoice(a,b)` -> `create_invoice`; `class User:` -> `User`."""
    text = symbol.strip()
    # Cut at the first '(' or ':' that follows a name; keep identifiers like _private.
    for index, char in enumerate(text):
        if char in "(:=":
            text = text[:index]
            break
    parts = text.split()
    return parts[-1] if parts else text


def draft_contracts_from_scan(
    project_root: Path,
    architecture: dict[str, Any],
    project: dict[str, Any],
    scan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate a contracts.json draft from architecture + importscan.

    The architecture supplies module ids, paths and dependency edges. The scan
    supplies public symbol candidates; private names are skipped by default, and
    exports are capped per module so the draft stays reviewable.
    """
    scan = scan or read_scan(project_root) or {}
    scan_files = scan.get("files", [])
    if not isinstance(scan_files, list):
        scan_files = []
    language = str(scan.get("language") or project.get("language") or "").strip()

    modules = []
    for module in architecture.get("modules", []):
        if not isinstance(module, dict) or not module.get("id"):
            continue
        module_path = str(module.get("path") or "").strip().replace("\\", "/").strip("/")
        exports: list[dict[str, Any]] = []
        for file_info in scan_files:
            if not isinstance(file_info, dict):
                continue
            file_path = str(file_info.get("path") or "").replace("\\", "/").strip("/")
            if module_path and not (
                file_path == module_path or file_path.startswith(module_path + "/")
            ):
                continue
            symbols = file_info.get("symbols")
            if not isinstance(symbols, list):
                continue
            for symbol in symbols:
                text = str(symbol).strip()
                name = _symbol_name(text)
                if not name or name.startswith("_"):
                    continue
                exports.append({
                    "symbol": name,
                    "kind": _symbol_kind(text),
                    "signature": "",
                    "since": "0.1.0",
                })
                if len(exports) >= _EXPORT_MAX_PER_MODULE:
                    break
            if len(exports) >= _EXPORT_MAX_PER_MODULE:
                break
        modules.append({
            "id": module["id"],
            "path": str(module.get("path") or ""),
            "depends_on": [
                dep for dep in module.get("depends_on", [])
                if isinstance(dep, str) and dep
            ],
            "exports": exports,
            "consumes": [],
        })

    return {
        "schema_version": CONTRACTS_SCHEMA_VERSION,
        "updated_at": utc_now(),
        "project": {
            "name": project.get("name") or "",
            "language": language or "unknown",
            "runtime": str(project.get("runtime") or ""),
        },
        "vocabulary": [],
        "shared_kernel": [],
        "commands": [],
        "modules": modules,
        "recipes": [],
    }


def write_contracts_draft(
    project_root: Path,
    architecture: dict[str, Any],
    project: dict[str, Any],
    scan: dict[str, Any] | None = None,
) -> None:
    """Write the initial contracts.json draft for an existing project.

    Called at finalize time, when the file tree is the source of truth for what
    the project actually provides — so a stale cached snapshot is not acceptable
    here. `ensure_scan` refreshes it when the tree changed since the last scan.
    """
    write_contracts(
        project_root,
        draft_contracts_from_scan(
            project_root,
            architecture,
            project,
            scan or ensure_scan(project_root),
        ),
    )


# --- Prompt assembly --------------------------------------------------------------

def scan_prompt_text(scan: dict[str, Any]) -> str:
    """Compact, bounded text the onboarding Agent can consume in one turn."""
    totals = scan.get("totals", {})
    lines: list[str] = [
        f"Detected language: {scan.get('language', 'unknown')}",
        (
            f"Totals: {totals.get('files', 0)} files "
            f"({totals.get('code', 0)} code, {totals.get('manifest', 0)} manifest, "
            f"{totals.get('data', 0)} data, {totals.get('doc', 0)} doc, "
            f"{totals.get('other', 0)} other, ~{totals.get('lines', 0)} lines)"
        ),
        f"Entry points (candidates): {', '.join(scan.get('entry_points', [])[:30]) or '(none)'}",
    ]

    manifests = scan.get("manifests", [])
    if manifests:
        lines.append("Manifests:")
        for item in manifests:
            facts = item.get("facts") or []
            lines.append(f"- {item['path']}" + (f" :: {', '.join(facts)}" if facts else ""))

    # Group file signatures by directory.
    by_dir: dict[str, list[str]] = {}
    for entry in scan.get("files", []):
        directory = Path(entry["path"]).parent.as_posix()
        bits = [entry["path"]]
        detail: list[str] = []
        if entry.get("purpose"):
            detail.append(entry["purpose"])
        if entry.get("symbols"):
            detail.append("defs: " + ", ".join(entry["symbols"]))
        if entry.get("imports"):
            detail.append("imports: " + ", ".join(entry["imports"]))
        if entry.get("todos"):
            detail.append(f"todos: {entry['todos']}")
        if detail:
            bits.append(" // ".join(detail))
        by_dir.setdefault(directory, []).append("  - " + " | ".join(bits))

    for directory in sorted(by_dir.keys()):
        lines.append(f"\n[{directory or 'root'}]")
        lines.extend(by_dir[directory])

    text = "\n".join(lines)
    if len(text) > MAX_PROMPT_CHARS:
        text = text[:MAX_PROMPT_CHARS].rstrip() + (
            "\n\n(scan truncated at character budget; the remainder of the file "
            "inventory is available on request via the scan endpoint)"
        )
    return text


ENTRY_MAX_FILES = 8
ENTRY_MAX_CHARS = 12_000


def entry_files_text(project_root: Path, scan: dict[str, Any]) -> str:
    """Full (bounded) content of entry points — the "read important files" tier.

    Entry points carry most of a project's wiring (server boot, CLI, app assembly),
    so their full text is worth the budget even when every other file is only skimmed.
    """
    entry_points = [str(p) for p in scan.get("entry_points", [])][:ENTRY_MAX_FILES]
    blocks: list[str] = []
    budget = ENTRY_MAX_CHARS
    for relative in entry_points:
        path = project_root / relative
        text = read_bounded_text(path)
        if text is None:
            continue
        if len(text) > budget:
            text = text[:budget] + "\n# ... (entry file truncated)"
        blocks.append(f"### {relative}\n{text}")
        budget -= len(text)
        if budget <= 0:
            break
    if not blocks:
        return "(no entry point content captured)"
    return "\n\n".join(blocks)


def scan_summary(scan: dict[str, Any]) -> dict[str, Any]:
    """The reduced shape returned to the frontend (no per-file payload)."""
    totals = scan.get("totals", {})
    return {
        "schema_version": scan.get("schema_version"),
        "scanned_at": scan.get("scanned_at"),
        "root": scan.get("root"),
        "language": scan.get("language"),
        "totals": totals,
        "by_ext": scan.get("by_ext", {}),
        "directories_count": len(scan.get("directories", [])),
        "entry_points": scan.get("entry_points", [])[:40],
        "manifests": [
            {"path": item["path"], "facts": item.get("facts", [])}
            for item in scan.get("manifests", [])
        ],
    }
