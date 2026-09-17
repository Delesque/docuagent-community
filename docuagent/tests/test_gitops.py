import subprocess
import tempfile
import unittest
from pathlib import Path

import gitops
from core import WorkspaceError


class GitOpsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=str(self.root),
            check=True,
            capture_output=True,
            text=True,
        )

    def _init_repo(self) -> None:
        self._git("init", "-q")
        self._git("config", "user.email", "test@example.com")
        self._git("config", "user.name", "Test User")

    def test_status_reports_untracked_file(self) -> None:
        self._init_repo()
        (self.root / "core.py").write_text("print('core')\n", encoding="utf-8")

        status = gitops.git_status(self.root)

        self.assertTrue(status["repo"])
        self.assertTrue(status["dirty"])
        self.assertTrue(any(change["path"] == "core.py" for change in status["changes"]))

    def test_diff_returns_working_tree_changes(self) -> None:
        self._init_repo()
        (self.root / "core.py").write_text("print('old')\n", encoding="utf-8")
        self._git("add", "core.py")
        self._git("commit", "-m", "initial")
        (self.root / "core.py").write_text("print('new')\n", encoding="utf-8")

        diff = gitops.git_diff(self.root, "core.py")

        self.assertIn("print('new')", diff["working"])
        self.assertIn("print('old')", diff["working"])

    def test_commit_stages_and_returns_new_head(self) -> None:
        self._init_repo()
        (self.root / "core.py").write_text("print('core')\n", encoding="utf-8")
        self._git("add", "core.py")
        self._git("commit", "-m", "initial")
        (self.root / "core.py").write_text("print('fixed')\n", encoding="utf-8")

        result = gitops.git_commit(self.root, "fix core")

        self.assertTrue(result["committed"])
        self.assertTrue(result["head"])
        self.assertFalse(result["status"]["dirty"])

    def test_status_returns_repo_false_outside_repository(self) -> None:
        status = gitops.git_status(self.root)
        self.assertFalse(status["repo"])
        self.assertEqual([], status["changes"])

    def test_relative_path_rejects_escape(self) -> None:
        with self.assertRaises(WorkspaceError):
            gitops._safe_relative_path("../outside.py")


if __name__ == "__main__":
    unittest.main()
