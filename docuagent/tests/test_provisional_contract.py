"""Provisional contract lifecycle (plan item 4, slices B & C).

Slice B: sub-agent context exposes `expected_public_api` — the proposed symbols
its module was declared to provide.
Slice C: a proposed export turns active only when a real production file
OUTSIDE the owning module references it; owner definitions and test files never
promote anything.
"""

import json
import tempfile
import unittest
from pathlib import Path

import agents
import contract_registry
import importscan
import task_patch
import workspace


def _payload() -> dict:
    return {
        "schema_version": 1,
        "project": {"name": "demo"},
        "vocabulary": [],
        "shared_kernel": [],
        "commands": [],
        "data_schema": [],
        "config_policy": [],
        "modules": [
            {
                "id": "parser",
                "path": "src/parser.js",
                "depends_on": [],
                "exports": [
                    {"symbol": "parseDependencies", "kind": "declared", "status": "proposed"}
                ],
                "consumes": [],
            }
        ],
        "recipes": [],
    }


def _make_project(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    importscan.ensure_scan(root)


class PromoteProposedExportsTest(unittest.TestCase):
    def test_real_production_file_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_project(root, {
                "src/parser.js": "function parseDependencies() {} // owner defines it",
                "src/main.js": "const parseDependencies = require('./parser');",
                "tests/parser.test.js": "const { parseDependencies } = require('../src/parser');",
            })
            payload = _payload()
            contract_registry.promote_proposed_exports(root, payload)
            status = payload["modules"][0]["exports"][0]["status"]
            self.assertEqual(status, "active")

    def test_test_only_reference_does_not_promote(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_project(root, {
                "src/parser.js": "function parseDependencies() {}",
                "tests/parser.test.js": "require('../src/parser').parseDependencies;",
            })
            payload = _payload()
            contract_registry.promote_proposed_exports(root, payload)
            status = payload["modules"][0]["exports"][0]["status"]
            self.assertEqual(status, "proposed")

    def test_owner_definition_alone_does_not_promote(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_project(root, {
                "src/parser.js": "export const parseDependencies = () => {};",
            })
            payload = _payload()
            contract_registry.promote_proposed_exports(root, payload)
            status = payload["modules"][0]["exports"][0]["status"]
            self.assertEqual(status, "proposed")

    def test_patch_file_promotes_when_scan_is_stale(self) -> None:
        """Greenfield reality: the bootstrap scan ran on an empty project, so it
        never lists the consumer files sub-agents write later. The patch's own
        pending content must count as the consuming evidence."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_project(root, {
                "src/parser.js": "function parseDependencies() {}",
            })
            # The consumer appears AFTER the scan: the scan stays stale.
            consumer = root / "src" / "main.js"
            consumer.parent.mkdir(parents=True, exist_ok=True)
            consumer.write_text(
                "const { parseDependencies } = require('./parser');",
                encoding="utf-8",
            )
            payload = _payload()
            # Without the patch text the stale scan knows nothing: no promotion.
            contract_registry.promote_proposed_exports(root, payload)
            self.assertEqual(payload["modules"][0]["exports"][0]["status"], "proposed")
            # With the patch's pending file content: promoted.
            contract_registry.promote_proposed_exports(root, payload, extra_texts={
                "src/main.js": "const { parseDependencies } = require('./parser');",
            })
            self.assertEqual(payload["modules"][0]["exports"][0]["status"], "active")

    def test_patch_extras_ignore_docs_and_managed_files(self) -> None:
        """Docs / managed state / tests name symbols all the time; none of them
        is a real consumer."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            _make_project(root, {
                "src/parser.js": "function parseDependencies() {}",
            })
            payload = _payload()
            contract_registry.promote_proposed_exports(root, payload, extra_texts={
                ".docuagent/contracts.json": '{"parseDependencies": "named here"}',
                "AI_ARCH.md": "uses parseDependencies",
                "test/parser.test.js": "require('../src/parser').parseDependencies;",
            })
            self.assertEqual(payload["modules"][0]["exports"][0]["status"], "proposed")


class MergeContractDeltaPromotionTest(unittest.TestCase):
    """Promotion must run on every patch, not only on patches with a delta.

    The cli-entry patch introduced no new public symbol (it only implemented the
    symbol it had been promised), so its delta was empty — yet it was the first
    real consumer of two other modules' declared exports.
    """

    def test_empty_delta_still_promotes_consumed_symbols(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".docuagent").mkdir(parents=True)
            workspace.write_contracts(root, _payload())
            changes = {
                "src/app.js": "const { parseDependencies } = require('./parser');"
            }
            task_patch._merge_contract_delta(root, [], changes)
            written = json.loads(changes[".docuagent/contracts.json"])
            self.assertEqual(
                written["modules"][0]["exports"][0]["status"], "active"
            )

    def test_project_without_registry_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            changes = {"src/app.js": "const x = 1;"}
            task_patch._merge_contract_delta(root, [], changes)
            self.assertNotIn(".docuagent/contracts.json", changes)


class EntryModulePromotionTest(unittest.TestCase):
    """Entry modules (bin/, main/cli) are consumed by the outside world, so the
    only possible consumer evidence is their own implementation."""

    def _modules(self) -> list[dict]:
        return [
            {
                "id": "cli",
                "path": "bin",
                "depends_on": [],
                "exports": [{"symbol": "runCli", "kind": "declared", "status": "proposed"}],
                "consumes": [],
            },
            {
                "id": "util",
                "path": "src/util",
                "depends_on": [],
                "exports": [{"symbol": "helper", "kind": "declared", "status": "proposed"}],
                "consumes": [],
            },
        ]

    def test_entry_module_self_evidence_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".docuagent").mkdir(parents=True)
            payload = {
                "schema_version": 1,
                "project": {"name": "demo"},
                "modules": self._modules(),
            }
            workspace.write_contracts(root, payload)
            contract_registry.promote_proposed_exports(root, payload, extra_texts={
                "bin/depcheck.js": "module.exports = { runCli };\n",
            })
            exports = payload["modules"][0]["exports"]
            self.assertEqual(exports[0]["status"], "active")

    def test_library_without_consumer_stays_proposed(self) -> None:
        """A path like src/util is not an entry even with no dependents."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".docuagent").mkdir(parents=True)
            payload = {
                "schema_version": 1,
                "project": {"name": "demo"},
                "modules": self._modules(),
            }
            workspace.write_contracts(root, payload)
            contract_registry.promote_proposed_exports(root, payload, extra_texts={
                "src/util/index.js": "function helper() {} module.exports = { helper };\n",
            })
            exports = payload["modules"][1]["exports"]
            self.assertEqual(exports[0]["status"], "proposed")

    def test_entry_shape_with_inbound_dependency_uses_real_consumer(self) -> None:
        """A bin/ module another module depends on is NOT an entry: it must be
        consumed like any other library."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".docuagent").mkdir(parents=True)
            modules = self._modules()
            modules[1]["depends_on"] = ["cli"]
            payload = {
                "schema_version": 1,
                "project": {"name": "demo"},
                "modules": modules,
            }
            workspace.write_contracts(root, payload)
            contract_registry.promote_proposed_exports(root, payload, extra_texts={
                "bin/depcheck.js": "module.exports = { runCli };\n",
            })
            exports = payload["modules"][0]["exports"]
            self.assertEqual(exports[0]["status"], "proposed")

    def test_entry_symbol_not_implemented_stays_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".docuagent").mkdir(parents=True)
            payload = {
                "schema_version": 1,
                "project": {"name": "demo"},
                "modules": self._modules(),
            }
            workspace.write_contracts(root, payload)
            contract_registry.promote_proposed_exports(root, payload, extra_texts={
                "bin/depcheck.js": "const start = () => {}; module.exports = { start };\n",
            })
            exports = payload["modules"][0]["exports"]
            self.assertEqual(exports[0]["status"], "proposed")


class ContractViewExpectedApiTest(unittest.TestCase):
    def test_expected_public_api_lists_only_proposed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".docuagent").mkdir(parents=True)
            payload = _payload()
            payload["modules"] = [{
                "id": "io",
                "path": "src/io.js",
                "depends_on": [],
                "exports": [
                    {"symbol": "readFile", "kind": "declared", "status": "proposed"},
                    {"symbol": "scanPath", "kind": "declared", "status": "proposed"},
                    {"symbol": "stable", "kind": "declared", "status": "active"},
                ],
                "consumes": [],
            }]
            workspace.write_contracts(root, payload)
            view = agents._contract_view(root, {"id": "io", "depends_on": []}, None)
            self.assertEqual(view["expected_public_api"], ["readFile", "scanPath"])

    def test_roundtrip_view_lists_declared_api(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".docuagent").mkdir(parents=True)
            workspace.write_contracts(root, _payload())
            view = agents._contract_view(root, {"id": "parser", "depends_on": []}, None)
            self.assertEqual(view["expected_public_api"], ["parseDependencies"])
