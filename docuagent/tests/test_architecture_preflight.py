import tempfile
import unittest
from pathlib import Path

from architecture_preflight import (
    architecture_preflight_issues,
    documented_command_issues,
    require_architecture_preflight,
    require_documented_command_consistency,
)
from core import WorkspaceError


def architecture(verification: str) -> dict:
    return {
        "verification": [verification],
        "modules": [
            {
                "id": "core",
                "path": "filesentinel/core.py",
                "target_files": ["filesentinel/core.py", "tests/test_core.py"],
                "verification": [verification],
            }
        ],
    }


class ArchitecturePreflightTest(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_unittest_top_level_fails_without_test_package(self) -> None:
        issues = architecture_preflight_issues(
            self.root,
            architecture(
                'python -m unittest discover -s tests -t . -p "test_core.py" -v'
            ),
        )

        self.assertEqual(1, len(issues))
        self.assertIn("tests/__init__.py", issues[0])

    def test_unittest_top_level_passes_with_test_package(self) -> None:
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")

        issues = architecture_preflight_issues(
            self.root,
            architecture(
                'python -m unittest discover -s tests -t . -p "test_core.py" -v'
            ),
        )

        self.assertEqual([], issues)

    def test_unittest_without_top_level_is_allowed(self) -> None:
        issues = architecture_preflight_issues(
            self.root,
            architecture(
                'python -m unittest discover -s tests -p "test_core.py" -v'
            ),
        )

        self.assertEqual([], issues)

    def test_duplicate_target_file_is_rejected(self) -> None:
        value = architecture("python -m unittest discover -s tests -v")
        value["modules"].append({
            "id": "other",
            "path": "filesentinel/other.py",
            "target_files": ["tests/test_core.py", "filesentinel/other.py"],
            "verification": ["python -m unittest discover -s tests -v"],
        })

        issues = architecture_preflight_issues(self.root, value)

        self.assertTrue(any("tests/test_core.py" in issue for issue in issues))

    def test_require_raises_actionable_error(self) -> None:
        with self.assertRaises(WorkspaceError) as caught:
            require_architecture_preflight(
                self.root,
                architecture(
                    'python -m unittest discover -s tests -t . -p "test_core.py" -v'
                ),
            )

        self.assertIn("架构与当前脚手架不一致", str(caught.exception))

    def test_stale_documented_command_is_rejected(self) -> None:
        (self.root / "README.md").write_text(
            "python -m unittest discover -s tests -t . -v\n",
            encoding="utf-8",
        )

        issues = documented_command_issues(self.root)

        self.assertEqual(1, len(issues))
        with self.assertRaises(WorkspaceError):
            require_documented_command_consistency(self.root)

    def test_documented_command_passes_with_test_package(self) -> None:
        (self.root / "README.md").write_text(
            "python -m unittest discover -s tests -t . -v\n",
            encoding="utf-8",
        )
        (self.root / "tests").mkdir()
        (self.root / "tests" / "__init__.py").write_text("", encoding="utf-8")

        self.assertEqual([], documented_command_issues(self.root))


if __name__ == "__main__":
    unittest.main()
