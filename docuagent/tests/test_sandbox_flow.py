import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sandbox
import tasks as tasks_module
from bootstrap import ProviderConfig
from task_state import write_apply_mode


class SandboxFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        # These tests pin the auto-apply path; review-mode settle has its own
        # suite in test_apply_mode.py.
        write_apply_mode(self.root, "auto")
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

    def test_sandbox_pass_applies_and_verifies_main(self) -> None:
        sb = sandbox.create_sandbox(self.root)
        try:
            work = Path(sb["path"])
            (work / "src" / "core.py").write_text("print('new')\n", encoding="utf-8")
            tasks_module._complete_sandbox_task(
                self.root,
                self.provider,
                {"modules": []},
                self.project,
                self.task,
                sb,
                {"done": True, "thinking": "ok"},
            )
        finally:
            sandbox.destroy_sandbox(sb)

        self.assertEqual("verified", self.task["status"])
        self.assertEqual(
            "print('new')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )
        self.assertFalse((self.root / "src" / "AI_ARCH.md").exists())
        self.assertEqual([], self.task["documentation_updated"])

    def test_cancel_after_write_resumes_saved_sandbox_in_review_mode(self) -> None:
        import agent_tools
        from agents import read_locks, read_registry
        from task_state import write_task_state, read_task_state
        write_apply_mode(self.root, "review")
        (self.root / "AI_ARCH.md").write_text("# Demo\n", encoding="utf-8")
        state = {"status": "confirmed", "tasks": [self.task]}
        write_task_state(self.root, state)
        tasks_module._register_wave(["core"])
        def response(*args, **kwargs):
            return {"content": "write", "tool_calls": [
                {"id": "nav", "name": "read_ai_arch", "args": {"path": "src/core.py"}},
                {"id": "write", "name": "write_file", "args": {"path": "src/core.py", "content": "print('saved')\n"}},
            ]}
        with patch("agent_tools.call_model_chat", side_effect=response):
            for event in tasks_module.stream_generate_one_task(self.root, self.task, self.provider, {"modules": []}, self.project):
                if "write_file" in event.get("text", ""):
                    tasks_module.cancel_tasks(["core"])
        self.assertEqual("pending", self.task["status"])
        self.assertTrue(agent_tools.has_loop_checkpoint(self.root, "core"))
        self.assertFalse(Path(self.task["sandbox_path"]).exists())
        self.assertEqual("print('old')\n", (self.root / "src/core.py").read_text(encoding="utf-8"))
        write_task_state(self.root, state)
        with patch("agent_tools.call_model_chat", return_value={"content": "complete", "tool_calls": []}) as model:
            resumed = tasks_module.resume_task(self.root, self.provider, {"modules": []}, self.project, "core")
        self.assertEqual(1, model.call_count)
        self.assertEqual("review", resumed["tasks"][0]["status"])
        self.assertTrue(any(entry["path"] == "src/core.py" and entry["after"] == "print('saved')\n" for entry in resumed["tasks"][0]["patch"]))
        self.assertFalse(agent_tools.has_loop_checkpoint(self.root, "core"))
        self.assertEqual({}, read_locks(self.root)["held"])
        self.assertEqual("review", read_registry(self.root)["agents"]["core"]["status"])
        self.assertEqual("review", read_task_state(self.root)["tasks"][0]["status"])

    def test_resume_rejects_changed_source_without_overwriting(self) -> None:
        import agent_tools
        from core import WorkspaceError
        sb = sandbox.create_sandbox(self.root)
        try:
            (Path(sb["path"]) / "src/core.py").write_text("print('saved')\n", encoding="utf-8")
            agent_tools.save_loop_checkpoint(self.root, "core", [{"turn": 1}], sandbox=sb)
        finally:
            sandbox.destroy_sandbox(sb)
        (self.root / "src/core.py").write_text("print('external')\n", encoding="utf-8")
        fresh = sandbox.create_sandbox(self.root)
        try:
            with self.assertRaisesRegex(WorkspaceError, "已变化"):
                agent_tools.restore_checkpoint_sandbox(self.root, "core", fresh)
            self.assertEqual("print('external')\n", (Path(fresh["path"]) / "src/core.py").read_text(encoding="utf-8"))
        finally:
            sandbox.destroy_sandbox(fresh)

    def test_environment_issue_falls_back_to_direct_write(self) -> None:
        failed = {
            "passed": False,
            "stdout": "",
            "stderr": "sandbox missing node_modules",
            "returncode": 1,
            "command": "npm test",
        }
        passed = {
            "passed": True,
            "stdout": "",
            "stderr": "",
            "returncode": 0,
            "command": "npm test",
        }
        sb = sandbox.create_sandbox(self.root)
        try:
            with patch(
                "tasks._sandbox.run_verification",
                side_effect=[failed, passed],
            ):
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

        self.assertEqual("verified", self.task["status"])
        self.assertEqual("direct", self.task["execution_mode"])
        self.assertEqual(1, self.task["direct_attempts"])

    def test_direct_failure_returns_to_repair_round(self) -> None:
        failed = {
            "passed": False,
            "stdout": "",
            "stderr": "boom",
            "returncode": 1,
            "command": "python -m unittest",
        }
        sb = sandbox.create_sandbox(self.root)
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
                    {"done": True, "sandbox_environment_issue": True},
                )
        finally:
            sandbox.destroy_sandbox(sb)

        self.assertEqual("failed", self.task["status"])
        repair_call.assert_called_once()
        self.assertEqual(
            tasks_module.REPAIR_PROMPT,
            repair_call.call_args.kwargs["system_prompt"],
        )


if __name__ == "__main__":
    unittest.main()
