"""Architecture declared exports -> provisional contract entries.

The architecture model declares the public symbols a module will provide to
other modules; these land in the initial contract as `proposed` so every
sub-agent sees them before any code exists. Tests pin normalization, the
prefill and the write/read round trip.
"""

import tempfile
import unittest
from pathlib import Path

import core
import scaffold
from contract_registry import _read_contracts

STATE = {
    "project": {"name": "demo", "mode": "new"},
    "architecture": {
        "stack": ["node"],
        "modules": [
            {
                "id": "parser",
                "name": "解析",
                "responsibility": "解析依赖",
                "path": "src/parser.js",
                "depends_on": [],
                "verification": [],
                "declared_exports": ["parseDependencies", "parseDependencies", "_private"],
            },
            {
                "id": "io",
                "name": "IO",
                "responsibility": "读写文件",
                "path": "src/io.js",
                "depends_on": ["parser"],
                "verification": [],
            },
        ],
    },
}


class DeclaredExportsTest(unittest.TestCase):
    def test_architecture_normalization_keeps_declared_exports(self) -> None:
        normalized = core.normalize_architecture(STATE["architecture"])
        # normalized_list keeps order but deduplicates
        self.assertEqual(
            normalized["modules"][0]["declared_exports"],
            ["parseDependencies", "_private"],
        )

    def test_minimal_contracts_prefills_proposed_exports(self) -> None:
        contracts = scaffold.minimal_contracts(STATE)
        exports = contracts["modules"][0]["exports"]
        # duplicates and underscore-private names are dropped
        self.assertEqual(len(exports), 1)
        self.assertEqual(exports[0]["symbol"], "parseDependencies")
        self.assertEqual(exports[0]["status"], "proposed")
        # a module without declarations gets no exports
        self.assertEqual(contracts["modules"][1]["exports"], [])

    def test_round_trip_keeps_proposed_status(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            scaffold.write_initial_contracts(root, STATE)
            reread = _read_contracts(root / ".docuagent" / "contracts.json")
            export = reread["modules"][0]["exports"][0]
            self.assertEqual(export["symbol"], "parseDependencies")
            self.assertEqual(export["status"], "proposed")
            self.assertEqual(export["kind"], "declared")
