"""Code intelligence service: the CodeIntelPort implementation.

It lazily spawns one LSP bridge per (project, language) when an external
language server is installed, and otherwise falls back to a built-in,
zero-dependency Python indexer (``pyindex``) so code intelligence works out of
the box for Python projects. Every operation degrades to an empty result when
nothing can serve it, so callers (and the UI) only need to check
``capabilities().available``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Optional

from .lsp_bridge import LspBridge, find_server_command, language_for_path
from .ports import (
    Capabilities,
    CodeIntelPort,
    Diagnostic,
    Location,
    ReconcileReport,
    Symbol,
    SymbolRecord,
)
from .symbol_index import SymbolIndex
from .generic_index import ALL_INDEXABLE_EXTENSIONS, EXT_PROFILE, GENERIC_EXTENSIONS
from . import tree_sitter_index


class CodeIntelService:
    """Manages LSP sessions and implements CodeIntelPort."""

    def __init__(self) -> None:
        self._sessions: dict[tuple[str, str], LspBridge] = {}
        self._symbol_indexes: dict[str, SymbolIndex] = {}
        self._ext_cache: dict[str, frozenset] = {}
        self._lock = threading.Lock()
        self._detected = self._detect_languages()

    # ---- detection -------------------------------------------------------
    def _detect_languages(self) -> list[str]:
        from .lsp_bridge import SERVER_CANDIDATES

        found: list[str] = []
        for language in SERVER_CANDIDATES:
            if find_server_command(language):
                found.append(language)
        return sorted(found)

    def _has_python(self, root: str) -> bool:
        rp = Path(root)
        if not rp.is_dir():
            return False
        skip = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".workbuddy", "dist", "build"}
        for child in rp.rglob("*.py"):
            if skip & set(child.parts):
                continue
            return True
        return False

    # ---- multi-language support (P4, zero-dependency) -------------------
    def _project_extensions(self, root: str) -> frozenset:
        """Extensions present in ``root`` that we can index (cached per root)."""
        key = str(Path(root).resolve())
        found: set[str] = set()
        rp = Path(root)
        if rp.is_dir():
            skip = {".venv", "venv", "env", "node_modules", "__pycache__", ".git", ".workbuddy", "dist", "build"}
            for child in rp.rglob("*"):
                if not child.is_file():
                    continue
                if skip & set(child.parts):
                    continue
                ext = child.suffix.lower().lstrip(".")
                if ext in ALL_INDEXABLE_EXTENSIONS:
                    found.add(ext)
        result = frozenset(found)
        self._ext_cache[key] = result
        return result

    def _has_indexable(self, root: str) -> bool:
        return bool(self._project_extensions(root))

    @staticmethod
    def _indexable(path: str) -> bool:
        return Path(path).suffix.lower().lstrip(".") in ALL_INDEXABLE_EXTENSIONS

    def _symbol_index(self, root: str) -> SymbolIndex:
        key = str(Path(root).resolve())
        with self._lock:
            idx = self._symbol_indexes.get(key)
            if idx is None:
                idx = SymbolIndex(key)
                self._symbol_indexes[key] = idx
            else:
                idx.refresh_if_changed()
            return idx

    def available(self, project_root: Optional[str] = None) -> bool:
        if self._detected:
            return True
        return bool(project_root) and self._has_indexable(project_root)

    def capabilities(self, project_root: Optional[str] = None) -> Capabilities:
        languages = list(self._detected)
        providers: list[str] = []
        if self._detected:
            providers.append("lsp")
        has_py = bool(project_root) and self._has_python(project_root)
        if has_py:
            if "python" not in languages:
                languages.append("python")
            providers.append("python-ast")
        # The generic-ast provider only applies to non-Python indexable files;
        # reporting it for a pure-Python project would be a redundant label.
        generic_exts = (
            [e for e in self._project_extensions(project_root) if e in GENERIC_EXTENSIONS]
            if project_root
            else []
        )
        if generic_exts:
            # When the optional tree-sitter provider is active it supersedes the
            # generic scanner for these languages; report it as the provider.
            if tree_sitter_index.is_available():
                providers.append("tree-sitter")
            else:
                providers.append("generic-ast")
            for ext in generic_exts:
                key = EXT_PROFILE.get(ext)
                if key and key not in languages:
                    languages.append(key)

        if not languages:
            return Capabilities(available=False, languages=[], features=[], provider="")

        # Features differ by provider: LSP adds rename + live diagnostics.
        features = [
            "goto_definition",
            "find_references",
            "hover",
            "document_symbols",
        ]
        if has_py or generic_exts:
            features += ["search", "reconcile", "module_symbols"]
        if "lsp" in providers:
            features += ["rename", "diagnostics"]

        return Capabilities(
            available=True,
            languages=sorted(set(languages)),
            features=features,
            provider="+".join(providers),
        )

    # ---- session management ---------------------------------------------
    def _session(self, project_root: str, language: str) -> Optional[LspBridge]:
        root_uri = Path(project_root).resolve().as_uri()
        key = (root_uri, language)
        with self._lock:
            sess = self._sessions.get(key)
            if sess is None:
                command = find_server_command(language)
                if not command:
                    return None
                sess = LspBridge(command, language, root_uri)
                self._sessions[key] = sess
            return sess

    def _prepare(self, project_root: str, path: str):
        language = language_for_path(path)
        if not language:
            return None
        sess = self._session(project_root, language)
        if sess is None:
            return None
        abs_path = Path(path).resolve()
        uri = abs_path.as_uri()
        try:
            text = abs_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        sess.open_document(uri, text, language)
        return sess, uri

    # ---- features (LSP first, built-in python index fallback) -----------
    def goto_definition(
        self, project_root: str, path: str, line: int, col: int
    ) -> list[Location]:
        prep = self._prepare(project_root, path)
        if prep:
            sess, uri = prep
            return sess.definition(uri, line, col)
        if self._indexable(path):
            return self._symbol_index(project_root).goto_definition(path, line, col)
        return []

    def find_references(
        self, project_root: str, path: str, line: int, col: int
    ) -> list[Location]:
        prep = self._prepare(project_root, path)
        if prep:
            sess, uri = prep
            return sess.references(uri, line, col)
        if self._indexable(path):
            return self._symbol_index(project_root).find_references(path, line, col)
        return []

    def hover(
        self, project_root: str, path: str, line: int, col: int
    ) -> Optional[str]:
        prep = self._prepare(project_root, path)
        if prep:
            sess, uri = prep
            return sess.hover(uri, line, col)
        if self._indexable(path):
            return self._symbol_index(project_root).hover(path, line, col)
        return None

    def document_symbols(self, project_root: str, path: str) -> list[Symbol]:
        prep = self._prepare(project_root, path)
        if prep:
            sess, uri = prep
            return sess.document_symbols(uri)
        if self._indexable(path):
            return self._symbol_index(project_root).document_symbols(path)
        return []

    def search(
        self, project_root: str, query: str, limit: int = 50
    ) -> list[SymbolRecord]:
        if not (project_root and self._has_indexable(project_root)):
            return []
        return self._symbol_index(project_root).search(query, limit)

    def reconcile(
        self, project_root: str, contracts: list[dict]
    ) -> ReconcileReport:
        if not (project_root and self._has_indexable(project_root)):
            return ReconcileReport(stale=[], orphan=[], unregistered=[])
        return self._symbol_index(project_root).reconcile(contracts or [])

    def reconcile_registry(
        self, project_root: str, items: list[dict]
    ) -> dict:
        """Typed-registry reconcile (§3.3/§3.7). ``items`` are already flattened
        by ``contract_registry.flatten_registry`` in the route layer.

        Returns the dict produced by ``SymbolIndex.reconcile_registry`` (stale /
        orphan / unregistered / enriched / by_type).
        """
        if not (project_root and self._has_indexable(project_root)):
            return {
                "stale": [],
                "orphan": [],
                "unregistered": [],
                "enriched": items or [],
                "by_type": {},
            }
        return self._symbol_index(project_root).reconcile_registry(items or [])

    def module_symbols(
        self, project_root: str, module_path: str
    ) -> list[SymbolRecord]:
        if not (project_root and self._has_indexable(project_root)):
            return []
        return self._symbol_index(project_root).module_symbols(module_path)

    def stats(self, project_root: str) -> dict:
        if not (project_root and self._has_indexable(project_root)):
            return {}
        return self._symbol_index(project_root).stats()

    # ---- file ignore list (KNOWN_ISSUES #5) --------------------------------
    def ignore_add(self, project_root: str, pattern: str) -> dict:
        if not (project_root and self._has_indexable(project_root)):
            return {}
        return self._symbol_index(project_root).add_ignore_pattern(pattern)

    def ignore_propose(self, project_root: str, pattern: str) -> dict:
        if not (project_root and self._has_indexable(project_root)):
            return {}
        return self._symbol_index(project_root).propose_ignore_pattern(pattern)

    def ignore_list(self, project_root: str) -> dict:
        if not (project_root and self._has_indexable(project_root)):
            return {}
        return self._symbol_index(project_root).list_ignore()

    def ignore_pending(self, project_root: str) -> list[str]:
        if not (project_root and self._has_indexable(project_root)):
            return []
        return self._symbol_index(project_root).pending_ignore()

    def ignore_approve(self, project_root: str, pattern: str) -> dict:
        if not (project_root and self._has_indexable(project_root)):
            return {}
        return self._symbol_index(project_root).approve_ignore(pattern)

    def ignore_reject(self, project_root: str, pattern: str) -> dict:
        if not (project_root and self._has_indexable(project_root)):
            return {}
        return self._symbol_index(project_root).reject_ignore(pattern)

    # ---- architecture-diagram projection (P3) ----------------------------
    def architecture_projection(
        self,
        project_root: str,
        modules: list[dict],
        merge_registry: bool = False,
    ) -> dict:
        """Project the derived symbol_index onto the architecture diagram.

        For every module (with a ``path``) we return its public API symbols and a
        ``status`` — ``active`` when the path still resolves to indexed source,
        ``stale`` when the declared path no longer matches any tracked file (drift
        after a rename/move). We also count public symbols in source that belong to
        no architecture module (``orphan``), the inverse drift signal.

        ``merge_registry`` (KNOWN_ISSUES #9) lets this projection share the single
        Contract Registry data source with ``registry_projection``: it pulls each
        module's ``depends_on`` and (for glue / IIFE modules with no module-level
        public symbols) its declared ``exports`` from ``contracts.json``.
        """
        if not (project_root and self._has_indexable(project_root)):
            return {"modules": {}, "orphan": {"count": 0, "files": []}}
        idx = self._symbol_index(project_root)
        module_paths = {m.get("path") for m in modules if m.get("path")}

        # Single-source-of-truth merge: contracts.json module ids -> module dict.
        registry_by_id: dict[str, dict] = {}
        if merge_registry:
            from workspace import read_contracts

            contracts = read_contracts(Path(project_root)) or {}
            for rm in contracts.get("modules", []) or []:
                if isinstance(rm, dict) and rm.get("id"):
                    registry_by_id[rm["id"]] = rm

        out: dict[str, dict] = {}
        for m in modules:
            mid = m.get("id")
            if not mid:
                continue
            mpath = m.get("path") or ""
            recs = idx.module_symbols(mpath)
            public = self._module_public_api(recs)
            tracked = idx._file_index(mpath) is not None
            status = "stale" if (not recs and not tracked) else "active"

            edges: list[dict] = []
            depends_on = list(m.get("depends_on", []) or [])
            reg_mod = registry_by_id.get(mid)
            if reg_mod:
                # Prefer the registry-declared dependencies (single source of truth).
                depends_on = depends_on or list(reg_mod.get("depends_on", []) or [])
                # Glue / IIFE module: if the source file yielded no module-level
                # public symbols, fill public_api from the registry's declared
                # exports so the node still shows its surface (KNOWN_ISSUES #8).
                if not public and reg_mod.get("exports"):
                    public = [
                        {
                            "name": ex.get("symbol") or ex.get("name"),
                            "kind": ex.get("kind", "function"),
                            "file": mpath,
                            "line": 0,
                            "character": 0,
                            "signature": ex.get("signature", ""),
                            "is_public": True,
                            "status": "active",
                        }
                        for ex in reg_mod["exports"]
                        if isinstance(ex, dict)
                    ]
            for dep in depends_on:
                edges.append(
                    {
                        "from": mid,
                        "to": dep,
                        "kind": "depends_on",
                        "label": "依赖",
                        "reason": f"{mid} -> {dep}",
                    }
                )

            out[mid] = {
                "path": mpath,
                "status": status,
                "total": len(recs),
                "public_api": public,
                "edges": edges,
            }

        orphan_files: set[str] = set()
        orphan_count = 0
        for r in idx.records:
            if r.is_public and r.file not in module_paths:
                orphan_count += 1
                orphan_files.add(r.file)
        return {
            "modules": out,
            "orphan": {"count": orphan_count, "files": sorted(orphan_files)[:50]},
        }

    @staticmethod
    def _module_public_api(recs: list[SymbolRecord]) -> list[dict]:
        """Dedupe a module's symbols for the diagram (KNOWN_ISSUES #8).

        Keeps only module-level (``scope == ""``) definitions so nested class
        methods are dropped, and collapses same-name duplicates (e.g. a top-level
        ``function step`` and a prototype assignment ``Snake.prototype.step``).
        When a module has no module-level public symbol — e.g. an IIFE / glue
        bootstrap file whose symbols all live inside a function expression — falls
        back to its shallow (function / class / constant / variable) definitions so
        the node is not rendered empty.
        """
        module_level = [r for r in recs if r.is_public and r.scope == ""]
        if not module_level:
            module_level = [
                r
                for r in recs
                if r.kind in ("function", "class", "constant", "variable")
            ]
        seen: set[str] = set()
        public: list[dict] = []
        for r in sorted(module_level, key=lambda r: (r.scope != "", r.line)):
            if r.name in seen:
                continue
            seen.add(r.name)
            public.append(CodeIntelService._record_dict(r))
        return public

    def registry_projection(self, project_root: str) -> dict:
        """Project the typed Contract Registry onto the architecture diagram (§3 step3).

        Thin wrapper over :func:`contract_registry.projection_graph` — the graph
        building and §3 step4 impact propagation live in the contract domain so the
        agent context builder and task boundaries reuse them without importing the
        codeintel package. Returns empty structures when no contract registry
        exists yet.
        """
        from workspace import read_contracts

        from contract_registry import projection_graph

        contracts = read_contracts(Path(project_root)) or {}
        return projection_graph(contracts)

    @staticmethod
    def _impact_propagation(nodes: list[dict], edges: list[dict]) -> dict:
        """Backwards-compatible shim: delegation moved to contract_registry."""
        from contract_registry import impact_for_roots

        stale_roots = sorted(
            {n["owner"] for n in nodes if n.get("status") == "stale" and n.get("owner")}
        )
        return impact_for_roots(nodes, edges, stale_roots)

    @staticmethod
    def _record_dict(r: SymbolRecord) -> dict:
        return {
            "name": r.name,
            "kind": r.kind,
            "file": r.file,
            "line": r.line,
            "character": r.character,
            "signature": r.signature,
            "is_public": r.is_public,
            "status": r.status,
        }

    def rename(
        self, project_root: str, path: str, line: int, col: int, new_name: str
    ) -> dict:
        prep = self._prepare(project_root, path)
        if not prep:
            # Built-in indexer does not support rename (use an LSP for that).
            return {}
        sess, uri = prep
        return sess.rename(uri, line, col, new_name)

    def diagnostics(self, project_root: str, path: str) -> list[Diagnostic]:
        language = language_for_path(path)
        if not language:
            return []
        sess = self._session(project_root, language)
        if sess is None:
            return []
        uri = Path(path).resolve().as_uri()
        return sess.latest_diagnostics(uri)

    def close_all(self) -> None:
        with self._lock:
            for sess in self._sessions.values():
                try:
                    sess.close()
                except Exception:
                    pass
            self._sessions.clear()
            self._symbol_indexes.clear()
            self._ext_cache.clear()
