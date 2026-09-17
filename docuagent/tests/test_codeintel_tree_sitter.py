"""Tests for the optional tree-sitter provider (ROADMAP §3 step5 candidate / HANDOFF §7.3).

The native ``tree_sitter`` / ``tree_sitter_languages`` packages are NOT required
to run these tests: availability is fail-closed, and the provider logic is
exercised through a lightweight fake parser tree so the registry wiring, symbol
extraction and graceful degrade can all be verified on a zero-dependency trunk.
"""

import os
import textwrap
from pathlib import Path

import pytest

from codeintel import symbol_index, tree_sitter_index
from codeintel.symbol_index import SymbolIndex, _register_default_scanners
from codeintel.service import CodeIntelService


# ---- fake tree-sitter shim (no native dependency) ---------------------------

class FakeNode:
    def __init__(self, type, text="", children=None, fields=None, start=None):
        self.type = type
        self._text = text
        self.children = children or []
        self._fields = fields or {}
        self.start_point = start or (0, 0)
        self.end_point = start or (0, 0)

    @property
    def named_children(self):
        return [c for c in self.children if isinstance(c, FakeNode)]

    @property
    def text(self):
        if isinstance(self._text, (bytes, bytearray)):
            return self._text
        return self._text

    def child_by_field_name(self, name):
        return self._fields.get(name)


class FakeTree:
    def __init__(self, root):
        self.root_node = root


class FakeParser:
    def __init__(self, root):
        self._root = root

    def parse(self, data):
        return FakeTree(self._root)


class FakeTSL:
    def __init__(self, root):
        self._root = root

    def get_parser(self, lang):
        return FakeParser(self._root)


# A tiny "program" with one function declaration `myFunc` (name at (0, 9)).
_NAME = FakeNode("identifier", text="myFunc", start=(0, 9))
_FUNC = FakeNode(
    "function_declaration",
    text="function myFunc() {}",
    children=[_NAME],
    fields={"name": _NAME},
    start=(0, 0),
)
# A genuine call site `myFunc()` (identifier node at (2, 0)) so the AST walk can
# find a real reference (not just the definition).
_CALL_NAME = FakeNode("identifier", text="myFunc", start=(2, 0))
_CALL = FakeNode(
    "call_expression",
    text="myFunc()",
    children=[_CALL_NAME],
    start=(2, 0),
)
_ROOT = FakeNode("program", children=[_FUNC, _CALL])


@pytest.fixture(autouse=True)
def _reset_registry():
    """Restore the default (generic) scanner registry after each test so a
    tree-sitter override registered during one test never leaks into another."""
    yield
    _register_default_scanners()


def _enable_with_fake_modules(monkeypatch):
    monkeypatch.setenv(tree_sitter_index.ENV_FLAG, "1")
    monkeypatch.setattr(
        tree_sitter_index, "_tree_sitter_modules", lambda: (object(), FakeTSL(_ROOT))
    )


# ---- availability is fail-closed -------------------------------------------

def test_unavailable_without_env_flag_when_packages_absent(monkeypatch):
    # Fail-closed: with no flag AND no importable packages, the provider stays off.
    monkeypatch.delenv(tree_sitter_index.ENV_FLAG, raising=False)
    monkeypatch.setattr(tree_sitter_index, "_tree_sitter_modules", lambda: None)
    assert tree_sitter_index.is_enabled() is False
    assert tree_sitter_index.is_available() is False


def test_auto_enabled_when_packages_present_and_flag_unset(monkeypatch):
    # Auto-detect: flag unset but the optional packages import -> provider on.
    monkeypatch.delenv(tree_sitter_index.ENV_FLAG, raising=False)
    monkeypatch.setattr(
        tree_sitter_index, "_tree_sitter_modules", lambda: (object(), object())
    )
    assert tree_sitter_index.is_enabled() is True
    assert tree_sitter_index.is_available() is True


def test_explicit_zero_forces_off_even_when_packages_present(monkeypatch):
    monkeypatch.setenv(tree_sitter_index.ENV_FLAG, "0")
    monkeypatch.setattr(
        tree_sitter_index, "_tree_sitter_modules", lambda: (object(), object())
    )
    assert tree_sitter_index.is_enabled() is False
    assert tree_sitter_index.is_available() is False


def test_unavailable_when_package_missing(monkeypatch):
    monkeypatch.setenv(tree_sitter_index.ENV_FLAG, "1")
    # Flag is set but the optional packages cannot be imported.
    monkeypatch.setattr(tree_sitter_index, "_tree_sitter_modules", lambda: None)
    assert tree_sitter_index.is_available() is False


def test_available_with_env_and_fake_modules(monkeypatch):
    _enable_with_fake_modules(monkeypatch)
    assert tree_sitter_index.is_available() is True


# ---- TreeSitterFileIndex extracts symbols from a parse tree -----------------

def test_file_index_extracts_defs_and_refs():
    text = "function myFunc() {}\n\nmyFunc();\n"
    idx = tree_sitter_index.TreeSitterFileIndex("C:/sample.ts", text, "typescript", parser=FakeParser(_ROOT))

    names = {s.name for s in idx.symbols}
    assert "myFunc" in names

    # goto: cursor on the definition name resolves to the def site.
    locs = idx.goto_definition(0, 9)
    assert len(locs) == 1
    assert locs[0].line == 0 and locs[0].character == 9

    # references: definition + the later usage.
    refs = idx.find_references(0, 9)
    assert len(refs) == 2

    # hover returns the captured signature.
    assert idx.hover(0, 9) == "function myFunc() {}"

    # records carry the SymbolRecord shape the indexer expects.
    recs = idx.records("sample.ts", "deadbeef", 1.0)
    assert any(r.name == "myFunc" and r.is_public for r in recs)


# Regression: compound declarations (TS `const`/`let`) put the identifier on a
# `variable_declarator` child, not on the declaration node itself. The naive
# _name_of() lookup misses these, so _extract_defs must walk the declarators.
def test_extracts_compound_const_declarations():
    pi_name = FakeNode("identifier", text="PI", start=(0, 13))
    pi_decl = FakeNode("variable_declarator", fields={"name": pi_name}, start=(0, 13))
    lex = FakeNode("lexical_declaration", children=[pi_decl], text="const PI = 3", start=(0, 6))
    root = FakeNode("program", children=[lex])

    idx = tree_sitter_index.TreeSitterFileIndex(
        "C:/sample.ts", "export const PI = 3\n", "typescript", parser=FakeParser(root)
    )

    names = {s.name for s in idx.symbols}
    assert "PI" in names  # previously missed -> the blind spot under test
    recs = idx.records("sample.ts", "h", 1.0)
    pi = next(r for r in recs if r.name == "PI")
    assert pi.kind == "constant"
    assert pi.is_public is True


# Regression: references are collected from AST identifier nodes, so a name that
# merely *appears* inside a comment or a string literal is NOT reported as a
# reference (the core precision advantage over the regex scanner).
def test_references_ignore_names_in_comments_and_strings():
    name_def = FakeNode("identifier", text="myFunc", start=(0, 9))
    func = FakeNode(
        "function_declaration",
        text="function myFunc() {}",
        children=[name_def],
        fields={"name": name_def},
        start=(0, 0),
    )
    # A real call site (identifier node) -> must be counted.
    name_use = FakeNode("identifier", text="myFunc", start=(2, 0))
    call = FakeNode("call_expression", text="myFunc()", children=[name_use], start=(2, 0))
    # The name reappears inside a comment and inside a string: opaque subtrees,
    # so the identifier children inside them must be skipped.
    comment_id = FakeNode("identifier", text="myFunc", start=(3, 5))
    comment = FakeNode("comment", text="// see myFunc", children=[comment_id], start=(3, 0))
    string_id = FakeNode("identifier", text="myFunc", start=(4, 7))
    string = FakeNode("string", text='"call myFunc"', children=[string_id], start=(4, 0))

    root = FakeNode("program", children=[func, call, comment, string])
    idx = tree_sitter_index.TreeSitterFileIndex(
        "C:/sample.ts", "function myFunc() {}\n\nmyFunc();\n// see myFunc\n\"call myFunc\"\n",
        "typescript", parser=FakeParser(root),
    )

    refs = idx.find_references(0, 9)
    ref_positions = {(r.line, r.character) for r in refs}
    # definition (0,9) + real call (2,0); NOT the comment (3,5) or string (4,7).
    assert ref_positions == {(0, 9), (2, 0)}


# ---- registry integration: SymbolIndex uses tree-sitter when enabled -------

def test_symbol_index_uses_tree_sitter_when_available(monkeypatch, tmp_path):
    _enable_with_fake_modules(monkeypatch)
    (tmp_path / "sample.ts").write_text("function myFunc() {}\n", encoding="utf-8")

    idx = SymbolIndex(str(tmp_path))
    names = {r.name for r in idx.records}
    assert "myFunc" in names  # produced by tree-sitter, not the generic scanner


def test_capabilities_advertise_tree_sitter(monkeypatch, tmp_path):
    _enable_with_fake_modules(monkeypatch)
    (tmp_path / "sample.ts").write_text("function myFunc() {}\n", encoding="utf-8")

    svc = CodeIntelService()
    caps = svc.capabilities(str(tmp_path))
    assert caps.available is True
    assert "tree-sitter" in caps.provider.split("+")


# ---- default path (flag off) still uses the generic scanner ----------------

def test_symbol_index_defaults_to_generic_without_flag(tmp_path):
    (tmp_path / "sample.ts").write_text(
        textwrap.dedent(
            """
            function foo() {
              return 1;
            }
            """
        ),
        encoding="utf-8",
    )
    idx = SymbolIndex(str(tmp_path))
    names = {r.name for r in idx.records}
    assert "foo" in names  # generic scanner still works with no flag


# ---- graceful degrade: a per-file parse failure must not abort the index ---

def test_per_file_parse_failure_falls_back_to_generic(monkeypatch, tmp_path):
    _enable_with_fake_modules(monkeypatch)

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("grammar exploded")

    monkeypatch.setattr(tree_sitter_index, "TreeSitterFileIndex", _Boom)
    (tmp_path / "sample.ts").write_text(
        textwrap.dedent(
            """
            function foo() {
              return 1;
            }
            """
        ),
        encoding="utf-8",
    )

    # No exception escapes; the file is still indexed, now via the generic scanner.
    idx = SymbolIndex(str(tmp_path))
    names = {r.name for r in idx.records}
    assert "foo" in names
    assert str(tmp_path / "sample.ts") in idx.files
