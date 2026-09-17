import tempfile
import unittest
from pathlib import Path

from task_state import (
    architecture_fingerprint,
    ensure_documentation_state,
    mark_architecture_docs_dirty,
    read_task_state,
    write_task_state,
)


class ArchitectureDocsFingerprintTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_fingerprint_ignores_architecture_version(self) -> None:
        first = {
            "architecture_version": 1,
            "verification": ["python -m unittest discover -s tests -v"],
            "modules": [{"id": "core"}],
        }
        second = {
            "architecture_version": 8,
            "modules": [{"id": "core"}],
            "verification": ["python -m unittest discover -s tests -v"],
        }

        self.assertEqual(
            architecture_fingerprint(first),
            architecture_fingerprint(second),
        )

    def test_architecture_change_marks_docs_dirty(self) -> None:
        architecture = {
            "verification": ["python -m unittest discover -s tests -v"],
            "modules": [{"id": "core"}],
        }
        state = {
            "schema_version": 1,
            "task_version": 1,
            "status": "done",
            "tasks": [],
            "documentation_task": {
                "status": "synced",
                "source_fingerprint": architecture_fingerprint(architecture),
            },
            "docs_in_sync": True,
            "docs_sync_required": False,
        }
        ensure_documentation_state(state)
        write_task_state(self.root, state)

        architecture["verification"] = [
            'python -m unittest discover -s tests -t . -p "test_core.py" -v'
        ]

        self.assertTrue(mark_architecture_docs_dirty(self.root, architecture))

        stored = read_task_state(self.root)
        self.assertFalse(stored["docs_in_sync"])
        self.assertTrue(stored["docs_sync_required"])
        self.assertEqual("pending", stored["documentation_task"]["status"])

    def test_unchanged_architecture_does_not_mark_docs_dirty(self) -> None:
        architecture = {
            "verification": ["python -m unittest discover -s tests -v"],
            "modules": [{"id": "core"}],
        }
        state = {
            "schema_version": 1,
            "task_version": 1,
            "status": "done",
            "tasks": [],
            "documentation_task": {
                "status": "synced",
                "source_fingerprint": architecture_fingerprint(architecture),
            },
            "docs_in_sync": True,
            "docs_sync_required": False,
        }
        ensure_documentation_state(state)
        write_task_state(self.root, state)

        self.assertFalse(mark_architecture_docs_dirty(self.root, architecture))
        stored = read_task_state(self.root)
        self.assertTrue(stored["docs_in_sync"])
        self.assertFalse(stored["docs_sync_required"])


if __name__ == "__main__":
    unittest.main()
