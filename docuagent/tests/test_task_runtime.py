"""The model-call seam that has to survive splitting tasks.py.

The suite drives the task pipeline by patching the model call. If that patch stops
reaching the code under test, nothing fails loudly - the test stays green and starts
issuing real model calls. These tests pin the seam so the split cannot introduce that
silently.
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import task_diff
import task_generate
import task_patch
import task_plan
import sandbox
import task_runtime
import task_verify
import tasks


def _relocated_caller():
    """Stand-in for a function moved to task_verify.py by the split.

    It resolves the name in its own module namespace, which is precisely why
    patching the tasks attribute would no longer reach it.
    """
    return task_runtime.call_model_json("cfg", "prompt", {})


class TaskRuntimeSeamTest(unittest.TestCase):
    def test_tasks_forwards_model_calls_to_the_seam(self) -> None:
        with patch("task_runtime.call_model_json", return_value={"ok": True}) as called:
            self.assertEqual({"ok": True}, tasks.call_model_json("cfg", "prompt", {}))
        called.assert_called_once()

    def test_patching_the_seam_reaches_relocated_callers(self) -> None:
        """The property the split depends on."""
        with patch("task_runtime.call_model_json", return_value={"ok": True}):
            self.assertEqual({"ok": True}, _relocated_caller())

    def test_patching_the_tasks_attribute_still_works(self) -> None:
        """Existing tests patch tasks.call_model_json; that must keep working."""
        with patch("tasks.call_model_json", return_value={"ok": "tasks"}):
            self.assertEqual({"ok": "tasks"}, tasks.call_model_json("c", "p", {}))

    def test_seam_covers_every_patched_model_entry_point(self) -> None:
        for name in ("call_model_json", "stream_json_model", "run_agent_tool_loop"):
            with self.subTest(name=name):
                self.assertTrue(hasattr(task_runtime, name))
                self.assertTrue(hasattr(tasks, name))

    def test_tasks_reexports_the_generation_stage(self) -> None:
        for name in (
            "generate_one_task",
            "stream_generate_one_task",
            "generate_wave",
            "stream_generate_wave",
            "cancel_tasks",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(tasks, name), getattr(task_generate, name))

    def test_legacy_tasks_patch_reaches_relocated_generator(self) -> None:
        with patch("tasks.call_model_json", return_value={"ok": "generation"}):
            self.assertEqual(
                {"ok": "generation"},
                task_generate.call_model_json("provider", "prompt", {}),
            )

    def test_tasks_reexports_the_patch_stage(self) -> None:
        for name in (
            "apply_task_patch",
            "apply_task_partial",
            "edit_task_patch",
            "apply_task_hunks",
            "task_hunks",
            "reject_task",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(tasks, name), getattr(task_patch, name))

    def test_tasks_preserves_the_sandbox_patch_target(self) -> None:
        self.assertIs(tasks._sandbox, sandbox)

    def test_tasks_reexports_the_verification_stage(self) -> None:
        for name in (
            "diagnose_file",
            "verify_task",
            "stream_verify_task",
            "retry_task",
            "resume_task",
            "repair_task",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(tasks, name), getattr(task_verify, name))

    def test_tasks_reexports_shared_diff_primitives(self) -> None:
        self.assertIs(tasks.diff_hunks, task_diff.diff_hunks)
        self.assertIs(tasks.apply_hunks, task_diff.apply_hunks)

    def test_tasks_reexports_the_planning_stage(self) -> None:
        for name in (
            "plan_tasks",
            "stream_plan_tasks",
            "normalize_task_plan",
            "sync_work_items_from_architecture",
        ):
            with self.subTest(name=name):
                self.assertIs(getattr(tasks, name), getattr(task_plan, name))

    def test_legacy_tasks_patch_reaches_relocated_planner(self) -> None:
        architecture = {
            "modules": [{"id": "core"}],
            "edges": [],
        }
        raw = {
            "tasks": [{
                "id": "core",
                "module_id": "core",
                "summary": "Implement core.",
                "target_files": ["src/core.py"],
                "depends_on": [],
                "verification": [],
            }]
        }
        with patch("task_plan.read_task_state", return_value=None), patch(
            "task_plan.write_task_state"
        ) as written, patch("tasks.call_model_json", return_value=raw) as model:
            result = task_plan.plan_tasks(
                Path("project"), object(), architecture, {"name": "Demo"}
            )

        self.assertEqual("planned", result["status"])
        model.assert_called_once()
        written.assert_called_once()

    def test_run_agent_tool_loop_is_routed_through_the_seam(self) -> None:
        with patch("task_runtime.run_agent_tool_loop", return_value={"done": True}):
            self.assertEqual({"done": True}, tasks.run_agent_tool_loop("ctx"))

    def test_stream_json_model_is_routed_through_the_seam(self) -> None:
        with patch("task_runtime.stream_json_model", return_value=iter([{"e": 1}])):
            self.assertEqual([{"e": 1}], list(tasks.stream_json_model("cfg", "p", {})))


if __name__ == "__main__":
    unittest.main()
