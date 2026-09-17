"""Tests for P3: architecture-diagram projection (public API + status + orphan).

The "click to open in the editor" path is intentionally NOT a backend file
read — DocuAgent hands the user a file:// deep link and lets them open the
symbol in their own editor (their VSCode). So projection is the only thing
the backend owns here; the editor jump is a pure frontend concern.
"""

import tempfile
from pathlib import Path

from codeintel.service import CodeIntelService

A_PY = """
def foo():
    return 1


def _bar():
    return 2


X = 42
"""

B_PY = """
class Widget:
    def render(self):
        return "ok"
"""

DEDUP_PY = """
def step():
    return 1


class Snake:
    def step(self):
        return 2
"""


def _make_project() -> Path:
    root = Path(tempfile.mkdtemp())
    (root / "a.py").write_text(A_PY, encoding="utf-8")
    (root / "b.py").write_text(B_PY, encoding="utf-8")
    return root


def test_projection_refreshes_generated_modified_and_deleted_files():
    root = _make_project()
    svc = CodeIntelService()
    modules = [{"id": "new", "path": "new.py"}]
    assert svc.architecture_projection(str(root), modules)["modules"]["new"]["status"] == "stale"
    (root / "new.py").write_text("def first(): pass\n", encoding="utf-8")
    result = svc.architecture_projection(str(root), modules)["modules"]["new"]
    assert result["status"] == "active"
    assert result["public_api"][0]["name"] == "first"
    (root / "new.py").write_text("def replacement(): pass\n", encoding="utf-8")
    assert svc.architecture_projection(str(root), modules)["modules"]["new"]["public_api"][0]["name"] == "replacement"
    (root / "new.py").unlink()
    assert svc.architecture_projection(str(root), modules)["modules"]["new"]["status"] == "stale"


def test_architecture_projection_active_and_stale():
    root = _make_project()
    svc = CodeIntelService()
    proj = svc.architecture_projection(
        str(root),
        [
            {"id": "m1", "path": "a.py"},
            {"id": "m2", "path": "ghost.py"},
        ],
    )
    # m1 resolves to indexed source -> active with 3 public symbols.
    m1 = proj["modules"]["m1"]
    assert m1["status"] == "active"
    assert m1["total"] == 3
    names = {p["name"] for p in m1["public_api"]}
    assert names == {"foo", "_bar", "X"}
    # m2 path never resolves -> stale.
    assert proj["modules"]["m2"]["status"] == "stale"

    # Orphan: b.py's public symbols (Widget) belong to no module path.
    assert proj["orphan"]["count"] == 1
    assert proj["orphan"]["files"] == ["b.py"]


def test_architecture_projection_non_python_is_empty():
    root = Path(tempfile.mkdtemp())
    svc = CodeIntelService()
    proj = svc.architecture_projection(
        str(root), [{"id": "m", "path": "x.js"}]
    )
    assert proj["modules"] == {}
    assert proj["orphan"]["count"] == 0


def test_route_architecture_projection_integration():
    from main_routes_codeintel import codeintel_architecture_projection_route

    root = _make_project()
    payload = {
        "path": str(root),
        "modules": [{"id": "m1", "path": "a.py"}],
    }
    out = codeintel_architecture_projection_route(payload)
    assert out["modules"]["m1"]["status"] == "active"
    assert len(out["modules"]["m1"]["public_api"]) == 3


# ---- KNOWN_ISSUES #8: same-name dedup + IIFE/glue fallback -----------------


def test_module_public_api_drops_methods_and_dedupes():
    from codeintel.ports import SymbolRecord

    recs = [
        SymbolRecord(name="step", kind="function", file="a.py", line=1, character=0, scope="", is_public=True),
        SymbolRecord(name="step", kind="function", file="a.py", line=5, character=4, scope="Snake", is_public=False),
        SymbolRecord(name="render", kind="function", file="a.py", line=8, character=4, scope="Snake", is_public=False),
    ]
    public = CodeIntelService._module_public_api(recs)
    assert [p["name"] for p in public] == ["step"]


def test_module_public_api_iife_glue_fallback():
    from codeintel.ports import SymbolRecord

    # All symbols nested inside an IIFE -> no module-level public symbol.
    recs = [
        SymbolRecord(name="init", kind="function", file="app.js", line=2, character=4, scope="nested", is_public=False),
        SymbolRecord(name="run", kind="function", file="app.js", line=6, character=4, scope="nested", is_public=False),
    ]
    public = CodeIntelService._module_public_api(recs)
    assert {p["name"] for p in public} == {"init", "run"}


def test_architecture_projection_dedupes_same_name_method_and_function():
    root = Path(tempfile.mkdtemp())
    (root / "dedup.py").write_text(DEDUP_PY, encoding="utf-8")
    svc = CodeIntelService()
    proj = svc.architecture_projection(str(root), [{"id": "m", "path": "dedup.py"}])
    names = [p["name"] for p in proj["modules"]["m"]["public_api"]]
    # Module-level `function step` + class `Snake` kept; `Snake.step` method dropped;
    # only one `step` (no duplicate of the method).
    assert set(names) == {"step", "Snake"}
    assert names.count("step") == 1


# ---- KNOWN_ISSUES #9: architecture_projection merges the registry ----------


def test_architecture_projection_merge_registry_fills_glue_and_edges():
    from workspace import write_contracts

    root = Path(tempfile.mkdtemp())
    (root / ".docuagent").mkdir(parents=True, exist_ok=True)
    (root / "glue.py").write_text("", encoding="utf-8")
    write_contracts(
        root,
        {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "Python 3.12"},
            "modules": [
                {"id": "core", "path": "core.py"},
                {"id": "ui", "path": "ui.py"},
                {
                    "id": "glue",
                    "path": "glue.py",
                    "depends_on": ["core", "ui"],
                    "exports": [{"symbol": "bootstrap", "kind": "function", "signature": "bootstrap()"}],
                }
            ],
        },
    )
    svc = CodeIntelService()
    proj = svc.architecture_projection(
        str(root), [{"id": "glue", "path": "glue.py"}], merge_registry=True
    )
    mod = proj["modules"]["glue"]
    assert [p["name"] for p in mod["public_api"]] == ["bootstrap"]
    assert {e["to"] for e in mod["edges"] if e["kind"] == "depends_on"} == {"core", "ui"}


def test_architecture_projection_merge_registry_off_is_not_merged():
    from workspace import write_contracts

    root = Path(tempfile.mkdtemp())
    (root / ".docuagent").mkdir(parents=True, exist_ok=True)
    (root / "glue.py").write_text("", encoding="utf-8")
    write_contracts(
        root,
        {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "Python 3.12"},
            "modules": [
                {"id": "core", "path": "core.py"},
                {
                    "id": "glue",
                    "path": "glue.py",
                    "depends_on": ["core"],
                    "exports": [{"symbol": "bootstrap", "kind": "function"}],
                }
            ],
        },
    )
    svc = CodeIntelService()
    proj = svc.architecture_projection(
        str(root), [{"id": "glue", "path": "glue.py"}], merge_registry=False
    )
    mod = proj["modules"]["glue"]
    assert mod["public_api"] == []
    assert mod["edges"] == []
