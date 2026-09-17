"""Tests for the zero-dependency Python indexer (``codeintel.pyindex``).

These run entirely on the standard library (``ast``) with real fixture files,
so they verify the out-of-the-box code intelligence without spawning any
language-server subprocess.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from codeintel.pyindex import PythonFileIndex, PythonProjectIndex


MODULE_A = '''"""Module a."""

from .b import Helper

NAME = "x"


class Widget:
    def render(self):
        return Helper().go()


def build():
    w = Widget()
    return w.render()
'''


MODULE_B = '''"""Module b."""


class Helper:
    def go(self):
        return 1
'''


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "a.py").write_text(MODULE_A, encoding="utf-8")
    (tmp_path / "b.py").write_text(MODULE_B, encoding="utf-8")
    return tmp_path


def test_document_symbols(project: Path) -> None:
    idx = PythonProjectIndex(str(project))
    syms = {s.name: s.kind for s in idx.document_symbols(str(project / "a.py"))}
    assert syms.get("Widget") == "class"
    assert syms.get("build") == "function"
    assert "Helper" not in syms  # imported, not defined here


def test_goto_local_function(project: Path) -> None:
    idx = PythonProjectIndex(str(project))
    # Cursor on the `build` call inside `build`? Use the `Widget()` usage.
    # Find the line/col of `Widget` usage in `build`.
    text = (project / "a.py").read_text(encoding="utf-8")
    lines = text.splitlines()
    # line with "    w = Widget()" -> use column of "Widget"
    target = next(i for i, ln in enumerate(lines) if "w = Widget()" in ln)
    col = lines[target].index("Widget")
    locs = idx.goto_definition(str(project / "a.py"), target, col)
    assert len(locs) == 1
    # Definition is the `class Widget:` line (0-based line 7 in MODULE_A).
    assert "a.py" in locs[0].uri
    assert locs[0].line == 7


def test_goto_cross_file_top_level(project: Path) -> None:
    idx = PythonProjectIndex(str(project))
    text = (project / "a.py").read_text(encoding="utf-8")
    lines = text.splitlines()
    target = next(i for i, ln in enumerate(lines) if "Helper().go()" in ln)
    col = lines[target].index("Helper")
    locs = idx.goto_definition(str(project / "a.py"), target, col)
    assert len(locs) == 1
    assert "b.py" in locs[0].uri  # defined in module b


def test_find_references_in_file(project: Path) -> None:
    idx = PythonProjectIndex(str(project))
    text = (project / "a.py").read_text(encoding="utf-8")
    lines = text.splitlines()
    def_line = next(i for i, ln in enumerate(lines) if ln.startswith("def build"))
    col = lines[def_line].index("build") + 1  # inside the name
    locs = idx.find_references(str(project / "a.py"), def_line, col)
    # definition line + the `build()` call site
    uris = {l.uri for l in locs}
    assert any("a.py" in u for u in uris)
    assert len(locs) >= 1


def test_hover_returns_signature(project: Path) -> None:
    idx = PythonProjectIndex(str(project))
    text = (project / "a.py").read_text(encoding="utf-8")
    lines = text.splitlines()
    def_line = next(i for i, ln in enumerate(lines) if ln.startswith("def build"))
    col = lines[def_line].index("build")
    hover = idx.hover(str(project / "a.py"), def_line, col)
    assert hover is not None
    assert "def build" in hover


def test_skips_cache_dirs(tmp_path: Path) -> None:
    # A python file inside a venv-like dir must NOT be indexed.
    venv = tmp_path / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "dep.py").write_text("class ShouldBeIgnored: pass\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("class Real: pass\n", encoding="utf-8")
    idx = PythonProjectIndex(str(tmp_path))
    assert "Real" in idx.global_defs
    assert "ShouldBeIgnored" not in idx.global_defs


def test_no_python_project(tmp_path: Path) -> None:
    idx = PythonProjectIndex(str(tmp_path))
    assert idx.files == {}
    assert idx.goto_definition("missing.py", 0, 0) == []


def test_single_file_index_handles_syntax_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.py"
    bad.write_text("def (((\n", encoding="utf-8")
    fi = PythonFileIndex(str(bad), bad.read_text(encoding="utf-8"))
    assert fi.tree is None
    assert fi.document_symbols() == []
