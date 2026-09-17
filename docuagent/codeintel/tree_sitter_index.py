"""Optional tree-sitter provider for DocuAgent code intelligence.

Opt-in via the ``DOCUAGENT_CODE_INTEL_TREE_SITTER`` feature flag, but **auto-enabled
when the optional packages are importable** and the flag is unset (set it to ``"0"``
to force off). The optional ``tree_sitter`` / ``tree_sitter_languages`` packages are
imported *lazily* inside ``_tree_sitter_modules``; if the flag is off or the packages
are missing, ``is_available()`` returns ``False`` and the rest of DocuAgent keeps using
the zero-dependency generic scanner. The main trunk never imports tree-sitter, so
enabling this provider has zero impact when the native packages are absent.

Wire-up (the "pluggable scanner registry" that ``symbol_index`` promises): when
``is_available()``, ``scanner_factories()`` returns a per-language factory that
builds a :class:`TreeSitterFileIndex`. ``symbol_index.SymbolIndex`` registers
these into its scanner registry on construction and consults the registry before
falling back to the generic scanner. A per-file parse failure degrades just that
file back to the generic scanner, so a single grammar hiccup never breaks the
whole index.

Interface: ``TreeSitterFileIndex`` mirrors the minimal surface of
``GenericFileIndex`` (``records`` / ``defs`` / ``imports`` / ``usages`` /
``goto_definition`` / ``find_references`` / ``hover`` / ``document_symbols`` /
``_identifier_at`` / ``abspath``) so the project-wide ``SymbolIndex`` treats it
as a drop-in replacement.
"""

from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any, Callable, Optional

from .generic_index import PROFILES
from .ports import Location, Symbol, SymbolRecord

ENV_FLAG = "DOCUAGENT_CODE_INTEL_TREE_SITTER"

# ---- optional dependency (lazily imported) ---------------------------------

def is_enabled() -> bool:
    """Tree-sitter provider is on when the operator explicitly opts in (flag == "1"),
    or automatically when its optional packages are importable and the flag is unset.
    An explicit ``"0"`` forces it off. This keeps the provider fail-closed (off when
    the packages are absent) while removing the need to set the flag by hand.
    """
    flag = os.environ.get(ENV_FLAG)
    if flag == "0":
        return False
    if flag == "1":
        return True
    return _tree_sitter_modules() is not None


def _tree_sitter_modules():
    """Lazily import the optional packages. Returns ``(ts, tsl)`` or ``None``.

    ``tsl`` (``tree_sitter_languages``) may be ``None`` if only the bare
    ``tree_sitter`` bindings are installed; the provider then has no grammars to
    load and is effectively unavailable.
    """
    try:
        import tree_sitter  # noqa: F401
    except Exception:
        return None
    try:
        import tree_sitter_languages as tsl  # type: ignore
    except Exception:
        tsl = None
    return (tree_sitter, tsl)


def is_available() -> bool:
    """Provider is usable when enabled AND the packages import."""
    if not is_enabled():
        return False
    return _tree_sitter_modules() is not None


# Per-language grammar packages (offline; the grammar is compiled into the wheel,
# so no network download is needed). Maps profile key -> (importable module,
# attribute returning a Language capsule). Preferred over the canonical
# ``tree_sitter_languages`` pack (which fetches prebuilt parsers at runtime) when
# the latter is unavailable. Add entries as more grammar packages are installed.
_GRAMMAR_PACKAGES: dict[str, tuple[str, str]] = {
    "typescript": ("tree_sitter_typescript", "language_typescript"),
    "go": ("tree_sitter_go", "language"),
    "rust": ("tree_sitter_rust", "language"),
}


def _parser_from_grammar_pkg(lang: str, _ts):
    """Build a parser from a per-language grammar package, or ``None``."""
    spec = _GRAMMAR_PACKAGES.get(lang)
    if spec is None:
        return None
    try:
        mod = importlib.import_module(spec[0])
        lang_obj = _ts.Language(getattr(mod, spec[1])())
        return _ts.Parser(lang_obj)
    except Exception:
        return None


def _get_parser(lang: str):
    """Return a tree-sitter parser for ``lang`` (a profile key) or ``None``."""
    mods = _tree_sitter_modules()
    if mods is None:
        return None
    _ts, tsl = mods
    # 1) canonical pack (tree_sitter_languages / tree_sitter_language_pack) if present
    if tsl is not None and hasattr(tsl, "get_parser"):
        try:
            return tsl.get_parser(lang)
        except Exception:
            pass
    # 2) per-language grammar packages (offline, no network needed)
    return _parser_from_grammar_pkg(lang, _ts)


# ---- grammar node maps ------------------------------------------------------

# tree-sitter node types that declare a named symbol, per language profile key.
_DEF_NODE_TYPES: dict[str, set[str]] = {
    "typescript": {
        "function_declaration",
        "method_definition",
        "generator_function_declaration",
        "function_expression",
        "class_declaration",
        "abstract_class_declaration",
        "interface_declaration",
        "type_alias_declaration",
        "enum_declaration",
        "lexical_function_declaration",
    },
    "go": {
        "function_declaration",
        "method_declaration",
        "type_declaration",
        "struct_type",
        "interface_type",
        "const_declaration",
    },
    "rust": {
        "function_item",
        "struct_item",
        "enum_item",
        "trait_item",
        "impl_item",
        "const_item",
        "static_item",
        "mod_item",
        "union_item",
    },
    "java": {
        "method_declaration",
        "constructor_declaration",
        "class_declaration",
        "interface_declaration",
        "enum_declaration",
        "record_declaration",
    },
    "c": {
        "function_definition",
        "struct_specifier",
        "enum_specifier",
        "union_specifier",
        "class_specifier",
    },
    "ruby": {"method", "class", "module"},
    "php": {
        "function_definition",
        "method_declaration",
        "class_declaration",
        "interface_declaration",
    },
}

# Compound declaration nodes whose defining identifier(s) live on a
# ``variable_declarator`` / ``const_spec`` / ``var_spec`` child rather than on the
# declaration node itself. Handled by a dedicated branch in ``_extract_defs``.
_VAR_DECL_TYPES: dict[str, set[str]] = {
    "typescript": {"lexical_declaration", "variable_declaration"},
    "go": {"const_spec", "var_spec"},
}

# AST node types that carry an identifier *reference*. Reference collection walks
# these instead of regex-scanning raw text, which is the provider's main
# precision win: an identifier that merely *looks* like a reference because it
# sits inside a string literal or a comment is not an identifier node in the
# parse tree at all, so it can never enter ``usages``.
_IDENT_NODE_TYPES: set[str] = {
    "identifier",
    "type_identifier",
    "field_identifier",
    "property_identifier",
    "shorthand_property_identifier",
    "shorthand_property_identifier_pattern",
    "namespace_identifier",
    "package_identifier",
    "statement_identifier",
}

# Subtrees that must never contribute references. In practice most grammars
# already model literals/comments as opaque leaf tokens, so this set is a
# defensive belt: some grammars do expose structure inside comments (doc tags),
# and we must not count those as code references either.
#
# NOTE: template/interpolated strings are deliberately NOT listed. Their
# ``${...}`` substitutions contain genuine code whose references must be kept;
# the inert text part is a separate leaf child (``string_fragment``) that carries
# no identifier nodes anyway.
_OPAQUE_NODE_TYPES: set[str] = {
    "comment",
    "line_comment",
    "block_comment",
    "doc_comment",
    "string",
    "string_literal",
    "string_fragment",
    "string_content",
    "escape_sequence",
    "regex",
    "regex_pattern",
}

# Default symbol kind for each definition node type.
_KIND_FOR_TYPE: dict[str, str] = {
    "function_declaration": "function",
    "method_definition": "function",
    "generator_function_declaration": "function",
    "function_expression": "function",
    "lexical_function_declaration": "function",
    "method_declaration": "function",
    "function_item": "function",
    "function_definition": "function",
    "method": "function",
    "class_declaration": "class",
    "abstract_class_declaration": "class",
    "class_specifier": "class",
    "class": "class",
    "struct_item": "class",
    "struct_specifier": "class",
    "struct_type": "class",
    "interface_declaration": "class",
    "interface_type": "class",
    "type_alias_declaration": "class",
    "enum_declaration": "class",
    "enum_specifier": "class",
    "enum_item": "class",
    "trait_item": "class",
    "impl_item": "class",
    "mod_item": "class",
    "union_item": "class",
    "union_specifier": "class",
    "record_declaration": "class",
    "module": "class",
    "type_declaration": "class",
    "const_declaration": "constant",
    "const_item": "constant",
    "static_item": "constant",
    "variable_declaration": "variable",
    "lexical_declaration": "constant",
    "const_spec": "constant",
    "var_spec": "variable",
}


# ---- helpers ----------------------------------------------------------------

def _node_text(node: Any) -> str:
    t = getattr(node, "text", None)
    if t is None:
        return ""
    if isinstance(t, (bytes, bytearray)):
        return t.decode("utf-8", "replace")
    return str(t)


def _name_of(node: Any) -> Optional[str]:
    """Resolve the defining identifier of a definition node, if any."""
    for field in ("name", "identifier", "type_identifier", "field_identifier"):
        cand = node.child_by_field_name(field)
        if cand is not None:
            return _node_text(cand) or None
    # Fallback: first named child that looks like an identifier.
    for c in getattr(node, "named_children", []) or []:
        if c.type in ("identifier", "type_identifier", "field_identifier"):
            return _node_text(c) or None
    return None


def _is_exported(decl_node: Any) -> bool:
    """True when ``decl_node`` is exported (TS/JS ``export_statement`` ancestor,
    or a Rust ``pub`` modifier on the declaration)."""
    p = getattr(decl_node, "parent", None)
    while p is not None:
        if p.type == "export_statement":
            return True
        p = getattr(p, "parent", None)
    text = _node_text(decl_node)
    if text.startswith("pub ") or text.startswith("pub("):
        return True
    return False


# ---- per-file index ---------------------------------------------------------

class TreeSitterFileIndex:
    """tree-sitter-backed per-file scanner (drop-in for ``GenericFileIndex``)."""

    def __init__(
        self,
        abspath: str,
        text: str,
        profile_key: str,
        parser: Optional[Any] = None,
    ) -> None:
        # Locations are exposed as file URIs, and POSIX refuses `as_uri()` on a
        # relative path, so the stored path is always absolute.
        self.abspath = str(Path(abspath).absolute())
        self.text = text
        self.lines = text.splitlines()
        self.profile_key = profile_key
        self.symbols: list[Symbol] = []
        self.defs: dict[str, list[tuple[int, int]]] = {}
        self.imports: list[dict] = []
        self.usages: dict[str, list[tuple[int, int]]] = {}
        self.hovers: dict[tuple[int, int], str] = {}
        self._def_meta: dict[str, dict] = {}
        self._build(parser)

    # ---- construction ------------------------------------------------------
    def _build(self, parser: Optional[Any]) -> None:
        parser = parser or _get_parser(self.profile_key)
        if parser is None:
            raise RuntimeError(
                f"no tree-sitter parser available for {self.profile_key!r}"
            )
        data = self.text.encode("utf-8") if isinstance(self.text, str) else self.text
        tree = parser.parse(data)
        self._root = tree.root_node
        self._extract_defs(tree.root_node)
        self._extract_imports()
        self._collect_usages(tree.root_node)

    def _extract_defs(self, root: Any) -> None:
        def_types = _DEF_NODE_TYPES.get(self.profile_key, set())
        var_types = _VAR_DECL_TYPES.get(self.profile_key, set())
        stack = [(root, 0)]
        while stack:
            node, depth = stack.pop()
            if node.type in def_types:
                name = _name_of(node)
                if name:
                    name_node = node.child_by_field_name("name") or node
                    self._emit_def(name, name_node, node, depth,
                                   _KIND_FOR_TYPE.get(node.type, "function"))
            elif node.type in var_types:
                # identifier(s) live on a child declarator / spec node
                for declarator in getattr(node, "named_children", []) or []:
                    if declarator.type not in ("variable_declarator", "const_spec", "var_spec"):
                        continue
                    nm_node = declarator.child_by_field_name("name")
                    if nm_node is None:
                        continue
                    nm = _node_text(nm_node) or None
                    if not nm:
                        continue
                    kind = ("constant"
                            if _node_text(node).lstrip().lower().startswith("const")
                            else "variable")
                    self._emit_def(nm, nm_node, node, depth, kind)
            for child in node.children:
                stack.append((child, depth + 1))
        # Sort each definition list by source position so `defs[name][0]` resolves
        # to the module-level (earliest) symbol, not a same-named nested method
        # (mirrors the project-wide global_defs preference, KNOWN_ISSUES #2).
        for sites in self.defs.values():
            sites.sort()

    def _emit_def(self, name: str, name_node: Any, decl_node: Any, depth: int, kind: str) -> None:
        line = getattr(name_node, "start_point", (0, 0))[0]
        col = getattr(name_node, "start_point", (0, 0))[1]
        sig = _node_text(decl_node)[:120]
        is_public = depth <= 1 or _is_exported(decl_node)
        self.symbols.append(
            Symbol(name=name, kind=kind, line=line, character=col, detail="")
        )
        self.defs.setdefault(name, []).append((line, col))
        self.hovers[(line, col)] = sig
        # ``scope`` tags module-level (depth <= 1) vs nested (inside a class/IIFE).
        # The diagram projector dedupes and drops nested methods, keeping only
        # module-level definitions (KNOWN_ISSUES #8).
        self._def_meta[(name, line, col)] = {
            "is_public": is_public,
            "kind": kind,
            "signature": sig,
            "scope": "" if depth <= 1 else "nested",
        }

    def _extract_imports(self) -> None:
        prof = PROFILES.get(self.profile_key)
        if prof is None:
            return
        for i, line in enumerate(self.lines):
            for pat in prof.import_patterns:
                for m in pat.finditer(line):
                    alias = m.groupdict().get("alias")
                    names = m.groupdict().get("names")
                    if alias:
                        self.imports.append(
                            {"alias": alias, "module": "", "line": i, "col": m.start("alias")}
                        )
                    elif names:
                        for nm in re.split(r"[,\s]+", names.strip()):
                            nm = nm.strip().strip("{}")
                            if nm:
                                col = line.find(nm)
                                self.imports.append(
                                    {"alias": nm, "module": "", "line": i, "col": max(col, 0)}
                                )

    def _collect_usages(self, root: Optional[Any] = None, names: Optional[set] = None) -> None:
        """Collect identifier references by walking the parse tree.

        This is where tree-sitter beats the regex scanner: only real identifier
        *nodes* count, so a name appearing inside a string literal, a comment or
        a regex never registers as a reference. Falls back to the regex scan only
        if no parse tree is available (defensive; ``_build`` always passes one).
        """
        if names is None:
            names = set(self.defs) | {imp["alias"] for imp in self.imports}
        names.discard("")
        if not names:
            return
        if root is None:
            self._collect_usages_regex(names)
            return
        stack = [root]
        while stack:
            node = stack.pop()
            ntype = getattr(node, "type", "")
            if ntype in _OPAQUE_NODE_TYPES:
                continue  # skip the whole comment / literal subtree
            if ntype in _IDENT_NODE_TYPES:
                name = _node_text(node)
                if name in names:
                    line, col = getattr(node, "start_point", (0, 0))
                    self.usages.setdefault(name, []).append((line, col))
            for child in getattr(node, "children", []) or []:
                stack.append(child)
        for name in self.usages:
            self.usages[name].sort()

    def collect_usages_project_wide(self, project_symbols: set) -> None:
        """Re-scan for usages of *any* symbol defined anywhere in the project.

        The base scan only tracks names defined or imported locally, so
        cross-module references without an explicit import (e.g. multi-<script>
        vanilla JS sharing globals) are missed. Passing the project-wide symbol
        set lets those references surface in ``find_references`` (KNOWN_ISSUES #1).
        """
        names = set(self.defs) | {imp["alias"] for imp in self.imports} | project_symbols
        self.usages = {}
        self._collect_usages(names=names)

    def _collect_usages_regex(self, names: set[str]) -> None:
        """Legacy raw-text scan; kept only as a no-parse-tree fallback."""
        for name in names:
            pat = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
            for i, line in enumerate(self.lines):
                for mm in pat.finditer(line):
                    self.usages.setdefault(name, []).append((i, mm.start()))

    # ---- queries (mirrors GenericFileIndex) --------------------------------
    def records(self, file_rel: str, source_hash: str, last_seen: float) -> list[SymbolRecord]:
        out: list[SymbolRecord] = []
        for sym in self.symbols:
            meta = self._def_meta.get((sym.name, sym.line, sym.character), {})
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

    def _identifier_at(self, line: int, col: int) -> Optional[str]:
        if line < 0 or line >= len(self.lines):
            return None
        text = self.lines[line]
        n = len(text)
        if col >= n:
            col = max(0, n - 1)
        if not (text[col].isalnum() or text[col] == "_" or text[col] == "$"):
            if col > 0:
                col -= 1
        start = col
        while start > 0 and (text[start - 1].isalnum() or text[start - 1] in "_$"):
            start -= 1
        end = col
        while end < n and (text[end].isalnum() or text[end] in "_$"):
            end += 1
        word = text[start:end]
        if not word:
            return None
        return word if (word[0].isalpha() or word[0] == "_" or word[0] == "$") else None

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
                return [
                    Location(
                        uri=uri,
                        line=imp["line"],
                        character=imp["col"],
                        end_line=imp["line"],
                        end_character=imp["col"],
                    )
                ]
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
        for l, c in self.usages.get(name, []):
            if (l, c) not in seen:
                seen.add((l, c))
                out.append(Location(uri=uri, line=l, character=c, end_line=l, end_character=c))
        return out

    def hover(self, line: int, col: int) -> Optional[str]:
        name = self._identifier_at(line, col)
        if not name:
            return None
        if (line, col) in self.hovers:
            return self.hovers[(line, col)]
        if name in self.defs:
            site = self.defs[name][0]
            return self.hovers.get(site, f"symbol: {name}")
        return None

    def document_symbols(self) -> list[Symbol]:
        return self.symbols


# ---- registry bridge --------------------------------------------------------

def scanner_factories() -> dict[str, Callable[[str, str], Any]]:
    """Per-profile-key factory closures building ``TreeSitterFileIndex``.

    Only call this when :func:`is_available` is true. ``symbol_index`` merges the
    result into its scanner registry. A factory is only emitted for a language
    whose grammar package actually imports here, so enabling the flag without the
    grammar packages installed degrades cleanly to the generic scanner instead of
    registering factories that always throw.
    """
    out: dict[str, Callable[[str, str], Any]] = {}
    for key, (modname, _func) in _GRAMMAR_PACKAGES.items():
        try:
            importlib.import_module(modname)
        except Exception:
            continue
        out[key] = lambda abspath, text, k=key: TreeSitterFileIndex(abspath, text, k)
    return out
