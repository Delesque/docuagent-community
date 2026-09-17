"""Tests for the derived symbol_index (code-intel single source of truth).

Pure stdlib, real files, no subprocess. Verifies the metadata, search,
reconcile, module_symbols, cross-file goto and stats that make symbol_index
the SSOT backing editor navigation, agent tools and diagram projection.
"""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

# Tests run with PYTHONPATH=docuagent so `codeintel` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from codeintel.pyindex import PythonFileIndex  # noqa: E402
from codeintel.symbol_index import SymbolIndex  # noqa: E402


def _make_project(root: Path) -> None:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "models.py").write_text(
        textwrap.dedent(
            """
            CONSTANT = 42

            class Widget:
                def render(self):
                    return "x"

            def helper():
                return 1
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (pkg / "app.py").write_text(
        textwrap.dedent(
            """
            from pkg.models import Widget, helper

            def run():
                w = Widget()
                return w.render() + str(helper())
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def test_records_carry_metadata():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        idx = SymbolIndex(str(root))
        assert idx.records, "expected symbols to be indexed"
        for r in idx.records:
            assert len(r.source_hash) == 64
            assert r.last_seen > 0
            assert r.status == "active"

        widget = next(r for r in idx.records if r.name == "Widget")
        assert widget.kind == "class"
        assert widget.is_public is True
        assert widget.file == "pkg/models.py"

        const = next(r for r in idx.records if r.name == "CONSTANT")
        assert const.kind == "constant"
        assert const.is_public is True

        render = next(r for r in idx.records if r.name == "render")
        # method, nested in a class -> not module-level public
        assert render.kind == "function"
        assert render.is_public is False


def test_search_finds_symbols():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        idx = SymbolIndex(str(root))
        assert any(r.name == "Widget" for r in idx.search("Widget"))
        assert any(r.name == "helper" for r in idx.search("hel"))
        assert idx.search("") == []
        assert idx.search("zzz_no_match") == []


def test_module_symbols_returns_file_symbols():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        idx = SymbolIndex(str(root))
        recs = idx.module_symbols("pkg/models.py")
        names = {r.name for r in recs}
        assert {"Widget", "helper", "CONSTANT", "render"} <= names


def test_reconcile_reports_drift():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        idx = SymbolIndex(str(root))
        contracts = [
            {"name": "Widget", "file": "pkg/models.py"},
            {"name": "missing_fn", "file": "pkg/models.py"},
            {"name": "orphan_sym"},
        ]
        report = idx.reconcile(contracts)
        assert {r["name"] for r in report.stale} == {"missing_fn"}
        assert {r["name"] for r in report.orphan} == {"missing_fn", "orphan_sym"}
        # public symbols not present in the registry are flagged unregistered
        assert "Widget" not in {r["name"] for r in report.unregistered}
        assert "helper" in {r["name"] for r in report.unregistered}


def test_cross_file_goto_resolves_to_real_definition():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        idx = SymbolIndex(str(root))
        app_src = (root / "pkg" / "app.py").read_text(encoding="utf-8")
        lines = app_src.splitlines()
        target_line = next(i for i, ln in enumerate(lines) if "Widget()" in ln)
        col = lines[target_line].index("Widget")
        locs = idx.goto_definition(str(root / "pkg" / "app.py"), target_line, col)
        assert len(locs) == 1
        assert "models.py" in locs[0].uri
        assert locs[0].line == 2  # 0-based: blank line then `class Widget` is line 2


def test_stats_aggregates_project():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        idx = SymbolIndex(str(root))
        stats = idx.stats()
        assert stats["files"] >= 3  # __init__.py, models.py, app.py
        assert stats["symbols"] > 0
        assert stats["public_symbols"] > 0
        assert "class" in stats["by_kind"]


def test_python_file_index_records_export():
    src = "X = 1\n\nclass A:\n    def m(self):\n        return 1\n"
    fi = PythonFileIndex("demo.py", src)
    recs = fi.records("demo.py", "deadbeef", 1.0)
    kinds = {r.name: r.kind for r in recs}
    assert kinds.get("X") == "constant"
    assert kinds.get("A") == "class"
    assert kinds.get("m") == "function"
    assert next(r for r in recs if r.name == "A").is_public is True
    assert next(r for r in recs if r.name == "m").is_public is False


def _make_cross_file_project(root: Path) -> None:
    """A `shared` symbol defined at module level AND as a nested method, used in
    a separate module. Exercises cross-file reference aggregation + module-level
    goto disambiguation (KNOWN_ISSUES #1 / #2)."""
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "a.py").write_text(
        "def shared():\n    return 1\n\n\nclass Service:\n    def shared(self):\n        return 2\n",
        encoding="utf-8",
    )
    (pkg / "b.py").write_text(
        "from pkg.a import shared\n\n\ndef use():\n    return shared()\n",
        encoding="utf-8",
    )


def test_find_references_is_project_wide():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_cross_file_project(root)
        idx = SymbolIndex(str(root))
        b_src = (root / "pkg" / "b.py").read_text(encoding="utf-8")
        b_lines = b_src.splitlines()
        use_line = next(i for i, ln in enumerate(b_lines) if "shared()" in ln)
        col = b_lines[use_line].index("shared")
        locs = idx.find_references(str(root / "pkg" / "b.py"), use_line, col)
        # one definition at module level (a.py:0), one nested method (a.py:4), one
        # cross-module usage (b.py). All three must surface — not just b.py.
        assert len(locs) == 3
        files = {Path(l.uri).name for l in locs}
        assert files == {"a.py", "b.py"}
        # the module-level definition is present
        assert any(Path(l.uri).name == "a.py" and l.line == 0 for l in locs)


def test_global_defs_prefers_module_level_symbol():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_cross_file_project(root)
        idx = SymbolIndex(str(root))
        # top-level `def shared` (a.py:0) must win over the nested method (a.py:4)
        assert idx.global_defs["shared"][1] == 0


def test_goto_disambiguates_to_module_level():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_cross_file_project(root)
        idx = SymbolIndex(str(root))
        b_src = (root / "pkg" / "b.py").read_text(encoding="utf-8")
        b_lines = b_src.splitlines()
        use_line = next(i for i, ln in enumerate(b_lines) if "shared()" in ln)
        col = b_lines[use_line].index("shared")
        locs = idx.goto_definition(str(root / "pkg" / "b.py"), use_line, col)
        assert len(locs) == 1
        assert Path(locs[0].uri).name == "a.py"
        assert locs[0].line == 0  # module-level def, not the nested method


def _make_js_namespace_project(root: Path) -> None:
    """A vanilla-JS project with module-level `var`/`const` namespaces shared across
    files without import statements (mimics the snake pilot). Exercises cross-file
    goto for `variable` kind symbols (KNOWN_ISSUES #3) and hover-on-usage (#4)."""
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "core.js").write_text(
        "var DIRS = { up: [0, -1] };\nconst SnakeCore = {};\nfunction step() { return 1; }\n",
        encoding="utf-8",
    )
    (pkg / "game.js").write_text(
        "DIRS; SnakeCore; step();\n",
        encoding="utf-8",
    )


def test_global_defs_includes_module_level_variables():
    # P1 #3: `var`/`const` namespaces must be cross-file goto targets, not just
    # function/class/constant. Under the tree-sitter provider `var` maps to the
    # "variable" kind, which previously dropped out of global_defs.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_js_namespace_project(root)
        idx = SymbolIndex(str(root))
        for nm in ("DIRS", "SnakeCore", "step"):
            assert nm in idx.global_defs, f"{nm} should be goto-able across files"
        # cross-file goto from game.js usage back to core.js definition
        g_src = (root / "pkg" / "game.js").read_text(encoding="utf-8")
        g_lines = g_src.splitlines()
        ln = 0
        col = g_lines[ln].index("DIRS")
        locs = idx.goto_definition(str(root / "pkg" / "game.js"), ln, col)
        assert locs and "core.js" in locs[0].uri


def test_hover_cross_file_resolves_to_definition():
    # P1 #4: hovering a usage point shows the target definition's hover text even
    # when the definition lives in another file.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_js_namespace_project(root)
        idx = SymbolIndex(str(root))
        g_src = (root / "pkg" / "game.js").read_text(encoding="utf-8")
        g_lines = g_src.splitlines()
        ln = 0
        col = g_lines[ln].index("step")
        text = idx.hover(str(root / "pkg" / "game.js"), ln, col)
        assert text is not None and "step" in text


def test_project_usages_only_external_public_symbols():
    # P0.5 / KNOWN_ISSUES #11: project-wide usage collection must track *external*
    # public symbols only, so a symbol defined and used purely within one file does
    # not leak into the cross-file reference graph as noise.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / "a.js").write_text(
            "function shared() { return 1; }\n", encoding="utf-8"
        )
        (root / "b.js").write_text(
            "shared();\nfunction localOnly() { return 2; }\nlocalOnly();\n",
            encoding="utf-8",
        )
        idx = SymbolIndex(str(root))
        # cross-file reference captured
        assert "shared" in idx.project_usages
        # localOnly is defined and used only inside b.js -> not a cross-file edge
        assert "localOnly" not in idx.project_usages


# ---- file ignore list (KNOWN_ISSUES #5) ----------------------------------
def _write_ignore(root: Path, patterns: list[str]) -> None:
    p = root / ".docuagent" / "codeintel.ignore"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("\n".join(patterns) + "\n", encoding="utf-8")


def test_ignore_file_excludes_matching_files():
    # A noise test file must not leak into index / reconcile / projection.
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        (root / "test_core.py").write_text("def core():\n    return 1\n", encoding="utf-8")
        _write_ignore(root, ["**/test_*.py"])
        idx = SymbolIndex(str(root))
        assert not any(r.file == "test_core.py" for r in idx.records)
        assert "test_core.py" not in idx.files
        assert idx.is_ignored("test_core.py")
        assert not idx.is_ignored("pkg/models.py")


def test_session_ignore_add_effective_immediately():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        (root / "scratch.py").write_text("def scratch():\n    return 1\n", encoding="utf-8")
        idx = SymbolIndex(str(root))
        assert any(r.file == "scratch.py" for r in idx.records)
        # Agent adds a session-only pattern; it takes effect immediately (rebuild).
        status = idx.add_ignore_pattern("**/scratch.py")
        assert not any(r.file == "scratch.py" for r in idx.records)
        assert "**/scratch.py" in status["session"]
        # Not persisted to disk.
        assert not (root / ".docuagent" / "codeintel.ignore").exists()


def test_ignore_propose_then_approve_persists():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        (root / "gen.py").write_text("def gen():\n    return 1\n", encoding="utf-8")
        idx = SymbolIndex(str(root))
        # Propose: active now (session) + queued for approval.
        idx.propose_ignore_pattern("**/gen.py")
        assert idx.is_ignored("gen.py")
        assert idx.pending_ignore() == ["**/gen.py"]
        # Main agent approves -> persisted to the human config file.
        idx.approve_ignore("**/gen.py")
        ignore_file = root / ".docuagent" / "codeintel.ignore"
        assert ignore_file.is_file()
        assert "**/gen.py" in ignore_file.read_text(encoding="utf-8")
        assert idx.pending_ignore() == []
        # A fresh index reads the persisted file.
        idx2 = SymbolIndex(str(root))
        assert idx2.is_ignored("gen.py")


def test_ignore_list_merges_config_and_session():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        _write_ignore(root, ["node_modules"])
        idx = SymbolIndex(str(root))
        idx.add_ignore_pattern("**/scratch.py")
        status = idx.list_ignore()
        assert status["persistent"] == ["node_modules"]
        assert "**/scratch.py" in status["session"]
        assert "node_modules" in status["effective"]
        assert "**/scratch.py" in status["effective"]


def test_ignore_reject_drops_proposal():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        (root / "x.py").write_text("def x():\n    return 1\n", encoding="utf-8")
        idx = SymbolIndex(str(root))
        idx.propose_ignore_pattern("**/x.py")
        assert idx.is_ignored("x.py")
        idx.reject_ignore("**/x.py")
        assert not idx.is_ignored("x.py")
        assert idx.pending_ignore() == []
        assert any(r.file == "x.py" for r in idx.records)


def test_ignore_bare_dir_name_matches_any_depth():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _make_project(root)
        (root / "lib").mkdir(parents=True)
        (root / "lib" / "impl.py").write_text("def impl():\n    return 1\n", encoding="utf-8")
        _write_ignore(root, ["lib"])
        idx = SymbolIndex(str(root))
        assert idx.is_ignored("lib/impl.py")
        assert not any(r.file == "lib/impl.py" for r in idx.records)


