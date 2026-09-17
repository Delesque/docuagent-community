import tempfile
import unittest
from pathlib import Path

import debug
from core import WorkspaceError


class DebugFileTest(unittest.TestCase):
    def test_debug_file_shows_locals(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / "probe.py"
            script.write_text(
                """x = 42
print(x)
""",
                encoding="utf-8",
            )
            result = debug.debug_file(root, "probe.py", 2)
            self.assertIn("x", result["stdout"])
            self.assertIn("42", result["stdout"])

    def test_debug_file_path_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(WorkspaceError):
                debug.debug_file(root, "../outside.py", 1)
