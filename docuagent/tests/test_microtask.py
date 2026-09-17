import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import microtask
from bootstrap import ProviderConfig
from core import WorkspaceError


class MicroTaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "core.py").write_text("print('old')\n", encoding="utf-8")
        self.architecture = {
            "modules": [
                {
                    "id": "core",
                    "name": "Core",
                    "path": "src",
                    "responsibility": "Core logic",
                    "depends_on": [],
                }
            ]
        }
        self.project = {
            "name": "Demo",
            "slug": "demo",
            "root": str(self.root),
            "mode": "new",
        }
        self.provider = ProviderConfig(
            base_url="http://localhost:11434/v1",
            model="local",
            api_key="",
        )

    def test_dispatch_micro_task_creates_review_diff_without_writing_main(self) -> None:
        judge = {
            "dispatch": True,
            "module_id": "core",
            "summary": "Change greeting default",
            "target_files": ["src/core.py"],
            "verification": ["python -c \"print('ok')\""],
        }
        generation = {
            "thinking": "small fix",
            "files": [{"path": "src/core.py", "content": "print('new')\n"}],
        }
        with patch("microtask.call_model_json", return_value=judge), patch(
            "microtask.run_agent_tool_loop",
            return_value=generation,
        ):
            result = microtask.dispatch_micro_task(
                self.root,
                self.provider,
                self.architecture,
                self.project,
                "给 core 的打印改成 new",
            )

        task = result["task"]
        self.assertEqual("review", task["status"])
        self.assertEqual(1, len(task["patch"]))
        self.assertEqual("src/core.py", task["patch"][0]["path"])
        self.assertEqual(
            "print('old')\n",
            (self.root / "src" / "core.py").read_text(encoding="utf-8"),
        )
        self.assertEqual("confirmed", result["tasks"]["status"])

    def test_dispatch_rejects_non_micro_request(self) -> None:
        with patch(
            "microtask.call_model_json",
            return_value={
                "dispatch": False,
                "summary": "this is architecture-level",
            },
        ):
            with self.assertRaisesRegex(WorkspaceError, "不派发微任务"):
                microtask.dispatch_micro_task(
                    self.root,
                    self.provider,
                    self.architecture,
                    self.project,
                    "加一个新的支付模块",
                )

    def test_dispatch_rejects_too_many_files(self) -> None:
        judge = {
            "dispatch": True,
            "module_id": "core",
            "summary": "too many files",
            "target_files": ["src/a.py", "src/b.py", "src/c.py", "src/d.py"],
            "verification": [],
        }
        with patch("microtask.call_model_json", return_value=judge):
            with self.assertRaisesRegex(WorkspaceError, "1 到 3"):
                microtask.dispatch_micro_task(
                    self.root,
                    self.provider,
                    self.architecture,
                    self.project,
                    "改太多文件",
                )

    def test_dispatch_carries_ui_context_into_micro_task(self) -> None:
        judge = {
            "dispatch": True,
            "module_id": "core",
            "summary": "Change greeting default",
            "target_files": ["src/core.py"],
            "verification": ["python -c \"print('ok')\""],
        }
        generation = {
            "thinking": "small fix",
            "files": [{"path": "src/core.py", "content": "print('new')\n"}],
        }
        ui_context = {
            "kind": "layout_delta",
            "selected_element": {"id": "n1", "selector": "[data-testid=\"save\"]"},
            "layout_context": {"ancestors": [], "element": {"id": "n1"}},
        }
        with patch("microtask.call_model_json", return_value=judge), patch(
            "microtask.run_agent_tool_loop",
            return_value=generation,
        ) as loop:
            result = microtask.dispatch_micro_task(
                self.root,
                self.provider,
                self.architecture,
                self.project,
                "给 core 的打印改成 new",
                ui_context=ui_context,
            )

        self.assertEqual("n1", result["task"]["ui_context"]["selected_element"]["id"])
        self.assertEqual(
            "n1",
            loop.call_args.args[4]["ui_context"]["layout_context"]["element"]["id"],
        )


if __name__ == "__main__":
    unittest.main()
