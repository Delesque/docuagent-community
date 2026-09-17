"""Zero-dependency Python code-intelligence provider (stdlib ``ast`` only).

This gives DocuAgent real, install-free symbol browsing, goto-definition,
find-references and hover for Python files. It is the *default* provider when
no external language server (pylsp / pyright / ...) is installed, so code
intelligence works out of the box and stays true to the project's
zero-third-party-dependency trunk. Richer, multi-language features remain on
the optional LSP path (see ``lsp_bridge.py``).

Design:
- ``PythonFileIndex`` parses one file and records its symbols, name definitions
  (``Store`` contexts), name usages (``Load`` contexts) and imports.
- ``PythonProjectIndex`` walks a project (skipping venv/node_modules/cache
  dirs) and builds a global top-level symbol registry so goto can leap across
  modules. Indexes are built once and reused.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Optional

from .ports import Location, Symbol, SymbolRecord

# Directories we never index (virtualenvs, build caches, VCS, our own data).
_SKIP_DIRS = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".workbuddy", "dist", "build"}


class PythonFileIndex:
    """Index of a single Python source file."""

    def __init__(self, abspath: str, text: str) -> None:
        # Locations are exposed as file URIs, and POSIX refuses `as_uri()` on a
        # relative path, so the stored path is always absolute.
        self.abspath = str(Path(abspath).absolute())
        self.text = text
        self.lines = text.splitlines()
        self.symbols: list[Symbol] = []
        # name -> list[(line, col)] definition sites (0-based)
        self.defs: dict[str, list[tuple[int, int]]] = {}
        # name -> list[(line, col)] usage sites (0-based)
        self.usages: dict[str, list[tuple[int, int]]] = {}
        # name -> list[(line, col)] import-statement sites (0-based)
        self.import_aliases: dict[str, list[tuple[int, int]]] = {}
        # (line, col) -> signature string for hover
        self.hovers: dict[tuple[int, int], str] = {}
        # import alias -> {"module", "name", "line", "col"}
        self.imports: list[dict] = []
        # name -> {"is_public":..., "signature":..., "kind":...} (module-level metadata)
        self._meta: dict[str, dict] = {}

        try:
            self.tree = ast.parse(text)
        except SyntaxError:
            self.tree = None
        if self.tree is not None:
            self._collect()

    # ---- collection ------------------------------------------------------
    def _add_symbol(self, name: str, kind: str, line: int, col: int) -> None:
        self.symbols.append(Symbol(name=name, kind=kind, line=line, character=col, detail=""))

    @staticmethod
    def _src(node: ast.AST) -> str:
        try:
            return ast.unparse(node)
        except Exception:
            return "..."

    def _record_def(self, node, kw: str, module_level: bool, container: str = "") -> None:
        line, col = node.lineno - 1, node.col_offset
        name_col = col + len(kw)
        kind = "class" if kw.startswith("class") else "function"
        self._add_symbol(node.name, kind, line, name_col)
        self.defs.setdefault(node.name, []).append((line, name_col))
        if kw.startswith("class"):
            bases = ", ".join(self._src(b) for b in node.bases)
            sig = f"class {node.name}({bases})" if bases else f"class {node.name}"
        else:
            sig = self._src(node).split("\n", 1)[0]
        self.hovers[(line, name_col)] = sig
        self._meta[(node.name, line, name_col)] = {
            "is_public": module_level,
            "signature": sig,
            "kind": kind,
            "scope": container,
        }

    def _record_const(self, target, container: str = "") -> None:
        line, col = target.lineno - 1, target.col_offset
        self._add_symbol(target.id, "constant", line, col)
        self.defs.setdefault(target.id, []).append((line, col))
        sig = f"constant {target.id}"
        self.hovers[(line, col)] = sig
        self._meta[(target.id, line, col)] = {
            "is_public": True,
            "signature": sig,
            "kind": "constant",
            "scope": container,
        }

    def _walk(self, node, module_level: bool, container: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kw = "async def " if isinstance(child, ast.AsyncFunctionDef) else "def "
                self._record_def(child, kw, module_level, container)
                self._walk(child, False, child.name)
            elif isinstance(child, ast.ClassDef):
                self._record_def(child, "class ", module_level, container)
                self._walk(child, False, child.name)
            elif isinstance(child, ast.Assign) and module_level:
                for tgt in child.targets:
                    if isinstance(tgt, ast.Name):
                        self._record_const(tgt, container)
                self._walk(child, False, container)
            elif isinstance(child, ast.Name):
                if isinstance(child.ctx, ast.Store):
                    self.defs.setdefault(child.id, []).append((child.lineno - 1, child.col_offset))
                elif isinstance(child.ctx, ast.Load):
                    self.usages.setdefault(child.id, []).append((child.lineno - 1, child.col_offset))
            else:
                # Recurse into every other statement/expression (return, expr,
                # aug-assign, call arguments, ...) so usages nested inside them
                # are still captured for find_references.
                self._walk(child, False, container)

    def _collect(self) -> None:
        self._walk(self.tree, True)

    def records(self, file_rel: str, source_hash: str, last_seen: float) -> list[SymbolRecord]:
        """Export this file's symbols as ``SymbolRecord`` for the project index."""
        out: list[SymbolRecord] = []
        for sym in self.symbols:
            meta = self._meta.get((sym.name, sym.line, sym.character), {})
            out.append(
                SymbolRecord(
                    name=sym.name,
                    kind=meta.get("kind", sym.kind),
                    file=file_rel,
                    line=sym.line,
                    character=sym.character,
                    signature=meta.get("signature", ""),
                    is_public=meta.get("is_public", False),
                    scope=meta.get("scope", ""),
                    source_hash=source_hash,
                    last_seen=last_seen,
                    status="active",
                )
            )
        return out

    # ---- queries --------------------------------------------------------
    def _identifier_at(self, line: int, col: int) -> Optional[str]:
        if self.tree is None or line < 0 or line >= len(self.lines):
            return None
        # Prefer an exact Name node whose column span covers the cursor.
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name) and (node.lineno - 1) == line:
                if node.col_offset <= col < node.end_col_offset:
                    return node.id
        # Fallback: tokenize the line around the cursor manually.
        text = self.lines[line]
        n = len(text)
        if col >= n:
            col = max(0, n - 1)
        start = col
        while start > 0 and start - 1 < n and (text[start - 1].isalnum() or text[start - 1] == "_"):
            start -= 1
        end = col
        while end < n and (text[end].isalnum() or text[end] == "_"):
            end += 1
        word = text[start:end]
        return word if word.isidentifier() else None

    def goto_definition(self, line: int, col: int) -> list[Location]:
        name = self._identifier_at(line, col)
        if not name:
            return []
        uri = Path(self.abspath).as_uri()
        if name in self.defs:
            l, c = self.defs[name][0]
            return [Location(uri=uri, line=l, character=c, end_line=l, end_character=c)]
        for imp in self.imports:
            if imp["alias"] == name:
                return [Location(uri=uri, line=imp["line"], character=imp["col"], end_line=imp["line"], end_character=imp["col"])]
        return []

    def find_references(self, line: int, col: int) -> list[Location]:
        name = self._identifier_at(line, col)
        if not name:
            return []
        uri = Path(self.abspath).as_uri()
        out: list[Location] = []
        seen: set[tuple[int, int]] = set()
        for l, c in self.defs.get(name, []):
            if (l, c) not in seen:
                seen.add((l, c))
                out.append(Location(uri=uri, line=l, character=c, end_line=l, end_character=c))
        for l, c in self.import_aliases.get(name, []):
            if (l, c) not in seen:
                seen.add((l, c))
                out.append(Location(uri=uri, line=l, character=c, end_line=l, end_character=c))
        for l, c in self.usages.get(name, []):
            if (l, c) not in seen:
                seen.add((l, c))
                out.append(Location(uri=uri, line=l, character=c, end_line=l, end_character=c))
        return out

    def hover(self, line: int, col: int) -> Optional[str]:
        name = self._identifier_at(line, col)
        if not name:
            return None
        for (l, c), sig in self.hovers.items():
            if (l, c) == (line, col):
                return sig
        if name in self.defs:
            site = self.defs[name][0]
            return self.hovers.get(site, f"symbol: {name}")
        return None

    def document_symbols(self) -> list[Symbol]:
        return self.symbols


class PythonProjectIndex:
    """Project-wide index built from all Python files under a root."""

    def __init__(self, root: str) -> None:
        self.root = str(Path(root).resolve())
        self.files: dict[str, PythonFileIndex] = {}  # abspath -> index
        self.global_defs: dict[str, tuple[str, int, int]] = {}  # name -> (abspath, line, col)
        self._build()

    def _build(self) -> None:
        root_path = Path(self.root)
        if not root_path.is_dir():
            return
        for py in root_path.rglob("*.py"):
            if _SKIP_DIRS & set(py.parts):
                continue
            try:
                text = py.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            abspath = str(py.resolve())
            fi = PythonFileIndex(abspath, text)
            self.files[abspath] = fi
            for sym in fi.symbols:
                if sym.name not in self.global_defs:
                    self.global_defs[sym.name] = (abspath, sym.line, sym.character)

    def _file_index(self, file: str) -> Optional[PythonFileIndex]:
        key = str(Path(file).resolve())
        fi = self.files.get(key)
        if fi is not None:
            return fi
        # Tolerate a path relative to the project root.
        rel = str(Path(self.root) / file)
        fi = self.files.get(str(Path(rel).resolve()))
        if fi is not None:
            return fi
        # Last resort: suffix match.
        for abspath, f in self.files.items():
            if abspath.endswith(file) or file.endswith(abspath):
                return f
        return None

    def goto_definition(self, file: str, line: int, col: int) -> list[Location]:
        fi = self._file_index(file)
        if fi is None:
            return []
        name = fi._identifier_at(line, col)
        if not name:
            return []
        uri = Path(fi.abspath).as_uri()
        # Local definition in the same file wins.
        if name in fi.defs:
            l, c = fi.defs[name][0]
            return [Location(uri=uri, line=l, character=c, end_line=l, end_character=c)]
        # Top-level symbol defined elsewhere in the project (e.g. an imported
        # class) jumps straight to its real definition, not the import line.
        if name in self.global_defs:
            abspath, l, c = self.global_defs[name]
            return [Location(uri=Path(abspath).as_uri(), line=l, character=c, end_line=l, end_character=c)]
        # Import alias without a resolvable definition -> the import statement.
        for imp in fi.imports:
            if imp["alias"] == name:
                return [Location(uri=uri, line=imp["line"], character=imp["col"], end_line=imp["line"], end_character=imp["col"])]
        return []

    def find_references(self, file: str, line: int, col: int) -> list[Location]:
        fi = self._file_index(file)
        if fi is None:
            return []
        return fi.find_references(line, col)

    def hover(self, file: str, line: int, col: int) -> Optional[str]:
        fi = self._file_index(file)
        if fi is None:
            return None
        return fi.hover(line, col)

    def document_symbols(self, file: str) -> list[Symbol]:
        fi = self._file_index(file)
        if fi is None:
            return []
        return fi.document_symbols()
