"""Zero-dependency, multi-language symbol indexer (stdlib only).

Extends the derived ``symbol_index`` to languages beyond Python without pulling
in tree-sitter (a third-party *native* dependency, forbidden by the project's
zero-dependency trunk). For each supported language we use a small regex +
brace-scoped scanner that extracts function / method / class-like / constant
definitions and their reference sites.

Accuracy is intentionally "good enough for locate / read / understand" -- the
same niche DocuAgent occupies (a workbench, not an IDE). Python keeps the
precise ``ast`` path; everything else rides on this generic scanner. Every
provider degrades gracefully and never raises on a parse failure.

No third-party runtime dependency. All imports are stdlib.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .ports import Location, Symbol, SymbolRecord
from .pyindex import _SKIP_DIRS

# Preceding context that still counts as a "definition position": whitespace,
# a statement separator, or the start of a line. Implemented as a negative
# look-behind so we never match identifiers that are mid-word or mid-call
# (e.g. `if (x = ...)` or `obj.foo()`).
_DEF_CTX = r"(?<![^\s;{}])"


@dataclass
class _Profile:
    """Per-language scanner configuration (data, not behaviour)."""

    key: str
    fn_patterns: list[re.Pattern[str]]
    type_patterns: list[re.Pattern[str]]
    const_patterns: list[re.Pattern[str]]
    import_patterns: list[re.Pattern[str]]
    line_comments: tuple[str, ...]
    block_comments: tuple[tuple[str, str], ...]
    string_quotes: str
    private_keywords: tuple[str, ...] = ()
    export_keywords: tuple[str, ...] = ()
    capitalized_public: bool = False
    export_required: bool = False
    hash_private: bool = False


def _rx(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern)


# --------------------------------------------------------------------------
# Language profiles
# --------------------------------------------------------------------------

_TS_FN = [
    _rx(_DEF_CTX + r"(?P<mod>export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\("),
    _rx(_DEF_CTX + r"(?P<mod>export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>"),
    _rx(_DEF_CTX + r"(?P<name>(?!if|for|while|switch|catch|else|return|typeof|await|yield|case|function|void|new|delete|do|try|when|guard)[A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{"),
]
_TS_TYPE = [
    _rx(_DEF_CTX + r"(?P<mod>export\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)"),
    _rx(_DEF_CTX + r"(?P<mod>export\s+)?interface\s+(?P<name>[A-Za-z_$][\w$]*)"),
    _rx(_DEF_CTX + r"(?P<mod>export\s+)?(?:enum|trait|type|struct|protocol|extension)\s+(?P<name>[A-Za-z_$][\w$]*)"),
]
_TS_CONST = [
    _rx(_DEF_CTX + r"(?P<mod>export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*="),
]
_TS_IMPORT = [
    _rx(r"import\s+(?P<alias>[A-Za-z_$][\w$]*)\s+from\s+[\"']"),
    _rx(r"import\s+\*\s+as\s+(?P<alias>[A-Za-z_$][\w$]*)\s+from"),
    _rx(r"import\s+\{(?P<names>[^}]*)\}\s+from"),
]
PROFILE_TS = _Profile(
    key="typescript",
    fn_patterns=_TS_FN,
    type_patterns=_TS_TYPE,
    const_patterns=_TS_CONST,
    import_patterns=_TS_IMPORT,
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_quotes="'\"`",
    private_keywords=(),
    export_keywords=("export",),
    hash_private=True,
)

_GO_FN = [
    _rx(_DEF_CTX + r"func\s+(?:\([^)]*\)\s+)?(?P<name>[A-Za-z_][\w]*)\s*\("),
]
_GO_TYPE = [
    _rx(_DEF_CTX + r"type\s+(?P<name>[A-Za-z_][\w]*)\s+(?:struct|interface|enum|union)?"),
    _rx(_DEF_CTX + r"(?:struct|interface|enum|trait|union)\s+(?P<name>[A-Za-z_][\w]*)"),
]
_GO_CONST = [
    _rx(_DEF_CTX + r"(?:var|const)\s+(?P<name>[A-Za-z_][\w]*)"),
]
_GO_IMPORT = [
    _rx(r"import\s+\((?P<names>[^)]*)\)"),
    _rx(r"import\s+[\"'](?P<names>[^\"']*)[\"']"),
]
PROFILE_GO = _Profile(
    key="go",
    fn_patterns=_GO_FN,
    type_patterns=_GO_TYPE,
    const_patterns=_GO_CONST,
    import_patterns=_GO_IMPORT,
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_quotes="'\"`",
    capitalized_public=True,
)

_RUST_FN = [
    _rx(_DEF_CTX + r"(?P<mod>pub(?:\s*\([^)]*\))?\s+)?fn\s+(?P<name>[A-Za-z_][\w]*)\s*\("),
]
_RUST_TYPE = [
    _rx(_DEF_CTX + r"(?P<mod>pub(?:\s*\([^)]*\))?\s+)?(?:struct|enum|trait|type|impl|union)\s+(?P<name>[A-Za-z_][\w]*)"),
]
_RUST_CONST = [
    _rx(_DEF_CTX + r"(?P<mod>pub(?:\s*\([^)]*\))?\s+)?(?:const|static)\s+(?P<name>[A-Za-z_][\w]*)"),
]
_RUST_IMPORT = [
    _rx(r"use\s+(?P<names>[^;]+);"),
]
PROFILE_RUST = _Profile(
    key="rust",
    fn_patterns=_RUST_FN,
    type_patterns=_RUST_TYPE,
    const_patterns=_RUST_CONST,
    import_patterns=_RUST_IMPORT,
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_quotes="'\"",
    export_required=True,
    export_keywords=("pub",),
)

_JAVA_FN = [
    _rx(
        _DEF_CTX
        + r"(?:<[\w,<>\?\s\[\]]+>\s+)?(?:public|private|protected|internal|static|final|abstract|async|synchronized|override|open|virtual|sealed|func|fun|@\w+\s+|\s)*?"
        r"(?P<name>(?!if|for|while|switch|catch|return|else|do|try|when|guard|new|throw|typeof|await|yield|case|sizeof|using)[A-Za-z_$][\w$]*)\s*\([^;]*\)\s*(?:\{|=>|where)"
    ),
]
_JAVA_TYPE = [
    _rx(
        _DEF_CTX
        + r"(?:(?:public|private|protected|internal)\s+)?(?:abstract\s+|final\s+|sealed\s+|static\s+|open\s+|data\s+|@\w+\s+)*?"
        r"(?:class|interface|enum|struct|record|trait|protocol|extension)\s+(?P<name>[A-Za-z_$][\w$]*)"
    ),
]
_JAVA_CONST = [
    _rx(
        _DEF_CTX
        + r"(?:public|private|protected|internal|static|final|const|val|let|var|@\w+\s+)*?"
        r"(?P<name>[A-Za-z_$][\w$]*)\s*(?::[\w<>\[\],\.\s]+)?\s*="
    ),
]
_JAVA_IMPORT = [
    _rx(r"import\s+(?:static\s+)?(?P<names>[\w\.\*]+)"),
    _rx(r"using\s+(?:static\s+)?(?P<names>[\w\.\*]+)"),
]
PROFILE_JAVA = _Profile(
    key="java",
    fn_patterns=_JAVA_FN,
    type_patterns=_JAVA_TYPE,
    const_patterns=_JAVA_CONST,
    import_patterns=_JAVA_IMPORT,
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_quotes="'\"",
    private_keywords=("private", "protected", "internal", "fileprivate"),
    export_keywords=("public",),
)

_C_FN = [
    _rx(
        _DEF_CTX
        + r"(?:inline\s+|static\s+|virtual\s+|explicit\s+|constexpr\s+|extern\s+|friend\s+|template\s*<[^>]*>\s+)*?"
        r"(?P<name>(?!if|for|while|switch|catch|return|else|do|try|sizeof|typeof|alignof|decltype)[A-Za-z_][\w]*)\s*\([^;]*\)\s*\{"
    ),
]
_C_TYPE = [
    _rx(_DEF_CTX + r"(?:typedef\s+)?(?:struct|class|union|enum)\s+(?P<name>[A-Za-z_][\w]*)"),
]
_C_CONST = [
    _rx(_DEF_CTX + r"(?:const|static)\s+[\w\s\*]+\s+(?P<name>[A-Za-z_][\w]*)\s*="),
    _rx(_DEF_CTX + r"#define\s+(?P<name>[A-Za-z_][\w]*)"),
]
_C_IMPORT = [
    _rx(r"#include\s*[<\"](?P<names>[^>\"]+)[>\"]"),
]
PROFILE_C = _Profile(
    key="c",
    fn_patterns=_C_FN,
    type_patterns=_C_TYPE,
    const_patterns=_C_CONST,
    import_patterns=_C_IMPORT,
    line_comments=("//",),
    block_comments=(("/*", "*/"),),
    string_quotes="'\"",
)

_RUBY_FN = [
    _rx(_DEF_CTX + r"def\s+(?:self\.)?(?P<name>[A-Za-z_][\w]*)(?:[?!])?\s*\("),
]
_RUBY_TYPE = [
    _rx(_DEF_CTX + r"(?:class|module)\s+(?P<name>[A-Za-z_][\w]*)"),
]
_RUBY_CONST = [
    _rx(_DEF_CTX + r"(?P<name>[A-Z][A-Z0-9_]*)\s*="),
]
_RUBY_IMPORT = [
    _rx(r"require(?:_relative)?\s+[\"'](?P<names>[^\"']+)[\"']"),
]
PROFILE_RUBY = _Profile(
    key="ruby",
    fn_patterns=_RUBY_FN,
    type_patterns=_RUBY_TYPE,
    const_patterns=_RUBY_CONST,
    import_patterns=_RUBY_IMPORT,
    line_comments=("#",),
    block_comments=(("=begin", "=end"),),
    string_quotes="'\"`",
)

_PHP_FN = [
    _rx(_DEF_CTX + r"(?:(?:public|private|protected|static|final|abstract|async)\s+)*?function\s+(?P<name>[A-Za-z_][\w]*)\s*\("),
]
_PHP_TYPE = [
    _rx(_DEF_CTX + r"(?:(?:public|private|protected|static|final|abstract)\s+)*?(?:class|interface|trait|enum)\s+(?P<name>[A-Za-z_][\w]*)"),
]
_PHP_CONST = [
    _rx(_DEF_CTX + r"const\s+(?P<name>[A-Za-z_][\w]*)\s*="),
]
_PHP_IMPORT = [
    _rx(r"(?:use|require(?:_once)?|include(?:_once)?)\s+[\"']?(?P<names>[^\"';]+)"),
]
PROFILE_PHP = _Profile(
    key="php",
    fn_patterns=_PHP_FN,
    type_patterns=_PHP_TYPE,
    const_patterns=_PHP_CONST,
    import_patterns=_PHP_IMPORT,
    line_comments=("//", "#"),
    block_comments=(("/*", "*/"),),
    string_quotes="'\"`",
    private_keywords=("private", "protected"),
    export_keywords=("public",),
)

PROFILES: dict[str, _Profile] = {
    "typescript": PROFILE_TS,
    "go": PROFILE_GO,
    "rust": PROFILE_RUST,
    "java": PROFILE_JAVA,
    "c": PROFILE_C,
    "ruby": PROFILE_RUBY,
    "php": PROFILE_PHP,
}

# extension (no dot, lower) -> profile key
EXT_PROFILE: dict[str, str] = {
    "ts": "typescript", "tsx": "typescript", "js": "typescript", "jsx": "typescript",
    "mjs": "typescript", "cjs": "typescript",
    "go": "go",
    "rs": "rust",
    "java": "java", "kt": "java", "kts": "java", "cs": "java", "scala": "java",
    "swift": "java", "dart": "java",
    "c": "c", "h": "c", "cc": "c", "cpp": "c", "cxx": "c", "hpp": "c", "hh": "c",
    "rb": "ruby",
    "php": "php",
}

GENERIC_EXTENSIONS: set[str] = set(EXT_PROFILE.keys())
ALL_INDEXABLE_EXTENSIONS: set[str] = GENERIC_EXTENSIONS | {"py"}


def profile_for_ext(ext: str) -> Optional[_Profile]:
    key = EXT_PROFILE.get(ext.lower().lstrip("."))
    return PROFILES.get(key) if key else None


# --------------------------------------------------------------------------
# Comment / string stripping
# --------------------------------------------------------------------------

def _strip_text(lines: list[str], prof: _Profile) -> list[str]:
    """Blank out comments and string literals so regexes never see them."""
    out: list[str] = []
    in_block = False
    block_end = ""
    for line in lines:
        res: list[str] = []
        i = 0
        n = len(line)
        while i < n:
            if in_block:
                if line[i : i + len(block_end)] == block_end:
                    in_block = False
                    i += len(block_end)
                    continue
                res.append(" ")
                i += 1
                continue
            started = False
            for bstart, bend in prof.block_comments:
                if line[i : i + len(bstart)] == bstart:
                    in_block = True
                    block_end = bend
                    i += len(bstart)
                    started = True
                    break
            if started:
                continue
            lc_hit = False
            for lc in prof.line_comments:
                if line[i : i + len(lc)] == lc:
                    lc_hit = True
                    break
            if lc_hit:
                break
            if line[i] in prof.string_quotes:
                q = line[i]
                res.append(" ")
                i += 1
                while i < n:
                    if line[i] == "\\":
                        res.append(" ")
                        res.append(" ")
                        i += 2
                        continue
                    if line[i] == q:
                        res.append(" ")
                        i += 1
                        break
                    res.append(" ")
                    i += 1
                continue
            res.append(line[i])
            i += 1
        out.append("".join(res))
    return out


# --------------------------------------------------------------------------
# Per-file index
# --------------------------------------------------------------------------

class GenericFileIndex:
    """Index of a single non-Python source file (zero-dependency scanner)."""

    def __init__(self, abspath: str, text: str, profile: _Profile) -> None:
        # Locations are exposed as file URIs, and POSIX refuses `as_uri()` on a
        # relative path, so the stored path is always absolute.
        self.abspath = str(Path(abspath).absolute())
        self.text = text
        self.lines = text.splitlines()
        self.profile = profile
        self.symbols: list[Symbol] = []
        self.defs: dict[str, list[tuple[int, int]]] = {}
        self.usages: dict[str, list[tuple[int, int]]] = {}
        self.imports: list[dict] = []
        self.hovers: dict[tuple[int, int], str] = {}
        self._def_meta: dict[str, dict] = {}
        self._build()

    # ---- construction ----------------------------------------------------
    def _build(self) -> None:
        prof = self.profile
        stripped = _strip_text(self.lines, prof)
        self._stripped = stripped  # reused by project-wide usage scan
        depths = self._brace_depths(stripped)
        for i, (raw, clean) in enumerate(zip(self.lines, stripped)):
            self._scan_line(i, raw, clean, depths[i] == 0)
        self._collect_usages(stripped)

    @staticmethod
    def _brace_depths(stripped: list[str]) -> list[int]:
        depths: list[int] = []
        depth = 0
        for line in stripped:
            depths.append(depth)
            for ch in line:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
        return depths

    def _scan_line(self, i: int, raw: str, clean: str, top: bool) -> None:
        prof = self.profile
        for pat in prof.import_patterns:
            for m in pat.finditer(clean):
                self._record_import(m, i, clean)
        # Capture every definition site on the line, not just the first. A single
        # line can declare several symbols (e.g. `class Foo { public void bar(){} }`
        # should yield both Foo and bar). When two patterns hit the same column we
        # keep the most specific one: type > constant > function.
        candidates = []
        for order, (kind, patterns) in enumerate(
            (
                ("class", prof.type_patterns),
                ("constant", prof.const_patterns),
                ("function", prof.fn_patterns),
            )
        ):
            for pat in patterns:
                for m in pat.finditer(clean):
                    if m.group("name"):
                        candidates.append((m.start("name"), order, kind, m))
        candidates.sort(key=lambda c: (c[0], c[1]))
        seen_cols: set[int] = set()
        for col, _order, kind, m in candidates:
            if col in seen_cols:
                continue
            seen_cols.add(col)
            self._record_def(m, i, raw, kind, top)

    def _record_def(self, m: "re.Match[str]", i: int, raw: str, kind: str, top: bool) -> None:
        name = m.group("name")
        col = m.start("name")
        self.symbols.append(Symbol(name=name, kind=kind, line=i, character=col, detail=""))
        self.defs.setdefault(name, []).append((i, col))
        is_public = top and self._export_ok(name, raw, self.profile)
        sig = raw.strip()[:120]
        self.hovers[(i, col)] = sig
        # ``scope`` tags module-level (top-level, inside no braces) vs nested
        # (inside a class/IIFE) so the diagram projector can dedupe and drop
        # methods, keeping only module-level definitions (KNOWN_ISSUES #8).
        self._def_meta[(name, i, col)] = {
            "is_public": is_public,
            "kind": kind,
            "signature": sig,
            "scope": "" if top else "nested",
        }

    def _record_import(self, m: "re.Match[str]", i: int, clean: str) -> None:
        alias = m.groupdict().get("alias")
        names = m.groupdict().get("names")
        if alias:
            self.imports.append({"alias": alias, "module": "", "line": i, "col": m.start("alias")})
        elif names:
            for nm in re.split(r"[,\s]+", names.strip()):
                nm = nm.strip().strip("{}")
                if nm:
                    col = clean.find(nm)
                    self.imports.append({"alias": nm, "module": "", "line": i, "col": max(col, 0)})

    def _collect_usages(self, stripped: list[str], names: Optional[set] = None) -> None:
        if names is None:
            names = set(self.defs) | {imp["alias"] for imp in self.imports}
        for name in names:
            if not name:
                continue
            pat = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
            for i, clean in enumerate(stripped):
                for mm in pat.finditer(clean):
                    self.usages.setdefault(name, []).append((i, mm.start()))

    def collect_usages_project_wide(self, project_symbols: set) -> None:
        """Re-scan for usages of *any* symbol defined anywhere in the project.

        Mirrors the tree-sitter provider: the base scan only tracks names defined
        or imported locally, so cross-module references without an explicit import
        are missed. See KNOWN_ISSUES #1.
        """
        names = set(self.defs) | {imp["alias"] for imp in self.imports} | project_symbols
        self.usages = {}
        # Scan the comment/string-stripped lines so references inside comments or
        # string literals are never counted (matches the base scan's precision).
        self._collect_usages(getattr(self, "_stripped", self.lines), names=names)

    @staticmethod
    def _export_ok(name: str, text: str, prof: _Profile) -> bool:
        if any(k in text for k in prof.private_keywords):
            return False
        if prof.hash_private and name.startswith("#"):
            return False
        if prof.capitalized_public:
            return bool(name) and name[0].isupper()
        if prof.export_required:
            return any(k in text for k in prof.export_keywords)
        if prof.export_keywords:
            return any(k in text for k in prof.export_keywords) or not name.startswith("_")
        return not name.startswith("_")

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

    # ---- queries --------------------------------------------------------
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
