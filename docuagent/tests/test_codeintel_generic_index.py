"""Tests for the zero-dependency multi-language symbol indexer (P4).

Pure stdlib, real files, no subprocess / no LSP spawn. Verifies that
``generic_index.GenericFileIndex`` extracts function / class / constant symbols
and references for non-Python languages, that ``SymbolIndex`` merges Python and
generic results into one SSOT, and that ``CodeIntelService.capabilities`` reports
the ``generic-ast`` provider only when a non-Python indexable file is present.

Mirrors the fake/stdlib fixture style of test_codeintel_symbol_index.py and
test_codeintel_pyindex.py.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

# Tests run with PYTHONPATH=docuagent so `codeintel` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codeintel.generic_index import (  # noqa: E402
    GenericFileIndex,
    PROFILES,
    profile_for_ext,
)
from codeintel.service import CodeIntelService  # noqa: E402
from codeintel.symbol_index import SymbolIndex  # noqa: E402


def _idx(path: str, text: str, ext: str) -> GenericFileIndex:
    prof = profile_for_ext(ext)
    assert prof is not None, f"no profile for .{ext}"
    # SymbolIndex always passes an absolute path; the per-file scanner calls
    # Path(abspath).as_uri(), which rejects relative paths.
    abspath = str(Path(path).absolute())
    return GenericFileIndex(abspath, text, prof)


def _records_by_name(fi: GenericFileIndex) -> dict:
    """Records carry is_public (Symbol objects do not)."""
    return {r.name: r for r in fi.records("mod", "deadbeef", 1.0)}


# ---------------------------------------------------------------------------
# Per-language definition extraction
# ---------------------------------------------------------------------------

def test_ts_extracts_function_constant_class():
    src = textwrap.dedent(
        """
        export function add(a, b) {
            return a + b;
        }
        const PI = 3.14;
        export class Calc {
            sum() { return 1; }
        }
        """
    ).strip() + "\n"
    fi = _idx("main.ts", src, "ts")
    by_name = _records_by_name(fi)
    assert "add" in by_name and by_name["add"].kind == "function"
    assert by_name["add"].is_public is True
    assert "PI" in by_name and by_name["PI"].kind == "constant"
    assert "Calc" in by_name and by_name["Calc"].kind == "class"
    assert by_name["Calc"].is_public is True
    # `sum` is a method inside the class braces -> not module-level public
    assert "sum" in by_name and by_name["sum"].is_public is False


def test_go_extracts_func_and_publicity():
    src = textwrap.dedent(
        """
        package main

        func Foo() {}

        func bar() {}

        func main() {
            Foo()
        }
        """
    ).strip() + "\n"
    fi = _idx("main.go", src, "go")
    by_name = _records_by_name(fi)
    assert "Foo" in by_name and by_name["Foo"].kind == "function"
    assert by_name["Foo"].is_public is True  # Go: capitalized exports
    assert "bar" in by_name and by_name["bar"].is_public is False
    assert "main" in by_name


def test_rust_extracts_pub_vs_private():
    src = textwrap.dedent(
        """
        pub fn hello() {}

        fn world() {}

        pub struct Point { x: i32 }
        """
    ).strip() + "\n"
    fi = _idx("lib.rs", src, "rs")
    by_name = _records_by_name(fi)
    assert by_name["hello"].kind == "function" and by_name["hello"].is_public is True
    assert by_name["world"].is_public is False  # no `pub` -> not exported
    assert by_name["Point"].kind == "class" and by_name["Point"].is_public is True


def test_java_extracts_class_and_method_on_one_line():
    # Both the class and the method declared on the same line must be captured
    # (regression: the old scanner returned after the first fn match).
    src = "public class Foo { public void bar() {} }\n"
    fi = _idx("Foo.java", src, "java")
    names = {s.name for s in fi.symbols}
    assert "Foo" in names, "class Foo must be extracted"
    assert "bar" in names, "method bar must be extracted"
    kinds = {s.name: s.kind for s in fi.symbols}
    assert kinds["Foo"] == "class"
    assert kinds["bar"] == "function"


def test_c_extracts_func_struct_and_define():
    src = textwrap.dedent(
        """
        int add(int a, int b) {
            return a + b;
        }
        struct Point { int x; int y; };
        #define MAX 100
        """
    ).strip() + "\n"
    fi = _idx("lib.c", src, "c")
    by_name = {s.name: s for s in fi.symbols}
    assert by_name["add"].kind == "function"
    assert by_name["Point"].kind == "class"
    assert by_name["MAX"].kind == "constant"


# ---------------------------------------------------------------------------
# References / identifier resolution
# ---------------------------------------------------------------------------

def test_generic_find_references_returns_def_and_usage():
    src = textwrap.dedent(
        """
        package main

        func main() {
            foo()
        }

        func foo() {
        }
        """
    ).strip() + "\n"
    fi = _idx("main.go", src, "go")
    lines = src.splitlines()
    call_line = next(i for i, ln in enumerate(lines) if "foo()" in ln)
    col = lines[call_line].index("foo")
    assert fi._identifier_at(call_line, col) == "foo"
    refs = fi.find_references(call_line, col)
    # definition site + the call site
    assert len(refs) == 2
    lines_found = {r.line for r in refs}
    assert call_line in lines_found
    assert any(r.line != call_line for r in refs)


def test_generic_identifier_at_skips_punctuation():
    src = "const PI = 3.14;\n"
    fi = _idx("c.ts", src, "ts")
    assert fi._identifier_at(0, 6) == "PI"  # col 6 lands on 'P'


# ---------------------------------------------------------------------------
# SymbolIndex multilingual merge (py + ts)
# ---------------------------------------------------------------------------

def _make_mixed_project(root: Path) -> None:
    (root / "models.py").write_text(
        textwrap.dedent(
            """
            class Widget:
                def render(self):
                    return 1
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (root / "app.ts").write_text(
        textwrap.dedent(
            """
            export function bootstrap() {
                return 42;
            }
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_symbol_index_merges_python_and_typescript():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_mixed_project(root)
        idx = SymbolIndex(str(root))
        names = {r.name for r in idx.records}
        assert "Widget" in names  # from the .py file
        assert "bootstrap" in names  # from the .ts file
        # global_defs should span both languages
        assert "Widget" in idx.global_defs
        assert "bootstrap" in idx.global_defs
        stats = idx.stats()
        assert stats["files"] >= 2
        assert stats["public_symbols"] >= 2


def test_symbol_index_search_spans_languages():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_mixed_project(root)
        idx = SymbolIndex(str(root))
        hits = idx.search("boot")
        assert any(r.name == "bootstrap" for r in hits)


def test_symbol_index_goto_resolves_generic_symbol():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_mixed_project(root)
        idx = SymbolIndex(str(root))
        src = (root / "app.ts").read_text(encoding="utf-8")
        lines = src.splitlines()
        call_line = next(i for i, ln in enumerate(lines) if "bootstrap()" in ln)
        if call_line < 0:
            call_line = 1  # fallback: the definition line itself
        col = lines[call_line].index("bootstrap") if "bootstrap()" in lines[call_line] else 0
        locs = idx.goto_definition(str(root / "app.ts"), call_line, col)
        assert len(locs) == 1
        assert "app.ts" in locs[0].uri


# ---------------------------------------------------------------------------
# Service capabilities: generic-ast only for non-Python indexable files
# ---------------------------------------------------------------------------

def test_capabilities_reports_generic_ast_for_go_project(monkeypatch):
    # Pin the optional tree-sitter provider OFF so the generic-ast path is
    # exercised (tree-sitter auto-detects when its packages are installed, in
    # which case it supersedes generic-ast as the reported provider).
    monkeypatch.setenv("DOCUAGENT_CODE_INTEL_TREE_SITTER", "0")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "main.go").write_text(
            "package main\n\nfunc main() {}\n", encoding="utf-8"
        )
        svc = CodeIntelService()
        caps = svc.capabilities(str(root))
        assert caps.available is True
        assert "generic-ast" in caps.provider
        assert "go" in caps.languages


def test_capabilities_does_not_report_generic_ast_for_pure_python():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "models.py").write_text(
            "class Widget:\n    def render(self):\n        return 1\n",
            encoding="utf-8",
        )
        svc = CodeIntelService()
        caps = svc.capabilities(str(root))
        assert caps.available is True
        assert "python-ast" in caps.provider
        # The pure-Python project must NOT advertise the generic-ast provider.
        assert "generic-ast" not in caps.provider


def test_capabilities_reports_both_providers_for_mixed_project(monkeypatch):
    # Pin tree-sitter OFF so the generic-ast path is exercised alongside python-ast.
    monkeypatch.setenv("DOCUAGENT_CODE_INTEL_TREE_SITTER", "0")
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "models.py").write_text("X = 1\n", encoding="utf-8")
        (root / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
        svc = CodeIntelService()
        caps = svc.capabilities(str(root))
        assert "python-ast" in caps.provider
        assert "generic-ast" in caps.provider
        assert "go" in caps.languages
        assert "python" in caps.languages
