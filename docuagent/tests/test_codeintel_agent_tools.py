"""Tests for the P2 code-intel sub-agent tool bridge.

Verifies that code intelligence is exposed to sub-agents as four optional,
capability-gated tools (code_goto / code_references / code_search / code_hover)
registered into agent_tools' external registry WITHOUT agent_tools importing
codeintel. Also checks the coordinate convention (1-based in/out) and the
explicit opt-in gate (DOCUAGENT_CODE_INTEL).
"""

from __future__ import annotations

import os
import sys
import tempfile
import textwrap
from pathlib import Path

# Tests run with PYTHONPATH=docuagent so `codeintel` / `agent_tools` are importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capabilities import CODE_INTEL, CODE_INTEL_ENV  # noqa: E402

import agent_tools  # noqa: E402
from codeintel import agent_bridge  # noqa: E402


CALC = textwrap.dedent(
    """
    def add(a, b):
        return a + b

    def main():
        return add(1, 2)
    """
).strip() + "\n"


def _make_project(root: Path) -> Path:
    pkg = root / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "calc.py").write_text(CALC, encoding="utf-8")
    return pkg / "calc.py"


def setup_module(module) -> None:  # noqa: ANN001
    # Explicitly opt in so the env-gate behavior is deterministic for these tests,
    # and ensure registration regardless of import-time env state.
    os.environ[CODE_INTEL_ENV] = "1"
    agent_bridge.register()


def test_tools_registered_once_each():
    for name in ("code_goto", "code_references", "code_search", "code_hover"):
        assert name in agent_tools.EXTERNAL_TOOL_HANDLERS
        assert agent_tools.EXTERNAL_TOOL_CAPABILITIES.get(name) == CODE_INTEL
    # Idempotent: a second register call must not duplicate descriptions.
    before = len(agent_tools.EXTERNAL_TOOL_DESCRIPTIONS)
    agent_bridge.register()
    assert len(agent_tools.EXTERNAL_TOOL_DESCRIPTIONS) == before


def test_register_is_opt_in_only():
    # Without the capability, the tools are invisible to the sub-agent's tool list
    # and no prompt addendum is produced.
    ctx_off = {"capabilities": ["read_project", "read_architecture"]}
    assert agent_tools._visible_external_tools(ctx_off) == []
    assert agent_tools._external_prompt_addendum(ctx_off) == ""
    # With the capability they appear.
    ctx_on = {"capabilities": ["read_project", CODE_INTEL]}
    names = {d["name"] for d in agent_tools._visible_external_tools(ctx_on)}
    assert names == {
        "code_goto",
        "code_references",
        "code_search",
        "code_hover",
        "code_ignore_list",
        "code_ignore_add",
        "code_ignore_propose",
        "code_ignore_pending",
        "code_ignore_approve",
        "code_ignore_reject",
    }
    assert agent_tools._external_prompt_addendum(ctx_on)


def test_build_agent_context_grants_capability_only_when_enabled():
    from agents import build_agent_context

    arch = {"modules": [{"id": "m1", "path": "pkg"}]}
    task = {"module_id": "m1", "target_files": ["pkg/calc.py"], "verification": [], "ui_context": None}

    os.environ.pop(CODE_INTEL_ENV, None)
    ctx_off = build_agent_context(Path(tempfile.mkdtemp()), "m1", arch, task)
    assert CODE_INTEL not in ctx_off["capabilities"]

    os.environ[CODE_INTEL_ENV] = "1"
    ctx_on = build_agent_context(Path(tempfile.mkdtemp()), "m1", arch, task)
    assert CODE_INTEL in ctx_on["capabilities"]


def test_code_goto_returns_definition_with_1based_coords():
    root = Path(tempfile.mkdtemp())
    calc = _make_project(root)
    handler = agent_tools.EXTERNAL_TOOL_HANDLERS["code_goto"]
    # Cursor on the `add` usage: line 5 (1-based), `add` starts at 0-based col 11.
    result = handler(root, "code_goto", {"path": "pkg/calc.py", "line": 5, "character": 12}, {})
    assert result["found"] is True
    assert result["definitions"] == [
        {"file": "pkg/calc.py", "line": 1, "character": 5}
    ]


def test_code_references_finds_def_and_usage():
    root = Path(tempfile.mkdtemp())
    _make_project(root)
    handler = agent_tools.EXTERNAL_TOOL_HANDLERS["code_references"]
    result = handler(root, "code_references", {"path": "pkg/calc.py", "line": 5, "character": 12}, {})
    assert result["found"] is True
    lines = {r["line"] for r in result["references"]}
    # definition (line 1) + usage in main() (line 5)
    assert lines == {1, 5}
    assert result["count"] == 2


def test_code_search_fuzzy_matches_symbol():
    root = Path(tempfile.mkdtemp())
    _make_project(root)
    handler = agent_tools.EXTERNAL_TOOL_HANDLERS["code_search"]
    result = handler(root, "code_search", {"term": "add", "limit": 10}, {})
    assert result["count"] >= 1
    sym = next(s for s in result["symbols"] if s["name"] == "add")
    assert sym["file"] == "pkg/calc.py"
    assert sym["line"] == 1
    assert sym["kind"] == "function"


def test_code_hover_returns_signature():
    root = Path(tempfile.mkdtemp())
    _make_project(root)
    handler = agent_tools.EXTERNAL_TOOL_HANDLERS["code_hover"]
    result = handler(root, "code_hover", {"path": "pkg/calc.py", "line": 5, "character": 12}, {})
    assert result["found"] is True
    assert result["text"] and "add" in result["text"]


def test_execute_tool_enforces_capability():
    from core import WorkspaceError

    root = Path(tempfile.mkdtemp())
    _make_project(root)
    task = {"module_id": "m1", "target_files": ["pkg/calc.py"], "verification": [], "ui_context": None}
    allowed = {"capabilities": [CODE_INTEL, "read_project"]}
    denied = {"capabilities": ["read_project"]}

    # Allowed context -> delegated to the handler, returns a real result.
    ok = agent_tools.execute_tool(
        root, task, {"tool": "code_goto", "args": {"path": "pkg/calc.py", "line": 5, "character": 12}},
        agent_context=allowed,
    )
    assert ok["found"] is True

    # Denied context -> fail closed with a capability error.
    try:
        agent_tools.execute_tool(
            root, task, {"tool": "code_goto", "args": {"path": "pkg/calc.py", "line": 5, "character": 12}},
            agent_context=denied,
        )
        assert False, "expected WorkspaceError for missing capability"
    except WorkspaceError:
        pass


def test_path_outside_project_is_rejected():
    from core import WorkspaceError

    root = Path(tempfile.mkdtemp())
    _make_project(root)
    handler = agent_tools.EXTERNAL_TOOL_HANDLERS["code_goto"]
    try:
        handler(root, "code_goto", {"path": "../../escape.py", "line": 1, "character": 1}, {})
        assert False, "expected WorkspaceError for out-of-project path"
    except WorkspaceError:
        pass


def test_missing_file_returns_graceful_not_found():
    root = Path(tempfile.mkdtemp())
    _make_project(root)
    handler = agent_tools.EXTERNAL_TOOL_HANDLERS["code_goto"]
    result = handler(root, "code_goto", {"path": "pkg/missing.py", "line": 1, "character": 1}, {})
    assert result["found"] is False
    assert result["definitions"] == []


# ---- ignore-list tools (KNOWN_ISSUES #5) ---------------------------------
def _ignore_project(root: Path) -> Path:
    """A project that also carries a noise file the ignore list should drop."""
    calc = _make_project(root)
    (root / "scratch.py").write_text("def scratch():\n    return 1\n", encoding="utf-8")
    return calc


def test_ignore_add_is_session_only_and_effective():
    root = Path(tempfile.mkdtemp())
    _ignore_project(root)
    handler = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_add"]
    status = handler(root, "code_ignore_add", {"pattern": "**/scratch.py"}, {})
    assert "**/scratch.py" in status["session"]
    assert "**/scratch.py" not in status["persistent"]
    # Effective immediately: a fresh search no longer sees the scratch symbol.
    search = agent_tools.EXTERNAL_TOOL_HANDLERS["code_search"]
    res = search(root, "code_search", {"term": "scratch"}, {})
    assert res["count"] == 0
    # Not persisted to disk.
    assert not (root / ".docuagent" / "codeintel.ignore").exists()


def test_ignore_propose_queues_and_approve_persists():
    root = Path(tempfile.mkdtemp())
    _ignore_project(root)
    propose = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_propose"]
    propose(root, "code_ignore_propose", {"pattern": "**/scratch.py"}, {})

    pending = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_pending"]
    assert pending(root, "code_ignore_pending", {}, {})["pending"] == ["**/scratch.py"]

    # Effective immediately (session), before approval.
    search = agent_tools.EXTERNAL_TOOL_HANDLERS["code_search"]
    assert search(root, "code_search", {"term": "scratch"}, {})["count"] == 0

    # Main agent approves (after human approval): persists to the human config file.
    approve = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_approve"]
    approve(root, "code_ignore_approve", {"pattern": "**/scratch.py"}, {})
    ignore_file = root / ".docuagent" / "codeintel.ignore"
    assert ignore_file.is_file()
    assert "**/scratch.py" in ignore_file.read_text(encoding="utf-8")
    # Queue cleared.
    assert pending(root, "code_ignore_pending", {}, {})["pending"] == []


def test_ignore_reject_drops_proposal():
    root = Path(tempfile.mkdtemp())
    _ignore_project(root)
    propose = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_propose"]
    reject = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_reject"]
    pending = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_pending"]

    propose(root, "code_ignore_propose", {"pattern": "**/scratch.py"}, {})
    reject(root, "code_ignore_reject", {"pattern": "**/scratch.py"}, {})
    assert pending(root, "code_ignore_pending", {}, {})["pending"] == []
    # Scratch symbol visible again.
    search = agent_tools.EXTERNAL_TOOL_HANDLERS["code_search"]
    assert search(root, "code_search", {"term": "scratch"}, {})["count"] == 1


def test_ignore_list_merges_persistent_and_session():
    root = Path(tempfile.mkdtemp())
    _ignore_project(root)
    ignore_file = root / ".docuagent" / "codeintel.ignore"
    ignore_file.parent.mkdir(parents=True, exist_ok=True)
    ignore_file.write_text("node_modules\n", encoding="utf-8")

    add = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_add"]
    add(root, "code_ignore_add", {"pattern": "**/scratch.py"}, {})
    lst = agent_tools.EXTERNAL_TOOL_HANDLERS["code_ignore_list"]
    status = lst(root, "code_ignore_list", {}, {})
    assert status["persistent"] == ["node_modules"]
    assert "**/scratch.py" in status["session"]
    assert "node_modules" in status["effective"]
    assert "**/scratch.py" in status["effective"]


def test_ignore_tools_require_pattern():
    from core import WorkspaceError

    root = Path(tempfile.mkdtemp())
    _ignore_project(root)
    for name in ("code_ignore_add", "code_ignore_propose", "code_ignore_approve", "code_ignore_reject"):
        handler = agent_tools.EXTERNAL_TOOL_HANDLERS[name]
        try:
            handler(root, name, {}, {})
            assert False, f"expected WorkspaceError for missing pattern in {name}"
        except WorkspaceError:
            pass
