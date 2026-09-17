"""File-snapshot summary for the architecture model.

The architecture agent must see what actually exists in the project before
designing modules and verification commands. These tests pin the summary text
and the ensure-scan behaviour.
"""

import tempfile
import unittest
from pathlib import Path

import importscan


def _scan(totals=None, files=None, manifests=None, language="python", entry_points=None):  # noqa: ANN001
    totals = totals or {
        "files": 0, "code": 0, "doc": 0, "manifest": 0, "data": 0, "other": 0, "lines": 0,
    }
    return {
        "totals": totals,
        "language": language,
        "files": files or [],
        "manifests": manifests or [],
        "entry_points": entry_points or [],
    }


class ScanSummaryTest(unittest.TestCase):
    def test_missing_scan_is_explicit(self) -> None:
        text = importscan.architect_summary(None)
        self.assertIn("尚未完成扫描", text)

    def test_empty_project_is_explicit(self) -> None:
        text = importscan.architect_summary(_scan())
        self.assertIn("没有任何文件", text)
        self.assertIn("0 个文件", text)

    def test_prod_and_test_files_are_separated(self) -> None:
        totals = {"files": 3, "code": 3, "doc": 0, "manifest": 0, "data": 0, "other": 0, "lines": 10}
        files = [
            {"path": "src/parser.js", "kind": "code", "lines": 5},
            {"path": "src/io/index.js", "kind": "code", "lines": 3},
            {"path": "tests/parser.test.js", "kind": "code", "lines": 2},
        ]
        text = importscan.architect_summary(_scan(totals, files, entry_points=["src/io/index.js"]))
        self.assertIn("源码 2 个、测试 1 个", text)
        self.assertIn("src/parser.js", text)
        self.assertIn("（入口）", text)
        self.assertIn("tests/parser.test.js", text)

    def test_manifest_snippet(self) -> None:
        totals = {"files": 1, "code": 0, "doc": 0, "manifest": 1, "data": 0, "other": 0, "lines": 0}
        manifests = [{"name": "package.json", "facts": {"dependencies": {"x": "1.0.0"}}}]
        text = importscan.architect_summary(_scan(totals, manifests=manifests))
        self.assertIn("package.json", text)
        self.assertIn("dependencies", text)


class EnsureScanTest(unittest.TestCase):
    def test_creates_then_reuses_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "a.js").write_text("const x = 1;\n", encoding="utf-8")
            first = importscan.ensure_scan(root)
            self.assertGreater(first["totals"]["files"], 0)
            second = importscan.ensure_scan(root)
            self.assertEqual(first["scanned_at"], second["scanned_at"])

    def test_rescans_when_file_added(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "a.js").write_text("const x = 1;\n", encoding="utf-8")
            first = importscan.ensure_scan(root)
            first_count = first["totals"]["files"]
            (root / "src" / "b.js").write_text("const y = 2;\n", encoding="utf-8")
            second = importscan.ensure_scan(root)
            self.assertEqual(second["totals"]["files"], first_count + 1)
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])
            paths = {f["path"] for f in second["files"]}
            self.assertIn("src/b.js", paths)

    def test_rescans_when_file_modified(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir(parents=True)
            target = root / "src" / "a.js"
            target.write_text("const x = 1;\n", encoding="utf-8")
            first = importscan.ensure_scan(root)
            # Same size is not required: any content change must invalidate.
            target.write_text("const x = 999999;\n", encoding="utf-8")
            second = importscan.ensure_scan(root)
            self.assertNotEqual(first["fingerprint"], second["fingerprint"])

    def test_rescans_when_file_removed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir(parents=True)
            target = root / "src" / "a.js"
            target.write_text("const x = 1;\n", encoding="utf-8")
            (root / "src" / "b.js").write_text("const y = 2;\n", encoding="utf-8")
            first = importscan.ensure_scan(root)
            target.unlink()
            second = importscan.ensure_scan(root)
            self.assertEqual(second["totals"]["files"], first["totals"]["files"] - 1)
            paths = {f["path"] for f in second["files"]}
            self.assertNotIn("src/a.js", paths)

    def test_legacy_snapshot_without_fingerprint_is_rescanned(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "a.js").write_text("const x = 1;\n", encoding="utf-8")
            importscan.ensure_scan(root)
            # Simulate a pre-fingerprint snapshot with stale (zeroed) content.
            legacy = {"schema_version": 1, "totals": {"files": 0}, "files": [], "language": "unknown"}
            importscan.write_scan(root, legacy)
            fresh = importscan.ensure_scan(root)
            self.assertIn("fingerprint", fresh)
            self.assertGreater(fresh["totals"]["files"], 0)
            paths = {f["path"] for f in fresh["files"]}
            self.assertIn("src/a.js", paths)

    def test_refresh_forces_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src").mkdir(parents=True)
            (root / "src" / "a.js").write_text("const x = 1;\n", encoding="utf-8")
            importscan.ensure_scan(root)
            # Fingerprint still matches, but the snapshot itself was corrupted:
            # default ensure_scan reuses it, refresh=True must rebuild it.
            corrupted = importscan.read_scan(root)
            corrupted["totals"]["files"] = 0
            importscan.write_scan(root, corrupted)
            reused = importscan.ensure_scan(root)
            self.assertEqual(reused["totals"]["files"], 0)  # cache hit keeps corruption
            rebuilt = importscan.ensure_scan(root, refresh=True)
            self.assertGreater(rebuilt["totals"]["files"], 0)


class TestPathTest(unittest.TestCase):
    def test_path_classification(self) -> None:
        self.assertTrue(importscan._is_test_path("tests/parser.test.js"))
        self.assertTrue(importscan._is_test_path("test/x.js"))
        self.assertTrue(importscan._is_test_path("__tests__/x.test.ts"))
        self.assertFalse(importscan._is_test_path("src/parser.js"))
        self.assertFalse(importscan._is_test_path("src/io/scanner.js"))
