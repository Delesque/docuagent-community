"""Tests for the typed Contract Registry helpers (§3.2 / §3.3 / §3.7).

Covers ``contract_registry.flatten_registry`` (six typed arrays -> uniform items),
``apply_reconcile_to_contracts`` (write-back by stable id), and the end-to-end
typed reconcile through ``CodeIntelService.reconcile_registry`` + the HTTP route.
"""

import tempfile
import unittest
from pathlib import Path

import contract_registry
import core


def _registry_with_all_types() -> dict:
    return {
        "schema_version": 1,
        "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
        "vocabulary": [{"term": "invoice_id", "owner": "billing"}],
        "shared_kernel": [{"symbol": "AppError", "owner": "shared", "consumers": ["billing"]}],
        "commands": [{"name": "create_invoice", "owner": "billing"}],
        "data_schema": [{
            "name": "Invoice", "owner": "billing", "kind": "entity",
            "fields": [{"name": "id", "type": "int", "required": True}],
        }],
        "config_policy": [{"name": "MAX_RETRIES", "owner": "billing", "kind": "env", "default": "3"}],
        "modules": [{
            "id": "billing", "path": "src/billing", "depends_on": [],
            "exports": [{"symbol": "create_invoice", "kind": "function"}], "consumes": [],
        }],
        "recipes": [],
    }


class FlattenRegistryTest(unittest.TestCase):
    def test_flatten_produces_six_typed_items(self) -> None:
        reg = core.normalize_contracts(_registry_with_all_types())
        items = contract_registry.flatten_registry(reg)
        by_type: dict[str, list] = {}
        for it in items:
            by_type.setdefault(it["type"], []).append(it)
        self.assertEqual(len(by_type["public_api"]), 1)
        self.assertEqual(len(by_type["data_schema"]), 1)
        self.assertEqual(len(by_type["config_policy"]), 1)
        self.assertEqual(len(by_type["commands_events"]), 1)
        self.assertEqual(len(by_type["shared_kernel"]), 1)
        self.assertEqual(len(by_type["vocabulary"]), 1)
        self.assertEqual(items[0]["id"], "public_api:billing.create_invoice")
        self.assertEqual(items[0]["owner"], "billing")

    def test_flatten_preserves_lifecycle_meta(self) -> None:
        reg = core.normalize_contracts(_registry_with_all_types())
        items = contract_registry.flatten_registry(reg)
        ds = next(i for i in items if i["type"] == "data_schema")
        self.assertEqual(ds["status"], "active")
        self.assertEqual(ds["source_hash"], "")

    def test_flatten_derives_id_from_symbol_and_warns(self) -> None:
        # KNOWN_ISSUES #7: 缺 `id` 但含 `symbol` 的 export 不应被静默丢弃，
        # 应按 `public_api:<module_id>.<symbol>` 推导 id 并告警。
        reg = {
            "schema_version": 1,
            "modules": [{
                "id": "billing", "path": "billing.py", "depends_on": [],
                "exports": [{"symbol": "create_invoice", "kind": "function"}],  # 无 id
                "consumes": [],
            }],
        }
        with self.assertLogs(contract_registry.__name__, level="WARNING") as cm:
            items = contract_registry.flatten_registry(reg)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], "public_api:billing.create_invoice")
        self.assertEqual(items[0]["owner"], "billing")
        self.assertEqual(items[0]["name"], "create_invoice")
        self.assertTrue(any("create_invoice" in m for m in cm.output))

    def test_flatten_drops_export_without_symbol_and_id(self) -> None:
        # 既无 id 也无 symbol 的 export 无法定位，应告警后跳过（而非静默）。
        reg = {
            "schema_version": 1,
            "modules": [{
                "id": "m", "path": "m.py", "depends_on": [],
                "exports": [{"kind": "function"}],  # 既无 id 也无 symbol
                "consumes": [],
            }],
        }
        with self.assertLogs(contract_registry.__name__, level="WARNING") as cm:
            items = contract_registry.flatten_registry(reg)
        self.assertEqual(items, [])
        self.assertTrue(any("跳过" in m for m in cm.output))


class ApplyReconcileTest(unittest.TestCase):
    def test_apply_writes_back_by_id(self) -> None:
        reg = core.normalize_contracts(_registry_with_all_types())
        enriched = [{
            "id": "data_schema:billing.Invoice",
            "source_hash": "abc",
            "last_seen": "2026-08-25T00:00:00Z",
            "status": "active",
        }]
        out = contract_registry.apply_reconcile_to_contracts(reg, enriched)
        target = next(d for d in out["data_schema"] if d["id"] == "data_schema:billing.Invoice")
        self.assertEqual(target["source_hash"], "abc")
        self.assertEqual(target["last_seen"], "2026-08-25T00:00:00Z")
        # original registry is untouched (pure function)
        self.assertEqual(reg["data_schema"][0]["source_hash"], "")

    def test_apply_noop_when_enriched_empty(self) -> None:
        reg = core.normalize_contracts(_registry_with_all_types())
        self.assertIs(contract_registry.apply_reconcile_to_contracts(reg, []), reg)


class ReconcileRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")

    def test_reconcile_fills_and_flags_per_type(self) -> None:
        from codeintel.service import CodeIntelService

        items = [
            {"id": "public_api:m.foo", "type": "public_api", "name": "foo",
             "owner": "m", "source_ref": {"file": "a.py", "line": 1}, "status": "active"},
            {"id": "public_api:m.ghost", "type": "public_api", "name": "ghost",
             "owner": "m", "status": "active"},
            {"id": "config_policy:m.MAX", "type": "config_policy", "name": "MAX",
             "owner": "m", "status": "active"},
        ]
        result = CodeIntelService().reconcile_registry(str(self.root), items)
        foo = next(e for e in result["enriched"] if e["id"] == "public_api:m.foo")
        self.assertTrue(foo["source_hash"])
        self.assertTrue(foo["last_seen"])
        self.assertEqual([o["name"] for o in result["orphan"]], ["ghost", "MAX"])
        self.assertEqual(result["by_type"]["config_policy"]["orphan"], 1)
        # public symbol `foo` is registered, so nothing is unregistered
        self.assertEqual(result["unregistered"], [])

    def test_reconcile_flags_stale_source_drift(self) -> None:
        from codeintel.service import CodeIntelService

        items = [{
            "id": "public_api:m.foo", "type": "public_api", "name": "foo",
            "owner": "m", "source_ref": {"file": "old_path.py", "line": 1}, "status": "active",
        }]
        result = CodeIntelService().reconcile_registry(str(self.root), items)
        self.assertEqual([s["name"] for s in result["stale"]], ["foo"])


class ReconcileRouteTest(unittest.TestCase):
    def test_route_accepts_full_registry_dict(self) -> None:
        from main_routes_codeintel import codeintel_reconcile_route

        root = Path(tempfile.mkdtemp())
        (root / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
        reg = core.normalize_contracts({
            "schema_version": 1,
            "project": {"name": "d"},
            "modules": [{
                "id": "m", "path": "a.py",
                "exports": [{"symbol": "foo", "kind": "function"}],
            }],
        })
        out = codeintel_reconcile_route({"path": str(root), "contracts": reg})
        self.assertIn("enriched", out)
        self.assertEqual(out["orphan"], [])
        # foo exists in source -> its enriched entry carries a source hash
        foo = next(e for e in out["enriched"] if e["name"] == "foo")
        self.assertTrue(foo["source_hash"])


if __name__ == "__main__":
    unittest.main()
