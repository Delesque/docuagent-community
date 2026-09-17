"""Tests for §3 step3: typed Contract Registry projection onto the architecture graph.

The backend derives registry nodes (six typed classes) and aggregate edges
(owns / depends_on / uses) from ``contracts.json``. The diagram renders these as a
collapsed cluster per module; this test only exercises the data the backend owns.
"""

import tempfile
from pathlib import Path

from codeintel.service import CodeIntelService
from workspace import write_contracts


def _seed_contracts(root: Path) -> None:
    contracts = {
        "schema_version": 1,
        "project": {"name": "demo", "language": "Python", "runtime": "Python 3.12"},
        "modules": [
            {
                "id": "auth",
                "path": "auth.py",
                "depends_on": ["billing"],
                "exports": [
                    {"symbol": "verify_token", "kind": "function", "signature": "verify_token(t)", "since": "0.1.0"},
                    {"symbol": "Token", "kind": "class", "signature": "class Token", "since": "0.1.0"},
                ],
            },
            {
                "id": "billing",
                "path": "billing.py",
                "depends_on": [],
                "exports": [
                    {"symbol": "invoice_id", "kind": "constant", "signature": "invoice_id: str", "since": "0.1.0"},
                ],
            },
        ],
        "vocabulary": [
            {"term": "Invoice", "owner": "billing", "kind": "identifier", "type": "", "format": "", "forbidden_aliases": []}
        ],
        "shared_kernel": [
            {"symbol": "parse_args", "kind": "function", "owner": "auth", "path": "util.py", "why": "shared parser", "consumers": ["auth", "billing"]}
        ],
        "commands": [
            {"name": "create_invoice", "verb": "create", "resource": "invoice", "owner": "billing", "handler": "h", "args": []}
        ],
        "data_schema": [
            {"name": "Invoice", "owner": "billing", "kind": "table", "fields": [], "rationale": ""}
        ],
        "config_policy": [
            {"name": "MAX_ITEMS", "owner": "billing", "kind": "env", "default": "100", "security_boundary": False, "status": "deprecated"}
        ],
        "recipes": [],
    }
    write_contracts(root, contracts)


def _scratch_root(name: str) -> Path:
    """A throwaway project directory: the fixture must not write into the source tree."""
    root = Path(tempfile.mkdtemp(prefix=f"docuagent-{name}-"))
    (root / ".docuagent").mkdir(parents=True, exist_ok=True)
    return root


def test_registry_projection_nodes_and_types():
    root = _scratch_root("registry")
    (root / ".docuagent").mkdir(parents=True, exist_ok=True)
    _seed_contracts(root)

    svc = CodeIntelService()
    proj = svc.registry_projection(str(root))

    # 2 auth exports + 1 billing export + 1 vocab + 1 shared_kernel + 1 command
    # + 1 data_schema + 1 config_policy = 8 typed entries.
    ids = {n["id"] for n in proj["nodes"]}
    assert "public_api:auth.verify_token" in ids
    assert "public_api:auth.Token" in ids
    assert "public_api:billing.invoice_id" in ids
    assert "vocabulary:billing.Invoice" in ids
    assert "shared_kernel:auth.parse_args" in ids
    assert "commands_events:billing.create_invoice" in ids
    assert "data_schema:billing.Invoice" in ids
    assert "config_policy:billing.MAX_ITEMS" in ids
    assert len(proj["nodes"]) == 8

    # Every node carries the fields the overlay renders.
    for n in proj["nodes"]:
        assert n["type"] and n["owner"] and n["name"] and n["status"]
        assert n["public"] in (True, False)

    # Deprecated entry is non-public.
    dep = next(n for n in proj["nodes"] if n["id"] == "config_policy:billing.MAX_ITEMS")
    assert dep["status"] == "deprecated"
    assert dep["public"] is False


def test_registry_projection_aggregate_edges():
    root = _scratch_root("registry")
    _seed_contracts(root)

    svc = CodeIntelService()
    proj = svc.registry_projection(str(root))

    by_kind = {}
    for e in proj["edges"]:
        by_kind.setdefault(e["kind"], 0)
        by_kind[e["kind"]] += 1

    # owns: one per node that has an owning module (all 8 here).
    assert by_kind["owns"] == 8
    # depends_on: auth's 2 public_api entries -> billing.
    dep_edges = [e for e in proj["edges"] if e["kind"] == "depends_on"]
    assert len(dep_edges) == 2
    assert all(e["to"] == "billing" for e in dep_edges)
    assert all(e["from"].startswith("public_api:auth.") for e in dep_edges)
    # uses: shared_kernel entry -> its 2 consumers.
    use_edges = [e for e in proj["edges"] if e["kind"] == "uses"]
    assert len(use_edges) == 2
    assert {e["to"] for e in use_edges} == {"auth", "billing"}


def test_registry_projection_empty_without_contracts():
    root = _scratch_root("empty-registry")
    svc = CodeIntelService()
    proj = svc.registry_projection(str(root))
    assert proj["nodes"] == []
    assert proj["edges"] == []


def test_route_registry_projection_integration():
    from main_routes_codeintel import codeintel_registry_projection_route

    root = _scratch_root("registry-route")
    _seed_contracts(root)
    out = codeintel_registry_projection_route({"path": str(root)})
    assert len(out["nodes"]) == 8
    assert any(e["kind"] == "depends_on" for e in out["edges"])
