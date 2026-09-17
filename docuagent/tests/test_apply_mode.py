"""Apply-mode switch: review (default) vs auto sandbox settle.

The switch decides what happens to a finished sandbox: review mode turns the
changed files into a patch and stops, auto mode applies them once verification
passes. Both modes must describe this differently to the agent, too.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sandbox
import tasks as tasks_module
from agent_tools import AGENT_TOOL_LOOP_PROMPT, agent_tool_loop_prompt
from bootstrap import ProviderConfig
from core import WorkspaceError
from task_state import read_apply_mode, write_apply_mode


class ApplyModeStateTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_defaults_to_review(self) -> None:
        self.assertEqual("review", read_apply_mode(self.root))

    def test_write_and_read_roundtrip(self) -> None:
        self.assertEqual("auto", write_apply_mode(self.root, "auto"))
        self.assertEqual("auto", read_apply_mode(self.root))

    def test_invalid_mode_rejected(self) -> None:
        with self.assertRaises(WorkspaceError):
            write_apply_mode(self.root, "yolo")
        self.assertEqual("review", read_apply_mode(self.root))


class ApplyModePromptTest(unittest.TestCase):
    def test_review_prompt_states_the_human_gate(self) -> None:
        prompt = agent_tool_loop_prompt("review")
        self.assertIn("审阅模式", prompt)
        self.assertNotIn("自动应用模式", prompt)
        self.assertNotIn("{apply_policy}", prompt)

    def test_auto_prompt_demands_verification(self) -> None:
        prompt = agent_tool_loop_prompt("auto")
        self.assertIn("自动应用模式", prompt)
        self.assertIn("run_verification", prompt)
        self.assertNotIn("审阅模式", prompt)
        self.assertNotIn("{apply_policy}", prompt)

    def test_fallback_constant_is_the_review_variant(self) -> None:
        self.assertNotIn("{apply_policy}", AGENT_TOOL_LOOP_PROMPT)
        self.assertIn("审阅模式", AGENT_TOOL_LOOP_PROMPT)


class SandboxSettleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "core.py").write_text("print('old')\n", encoding="utf-8")
        self.task = {
            "id": "core",
            "module_id": "core",
            "summary": "Core",
            "target_files": ["src/core.py"],
            "depends_on": [],
            "verification": [],
            "status": "running",
            "direct_attempts": 0,
        }
        self.provider = ProviderConfig(
            base_url="http://localhost:11434/v1",
            model="local",
            api_key="",
        )
        self.project = {
            "name": "Demo",
            "slug": "demo",
            "root": str(self.root),
            "mode": "new",
        }

    def _make_sandbox(self) -> dict:
        sb = sandbox.create_sandbox(self.root)
        (Path(sb["path"]) / "src" / "core.py").write_text(
            "print('new')\n", encoding="utf-8"
        )
        return sb

    def _run_complete(self, sb: dict, result: dict) -> None:
        try:
            tasks_module._complete_sandbox_task(
                self.root,
                self.provider,
                {"modules": []},
                self.project,
                self.task,
                sb,
                result,
            )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_review_mode_stops_at_patch(self) -> None:
        write_apply_mode(self.root, "review")
        self._run_complete(self._make_sandbox(), {"done": True, "thinking": "ok"})

        self.assertEqual("review", self.task["status"])
        self.assertEqual(
            "print('old')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )
        patch_entries = self.task["patch"]
        self.assertEqual(["src/core.py"], [entry["path"] for entry in patch_entries])
        self.assertIn("print('new')", patch_entries[0]["after"])

    def test_auto_mode_applies_immediately(self) -> None:
        write_apply_mode(self.root, "auto")
        self._run_complete(self._make_sandbox(), {"done": True, "thinking": "ok"})

        self.assertEqual("verified", self.task["status"])
        self.assertEqual(
            "print('new')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )

    def _write_contracts(self, modules: list[dict]) -> None:
        """Write a minimal registry so contract lint actually checks content."""
        from core import CONTRACTS_SCHEMA_VERSION
        from workspace import atomic_write_json, managed_path

        atomic_write_json(
            managed_path(self.root, "contracts.json"),
            {
                "schema_version": CONTRACTS_SCHEMA_VERSION,
                "modules": modules,
            },
        )

    def test_auto_mode_contract_violation_degrades_to_review(self) -> None:
        """Auto must never land a change that violates the registry.

        The sandbox change imports `beta` while `core` declares no dependency on
        it — a dependency-boundary violation. Auto mode must stop at a review
        patch (same artifact review mode would produce) instead of writing the
        project, exactly like the manual apply route's hard contract gate.
        """
        write_apply_mode(self.root, "auto")
        self._write_contracts([
            {"id": "core", "path": "src/core.py", "depends_on": []},
            {"id": "beta", "path": "src/beta.py", "depends_on": []},
        ])
        sb = sandbox.create_sandbox(self.root)
        (Path(sb["path"]) / "src" / "core.py").write_text(
            "import beta\nprint('new')\n", encoding="utf-8"
        )
        try:
            self._run_complete(sb, {"done": True, "thinking": "ok"})
        finally:
            sandbox.destroy_sandbox(sb)

        self.assertEqual("review", self.task["status"])
        self.assertTrue(self.task.get("contract_lint_violations"))
        # The project file is untouched — the violation never landed.
        self.assertEqual(
            "print('old')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )
        patch_paths = [entry["path"] for entry in self.task["patch"]]
        self.assertIn("src/core.py", patch_paths)

    def test_auto_mode_no_contract_still_applies(self) -> None:
        """Without a registry the gate is advisory (skipped), never blocking."""
        write_apply_mode(self.root, "auto")
        self._run_complete(self._make_sandbox(), {"done": True, "thinking": "ok"})

        self.assertEqual("verified", self.task["status"])
        self.assertEqual(
            "print('new')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )

    def test_review_mode_contract_violation_stops_at_patch(self) -> None:
        """Review already produced a patch; the violation must keep it at review."""
        write_apply_mode(self.root, "review")
        self._write_contracts([
            {"id": "core", "path": "src/core.py", "depends_on": []},
            {"id": "beta", "path": "src/beta.py", "depends_on": []},
        ])
        sb = sandbox.create_sandbox(self.root)
        (Path(sb["path"]) / "src" / "core.py").write_text(
            "import beta\nprint('new')\n", encoding="utf-8"
        )
        try:
            self._run_complete(sb, {"done": True, "thinking": "ok"})
        finally:
            sandbox.destroy_sandbox(sb)

        self.assertEqual("review", self.task["status"])
        self.assertTrue(self.task.get("contract_lint_violations"))
        self.assertEqual(
            "print('old')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )

    def test_review_mode_blocks_direct_write_fallback(self) -> None:
        """Environment-issue fallback writes the real project; review forbids it."""
        write_apply_mode(self.root, "review")
        failed = {
            "passed": False,
            "stdout": "",
            "stderr": "sandbox missing node_modules",
            "returncode": 1,
            "command": "npm test",
        }
        sb = self._make_sandbox()
        try:
            with patch(
                "tasks._sandbox.run_verification",
                side_effect=[failed, failed, failed],
            ), patch(
                "tasks.run_agent_tool_loop",
                return_value={"done": True, "thinking": "repair"},
            ) as repair_call:
                tasks_module._complete_sandbox_task(
                    self.root,
                    self.provider,
                    {"modules": []},
                    self.project,
                    self.task,
                    sb,
                    {"done": True, "sandbox_environment_issue": True, "reason": "missing deps"},
                )
        finally:
            sandbox.destroy_sandbox(sb)

        self.assertEqual("review", self.task["status"])
        self.assertNotEqual("direct", self.task.get("execution_mode"))
        repair_call.assert_not_called()
        self.assertEqual(
            "print('old')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
