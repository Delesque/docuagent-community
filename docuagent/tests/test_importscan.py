import tempfile
import unittest
from pathlib import Path

import docuagent
import importscan


class ImportScanTest(unittest.TestCase):
    def write_tree(self, root: Path) -> None:
        (root / "main.py").write_text(
            '"""Entry point for the CLI."""\nimport os\nimport sys\n\ndef run() -> None:\n    print("hi")\n',
            encoding="utf-8",
        )
        (root / "package.json").write_text(
            '{"name": "demo", "dependencies": {"fastapi": "*"}}',
            encoding="utf-8",
        )
        (root / "src").mkdir()
        (root / "src" / "api.py").write_text(
            "# HTTP handlers.\nclass Api:\n    def get(self):\n        pass\n",
            encoding="utf-8",
        )
        (root / "src" / "api").mkdir()
        (root / "src" / "api" / "routes.py").write_text(
            "import fastapi\n\ndef router():\n    return None\n# TODO: add auth\n",
            encoding="utf-8",
        )
        (root / "node_modules").mkdir()
        (root / "node_modules" / "junk.js").write_text("// ignored", encoding="utf-8")
        (root / "data.bin").write_bytes(b"\x00\x01\x02\x03")

    def test_scan_detects_kinds_language_and_entry_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tree(root)
            scan = importscan.scan_project(root)

            self.assertEqual(scan["language"], "python")
            self.assertIn("main.py", scan["entry_points"])
            totals = scan["totals"]
            self.assertGreaterEqual(totals["files"], 5)
            self.assertGreaterEqual(totals["code"], 2)

            manifests = {item["path"] for item in scan["manifests"]}
            self.assertIn("package.json", manifests)

            by_path = {item["path"]: item for item in scan["files"]}
            self.assertNotIn("node_modules/junk.js", by_path)
            self.assertEqual(by_path["main.py"]["kind"], "code")
            self.assertIn("def run", by_path["main.py"].get("symbols", []))
            self.assertIn("import os", by_path["main.py"].get("imports", []))
            self.assertEqual(by_path["data.bin"]["kind"], "other")
            self.assertNotIn("symbols", by_path["data.bin"])
            # routes.py has a TODO marker.
            self.assertEqual(by_path["src/api/routes.py"].get("todos"), 1)

    def test_scan_prompt_text_is_bounded_and_grouped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tree(root)
            scan = importscan.scan_project(root)
            text = importscan.scan_prompt_text(scan)
            self.assertIn("Detected language: python", text)
            self.assertIn("main.py", text)
            self.assertIn("[src/api]", text)
            self.assertLessEqual(len(text), importscan.MAX_PROMPT_CHARS + 200)

    def test_read_scan_roundtrip_via_docuagent_surface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_tree(root)
            scan = importscan.scan_project(root)
            importscan.write_scan(root, scan)
            loaded = docuagent.read_scan(root)
            self.assertEqual(loaded["totals"]["files"], scan["totals"]["files"])


if __name__ == "__main__":
    unittest.main()
