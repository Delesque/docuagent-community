"""Tests for the project-level documentation-ignore configuration (P1-3)."""

import tempfile
import unittest
from pathlib import Path

from task_docs import (
    DOCUMENT_IGNORE_FILE,
    _DOCUMENT_TREE_IGNORED_DIRS,
    _affected_child_directories,
    _iter_document_directories,
    read_document_ignored_dirs,
    write_document_ignored_dirs,
)
from workspace import atomic_write_json, managed_path


class DocIgnoreTest(unittest.TestCase):
    def _root(self) -> Path:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        return root

    def _dir(self, root: Path, *parts: str) -> Path:
        target = root.joinpath(*parts)
        target.mkdir(parents=True, exist_ok=True)
        return target

    def test_defaults_exclude_dependency_and_cache_directories(self) -> None:
        root = self._root()
        self._dir(root, "node_modules", "left-pad")
        self._dir(root, "src")
        names = {path.name for path in _iter_document_directories(root)}
        self.assertIn("src", names)
        self.assertNotIn("node_modules", names)
        self.assertTrue(_DOCUMENT_TREE_IGNORED_DIRS)

    def test_project_config_adds_project_specific_ignores(self) -> None:
        root = self._root()
        self._dir(root, "vendor", "lib")
        atomic_write_json(
            managed_path(root, DOCUMENT_IGNORE_FILE),
            {"schema_version": 1, "ignored_dirs": ["vendor"]},
        )
        names = {path.name for path in _iter_document_directories(root)}
        self.assertNotIn("vendor", names)
        merged = read_document_ignored_dirs(root)
        self.assertIn("node_modules", merged)
        self.assertIn("vendor", merged)

    def test_affected_directories_stop_at_ignored_ancestor(self) -> None:
        root = self._root()
        self._dir(root, "vendor", "lib")
        atomic_write_json(
            managed_path(root, DOCUMENT_IGNORE_FILE),
            {"schema_version": 1, "ignored_dirs": ["vendor"]},
        )
        task = {
            "id": "t1",
            "target_files": ["vendor/lib/core.py"],
        }
        self.assertEqual([], _affected_child_directories(root, [task]))

    def test_invalid_entries_are_skipped_on_read(self) -> None:
        root = self._root()
        atomic_write_json(
            managed_path(root, DOCUMENT_IGNORE_FILE),
            {"schema_version": 1, "ignored_dirs": ["ok", "a/b", "..", "", 42]},
        )
        merged = read_document_ignored_dirs(root)
        self.assertIn("ok", merged)
        self.assertNotIn("a/b", merged)
        self.assertNotIn("..", merged)
        self.assertIn("node_modules", merged)

    def test_writer_cleans_and_persists(self) -> None:
        root = self._root()
        cleaned = write_document_ignored_dirs(root, ["vendor", " generated ", "vendor"])
        self.assertEqual(["generated", "vendor"], cleaned)
        reread = read_document_ignored_dirs(root)
        self.assertIn("generated", reread)
        self.assertIn("vendor", reread)

    def test_writer_rejects_path_separators(self) -> None:
        from core import WorkspaceError

        root = self._root()
        with self.assertRaises(WorkspaceError):
            write_document_ignored_dirs(root, ["a/b"])
        # Nothing persisted on rejection.
        self.assertFalse((root / ".docuagent" / DOCUMENT_IGNORE_FILE).exists())


class DocIgnoreRouteTest(unittest.TestCase):
    def test_route_reads_and_writes(self) -> None:
        from main_routes_tasks import doc_ignore_route

        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)

        readback = doc_ignore_route({"path": str(root)})
        self.assertIn("defaults", readback)
        self.assertIn("node_modules", readback["ignored_dirs"])

        written = doc_ignore_route({"path": str(root), "ignored_dirs": ["vendor"]})
        self.assertIn("vendor", written["ignored_dirs"])
        self.assertIn("node_modules", written["ignored_dirs"])

        self.assertEqual(
            ["vendor"], sorted(read_document_ignored_dirs(root) - _DOCUMENT_TREE_IGNORED_DIRS)
        )


if __name__ == "__main__":
    unittest.main()
