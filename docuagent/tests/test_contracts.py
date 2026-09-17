import tempfile
import unittest
from pathlib import Path

import core
import importscan
import scaffold
import workspace


class ContractsSchemaTest(unittest.TestCase):
    def test_normalize_minimal_contracts_fills_required_fields(self) -> None:
        normalized = core.normalize_contracts({
            "schema_version": 1,
            "updated_at": "",
            "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
            "vocabulary": [],
            "shared_kernel": [],
            "commands": [],
            "modules": [],
            "recipes": [],
        })
        self.assertEqual([], normalized["vocabulary"])
        self.assertEqual([], normalized["modules"])

    def test_normalize_contracts_rejects_wrong_version(self) -> None:
        with self.assertRaises(core.WorkspaceError):
            core.normalize_contracts({"schema_version": 2})

    def test_normalize_contracts_tolerates_missing_schema_version(self) -> None:
        # KNOWN_ISSUES #7: 缺 schema_version 时容错按当前版本处理并告警，不再直接抛错。
        with self.assertLogs(core.__name__, level="WARNING") as cm:
            normalized = core.normalize_contracts({
                "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
                "vocabulary": [],
                "shared_kernel": [],
                "commands": [],
                "modules": [],
                "recipes": [],
            })
        self.assertEqual(normalized["schema_version"], core.CONTRACTS_SCHEMA_VERSION)
        self.assertEqual([], normalized["modules"])
        self.assertTrue(any("schema_version" in m for m in cm.output))

    def test_normalize_contracts_validates_module_exports(self) -> None:
        normalized = core.normalize_contracts({
            "schema_version": 1,
            "project": {"name": "demo"},
            "modules": [{
                "id": "billing",
                "path": "src/billing",
                "depends_on": ["pricing"],
                "exports": [{"symbol": "create_invoice", "kind": "function"}],
                "consumes": [{"from": "pricing", "symbols": ["calculate_price"]}],
            }, {
                "id": "pricing",
                "path": "src/pricing",
                "exports": [{"symbol": "calculate_price", "kind": "function"}],
            }],
        })
        self.assertEqual("function", normalized["modules"][0]["exports"][0]["kind"])
        self.assertEqual(["calculate_price"], normalized["modules"][0]["consumes"][0]["symbols"])

    def test_normalize_contracts_rejects_duplicate_registry_entries(self) -> None:
        with self.assertRaisesRegex(core.WorkspaceError, "modules.id"):
            core.normalize_contracts({
                "schema_version": 1,
                "modules": [{"id": "billing"}, {"id": "billing"}],
            })

    def test_normalize_contracts_rejects_unknown_owner_and_consumer(self) -> None:
        with self.assertRaisesRegex(core.WorkspaceError, "owner"):
            core.normalize_contracts({
                "schema_version": 1,
                "vocabulary": [{"term": "invoice_id", "owner": "missing"}],
                "modules": [{"id": "billing"}],
            })
        with self.assertRaisesRegex(core.WorkspaceError, "consumers"):
            core.normalize_contracts({
                "schema_version": 1,
                "shared_kernel": [{"symbol": "AppError", "consumers": ["missing"]}],
                "modules": [{"id": "billing"}],
            })


class ContractsPersistenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_write_read_roundtrip(self) -> None:
        contracts = {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
            "vocabulary": [],
            "shared_kernel": [],
            "commands": [],
            "modules": [],
            "recipes": [],
        }
        workspace.write_contracts(self.root, contracts)
        self.assertIsNone(None) if False else None
        self.assertTrue(workspace.contracts_path(self.root).exists())
        self.assertEqual("demo", workspace.read_contracts(self.root)["project"]["name"])

    def test_read_missing_returns_none(self) -> None:
        self.assertIsNone(workspace.read_contracts(self.root))


class ContractsDraftTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_minimal_contracts_derive_modules_from_architecture(self) -> None:
        state = {
            "project": {"name": "demo", "slug": "demo", "mode": "new"},
            "architecture": {
                "stack": ["python"],
                "modules": [
                    {"id": "billing", "path": "src/billing", "depends_on": ["pricing"]},
                ],
            },
        }
        contracts = scaffold.minimal_contracts(state)
        self.assertEqual("billing", contracts["modules"][0]["id"])
        self.assertEqual([], contracts["modules"][0]["exports"])
        self.assertEqual("Python", contracts["project"]["language"])

    def test_draft_contracts_extract_public_symbols_from_scan(self) -> None:
        scan = {
            "language": "python",
            "files": [
                {
                    "path": "src/billing/invoice.py",
                    "ext": ".py",
                    "symbols": ["def create_invoice(customer_id, items)", "def _private_helper()", "class Invoice"],
                    "imports": [],
                },
                {
                    "path": "src/pricing/price.py",
                    "ext": ".py",
                    "symbols": ["def calculate_price(items)"],
                    "imports": [],
                },
            ],
        }
        architecture = {
            "modules": [
                {"id": "billing", "path": "src/billing", "depends_on": ["pricing"]},
                {"id": "pricing", "path": "src/pricing", "depends_on": []},
            ]
        }
        project = {"name": "demo", "language": "python"}
        contracts = importscan.draft_contracts_from_scan(self.root, architecture, project, scan)
        billing = next(m for m in contracts["modules"] if m["id"] == "billing")
        pricing = next(m for m in contracts["modules"] if m["id"] == "pricing")
        self.assertEqual(["create_invoice", "Invoice"], [e["symbol"] for e in billing["exports"]])
        self.assertEqual(["calculate_price"], [e["symbol"] for e in pricing["exports"]])


class TypedRegistrySchemaTest(unittest.TestCase):
    def test_normalize_data_schema_and_config_policy(self) -> None:
        normalized = core.normalize_contracts({
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python"},
            "data_schema": [{
                "name": "Invoice", "owner": "billing",
                "fields": [{"name": "id", "type": "int", "required": True}],
            }],
            "config_policy": [{"name": "MAX", "owner": "billing", "kind": "env", "default": "3"}],
            "modules": [{"id": "billing", "path": "src/billing",
                         "exports": [{"symbol": "create_invoice", "kind": "function"}]}],
        })
        ds = normalized["data_schema"][0]
        self.assertEqual(ds["id"], "data_schema:billing.Invoice")
        self.assertEqual(ds["status"], "active")
        self.assertEqual(ds["fields"][0]["name"], "id")
        self.assertTrue(ds["fields"][0]["required"])
        cp = normalized["config_policy"][0]
        self.assertEqual(cp["id"], "config_policy:billing.MAX")
        self.assertEqual(cp["kind"], "env")

    def test_normalize_attaches_meta_to_all_entries(self) -> None:
        normalized = core.normalize_contracts({
            "schema_version": 1,
            "modules": [{"id": "billing", "path": "src/billing",
                         "exports": [{"symbol": "create_invoice"}]}],
        })
        export = normalized["modules"][0]["exports"][0]
        self.assertEqual(export["id"], "public_api:billing.create_invoice")
        self.assertEqual(export["status"], "active")
        self.assertEqual(export["source_ref"], {"file": "", "line": 0})
        self.assertEqual(export["source_hash"], "")
        self.assertEqual(export["last_seen"], "")

    def test_normalize_rejects_unknown_owner_in_typed_arrays(self) -> None:
        with self.assertRaisesRegex(core.WorkspaceError, "data_schema"):
            core.normalize_contracts({
                "schema_version": 1,
                "data_schema": [{"name": "Invoice", "owner": "ghost"}],
                "modules": [{"id": "billing"}],
            })

    def test_normalize_rejects_duplicate_typed_ids(self) -> None:
        with self.assertRaisesRegex(core.WorkspaceError, "data_schema.id"):
            core.normalize_contracts({
                "schema_version": 1,
                "data_schema": [
                    {"name": "Invoice", "owner": "billing"},
                    {"name": "Invoice", "owner": "billing"},
                ],
                "modules": [{"id": "billing"}],
            })


if __name__ == "__main__":
    unittest.main()
