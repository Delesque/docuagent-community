"""The derived ``symbol_index`` — the single source of truth for code intelligence.

Per ROADMAP (3.2 / 3.3 / 3.7) and ARCHITECTURE (6.1 / 7), ``symbol_index`` is an
*auto-generated* inventory of every function, class, constant and exported
symbol in the project, produced by the source scanner. It is deliberately
separate from the Contract Registry (which holds manually-registered
cross-module public contracts) and is **never** painted as nodes on the
architecture diagram — the diagram only projects its aggregate counts / drift
status.

This module is the project-wide aggregator. For Python it reuses the zero-
dependency ``PythonFileIndex`` scanner from ``pyindex``; for every other
supported language it uses the zero-dependency ``GenericFileIndex`` scanner from
``generic_index`` (P4). The scanner registry is pluggable so future providers
(e.g. an optional tree-sitter backend behind a feature flag) slot in without
touching the rest of DocuAgent. Every symbol carries ``source_hash`` /
``last_seen`` / ``status`` so reconciliation (stale / orphan / unregistered)
against the Contract Registry is possible, exactly as ROADMAP 3.3.4 / 3.7.2
describe.

Zero third-party runtime dependencies. All imports are stdlib.
"""

from __future__ import annotations

import fnmatch
import hashlib
import time
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import unquote, urlparse

from .ports import Location, ReconcileReport, Symbol, SymbolRecord
from .pyindex import PythonFileIndex, _SKIP_DIRS
from .generic_index import (
    ALL_INDEXABLE_EXTENSIONS,
    GenericFileIndex,
    PROFILES,
    profile_for_ext,
)
from . import tree_sitter_index


def _norm_path(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().strip("/")


def _matches_file(a: str, b: str) -> bool:
    """Tolerant path equality: handles relative vs absolute and trailing segments."""
    na, nb = _norm_path(a), _norm_path(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    if na in nb or nb in na:
        return True
    return na.split("/")[-1] == nb.split("/")[-1]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---- file ignore list (KNOWN_ISSUES #5) ----------------------------------
# A unified, gitignore-style exclude list shared by the indexer, the contract
# reconciler and the diagram projector. All three consult ``SymbolIndex.is_ignored``,
# so a single list keeps them from diverging. It has three tiers:
#   1. persistent:  .docuagent/codeintel.ignore  (human-owned source of truth)
#   2. session:     agent-added patterns, active immediately, never persisted
#   3. proposed:    agent-proposed patterns awaiting main-agent + human approval
IGNORE_FILE_NAME = ".docuagent/codeintel.ignore"


def _path_matches(pattern: str, rel: str) -> bool:
    """gitignore-style glob match of ``pattern`` against a posix ``rel`` path.

    Handles the documented cases: bare dir names (``node_modules``), full-path
    globs (``src/gen/**``), basename globs at any depth (``**/test-*.js``,
    ``**/*.test.js``) and plain basename globs (``test-*.py``). A ``**/``-prefixed
    pattern also matches the root level (gitignore semantics: ``**/`` means zero or
    more directories), so ``**/scratch.py`` matches both ``scratch.py`` and
    ``sub/scratch.py``.
    """
    pattern = pattern.strip().lstrip("/")
    if not pattern:
        return False
    rel = rel.replace("\\", "/").strip().strip("/")
    if not rel:
        return False
    # A `**/`-prefixed pattern must also be tried without the prefix so it matches
    # files at the project root (no leading slash in `rel`).
    candidates = [pattern]
    if pattern.startswith("**/"):
        candidates.append(pattern[3:])
    segments = rel.split("/")
    for cand in candidates:
        # Directory-only / single-segment pattern matches any path segment.
        if "/" not in cand and "**" not in cand:
            if any(fnmatch.fnmatchcase(s, cand) for s in segments):
                return True
        # Full relative-path glob (handles **/, nested dirs, extension globs).
        if fnmatch.fnmatchcase(rel, cand):
            return True
    return False


# ---- pluggable scanner registry --------------------------------------------
# profile_key (e.g. "typescript") -> factory(abspath, text) -> per-file index.
# The generic scanner is the default; an optional provider (tree-sitter) may
# override entries when its feature flag + dependency are present. ``SymbolIndex``
# consults this before building, so providers slot in without touching callers.
_SCANNER_FACTORIES: dict[str, Callable[[str, str], Any]] = {}


def register_scanner(profile_key: str, factory: Callable[[str, str], Any]) -> None:
    """Register (or override) the per-file scanner factory for a language profile."""
    _SCANNER_FACTORIES[profile_key] = factory


def _build_file_index(abspath: str, text: str, prof: Any) -> Any:
    """Build a per-file index, preferring any registered provider with fallback.

    A single file whose provider raises (e.g. a grammar that can't parse it)
    degrades just that file back to the generic scanner instead of aborting the
    whole project index.
    """
    factory = _SCANNER_FACTORIES.get(prof.key)
    if factory is not None:
        try:
            return factory(abspath, text)
        except Exception:
            return GenericFileIndex(abspath, text, prof)
    return GenericFileIndex(abspath, text, prof)


def _register_default_scanners() -> None:
    for key, prof in PROFILES.items():
        register_scanner(key, lambda a, t, p=prof: GenericFileIndex(a, t, p))


_register_default_scanners()


class SymbolIndex:
    """Project-wide, derived symbol index (the code-intel single source of truth).

    Indexes every supported source file: Python rides on the precise ``ast``
    scanner (``PythonFileIndex``) while all other languages ride on the
    zero-dependency generic scanner (``GenericFileIndex``). Both present the
    same minimal interface, so the rest of this class is language-agnostic.
    """

    def __init__(self, root: str) -> None:
        self.root = str(Path(root).resolve())
        # Ignore-list state (KNOWN_ISSUES #5). See _path_matches for the three tiers.
        self._persistent_ignore: list[str] = []
        self._session_ignore: set[str] = set()
        self._proposed_ignore: list[str] = []
        self._ignore_file = Path(self.root) / IGNORE_FILE_NAME
        self._load_ignore_file()
        # If the optional tree-sitter provider is enabled + installed, register its
        # per-language factories into the scanner registry before building.
        if tree_sitter_index.is_available():
            for k, f in tree_sitter_index.scanner_factories().items():
                register_scanner(k, f)
        self._build()
        self._disk_signature = self._source_signature()

    def _source_signature(self) -> tuple:
        root = Path(self.root)
        files = []
        for path in root.rglob("*"):
            if (not path.is_file() or _SKIP_DIRS & set(path.parts)
                    or path.suffix.lstrip(".").lower() not in ALL_INDEXABLE_EXTENSIONS):
                continue
            relative = path.relative_to(root).as_posix()
            if self.is_ignored(relative):
                continue
            try:
                stat = path.stat()
                files.append((relative, stat.st_mtime_ns, stat.st_size))
            except OSError:
                continue
        return tuple(sorted(files))

    def refresh_if_changed(self) -> None:
        signature = self._source_signature()
        if signature != self._disk_signature:
            self._build()
            self._disk_signature = signature

    # ---- construction ----------------------------------------------------
    def _build(self) -> None:
        # Reset accumulators so a rebuild (after an ignore-list change) starts clean.
        self.files = {}
        self.records = []
        self.global_defs = {}
        self.project_defs = {}
        self.project_usages = {}
        self.source_hashes = {}
        root_path = Path(self.root)
        if not root_path.is_dir():
            return
        now = time.time()
        for child in root_path.rglob("*"):
            if not child.is_file():
                continue
            if _SKIP_DIRS & set(child.parts):
                continue
            try:
                rel = str(child.relative_to(root_path)).replace("\\", "/")
            except ValueError:
                rel = str(child)
            # Unified ignore filter: the single chokepoint shared by the indexer,
            # the contract reconciler and the diagram projector (KNOWN_ISSUES #5).
            if self.is_ignored(rel):
                continue
            ext = child.suffix.lower().lstrip(".")
            if ext not in ALL_INDEXABLE_EXTENSIONS:
                continue
            try:
                text = child.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            abspath = str(child.resolve())
            digest = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()
            self.source_hashes[abspath] = digest
            if ext == "py":
                fi: Any = PythonFileIndex(abspath, text)
            else:
                prof = profile_for_ext(ext)
                if prof is None:
                    continue
                fi = _build_file_index(abspath, text, prof)
            self.files[abspath] = fi
            try:
                rel = str(child.relative_to(root_path)).replace("\\", "/")
            except ValueError:
                rel = abspath
            recs = fi.records(rel, digest, now)
            self.records.extend(recs)
            for r in recs:
                # Public module-level symbols are cross-file goto targets. Include
                # `variable` so namespaces declared as `var`/`let` (e.g. a singleton
                # object) are jumpable too (KNOWN_ISSUES #3).
                if r.is_public and r.kind in ("function", "class", "constant", "variable"):
                    # Prefer the shallowest / earliest definition so a cross-file
                    # goto lands on the module-level symbol rather than a same-named
                    # nested method (KNOWN_ISSUES #2).
                    cur = self.global_defs.get(r.name)
                    if cur is None or r.line < cur[1]:
                        self.global_defs[r.name] = (abspath, r.line, r.character)
            # Definitions are stable; aggregate them into the project index now.
            for name, sites in fi.defs.items():
                bucket = self.project_defs.setdefault(name, [])
                for (l, c) in sites:
                    bucket.append((abspath, l, c))

        # Pass 2: extend per-file usage collection to project-wide symbols so that
        # cross-module references surface even without import statements (KNOWN_ISSUES #1).
        # We only re-scan for *external* public symbols — those defined in some OTHER
        # file — which (a) keeps the reference graph focused on real module connectors
        # and (b) avoids counting a local variable that happens to share a name with a
        # global as a cross-file reference (noise reduction, KNOWN_ISSUES #11 / P0.5).
        public_symbols: set = set(self.global_defs)
        for fi in self.files.values():
            local_defs = set(getattr(fi, "defs", {}).keys())
            ext_method = getattr(fi, "collect_usages_project_wide", None)
            if ext_method is not None:
                ext_method(public_symbols - local_defs)
            # Aggregate only the *cross-file* edges: a usage whose name is also
            # defined in this same file is a same-file reference, not a module
            # connector, so it stays out of the project-wide graph (KNOWN_ISSUES #11 / P0.5).
            for name, sites in fi.usages.items():
                if name in local_defs:
                    continue
                bucket = self.project_usages.setdefault(name, [])
                for (l, c) in sites:
                    bucket.append((fi.abspath, l, c))

        # Stable, deterministic order for the projected reference lists.
        for bucket in self.project_defs.values():
            bucket.sort()
        for bucket in self.project_usages.values():
            bucket.sort()

    # ---- file ignore list (KNOWN_ISSUES #5) ---------------------------------
    def _load_ignore_file(self) -> None:
        """Read the persistent ignore list from ``.docuagent/codeintel.ignore``."""
        self._persistent_ignore = []
        if not self._ignore_file.is_file():
            return
        try:
            text = self._ignore_file.read_text(encoding="utf-8")
        except OSError:
            return
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            self._persistent_ignore.append(line)

    def _append_ignore_file(self, pattern: str) -> None:
        """Persist ``pattern`` to the human-owned ignore file (best-effort, deduped)."""
        try:
            self._ignore_file.parent.mkdir(parents=True, exist_ok=True)
            existing: set[str] = set()
            if self._ignore_file.is_file():
                try:
                    existing = {
                        ln.strip()
                        for ln in self._ignore_file.read_text(encoding="utf-8").splitlines()
                        if ln.strip() and not ln.strip().startswith("#")
                    }
                except OSError:
                    existing = set()
            if pattern not in existing:
                with self._ignore_file.open("a", encoding="utf-8") as fh:
                    fh.write(pattern + "\n")
        except OSError:
            # Persistence is best-effort; the session copy still applies this run.
            pass
        if pattern not in self._persistent_ignore:
            self._persistent_ignore.append(pattern)

    def is_ignored(self, rel_path: str) -> bool:
        """True when ``rel_path`` (posix, relative to root) matches any ignore tier."""
        rel = (rel_path or "").replace("\\", "/").strip().strip("/")
        if not rel:
            return False
        for pat in (*self._persistent_ignore, *self._session_ignore):
            if _path_matches(pat, rel):
                return True
        return False

    def add_ignore_pattern(self, pattern: str) -> dict:
        """Add a *session-scoped* ignore pattern: effective immediately, not persisted."""
        pat = (pattern or "").strip()
        if not pat:
            raise ValueError("ignore pattern must not be empty")
        self._session_ignore.add(pat)
        self._build()
        return self._ignore_status()

    def propose_ignore_pattern(self, pattern: str) -> dict:
        """Propose a pattern for persistence: active now (session) + queued for approval."""
        pat = (pattern or "").strip()
        if not pat:
            raise ValueError("ignore pattern must not be empty")
        self._session_ignore.add(pat)
        if pat not in self._proposed_ignore:
            self._proposed_ignore.append(pat)
        self._build()
        return self._ignore_status()

    def pending_ignore(self) -> list[str]:
        """Patterns awaiting main-agent review + human approval for persistence."""
        return list(self._proposed_ignore)

    def approve_ignore(self, pattern: str) -> dict:
        """Persist a proposed pattern to the project ignore file (caller-gated).

        Only the main agent (after human approval) should call this; it writes to
        the human-owned ``.docuagent/codeintel.ignore``.
        """
        pat = (pattern or "").strip()
        if pat in self._proposed_ignore:
            self._proposed_ignore.remove(pat)
        self._append_ignore_file(pat)
        self._session_ignore.discard(pat)
        self._build()
        return self._ignore_status()

    def reject_ignore(self, pattern: str) -> dict:
        """Drop a proposed pattern: remove from the queue and the session overlay."""
        pat = (pattern or "").strip()
        if pat in self._proposed_ignore:
            self._proposed_ignore.remove(pat)
        self._session_ignore.discard(pat)
        self._build()
        return self._ignore_status()

    def list_ignore(self) -> dict:
        """Merged, effective ignore sets — transparency for the (sub-)agent."""
        return self._ignore_status()

    def _ignore_status(self) -> dict:
        return {
            "persistent": list(self._persistent_ignore),
            "session": sorted(self._session_ignore),
            "pending": list(self._proposed_ignore),
            "effective": sorted(set(self._persistent_ignore) | self._session_ignore),
        }

    # ---- lookup helpers --------------------------------------------------
    def _file_index(self, file: str) -> Optional[Any]:
        key = str(Path(file).resolve())
        fi = self.files.get(key)
        if fi is not None:
            return fi
        rel = str(Path(self.root) / file)
        fi = self.files.get(str(Path(rel).resolve()))
        if fi is not None:
            return fi
        for abspath, f in self.files.items():
            if abspath.endswith(file) or file.endswith(abspath):
                return f
        return None

    def _rel_for(self, abspath: str) -> str:
        try:
            return str(Path(abspath).relative_to(self.root)).replace("\\", "/")
        except ValueError:
            return abspath

    # ---- core navigation (delegated to the per-file scanner) --------------
    def goto_definition(self, file: str, line: int, col: int) -> list[Location]:
        fi = self._file_index(file)
        if fi is None:
            return []
        name = fi._identifier_at(line, col)
        if not name:
            return []
        uri = Path(fi.abspath).as_uri()
        if name in fi.defs:
            l, c = fi.defs[name][0]
            return [Location(uri=uri, line=l, character=c, end_line=l, end_character=c)]
        if name in self.global_defs:
            abspath, l, c = self.global_defs[name]
            return [Location(uri=Path(abspath).as_uri(), line=l, character=c, end_line=l, end_character=c)]
        for imp in fi.imports:
            if imp["alias"] == name:
                return [Location(uri=uri, line=imp["line"], character=imp["col"], end_line=imp["line"], end_character=imp["col"])]
        return []

    def find_references(self, file: str, line: int, col: int) -> list[Location]:
        fi = self._file_index(file)
        if fi is None:
            return []
        name = fi._identifier_at(line, col)
        if not name:
            return []
        out: list[Location] = []
        seen: set[tuple[str, int, int]] = set()
        # Same-file references (definition sites + same-file usages) come straight
        # from the per-file scanner, so single-file find_references stays precise.
        for loc in fi.find_references(line, col):
            key = (loc.uri, loc.line, loc.character)
            if key in seen:
                continue
            seen.add(key)
            out.append(loc)
        # Cross-file edges: every definition + usage site across all files.
        for abspath, l, c in self.project_defs.get(name, []):
            key = (Path(abspath).as_uri(), l, c)
            if key in seen:
                continue
            seen.add(key)
            out.append(Location(uri=Path(abspath).as_uri(), line=l, character=c, end_line=l, end_character=c))
        for abspath, l, c in self.project_usages.get(name, []):
            key = (Path(abspath).as_uri(), l, c)
            if key in seen:
                continue
            seen.add(key)
            out.append(Location(uri=Path(abspath).as_uri(), line=l, character=c, end_line=l, end_character=c))
        return out

    def hover(self, file: str, line: int, col: int) -> Optional[str]:
        fi = self._file_index(file)
        if fi is None:
            return None
        text = fi.hover(line, col)
        if text:
            return text
        # Usage point with no local hover: resolve to the (possibly cross-file)
        # definition and return that definition's hover text, so hovering a call
        # site shows the target signature even when the definition lives in another
        # file (KNOWN_ISSUES #4).
        for loc in self.goto_definition(file, line, col):
            target = self._file_index_from_uri(loc.uri)
            if target is None:
                continue
            ttext = target.hover(loc.line, loc.character)
            if ttext:
                return ttext
        return None

    def _file_index_from_uri(self, uri: str) -> Optional[Any]:
        """Resolve a ``file://`` Location uri back to its per-file index."""
        raw = unquote(urlparse(uri).path)
        if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
            raw = raw[1:]
        return self._file_index(raw)

    def document_symbols(self, file: str) -> list[Symbol]:
        fi = self._file_index(file)
        if fi is None:
            return []
        return fi.document_symbols()

    # ---- symbol_index exclusive capabilities -----------------------------
    def search(self, query: str, limit: int = 50) -> list[SymbolRecord]:
        """Fuzzy substring search across all symbols (backs ``search_reuse``)."""
        q = (query or "").strip().lower()
        if not q:
            return []
        scored: list[tuple[bool, str, int, SymbolRecord]] = []
        for r in self.records:
            lowered = r.name.lower()
            if q in lowered:
                scored.append((not lowered.startswith(q), r.file, r.line, r))
        scored.sort(key=lambda t: (t[0], t[1], t[2]))
        return [t[3] for t in scored[:limit]]

    def module_symbols(self, module_path: str) -> list[SymbolRecord]:
        """Symbols belonging to one module/file (drives the diagram expand view)."""
        target_rel: Optional[str] = None
        norm = module_path.replace("\\", "/")
        try:
            target_rel = str(Path(module_path).resolve().relative_to(self.root)).replace("\\", "/")
        except ValueError:
            for abspath in self.files:
                if abspath.replace("\\", "/").endswith(norm):
                    target_rel = self._rel_for(abspath)
                    break
        if target_rel is None:
            return []
        return [r for r in self.records if r.file == target_rel]

    def reconcile(self, contracts: list[dict]) -> ReconcileReport:
        """Diff the Contract Registry against the derived symbol_index.

        - ``unregistered``: public symbols in source with no matching registry entry.
        - ``orphan``: registry entries whose name has no symbol anywhere in source.
        - ``stale``: registry entries pointing at a file whose symbol is gone (drift).
        """
        indexed_names = {r.name for r in self.records}
        public_names = {r.name for r in self.records if r.is_public}
        contract_names = {c.get("name") for c in contracts if c.get("name")}

        unregistered = [{"name": n} for n in sorted(public_names - contract_names)]

        orphan: list[dict] = []
        stale: list[dict] = []
        for c in contracts:
            name = c.get("name")
            if not name:
                continue
            if name not in indexed_names:
                orphan.append({"name": name, "file": c.get("file")})
            fname = c.get("file")
            if fname:
                fkey = str(Path(fname).resolve())
                fi = self.files.get(fkey)
                if fi is None:
                    # tolerate a path relative to the project root
                    fi = self.files.get(str(Path(self.root) / fname))
                if fi is not None:
                    file_recs = {r.name for r in self.records if r.file == self._rel_for(fi.abspath)}
                    if name not in file_recs:
                        stale.append({"name": name, "file": fname})
        return ReconcileReport(stale=stale, orphan=orphan, unregistered=unregistered)

    def reconcile_registry(self, items: list[dict]) -> dict:
        """Reconcile the typed Contract Registry against the derived symbol_index (§3.3/§3.7).

        Input: typed registry items (see ``contract_registry.flatten_registry``),
        each carrying ``id`` / ``type`` / ``name`` / ``owner`` / ``source_ref`` /
        ``status``. Returns a dict with:

        - ``stale`` / ``orphan`` / ``unregistered``: per-type drift lists
        - ``enriched``: the input items with ``source_hash`` / ``last_seen`` /
          ``status`` filled wherever the symbol still exists in source
        - ``by_type``: per-type tally of orphan / stale counts

        This is the typed successor to :meth:`reconcile`; the latter is retained
        for the legacy flat ``{name, file}`` list contract.
        """
        rec_by_name: dict[str, list] = {}
        for r in self.records:
            rec_by_name.setdefault(r.name, []).append(r)
        public_names = {r.name for r in self.records if r.is_public}

        orphan: list[dict] = []
        stale: list[dict] = []
        enriched: list[dict] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            name = it.get("name")
            if not name:
                enriched.append(it)
                continue
            typ = it.get("type") or "public_api"
            rid = it.get("id") or f"{typ}:{(it.get('owner') or '_')}.{name}"
            ref = it.get("source_ref") or {}
            ref_file = ref.get("file") if isinstance(ref, dict) else ""
            recs = rec_by_name.get(name)
            if not recs:
                orphan.append({
                    "id": rid,
                    "type": typ,
                    "name": name,
                    "owner": it.get("owner"),
                })
                enriched.append(it)
                continue
            # Symbol exists in source: verify the declared source file still holds it.
            if ref_file and not any(_matches_file(ref_file, r.file) for r in recs):
                stale.append({
                    "id": rid,
                    "type": typ,
                    "name": name,
                    "file": ref_file,
                    "owner": it.get("owner"),
                })
            primary = recs[0]
            new_item = dict(it)
            new_item["source_hash"] = self.source_hash_for(primary.file) or ""
            new_item["last_seen"] = _now_iso()
            new_item["status"] = it.get("status") or "active"
            enriched.append(new_item)

        registry_names = {
            it.get("name") for it in (items or [])
            if isinstance(it, dict) and it.get("name")
        }
        unregistered = [{"name": n} for n in sorted(public_names - registry_names)]
        by_type: dict[str, dict[str, int]] = {}
        for bucket_name, bucket in (("orphan", orphan), ("stale", stale)):
            for entry in bucket:
                tally = by_type.setdefault(entry["type"], {"orphan": 0, "stale": 0})
                tally[bucket_name] += 1
        return {
            "stale": stale,
            "orphan": orphan,
            "unregistered": unregistered,
            "enriched": enriched,
            "by_type": by_type,
        }

    def stats(self) -> dict:
        by_kind: dict[str, int] = {}
        public = 0
        for r in self.records:
            by_kind[r.kind] = by_kind.get(r.kind, 0) + 1
            if r.is_public:
                public += 1
        return {
            "files": len(self.files),
            "symbols": len(self.records),
            "public_symbols": public,
            "by_kind": by_kind,
        }

    def source_hash_for(self, file: str) -> Optional[str]:
        fi = self._file_index(file)
        if fi is None:
            return None
        return self.source_hashes.get(fi.abspath)
