import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import sandbox
from command_fixtures import verification_command


class SandboxTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "core.py").write_text("print('old')\n", encoding="utf-8")

    def _git(self, *args: str) -> None:
        subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            check=True,
            capture_output=True,
            text=True,
        )

    def test_copy_sandbox_applies_and_cleans_up(self) -> None:
        sb = sandbox.create_sandbox(self.root)
        work = Path(sb["path"])
        try:
            (work / "src" / "core.py").write_text("print('new')\n", encoding="utf-8")
            applied = sandbox.apply_sandbox(self.root, sb)
            self.assertIn("src/core.py", applied)
            self.assertEqual(
                "print('new')\n",
                (self.root / "src" / "core.py").read_text(encoding="utf-8"),
            )
        finally:
            sandbox.destroy_sandbox(sb)
        self.assertFalse(Path(sb["temp_root"]).exists())

    def test_git_worktree_sandbox_applies_changes(self) -> None:
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        self._git("add", ".")
        self._git("commit", "-m", "initial")

        sb = sandbox.create_sandbox(self.root)
        try:
            self.assertEqual("git", sb["kind"])
            work = Path(sb["path"])
            (work / "src" / "core.py").write_text("print('worktree')\n", encoding="utf-8")
            sandbox.apply_sandbox(self.root, sb)
            self.assertEqual(
                "print('worktree')\n",
                (self.root / "src" / "core.py").read_text(encoding="utf-8"),
            )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_apply_sandbox_rejects_main_project_conflict_without_partial_write(self) -> None:
        sb = sandbox.create_sandbox(self.root)
        work = Path(sb["path"])
        try:
            (work / "src" / "core.py").write_text("print('agent')\n", encoding="utf-8")
            (work / "src" / "new.py").write_text("print('new')\n", encoding="utf-8")
            (self.root / "src" / "core.py").write_text(
                "print('user edit')\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(sandbox.WorkspaceError, "沙箱生成后被修改"):
                sandbox.apply_sandbox(self.root, sb)

            self.assertFalse((self.root / "src" / "new.py").exists())
            self.assertEqual(
                "print('user edit')\n",
                (self.root / "src" / "core.py").read_text(encoding="utf-8"),
            )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_apply_sandbox_rejects_new_file_conflict(self) -> None:
        sb = sandbox.create_sandbox(self.root)
        work = Path(sb["path"])
        try:
            (work / "src" / "new.py").write_text("print('agent')\n", encoding="utf-8")
            (self.root / "src" / "new.py").write_text(
                "print('user')\n", encoding="utf-8"
            )

            with self.assertRaisesRegex(sandbox.WorkspaceError, "已在主项目中存在"):
                sandbox.apply_sandbox(self.root, sb)
        finally:
            sandbox.destroy_sandbox(sb)

    def test_apply_sandbox_ignores_unrelated_uncommitted_changes(self) -> None:
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")
        (self.root / "src" / "other.py").write_text("print('base')\n", encoding="utf-8")
        self._git("add", ".")
        self._git("commit", "-m", "initial")

        sb = sandbox.create_sandbox(self.root)
        work = Path(sb["path"])
        try:
            (self.root / "src" / "core.py").write_text(
                "print('user work')\n", encoding="utf-8"
            )
            (work / "src" / "other.py").write_text(
                "print('agent work')\n", encoding="utf-8"
            )

            applied = sandbox.apply_sandbox(self.root, sb)

            self.assertEqual(["src/other.py"], applied)
            self.assertEqual(
                "print('user work')\n",
                (self.root / "src" / "core.py").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                "print('agent work')\n",
                (self.root / "src" / "other.py").read_text(encoding="utf-8"),
            )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_verification_runs_inside_sandbox(self) -> None:
        task = {"verification": [verification_command(self.root)]}
        result = sandbox.run_verification(self.root, task)
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
