"""importscan symbol extraction: the explicit export surface wins.

The old heuristic registered every top-level name as an export, so
`const fs = require('fs')` made module "fs" an export — consumption read
as production. A file offers what it puts on module.exports / exports.x /
export / __all__; only files with no export statement fall back to
top-level public definitions (still excluding require assignments).
"""

import unittest

import importscan


class PythonSymbolExtractionTest(unittest.TestCase):
    def test_all_list_wins_over_defs(self) -> None:
        text = (
            "import os\n"
            "def run(a): pass\n"
            "def _hidden(): pass\n"
            "def helper(a): pass\n"
            "__all__ = ['run']\n"
        )
        symbols, imports = importscan._python_symbols(text)
        self.assertEqual(symbols, ["def run"])
        self.assertTrue(any("import os" in item for item in imports))

    def test_all_with_re_exported_name_keeps_bare(self) -> None:
        text = (
            "from .util import calculate\n"
            "__all__ = ['calculate']\n"
        )
        symbols, _ = importscan._python_symbols(text)
        self.assertEqual(symbols, ["calculate"])

    def test_no_all_uses_public_defs_only(self) -> None:
        text = "import os\ndef run(): pass\ndef _private(): pass\n"
        symbols, imports = importscan._python_symbols(text)
        self.assertEqual(symbols, ["def run"])
        self.assertTrue(any("import os" in item for item in imports))


class JsSymbolExtractionTest(unittest.TestCase):
    def test_module_exports_block_ignores_require_noise(self) -> None:
        text = (
            "const fs = require('fs');\n"
            "const path = require('path');\n"
            "async function listSourceFiles(root) {}\n"
            "async function collectSourceFiles(root) {}\n"
            "module.exports = {\n"
            "  listSourceFiles,\n"
            "  collectSourceFiles,\n"
            "};\n"
        )
        symbols, imports = importscan._js_symbols(text)
        self.assertEqual(symbols, ["function listSourceFiles", "function collectSourceFiles"])
        self.assertEqual(len(imports), 2)

    def test_dot_form_exports(self) -> None:
        text = "exports.parseArgs = parseArgs;\nexports.runCli = runCli;\n"
        symbols, _ = importscan._js_symbols(text)
        self.assertEqual(symbols, ["parseArgs", "runCli"])

    def test_esm_export_list_resolves_alias(self) -> None:
        text = "export { parseArgs, runCli as start };\n"
        symbols, _ = importscan._js_symbols(text)
        self.assertEqual(symbols, ["parseArgs", "start"])

    def test_single_expression_export(self) -> None:
        text = (
            "async function runCli() {}\n"
            "module.exports = runCli;\n"
        )
        symbols, _ = importscan._js_symbols(text)
        self.assertEqual(symbols, ["function runCli"])

    def test_no_export_statement_falls_back_without_requires(self) -> None:
        text = "const fs = require('fs');\nfunction helper() {}\n"
        symbols, imports = importscan._js_symbols(text)
        self.assertEqual(symbols, ["function helper"])
        self.assertEqual(len(imports), 1)

    def test_exported_constant_keeps_kind(self) -> None:
        text = (
            "const MAX_RETRIES = 3;\n"
            "module.exports = { MAX_RETRIES };\n"
        )
        symbols, _ = importscan._js_symbols(text)
        self.assertEqual(symbols, ["constant MAX_RETRIES"])


class ScanRecordSymbolsTest(unittest.TestCase):
    def test_full_scan_drops_require_imports_from_symbols(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "src"
            target.mkdir()
            (target / "reader.js").write_text(
                "const fs = require('fs');\n"
                "const path = require('path');\n"
                "function readPackageJson(dir) {}\n"
                "function findPackageJson(dir) {}\n"
                "module.exports = { readPackageJson, findPackageJson };\n",
                encoding="utf-8",
            )
            scan = importscan.scan_project(root)
            record = next(
                entry for entry in scan["files"] if entry["path"] == "src/reader.js"
            )
            self.assertEqual(
                record["symbols"],
                ["function readPackageJson", "function findPackageJson"],
            )
            self.assertEqual(len(record["imports"]), 2)


if __name__ == "__main__":
    unittest.main()
