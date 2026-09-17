"""Fresh directories do not receive program-written AI_ARCH placeholders."""

import tempfile
import unittest
from pathlib import Path

from task_docs import ensure_nav_docs_in_tree

TASK = {
    "id": "t1",
    "module_id": "analysis",
    "target_files": [
        "src/analysis/index.js",
        "test/analysis.test.js",
        "src/io/scanner.js",  # parent exists with real doc -> untouched
    ],
}


class EnsureNavDocsTest(unittest.TestCase):
    def test_does_not_plant_templates_into_fresh_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "src" / "io").mkdir(parents=True)
            (root / "src" / "io" / "scanner.js").write_text("// x", encoding="utf-8")
            (root / "src" / "io" / "AI_ARCH.md").write_text("# 真实文档", encoding="utf-8")

            created = ensure_nav_docs_in_tree(root, TASK)

            fresh_doc = root / "test" / "AI_ARCH.md"
            src_doc = root / "src" / "analysis" / "AI_ARCH.md"
            self.assertEqual([], created)
            self.assertFalse(fresh_doc.exists())
            self.assertFalse(src_doc.exists())
            self.assertEqual((root / "src" / "io" / "AI_ARCH.md").read_text(encoding="utf-8"), "# 真实文档")

    def test_does_not_create_target_directories(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            created = ensure_nav_docs_in_tree(root, TASK)
            self.assertEqual([], created)
            self.assertFalse((root / "test").exists())
            self.assertFalse((root / "src").exists())

    def test_no_target_files_is_silent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            created = ensure_nav_docs_in_tree(Path(temp_dir), {"id": "t", "target_files": []})
            self.assertEqual(created, [])
