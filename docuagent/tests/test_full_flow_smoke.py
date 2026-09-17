import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docuagent
import tasks
from bootstrap import ToolCallingNotSupported
from unittest.mock import patch


class FullFlowSmokeTest(unittest.TestCase):
    """One mocked-model trip through the entire production pipeline."""

    def setUp(self) -> None:
        # The smoke trip below mocks `tasks.call_model_json`, so force the native
        # tool-calling path to fall back exactly like an unsupporting provider.
        patcher = patch(
            "agent_tools.call_model_chat",
            side_effect=ToolCallingNotSupported("test fallback"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    TEST_PROVIDER = {
        "enabled": True,
        "base_url": "https://example.test/v1",
        "model": "smoke-model",
        "api_key": "secret",
    }

    @staticmethod
    def doc_result(*args, **kwargs):
        payload = args[2]
        target = payload["target"]
        lines = [f"# {target['directory']}"]
        lines.extend(f"- `{entry['name']}`" for entry in target["entries"])
        if target["path"] == "AI_ARCH.md":
            facts = payload["workspace_facts"]
            lines.extend(f"{name}/AI_ARCH.md" for name in facts["top_level_directories"])
            if facts.get("requires_python"):
                lines.append(facts["requires_python"])
        return {"files": [{"path": target["path"], "content": "\n".join(lines)}]}

    def architecture_model_result(self):
        return docuagent.validate_architecture_model_result(
            {
                "architecture": {
                    "summary": "A smoke-test project.",
                    "platform": "Linux",
                    "language": "Python",
                    "runtime": "Python 3.12",
                    "frameworks": [],
                    "stack": ["Python 3.12"],
                    "modules": [
                        {
                            "id": "core",
                            "name": "Core",
                            "responsibility": "Implement core logic.",
                            "path": "src/core.py",
                            "depends_on": [],
                            "verification": [],
                        },
                        {
                            "id": "runner",
                            "name": "Runner",
                            "responsibility": "Expose the core as a runnable entry point.",
                            "path": "src/runner.py",
                            "depends_on": ["core"],
                            "verification": [],
                        },
                        {
                            "id": "docs",
                            "name": "Docs",
                            "responsibility": "Keep the generated AI_ARCH.md tree current.",
                            "path": "AI_ARCH.md",
                            "depends_on": ["core"],
                            "verification": [],
                        },
                    ],
                    "data": [],
                    "integrations": [],
                    "constraints": ["No chat history"],
                    "verification": ["Run unit tests"],
                    "risks": [],
                    "unresolved": [],
                },
                "ready": True,
                "next_question": None,
            },
            {},
        )

    def test_initialize_plan_generate_apply_verify_sync(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "smoke-app"
            provider = docuagent.ProviderConfig(
                base_url=self.TEST_PROVIDER["base_url"],
                model=self.TEST_PROVIDER["model"],
                api_key=self.TEST_PROVIDER["api_key"],
            )

            # 1) Complete the five profile facts, then architecture interview.
            state = docuagent.start_bootstrap({
                "path": str(root), "name": "Smoke App",
                "description": "A smoke-test Python tool.",
                "provider": self.TEST_PROVIDER, "interview_mode": "guided",
            })
            for index in range(5):
                payload = {"path": str(root), "answer": f"profile-{index}", "provider": self.TEST_PROVIDER}
                if index == 4:
                    with patch("docuagent.call_architecture_model", return_value=self.architecture_model_result()):
                        state = docuagent.answer_bootstrap(payload)
                else:
                    with patch("docuagent.call_architecture_model") as model_call:
                        state = docuagent.answer_bootstrap(payload)
                    self.assertEqual(0, model_call.call_count)
            self.assertEqual("review", state["status"])
            self.assertEqual(5, len(state["user_profile"]))
            state = docuagent.confirm_bootstrap({"path": str(root)})
            self.assertEqual("ready", state["status"])
            with patch("tasks.call_model_json", side_effect=self.doc_result):
                finalized = docuagent.finalize_bootstrap({
                    "path": str(root), "provider": self.TEST_PROVIDER,
                })
            self.assertEqual("initialized", finalized["status"])

            bootstrap_state = docuagent.read_json(docuagent.managed_path(root, "bootstrap.json"))
            architecture = bootstrap_state["architecture"]
            project = bootstrap_state["project"]
            verification = shlex.join([sys.executable, "--version"])

            implementation_result = {
                "thinking": "smoke implementation",
                "files": [{"path": "src/core.py", "content": "print('core')\n"}],
            }
            # 2) Architecture modules are work items already; no task-planning turn.
            works = docuagent.read_task_state(root)
            self.assertEqual("confirmed", works["status"])
            self.assertEqual(3, len(works["tasks"]))
            self.assertEqual("core", works["tasks"][0]["id"])
            self.assertEqual(["src/core.py"], works["tasks"][0]["target_files"])
            works["tasks"][0]["verification"] = [verification]
            docuagent.write_task_state(root, works)

            with patch(
                "tasks.call_model_json",
                side_effect=lambda *args, **kwargs: (
                    implementation_result
                    if "target" not in args[2]
                    else self.doc_result(*args, **kwargs)
                ),
            ):
                generated = docuagent.generate_next_task(root, provider, architecture, project)
                task = generated["tasks"][0]
                self.assertEqual("review", task["status"])
                self.assertEqual(1, len(task["patch"]))
                self.assertIn("+print('core')", task["patch"][0]["diff"])

                applied = docuagent.apply_task_patch(root, "core")
                self.assertEqual("applied", applied["tasks"][0]["status"])
                self.assertEqual("print('core')\n", (root / "src" / "core.py").read_text(encoding="utf-8"))

                verified = docuagent.verify_task(root, "core")
                self.assertEqual("verified", verified["tasks"][0]["status"])
                self.assertEqual(0, verified["tasks"][0]["verification_output"]["returncode"])

                # The extra modules exist to satisfy the 3-8 first-version module floor;
                # this smoke only exercises one implementation pipeline end-to-end.
                works = docuagent.read_task_state(root)
                for task in works["tasks"]:
                    if task["id"] != "core":
                        task["status"] = "rejected"
                docuagent.write_task_state(root, works)

                synced = docuagent.sync_docs(root, provider, architecture, project)
                self.assertEqual("done", synced["status"])
                self.assertIn("AI_ARCH.md", synced["updated"])
                self.assertIn("src/AI_ARCH.md", (root / "AI_ARCH.md").read_text(encoding="utf-8"))
                self.assertTrue((root / "src" / "AI_ARCH.md").exists())
                self.assertIn("core.py", (root / "src" / "AI_ARCH.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
