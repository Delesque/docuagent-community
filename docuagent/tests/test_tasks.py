import importlib.util
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import docuagent
import task_jobs
from agent_tools import AgentLoopCancelled
from bootstrap import ToolCallingNotSupported
from command_fixtures import verification_command


class TasksTest(unittest.TestCase):
    def setUp(self) -> None:
        # Generation now prefers native tool calling. These tests exercise the
        # legacy fallback by patching `tasks.call_model_json`, so make the native
        # first attempt fail the same way an unsupporting provider would.
        patcher = patch(
            "agent_tools.call_model_chat",
            side_effect=ToolCallingNotSupported("test fallback"),
        )
        patcher.start()
        self.addCleanup(patcher.stop)
    PROVIDER = {
        "enabled": True,
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key": "secret",
    }

    @staticmethod
    def doc_result(payload: dict, *, root_suffix: str = "") -> dict:
        target = payload["target"]
        lines = [f"# {target['directory']}"]
        lines.extend(f"- `{entry['name']}`" for entry in target["entries"])
        if target["path"] == "AI_ARCH.md":
            facts = payload["workspace_facts"]
            lines.extend(f"{name}/AI_ARCH.md" for name in facts["top_level_directories"])
            if facts.get("requires_python"):
                lines.append(facts["requires_python"])
            lines.append(root_suffix)
        return {"files": [{"path": target["path"], "content": "\n".join(lines)}]}

    def architecture(self) -> dict:
        return docuagent.normalize_architecture({
            "summary": "A small project.",
            "platform": "Windows",
            "language": "Python",
            "runtime": "Python 3.12",
            "frameworks": [],
            "stack": ["Python 3.12"],
            "modules": [
                {
                    "id": "core",
                    "name": "Core",
                    "responsibility": "Core logic.",
                    "path": "src/core",
                    "depends_on": [],
                }
            ],
            "data": [],
            "integrations": [],
            "constraints": [],
            "verification": ["python -m unittest"],
            "risks": [],
            "unresolved": [],
        })

    def project(self) -> tuple[Path, dict]:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        docuagent.atomic_write_json(
            docuagent.managed_path(root, "architecture.json"),
            {
                "schema_version": 1,
                "architecture_version": 1,
                "generated_at": "T",
                "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
                **self.architecture(),
            },
        )
        return root, self.architecture()

    def test_plan_validates_dag(self) -> None:
        with self.assertRaisesRegex(docuagent.WorkspaceError, "重复任务"):
            docuagent.normalize_task_plan({
                "tasks": [
                    {"id": "a", "summary": "A", "target_files": ["a.py"], "depends_on": []},
                    {"id": "a", "summary": "A2", "target_files": ["b.py"], "depends_on": []},
                ]
            }, ["a"])
        with self.assertRaisesRegex(docuagent.WorkspaceError, "依赖未知任务"):
            docuagent.normalize_task_plan({
                "tasks": [
                    {"id": "a", "summary": "A", "target_files": ["a.py"], "depends_on": ["nope"]},
                ]
            }, ["a"])
        with self.assertRaisesRegex(docuagent.WorkspaceError, "带环"):
            docuagent.normalize_task_plan({
                "tasks": [
                    {"id": "a", "module_id": "a", "summary": "A", "target_files": ["a.py"], "depends_on": ["b"]},
                    {"id": "b", "module_id": "b", "summary": "B", "target_files": ["b.py"], "depends_on": ["a"]},
                ]
            }, ["a", "b"])
        with self.assertRaisesRegex(docuagent.WorkspaceError, "不安全"):
            docuagent.normalize_task_plan({
                "tasks": [
                    {"id": "a", "summary": "A", "target_files": ["../escape.py"], "depends_on": []},
                ]
            }, ["a"])
        with self.assertRaisesRegex(docuagent.WorkspaceError, "缺少 module_id"):
            docuagent.normalize_task_plan({
                "tasks": [
                    {"id": "a", "summary": "A", "target_files": ["a.py"], "depends_on": []},
                ]
            }, ["core", "auth"])

    def test_normalize_task_plan_defaults_priority_to_medium(self) -> None:
        plan = docuagent.normalize_task_plan(
            {
                "tasks": [
                    {
                        "id": "core",
                        "module_id": "core",
                        "summary": "Core.",
                        "target_files": ["src/core.py"],
                        "depends_on": [],
                        "verification": [],
                    }
                ]
            },
            ["core"],
        )
        self.assertEqual("medium", plan["tasks"][0]["priority"])

    def test_sync_work_items_from_architecture_derives_one_per_module(self) -> None:
        root, architecture = self.project()
        architecture = {
            **architecture,
            "modules": [
                {
                    "id": "core",
                    "name": "Core",
                    "responsibility": "Core logic.",
                    "brief": "Implement core.",
                    "path": "src/core.py",
                    "depends_on": [],
                    "verification": [verification_command(root, "pass\n")],
                },
                {
                    "id": "shell",
                    "name": "Shell",
                    "responsibility": "Entry shell.",
                    "brief": "Wire modules.",
                    "path": "src/shell.py",
                    "depends_on": ["core"],
                    "verification": [],
                },
            ],
        }
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}

        state = docuagent.sync_work_items_from_architecture(root, architecture, project)

        self.assertEqual("confirmed", state["status"])
        self.assertEqual("architecture", state["source"])
        self.assertEqual(["core", "shell"], [task["id"] for task in state["tasks"]])
        self.assertEqual(["core"], state["tasks"][1]["depends_on"])
        self.assertEqual(["src/core.py"], state["tasks"][0]["target_files"])
        self.assertEqual([verification_command(root, "pass\n")], state["tasks"][0]["verification"])

    def test_set_task_priority_updates_task_and_metadata(self) -> None:
        root, architecture = self.project()
        docuagent.write_task_state(
            root,
            {
                "schema_version": 1,
                "task_version": 1,
                "status": "planned",
                "tasks": [
                    {
                        "id": "core",
                        "module_id": "core",
                        "summary": "Core.",
                        "target_files": ["src/core.py"],
                        "depends_on": [],
                        "verification": [],
                        "priority": "medium",
                        "status": "pending",
                        "patch": [],
                        "thinking": "",
                        "last_error": "",
                    }
                ],
                "orchestration": {
                    "summary": "Plan.",
                    "conflicts": [],
                    "priorities": {"core": "medium"},
                    "scope_module_ids": ["core"],
                },
                "last_error": "",
            },
        )
        state = docuagent.set_task_priority(root, "core", "high")
        self.assertEqual("high", state["tasks"][0]["priority"])
        self.assertEqual("high", state["orchestration"]["priorities"]["core"])

        with self.assertRaisesRegex(docuagent.WorkspaceError, "找不到任务"):
            docuagent.set_task_priority(root, "ghost", "low")
        with self.assertRaisesRegex(docuagent.WorkspaceError, "优先级"):
            docuagent.set_task_priority(root, "core", "urgent")

    def test_stream_plan_tasks_yields_reasoning_and_done(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        plan_result = {
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Implement core.",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [verification_command(root, "pass\n")],
                }
            ]
        }

        def fake_stream(*_args, **_kwargs):
            yield {"type": "reasoning", "text": "拆分任务依赖"}
            yield {"type": "done", "result": plan_result}

        with patch("tasks.stream_json_model", side_effect=fake_stream):
            events = list(
                docuagent.stream_plan_tasks(root, provider, architecture, project)
            )

        self.assertEqual("reasoning", events[0]["type"])
        self.assertIn("拆分", events[0]["text"])
        self.assertEqual("done", events[-1]["type"])
        self.assertEqual("planned", events[-1]["tasks"]["status"])
        state = docuagent.read_json(
            docuagent.managed_path(root, "tasks.json")
        )
        self.assertEqual("planned", state["status"])

    def test_plan_tasks_filters_to_requested_module_and_merges(self) -> None:
        root, architecture = self.project()
        architecture["modules"].append({
            "id": "auth",
            "name": "Auth",
            "responsibility": "Handle login.",
            "path": "src/auth",
            "depends_on": ["core"],
            "needs_ui": False,
            "group": None,
        })
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )

        with patch(
            "tasks.call_model_json",
            return_value={
                "tasks": [
                    {
                        "id": "auth",
                        "module_id": "auth",
                        "summary": "Implement auth.",
                        "target_files": ["src/auth.py"],
                        "depends_on": [],
                        "verification": [],
                    }
                ]
            },
        ):
            plan = docuagent.plan_tasks(
                root, provider, architecture, project, module_id="auth"
            )

        self.assertEqual(["auth"], [task["module_id"] for task in plan["tasks"]])

        with patch(
            "tasks.call_model_json",
            return_value={
                "tasks": [
                    {
                        "id": "core",
                        "module_id": "core",
                        "summary": "Implement core.",
                        "target_files": ["src/core.py"],
                        "depends_on": [],
                        "verification": [],
                    }
                ]
            },
        ):
            plan = docuagent.plan_tasks(
                root, provider, architecture, project, module_id="core"
            )

        self.assertEqual(
            {"auth", "core"},
            {task["module_id"] for task in plan["tasks"]},
        )
        self.assertEqual("planned", plan["status"])

    def test_stream_plan_tasks_filters_to_requested_module(self) -> None:
        root, architecture = self.project()
        architecture["modules"].append({
            "id": "auth",
            "name": "Auth",
            "responsibility": "Handle login.",
            "path": "src/auth",
            "depends_on": ["core"],
            "needs_ui": False,
            "group": None,
        })
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )

        def fake_stream(*_args, **_kwargs):
            yield {
                "type": "done",
                "result": {
                    "tasks": [
                        {
                            "id": "auth",
                            "module_id": "auth",
                            "summary": "Implement auth.",
                            "target_files": ["src/auth.py"],
                            "depends_on": [],
                            "verification": [],
                        }
                    ]
                },
            }

        with patch("tasks.stream_json_model", side_effect=fake_stream):
            events = list(
                docuagent.stream_plan_tasks(
                    root, provider, architecture, project, module_id="auth"
                )
            )

        done = events[-1]["tasks"]
        self.assertEqual(["auth"], [task["module_id"] for task in done["tasks"]])

    def test_plan_tasks_rejects_foreign_module_tasks(self) -> None:
        root, architecture = self.project()
        architecture["modules"].append({
            "id": "auth",
            "name": "Auth",
            "responsibility": "Handle login.",
            "path": "src/auth",
            "depends_on": ["core"],
            "needs_ui": False,
            "group": None,
        })
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )

        with patch(
            "tasks.call_model_json",
            return_value={
                "tasks": [
                    {
                        "id": "auth",
                        "module_id": "auth",
                        "summary": "Auth outside scope.",
                        "target_files": ["src/auth.py"],
                        "depends_on": [],
                        "verification": [],
                    }
                ]
            },
        ):
            with self.assertRaisesRegex(
                docuagent.WorkspaceError,
                "只允许目标模块",
            ):
                docuagent.plan_tasks(
                    root, provider, architecture, project, module_id="core"
                )

    def test_plan_tasks_rejects_unknown_module(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        with self.assertRaisesRegex(docuagent.WorkspaceError, "不存在模块"):
            docuagent.plan_tasks(
                root, provider, architecture, project, module_id="missing"
            )

    def test_full_loop_plan_generate_apply_verify_sync(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        plan_result = {
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Implement core.",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [verification_command(root, "pass\n")],
                }
            ]
        }
        impl_result = {
            "thinking": "ok",
            "files": [{"path": "src/core.py", "content": "print('core')\n"}],
        }
        results = iter([plan_result, impl_result])
        def model_side_effect(*args, **kwargs):
            try:
                return next(results)
            except StopIteration:
                return self.doc_result(args[2])

        with patch("tasks.call_model_json", side_effect=model_side_effect):
            plan = docuagent.plan_tasks(root, provider, architecture, project)
            self.assertEqual("planned", plan["status"])

            confirmed = docuagent.confirm_tasks(root)
            self.assertEqual("confirmed", confirmed["status"])

            generated = docuagent.generate_next_task(root, provider, architecture, project)
            task = generated["tasks"][0]
            self.assertEqual("review", task["status"])
            self.assertEqual(1, len(task["patch"]))
            self.assertIn("print('core')", task["patch"][0]["diff"])

            applied = docuagent.apply_task_patch(root, "core")
            self.assertEqual("applied", applied["tasks"][0]["status"])
            self.assertTrue((root / "src" / "core.py").exists())

            verified = docuagent.verify_task(root, "core")
            self.assertEqual("verified", verified["tasks"][0]["status"])

            synced = docuagent.sync_docs(root, provider, architecture, project)
            self.assertEqual("done", synced["status"])
            self.assertIn("AI_ARCH.md", synced["updated"])
            self.assertIn("src/AI_ARCH.md", (root / "AI_ARCH.md").read_text(encoding="utf-8"))
            child_arch = root / "src" / "AI_ARCH.md"
            self.assertTrue(child_arch.exists())
            child_content = child_arch.read_text(encoding="utf-8")
            self.assertIn("core.py", child_content)
            self.assertNotIn("- `AI_ARCH.md`", child_content)

    def test_code_write_creates_an_independent_documentation_task(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "review")
        docuagent.write_task_state(root, state)

        applied = docuagent.apply_task_patch(root, "core")

        self.assertEqual("applied", applied["tasks"][0]["status"])
        documentation = applied["documentation_task"]
        self.assertEqual("pending", documentation["status"])
        self.assertEqual("core", documentation["code_task_id"])
        self.assertEqual(
            {"src/core.py", "src/util.py"},
            set(documentation["changed_files"]),
        )
        self.assertEqual(["core"], documentation["affected_modules"])
        self.assertEqual("blocked", applied["delivery_status"])

    def test_document_sync_uses_disk_facts_and_blocks_missing_navigation(self) -> None:
        root, architecture = self.project()
        (root / "src").mkdir()
        (root / "src/main.py").write_text("from pathlib import Path\n", encoding="utf-8")
        (root / "pyproject.toml").write_text('[project]\nrequires-python = ">=3.11"\n', encoding="utf-8")
        (root / ".env").write_text("PRIVATE_SENTINEL", encoding="utf-8")
        (root / "AI_ARCH.md").write_text("original", encoding="utf-8")
        docuagent.write_task_state(root, self._partial_state(root, "verified"))
        provider = docuagent.ProviderConfig(**{k: self.PROVIDER[k] for k in ("base_url", "model", "api_key")})
        def incomplete_root(*args, **kwargs):
            payload = args[2]
            if payload["target"]["path"] == "AI_ARCH.md":
                return {"files": [{"path": "AI_ARCH.md", "content": "# Incomplete"}]}
            return self.doc_result(payload)
        with patch("tasks.call_model_json", side_effect=incomplete_root) as model:
            result = docuagent.sync_docs(root, provider, architecture, {})
        self.assertEqual("blocked", result["delivery_status"])
        self.assertEqual("verified", result["tasks"][0]["status"])
        self.assertEqual("original", (root / "AI_ARCH.md").read_text(encoding="utf-8"))
        root_call = next(call for call in reversed(model.call_args_list) if call.args[2]["target"]["path"] == "AI_ARCH.md")
        facts = root_call.args[2]["workspace_facts"]
        self.assertIn("src/main.py", facts["files"])
        self.assertEqual(">=3.11", facts["requires_python"])
        self.assertEqual(["from pathlib import Path"], facts["python_imports"]["src/main.py"])
        self.assertNotIn("PRIVATE_SENTINEL", str(model.call_args_list))
        self.assertTrue(root_call.args[2]["previous_sync_error"])
        with patch("tasks.call_model_json", side_effect=lambda *args, **kwargs: self.doc_result(args[2])):
            result = docuagent.retry_documentation(root, provider, architecture, {})
        self.assertEqual("ready", result["delivery_status"])

    def test_agent_registry_tracks_review_apply_verify(self) -> None:
        from agents import register_agent, read_registry
        root, _ = self.project()
        register_agent(root, "core", "core", ["src/core.py"], "review")
        docuagent.write_task_state(root, self._partial_state(root, "review"))
        docuagent.apply_task_patch(root, "core")
        self.assertEqual("idle", read_registry(root)["agents"]["core"]["status"])
        docuagent.verify_task(root, "core")
        self.assertEqual("idle", read_registry(root)["agents"]["core"]["status"])

    def test_documentation_failure_retries_twice_without_failing_code(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        state = self._partial_state(root, "verified")
        state["tasks"][0]["target_files"] = ["src/core.py"]
        state["documentation_task"] = {
            "status": "pending",
            "code_task_id": "core",
            "code_task_ids": ["core"],
            "changed_files": ["src/core.py"],
            "affected_modules": ["core"],
        }
        state["docs_in_sync"] = False
        state["docs_sync_required"] = True
        docuagent.write_task_state(root, state)

        with patch(
            "tasks.call_model_json",
            side_effect=[
                RuntimeError("doc attempt 1"),
                RuntimeError("doc attempt 2"),
                RuntimeError("doc attempt 3"),
            ],
        ) as model:
            failed = docuagent.sync_docs(root, provider, architecture, project)

        self.assertEqual(3, model.call_count)
        self.assertEqual("verified", failed["tasks"][0]["status"])
        self.assertEqual("blocked", failed["documentation_task"]["status"])
        self.assertEqual(2, failed["documentation_task"]["auto_retry_count"])
        self.assertEqual("blocked", failed["delivery_status"])
        self.assertIn("doc attempt 3", failed["documentation_task"]["last_error"])

    def test_retry_documentation_only_calls_doc_maintainer_and_recovers(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        state = self._partial_state(root, "verified")
        state["tasks"][0]["target_files"] = ["src/core.py"]
        state["documentation_task"] = {
            "status": "blocked",
            "code_task_id": "core",
            "code_task_ids": ["core"],
            "changed_files": ["src/core.py"],
            "affected_modules": ["core"],
            "last_error": "previous failure",
        }
        state["docs_in_sync"] = False
        state["docs_sync_required"] = True
        docuagent.write_task_state(root, state)

        with patch(
            "tasks.call_model_json",
            return_value={"files": [{"path": "AI_ARCH.md", "content": "# Demo\n"}]},
        ) as model:
            recovered = docuagent.retry_documentation(
                root, provider, architecture, project
            )

        self.assertEqual(1, model.call_count)
        self.assertEqual("verified", recovered["tasks"][0]["status"])
        self.assertEqual("synced", recovered["documentation_task"]["status"])
        self.assertEqual("ready", recovered["delivery_status"])

    def test_apply_patch_defers_nested_directory_documents_to_doc_agent(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "review")
        state["tasks"][0]["patch"] = [{
            "path": "src/nested/core.py",
            "before": "",
            "after": "print('core')\n",
            "diff": "+print('core')",
        }]
        state["tasks"][0]["target_files"] = ["src/nested/core.py"]
        docuagent.write_task_state(root, state)

        applied = docuagent.apply_task_patch(root, "core")

        self.assertEqual("applied", applied["tasks"][0]["status"])
        self.assertFalse(applied.get("docs_in_sync", True))
        self.assertFalse((root / "src" / "AI_ARCH.md").exists())
        self.assertFalse((root / "src" / "nested" / "AI_ARCH.md").exists())

    def test_verify_leaves_document_tree_for_doc_agent(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "applied")
        state["tasks"][0]["patch"] = []
        state["tasks"][0]["target_files"] = ["src/nested/core.py"]
        state["tasks"][0]["code_changed_files"] = ["src/nested/core.py"]
        (root / "src" / "nested").mkdir(parents=True)
        (root / "src" / "nested" / "core.py").write_text("print('core')\n", encoding="utf-8")
        docuagent.write_task_state(root, state)

        verified = docuagent.verify_task(root, "core")

        self.assertEqual("verified", verified["tasks"][0]["status"])
        self.assertEqual([], verified["tasks"][0]["documentation_updated"])
        self.assertFalse((root / "src" / "nested" / "AI_ARCH.md").exists())
        self.assertFalse(verified.get("docs_in_sync", True))

    def test_verify_without_a_recorded_write_does_not_create_documentation_task(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "applied")
        state["tasks"][0]["patch"] = []
        state["tasks"][0]["target_files"] = ["src/core.py"]
        (root / "src").mkdir(parents=True)
        (root / "src" / "core.py").write_text("print('existing')\n", encoding="utf-8")
        docuagent.write_task_state(root, state)

        verified = docuagent.verify_task(root, "core")

        self.assertEqual("verified", verified["tasks"][0]["status"])
        self.assertEqual("not_required", verified["documentation_task"]["status"])
        self.assertEqual("ready", verified["delivery_status"])

    def test_document_tree_includes_empty_dirs_but_ignores_caches(self) -> None:
        root, _ = self.project()
        (root / "src" / "empty").mkdir(parents=True)
        (root / ".cache" / "tool").mkdir(parents=True)
        (root / ".cache" / "tool" / "state.bin").write_text("x", encoding="utf-8")
        (root / "AI_ARCH.md").write_text("# Demo\n", encoding="utf-8")

        with self.assertRaisesRegex(docuagent.WorkspaceError, "src/AI_ARCH.md"):
            docuagent.maintain_document_tree(root)

        self.assertFalse((root / ".cache" / "AI_ARCH.md").exists())
        self.assertEqual(
            ["src/AI_ARCH.md", "src/empty/AI_ARCH.md"],
            docuagent.document_tree_gaps(root),
        )

    def _partial_state(self, root: Path, status: str) -> dict:
        return {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py", "src/util.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": status,
                    "patch": [
                        {
                            "path": "src/core.py",
                            "before": "",
                            "after": "print('core')\n",
                            "diff": "+print('core')",
                        },
                        {
                            "path": "src/util.py",
                            "before": "",
                            "after": "print('util')\n",
                            "diff": "+print('util')",
                        },
                    ],
                    "thinking": "",
                    "last_error": "",
                    "applied_files": [],
                    "rejected_files": [],
                    "pending_files": [],
                }
            ],
            "last_error": "",
        }

    def test_apply_task_patch_is_atomic_when_any_file_is_stale(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "review")
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "util.py").write_text("changed by user\n", encoding="utf-8")
        docuagent.write_task_state(root, state)

        result = docuagent.apply_task_patch(root, "core")

        task = result["tasks"][0]
        self.assertEqual("failed", task["status"])
        self.assertIn("STALE_VERSION", task["last_error"])
        self.assertFalse((root / "src" / "core.py").exists())
        self.assertEqual(
            "changed by user\n",
            (root / "src" / "util.py").read_text(encoding="utf-8"),
        )

    def test_apply_task_partial_is_atomic_when_any_accepted_file_is_stale(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "review")
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "util.py").write_text("changed by user\n", encoding="utf-8")
        docuagent.write_task_state(root, state)

        result = docuagent.apply_task_partial(
            root,
            "core",
            {"src/core.py": "accept", "src/util.py": "accept"},
        )

        task = result["tasks"][0]
        self.assertEqual("failed", task["status"])
        self.assertIn("STALE_VERSION", task["last_error"])
        self.assertEqual([], task["applied_files"])
        self.assertFalse((root / "src" / "core.py").exists())

    def test_apply_task_partial_writes_only_accepted_files(self) -> None:
        root, _ = self.project()
        docuagent.write_task_state(root, self._partial_state(root, "review"))

        state = docuagent.apply_task_partial(
            root,
            "core",
            {"src/core.py": "accept", "src/util.py": "pending"},
        )

        task = state["tasks"][0]
        self.assertEqual("partially_applied", task["status"])
        self.assertEqual(["src/core.py"], task["applied_files"])
        self.assertEqual(["src/util.py"], task["pending_files"])
        self.assertTrue((root / "src" / "core.py").exists())
        self.assertFalse((root / "src" / "util.py").exists())

    def test_apply_task_partial_continues_from_partial_status(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "partially_applied")
        state["tasks"][0]["applied_files"] = ["src/core.py"]
        state["tasks"][0]["pending_files"] = ["src/util.py"]
        docuagent.write_task_state(root, state)

        applied = docuagent.apply_task_partial(
            root,
            "core",
            {"src/core.py": "accept", "src/util.py": "accept"},
        )

        task = applied["tasks"][0]
        self.assertEqual("applied", task["status"])
        self.assertEqual(["src/core.py", "src/util.py"], task["applied_files"])
        self.assertEqual([], task["pending_files"])
        self.assertTrue((root / "src" / "util.py").exists())

    def test_apply_task_partial_merges_rejected_decision(self) -> None:
        root, _ = self.project()
        docuagent.write_task_state(root, self._partial_state(root, "review"))

        state = docuagent.apply_task_partial(
            root,
            "core",
            {"src/core.py": "reject", "src/util.py": "pending"},
        )

        task = state["tasks"][0]
        self.assertEqual("partially_applied", task["status"])
        self.assertEqual(["src/core.py"], task["rejected_files"])
        self.assertFalse((root / "src" / "core.py").exists())

    def test_apply_task_partial_maintains_only_accepted_directories(self) -> None:
        """ROADMAP P1-3: a rejected file must not drag its directory into the
        documentation refresh — only what actually reached the disk."""
        root, _ = self.project()
        state = self._partial_state(root, "review")
        state["tasks"][0]["target_files"] = ["src/core.py", "tools/cli.py"]
        state["tasks"][0]["patch"].append(
            {
                "path": "tools/cli.py",
                "before": "",
                "after": "print('cli')\n",
                "diff": "+print('cli')",
            }
        )
        docuagent.write_task_state(root, state)

        result = docuagent.apply_task_partial(
            root,
            "core",
            {"src/core.py": "accept", "tools/cli.py": "pending"},
        )

        documentation = result["documentation_task"]
        self.assertEqual(["src/core.py"], documentation["changed_files"])
        self.assertFalse((root / "src" / "AI_ARCH.md").exists())
        self.assertFalse((root / "tools" / "AI_ARCH.md").exists())

    def test_apply_task_hunks_maintains_only_that_file_directory(self) -> None:
        """ROADMAP P1-3: hunk application refreshes the one touched directory."""
        root, _ = self.project()
        state = self._partial_state(root, "review")
        state["tasks"][0]["target_files"] = ["src/core.py", "tools/cli.py"]
        state["tasks"][0]["patch"] = [
            {
                "path": "src/core.py",
                "before": "",
                "after": "print('core')\n",
                "diff": "+print('core')",
            },
            {
                "path": "tools/cli.py",
                "before": "",
                "after": "print('cli')\n",
                "diff": "+print('cli')",
            },
        ]
        docuagent.write_task_state(root, state)
        hunk_ids = [hunk["id"] for hunk in docuagent.task_hunks(root, "core", "src/core.py")]

        docuagent.apply_task_hunks(root, "core", "src/core.py", hunk_ids)

        state = docuagent.read_task_state(root)
        documentation = state["documentation_task"]
        self.assertEqual(["src/core.py"], documentation["changed_files"])
        self.assertFalse((root / "src" / "AI_ARCH.md").exists())
        self.assertFalse((root / "tools" / "AI_ARCH.md").exists())

    def test_sandbox_writes_register_only_applied_files_through_the_state_layer(self) -> None:
        """ROADMAP P1-3: sandbox apply registers exactly the files that landed,
        never the task's full target list, and through the shared state layer."""
        root, _ = self.project()
        state = self._partial_state(root, "applied")
        state["tasks"][0]["code_changed_files"] = ["src/core.py"]
        docuagent.register_completed_documentation(state)

        documentation = state["documentation_task"]
        self.assertEqual(["src/core.py"], documentation["changed_files"])
        self.assertEqual("pending", documentation["status"])
        self.assertEqual("blocked", state["delivery_status"])

    def test_sandbox_write_failures_block_delivery_through_the_state_layer(self) -> None:
        root, _ = self.project()
        state = self._partial_state(root, "applied")
        task = state["tasks"][0]
        task["code_changed_files"] = ["src/core.py"]
        task["documentation_error"] = "doc maintainer exploded"
        docuagent.register_completed_documentation(state)

        documentation = state["documentation_task"]
        self.assertEqual("blocked", documentation["status"])
        self.assertIn("doc maintainer exploded", documentation["last_error"])
        self.assertEqual("blocked", state["delivery_status"])
        self.assertTrue(task["documentation_registered"])

    def test_generated_patch_cannot_escape_project(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })
        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "bad",
                "files": [{"path": "../escape.py", "content": "x"}],
            },
        ):
            state = docuagent.generate_next_task(root, provider, architecture, project)
        self.assertEqual("failed", state["tasks"][0]["status"])
        self.assertIn("不安全", state["tasks"][0]["last_error"])

    def test_generated_python_syntax_error_fails(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "bad syntax",
                "files": [
                    {"path": "src/core.py", "content": "def broken(:\n    pass\n"}
                ],
            },
        ):
            state = docuagent.generate_next_task(root, provider, architecture, project)

        task = state["tasks"][0]
        self.assertEqual("failed", task["status"])
        self.assertIn("语法错误", task["last_error"])

    @unittest.skipUnless(
        importlib.util.find_spec("pyflakes") is not None,
        "pyflakes not installed",
    )
    def test_generated_python_undefined_name_fails(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "bad name",
                "files": [{"path": "src/core.py", "content": "print(missing_name)\n"}],
            },
        ):
            state = docuagent.generate_next_task(root, provider, architecture, project)

        task = state["tasks"][0]
        self.assertEqual("failed", task["status"])
        self.assertIn("undefined name", task["last_error"])

    def test_reject_task(self) -> None:
        root, _ = self.project()
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })
        state = docuagent.reject_task(root, "core")
        self.assertEqual("rejected", state["tasks"][0]["status"])

    def test_reject_blocked_handoff_task(self) -> None:
        root, _ = self.project()
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "blocked",
                    "patch": [],
                    "thinking": "",
                    "last_error": "已转接对话流：这个改动会改变模块边界。",
                }
            ],
            "last_error": "",
        })

        state = docuagent.reject_task(root, "core")

        self.assertEqual("rejected", state["tasks"][0]["status"])
        self.assertEqual("", state["tasks"][0]["last_error"])
        self.assertIn("越界转接已关闭", docuagent.read_work_log(root, "core"))

    def test_generate_wave_runs_ready_tasks_in_parallel(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "a",
                    "summary": "A",
                    "target_files": ["src/a.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
                {
                    "id": "b",
                    "summary": "B",
                    "target_files": ["src/b.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
            ],
            "last_error": "",
        })

        def impl_result(task_id: str) -> dict:
            return {
                "thinking": "ok",
                "files": [{"path": f"src/{task_id}.py", "content": f"print('{task_id}')\n"}],
            }

        with patch(
            "tasks.call_model_json",
            side_effect=[impl_result("a"), impl_result("b")],
        ) as model_call:
            state = docuagent.generate_wave(root, provider, architecture, project)

        self.assertEqual(2, model_call.call_count)
        self.assertEqual({"review", "review"}, {t["status"] for t in state["tasks"]})
        self.assertTrue((root / "src" / "a.py").exists() is False)

    def test_generate_wave_recovers_interrupted_running_task(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        # A zombie left by a run cut short before its final state was written.
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "a",
                    "summary": "A",
                    "target_files": ["src/a.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "running",
                    "patch": [],
                    "thinking": "",
                    "last_error": "模型返回值不是有效的 JSON 对象。",
                },
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "ok",
                "files": [{"path": "src/a.py", "content": "print('a')\n"}],
            },
        ):
            state = docuagent.generate_wave(root, provider, architecture, project)

        self.assertEqual("review", state["tasks"][0]["status"])
        self.assertEqual("", state["tasks"][0]["last_error"])

    def test_generate_wave_respects_dependencies(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "a",
                    "summary": "A",
                    "target_files": ["src/a.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
                {
                    "id": "b",
                    "summary": "B",
                    "target_files": ["src/b.py"],
                    "depends_on": ["a"],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "ok",
                "files": [{"path": "src/a.py", "content": "print('a')\n"}],
            },
        ) as model_call:
            state = docuagent.generate_wave(root, provider, architecture, project)

        self.assertEqual(1, model_call.call_count)
        self.assertEqual("review", state["tasks"][0]["status"])
        self.assertEqual("pending", state["tasks"][1]["status"])

    def test_stream_generate_wave_emits_progress_events(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "a",
                    "module_id": "core",
                    "summary": "A",
                    "target_files": ["src/a.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
                {
                    "id": "b",
                    "module_id": "core",
                    "summary": "B",
                    "target_files": ["src/b.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
            ],
            "last_error": "",
        })

        impl_results = {
            "a": {"thinking": "ok", "files": [{"path": "src/a.py", "content": "print('a')\n"}]},
            "b": {"thinking": "ok", "files": [{"path": "src/b.py", "content": "print('b')\n"}]},
        }

        def fake_call(*args, **_kwargs):
            task_id = args[2]["task"]["id"]
            return impl_results[task_id]

        with patch("tasks.call_model_json", side_effect=fake_call):
            events = list(
                docuagent.stream_generate_wave(root, provider, architecture, project)
            )

        types = [event["type"] for event in events]
        self.assertIn("task_started", types)
        reasoning = [event for event in events if event["type"] == "task_reasoning"]
        self.assertEqual({"a", "b"}, {event["task_id"] for event in reasoning})
        self.assertIn("task_done", types)
        self.assertEqual("done", events[-1]["type"])
        done = events[-1]["tasks"]
        self.assertEqual({"review", "review"}, {t["status"] for t in done["tasks"]})

    def test_cancel_tasks_marks_wave_events(self) -> None:
        tasks_module = __import__("tasks")
        tasks_module._register_wave(["a", "b"])
        self.assertEqual(1, docuagent.cancel_tasks(["a"]))
        self.assertTrue(tasks_module._task_cancelled("a"))
        self.assertFalse(tasks_module._task_cancelled("b"))
        self.assertEqual(2, docuagent.cancel_tasks(None))
        self.assertTrue(tasks_module._task_cancelled("a"))
        self.assertTrue(tasks_module._task_cancelled("b"))

    def test_nonstream_generate_wave_is_cancellable(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [{
                "id": "a",
                "module_id": "core",
                "summary": "A",
                "target_files": ["src/a.py"],
                "depends_on": [],
                "verification": [],
                "status": "pending",
                "patch": [],
                "thinking": "",
                "last_error": "",
            }],
            "last_error": "",
        })

        def blocking_stream(
            _project_root,
            task,
            _provider,
            _architecture,
            _project,
            cancel_check=None,
        ):
            while not cancel_check():
                time.sleep(0.01)
            task["status"] = "pending"
            task["last_error"] = "已停止"
            yield {
                "type": "task_done",
                "task_id": task["id"],
                "status": "pending",
                "error": "已停止",
            }

        result: dict = {}
        with patch(
            "task_generate.stream_generate_one_task",
            side_effect=blocking_stream,
        ):
            worker = threading.Thread(
                target=lambda: result.setdefault(
                    "state",
                    docuagent.generate_wave(root, provider, architecture, project),
                )
            )
            worker.start()
            deadline = time.time() + 5
            while not task_jobs.active_task_ids(root) and time.time() < deadline:
                time.sleep(0.01)

            self.assertEqual(
                1,
                docuagent.cancel_tasks(["a"], project_root=root),
            )
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual("pending", result["state"]["tasks"][0]["status"])
        self.assertEqual("已停止", result["state"]["tasks"][0]["last_error"])
        self.assertEqual("cancelled", task_jobs.read_jobs(root)[-1]["status"])

    def test_stream_generate_one_task_cancelled_returns_pending(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        task = {
            "id": "a",
            "module_id": "core",
            "summary": "A",
            "target_files": ["src/a.py"],
            "depends_on": [],
            "verification": [],
            "status": "running",
            "patch": [],
            "thinking": "",
            "last_error": "",
        }
        tasks_module = __import__("tasks")
        tasks_module._register_wave(["a"])
        tasks_module.cancel_tasks(["a"])

        events = list(
            docuagent.stream_generate_one_task(
                root, task, provider, architecture, project
            )
        )

        self.assertEqual("task_done", events[0]["type"])
        last = events[-1]
        self.assertEqual("pending", last["status"])
        self.assertEqual("已停止", last["error"])
        self.assertEqual("pending", task["status"])

    def test_generate_wave_skips_conflicting_workspace_tasks(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "a",
                    "module_id": "core",
                    "summary": "A",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
                {
                    "id": "b",
                    "module_id": "auth",
                    "summary": "B",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                },
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            side_effect=[
                {"thinking": "ok", "files": [{"path": "src/core.py", "content": "print('x')\n"}]},
            ],
        ):
            state = docuagent.generate_wave(root, provider, architecture, project)

        statuses = {task["id"]: task["status"] for task in state["tasks"]}
        self.assertEqual("review", statuses["a"])
        self.assertEqual("pending", statuses["b"])
        conflict = next(task for task in state["tasks"] if task["id"] == "b")
        self.assertIn("冲突", conflict["last_error"])
        agents = {agent["module_id"]: agent for agent in docuagent.list_agents(root)}
        self.assertEqual("review", agents["core"]["status"])

    def test_generate_next_injects_bounded_context_and_writes_work_log(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [verification_command(root, "pass\n")],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })
        captured: dict = {}

        def fake_call(*_args, **_kwargs):
            captured["input"] = _kwargs.get("model_input") or _args[2]
            return {
                "thinking": "bounded",
                "files": [{"path": "src/core.py", "content": "print('core')\n"}],
            }

        with patch("tasks.call_model_json", side_effect=fake_call):
            state = docuagent.generate_next_task(root, provider, architecture, project)

        self.assertEqual("review", state["tasks"][0]["status"])
        self.assertIn("agent_context", captured["input"])
        self.assertEqual("core", captured["input"]["agent_context"]["module_contract"]["id"])
        self.assertIn("src/core.py", captured["input"]["agent_context"]["allowed_read_scope"])
        self.assertNotIn("architecture", captured["input"])
        work_log = docuagent.read_work_log(root, "core")
        self.assertIn("生成完成", work_log)

    def test_generate_next_handoff_blocks_without_patch(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "outside scope",
                "files": [],
                "needs_handoff": "这个改动会改变模块边界。",
            },
        ):
            state = docuagent.generate_next_task(root, provider, architecture, project)

        task = state["tasks"][0]
        self.assertEqual("blocked", task["status"])
        self.assertEqual([], task["patch"])
        self.assertIn("转接对话流", task["last_error"])
        self.assertIn("越界转接", docuagent.read_work_log(root, "core"))
        self.assertEqual("generation", docuagent.read_error_memory(root, "core")[0]["source"])

    def test_retry_task_reruns_blocked_handoff(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "blocked",
                    "patch": [],
                    "thinking": "",
                    "last_error": "已转接对话流：这个改动会改变模块边界。",
                }
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "ok",
                "files": [{"path": "src/core.py", "content": "print('core')\n"}],
            },
        ):
            state = docuagent.retry_task(
                root, provider, architecture, project, "core"
            )

        task = state["tasks"][0]
        self.assertEqual("review", task["status"])
        self.assertEqual(1, len(task["patch"]))
        self.assertIn("对话流裁决", docuagent.read_work_log(root, "core"))

    def test_retry_task_rejects_non_handoff_blocked(self) -> None:
        root, _ = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "blocked",
                    "patch": [],
                    "thinking": "",
                    "last_error": "模块已过期，需要重新确认后生成。",
                }
            ],
            "last_error": "",
        })
        with self.assertRaisesRegex(docuagent.WorkspaceError, "越界转接"):
            docuagent.retry_task(
                root, provider, {}, project, "core"
            )

    def test_retry_task_regenerates_interrupted_running_task(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "running",
                    "patch": [],
                    "thinking": "",
                    "last_error": "上次生成被中断",
                }
            ],
            "last_error": "上次生成被中断",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "ok",
                "files": [{"path": "src/core.py", "content": "print('core')\n"}],
            },
        ):
            state = docuagent.retry_task(
                root, provider, architecture, project, "core"
            )

        self.assertEqual("review", state["tasks"][0]["status"])
        self.assertEqual(1, len(state["tasks"][0]["patch"]))
        self.assertIn("print('core')", state["tasks"][0]["patch"][0]["after"])

    def test_retry_task_regenerates_failed_generation(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "failed",
                    "patch": [],
                    "thinking": "",
                    "last_error": "生成失败：model error",
                }
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "ok",
                "files": [{"path": "src/core.py", "content": "print('core')\n"}],
            },
        ):
            state = docuagent.retry_task(
                root, provider, architecture, project, "core"
            )

        task = state["tasks"][0]
        self.assertEqual("review", task["status"])
        self.assertEqual(1, len(task["patch"]))
        self.assertIn("错误决策重试", docuagent.read_work_log(root, "core"))

    def test_retry_task_reverifies_failed_patch(self) -> None:
        root, _ = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "failed",
                    "patch": [
                        {
                            "path": "src/core.py",
                            "before": "",
                            "after": "print('core')\n",
                            "diff": "+print('core')",
                        }
                    ],
                    "thinking": "",
                    "last_error": "验证失败：boom",
                    "verification_output": {
                        "stdout": "",
                        "stderr": "boom",
                        "returncode": 1,
                        "command": "python -c boom",
                        "timestamp": "",
                    },
                }
            ],
            "last_error": "",
        })

        state = docuagent.retry_task(
            root, provider, {}, project, "core"
        )

        task = state["tasks"][0]
        self.assertEqual("verified", task["status"])
        self.assertEqual("", task["last_error"])
        self.assertIn("错误决策重试", docuagent.read_work_log(root, "core"))

    def test_repair_task_replaces_failed_patch(self) -> None:
        root, architecture = self.project()
        # This test pins the auto-apply repair path; review mode settles a
        # repair into a patch instead (see test_apply_mode.py).
        docuagent.write_apply_mode(root, "auto")
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        (root / "src").mkdir(parents=True, exist_ok=True)
        (root / "src" / "core.py").write_text("print('old')\n", encoding="utf-8")
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [verification_command(root, "print('ok')\n", name="verify_ok.py")],
                    "status": "failed",
                    "patch": [
                        {
                            "path": "src/core.py",
                            "before": "",
                            "after": "print('old')\n",
                            "diff": "+print('old')",
                        }
                    ],
                    "thinking": "",
                    "last_error": "验证失败：boom",
                    "verification_output": {
                        "stdout": "",
                        "stderr": "boom",
                        "returncode": 1,
                        "command": "python -c boom",
                        "timestamp": "",
                    },
                }
            ],
            "last_error": "",
        })

        with patch(
            "tasks.call_model_json",
            return_value={
                "thinking": "fix it",
                "summary": "replace old print",
                "files": [{"path": "src/core.py", "content": "print('fixed')\n"}],
            },
        ):
            state = docuagent.repair_task(
                root, provider, architecture, project, "core"
            )

        task = state["tasks"][0]
        self.assertEqual("verified", task["status"])
        self.assertEqual(1, task["repair_attempts"])
        self.assertEqual(
            "print('fixed')\n",
            (root / "src" / "core.py").read_text(encoding="utf-8"),
        )
        self.assertIn("验证修复", docuagent.read_work_log(root, "core"))

    def test_repair_task_is_cancellable(self) -> None:
        root, architecture = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [{
                "id": "core",
                "module_id": "core",
                "summary": "Core",
                "target_files": ["src/core.py"],
                "depends_on": [],
                "verification": ["python -m unittest"],
                "status": "failed",
                "patch": [{
                    "path": "src/core.py",
                    "before": "",
                    "after": "print('old')\n",
                    "diff": "",
                    "hunks": [],
                }],
                "thinking": "",
                "last_error": "验证失败",
                "verification_output": {
                    "command": "python -m unittest",
                    "stdout": "",
                    "stderr": "failed",
                    "returncode": 1,
                },
            }],
            "last_error": "验证失败",
        })

        def blocking_repair(*_args, **kwargs):
            cancel_check = kwargs["cancelled"]
            while not cancel_check():
                time.sleep(0.01)
            raise AgentLoopCancelled()

        result: dict = {}
        with patch("task_verify.run_agent_tool_loop", side_effect=blocking_repair):
            worker = threading.Thread(
                target=lambda: result.setdefault(
                    "state",
                    docuagent.repair_task(
                        root,
                        provider,
                        architecture,
                        project,
                        "core",
                    ),
                )
            )
            worker.start()
            deadline = time.time() + 5
            while not task_jobs.active_task_ids(root) and time.time() < deadline:
                time.sleep(0.01)

            self.assertEqual(
                1,
                docuagent.cancel_tasks(["core"], project_root=root),
            )
            worker.join(timeout=5)

        self.assertFalse(worker.is_alive())
        self.assertEqual("pending", result["state"]["tasks"][0]["status"])
        self.assertEqual("已停止", result["state"]["tasks"][0]["last_error"])
        self.assertEqual("cancelled", task_jobs.read_jobs(root)[-1]["status"])

    def test_repair_task_stops_after_max_attempts(self) -> None:
        root, _ = self.project()
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        provider = docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "failed",
                    "patch": [
                        {
                            "path": "src/core.py",
                            "before": "",
                            "after": "print('old')\n",
                            "diff": "+print('old')",
                        }
                    ],
                    "thinking": "",
                    "last_error": "验证失败：boom",
                    "repair_attempts": __import__("tasks").MAX_REPAIR_ATTEMPTS,
                    "verification_output": {
                        "stdout": "",
                        "stderr": "boom",
                        "returncode": 1,
                        "command": "python -c boom",
                        "timestamp": "",
                    },
                }
            ],
            "last_error": "",
        })

        with self.assertRaisesRegex(docuagent.WorkspaceError, "自动修复上限"):
            docuagent.repair_task(
                root, provider, {}, project, "core"
            )

    def test_stream_verify_task_yields_output_and_verifies(self) -> None:
        root, _ = self.project()
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [verification_command(root, "print('stream ok')\n", name="verify_stream.py")],
                    "status": "applied",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })

        events = list(docuagent.stream_verify_task(root, "core"))

        lines = [event["line"] for event in events if event["type"] == "output"]
        self.assertTrue(any("stream ok" in line for line in lines))
        done = events[-1]
        self.assertEqual("done", done["type"])
        self.assertEqual("verified", done["state"]["tasks"][0]["status"])

    def test_verify_task_process_is_cancellable(self) -> None:
        root, _ = self.project()
        (root / "slow.py").write_text(
            "import time\nprint('start', flush=True)\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [{
                "id": "core",
                "module_id": "core",
                "summary": "Core",
                "target_files": ["src/core.py"],
                "depends_on": [],
                "verification": [f"{sys.executable} slow.py"],
                "status": "applied",
                "patch": [],
                "thinking": "",
                "last_error": "",
            }],
            "last_error": "",
        })
        handle = task_jobs.begin_job(
            root,
            ["core"],
            kind="verify",
            transport="sync",
        )
        result: dict = {}

        worker = threading.Thread(
            target=lambda: result.setdefault(
                "state",
                docuagent.verify_task(
                    root,
                    "core",
                    cancel_check=lambda: handle.cancelled("core"),
                ),
            )
        )
        worker.start()
        time.sleep(0.3)
        self.assertEqual(
            1,
            docuagent.cancel_tasks(["core"], project_root=root),
        )
        worker.join(timeout=5)
        task_jobs.finish_job(handle, "cancelled")

        self.assertFalse(worker.is_alive())
        self.assertEqual("applied", result["state"]["tasks"][0]["status"])
        self.assertEqual("已停止验证。", result["state"]["tasks"][0]["last_error"])

    def test_stream_verify_task_marks_failed_on_nonzero(self) -> None:
        root, _ = self.project()
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [verification_command(root, "raise SystemExit(7)\n", name="verify_exit7.py")],
                    "status": "applied",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })

        events = list(docuagent.stream_verify_task(root, "core"))

        done = events[-1]
        self.assertEqual("failed", done["state"]["tasks"][0]["status"])
        self.assertEqual(
            7,
            done["state"]["tasks"][0]["verification_output"]["returncode"],
        )

    def test_stream_verify_task_requires_applied_status(self) -> None:
        root, _ = self.project()
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "review",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })

        with self.assertRaisesRegex(docuagent.WorkspaceError, "还没有应用"):
            list(docuagent.stream_verify_task(root, "core"))

    def test_mark_stale_tasks_blocks_owned_tasks(self) -> None:
        root, _ = self.project()
        docuagent.write_stale_modules(root, {"core"})
        docuagent.write_task_state(root, {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "pending",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                }
            ],
            "last_error": "",
        })

        state = docuagent.mark_stale_tasks(
            root,
            docuagent.read_task_state(root),
        )
        self.assertEqual("blocked", state["tasks"][0]["status"])


class FileVersionGuardTest(unittest.TestCase):
    """Version guard: a patch write must not clobber a newer on-disk edit."""

    def test_passes_when_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.py"
            target.write_text("same", encoding="utf-8")
            docuagent.ensure_file_unchanged(target, "a.py", "same")

    def test_raises_when_stale(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "a.py"
            target.write_text("newer", encoding="utf-8")
            with self.assertRaisesRegex(docuagent.WorkspaceError, "STALE_VERSION"):
                docuagent.ensure_file_unchanged(target, "a.py", "older")

    def test_missing_file_expected_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "missing.py"
            docuagent.ensure_file_unchanged(target, "missing.py", "")

    def test_missing_file_expected_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "missing.py"
            with self.assertRaisesRegex(docuagent.WorkspaceError, "STALE_VERSION"):
                docuagent.ensure_file_unchanged(target, "missing.py", "expected")


class EditTaskPatchTest(unittest.TestCase):
    """Editable patch: a user edit rewrites after + regenerates the diff."""

    def _review_state(self) -> dict:
        return {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "review",
                    "patch": [
                        {
                            "path": "src/core.py",
                            "before": "",
                            "after": "print('old')\n",
                            "diff": "+print('old')",
                        }
                    ],
                    "thinking": "",
                    "last_error": "",
                    "applied_files": [],
                    "rejected_files": [],
                    "pending_files": [],
                }
            ],
            "last_error": "",
        }

    def test_edit_patch_updates_after_and_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docuagent.write_task_state(root, self._review_state())
            state = docuagent.edit_task_patch(root, "core", "src/core.py", "print('new')\n")
            entry = state["tasks"][0]["patch"][0]
            self.assertEqual("print('new')\n", entry["after"])
            self.assertIn("print('new')", entry["diff"])

    def test_edit_patch_rejects_stale_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docuagent.write_task_state(root, self._review_state())
            docuagent.edit_task_patch(root, "core", "src/core.py", "print('new')\n")
            with self.assertRaisesRegex(docuagent.WorkspaceError, "STALE_PATCH"):
                docuagent.edit_task_patch(
                    root,
                    "core",
                    "src/core.py",
                    "print('old draft')\n",
                    base_after="print('old')\n",
                )


    def test_edit_patch_rejects_unknown_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docuagent.write_task_state(root, self._review_state())
            with self.assertRaisesRegex(docuagent.WorkspaceError, "没有文件"):
                docuagent.edit_task_patch(root, "core", "missing.py", "x")


class HunkApplyTest(unittest.TestCase):
    BEFORE = "def a():\n    pass\ndef b():\n    pass\n"
    AFTER = "def a():\n    return 1\ndef b():\n    return 2\n"

    def test_diff_hunks_splits_changes(self) -> None:
        hunks = docuagent.diff_hunks(self.BEFORE, self.AFTER)
        self.assertEqual(2, len(hunks))
        self.assertTrue(all(h["tag"] == "replace" for h in hunks))

    def test_apply_hunks_partial(self) -> None:
        hunks = docuagent.diff_hunks(self.BEFORE, self.AFTER)
        partial = docuagent.apply_hunks(self.BEFORE, self.AFTER, {hunks[0]["id"]})
        self.assertIn("return 1", partial)
        self.assertIn("    pass\n", partial)
        self.assertNotIn("return 2", partial)

    def test_apply_hunks_all(self) -> None:
        hunks = docuagent.diff_hunks(self.BEFORE, self.AFTER)
        partial = docuagent.apply_hunks(
            self.BEFORE, self.AFTER, {h["id"] for h in hunks}
        )
        self.assertEqual(self.AFTER, partial)

    def test_task_hunks_returns_entry_hunks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            docuagent.write_task_state(root, {
                "schema_version": 1,
                "task_version": 1,
                "status": "confirmed",
                "tasks": [{
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "review",
                    "patch": [{
                        "path": "src/core.py",
                        "before": self.BEFORE,
                        "after": self.AFTER,
                        "diff": "",
                    }],
                    "thinking": "",
                    "last_error": "",
                    "applied_files": [],
                    "rejected_files": [],
                    "pending_files": [],
                }],
                "last_error": "",
            })
            hunks = docuagent.task_hunks(root, "core", "src/core.py")
            self.assertEqual(2, len(hunks))
            self.assertEqual("replace", hunks[0]["tag"])

    def test_apply_task_hunks_writes_partial(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir(parents=True)
            (root / "src" / "core.py").write_text(self.BEFORE, encoding="utf-8")
            docuagent.write_task_state(root, {
                "schema_version": 1,
                "task_version": 1,
                "status": "confirmed",
                "tasks": [{
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "review",
                    "patch": [{
                        "path": "src/core.py",
                        "before": self.BEFORE,
                        "after": self.AFTER,
                        "diff": "",
                    }],
                    "thinking": "",
                    "last_error": "",
                    "applied_files": [],
                    "rejected_files": [],
                    "pending_files": [],
                }],
                "last_error": "",
            })
            hunks = docuagent.diff_hunks(self.BEFORE, self.AFTER)
            state = docuagent.apply_task_hunks(
                root, "core", "src/core.py", [hunks[0]["id"]]
            )
            content = (root / "src" / "core.py").read_text(encoding="utf-8")
            self.assertIn("return 1", content)
            self.assertIn("    pass\n", content)
            self.assertNotIn("return 2", content)
            self.assertEqual(["src/core.py"], state["tasks"][0]["applied_files"])
            self.assertEqual(["src/core.py"], state["tasks"][0]["code_changed_files"])
            self.assertEqual("applied", state["tasks"][0]["status"])


class DiagnoseFileTest(unittest.TestCase):
    def test_diagnose_syntax_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.py").write_text("def f(:", encoding="utf-8")
            result = docuagent.diagnose_file(root, "bad.py")
            self.assertEqual(1, len(result["diagnostics"]))
            self.assertEqual("error", result["diagnostics"][0]["severity"])
            self.assertEqual("syntax", result["diagnostics"][0]["source"])

    @unittest.skipUnless(
        importlib.util.find_spec("pyflakes") is not None,
        "pyflakes not installed",
    )
    def test_diagnose_pyflakes_issue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "unused.py").write_text("import os", encoding="utf-8")
            result = docuagent.diagnose_file(root, "unused.py")
            messages = [d["message"] for d in result["diagnostics"]]
            self.assertTrue(any("imported but unused" in m for m in messages))

    def test_diagnose_non_python_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "notes.md").write_text("# hi", encoding="utf-8")
            result = docuagent.diagnose_file(root, "notes.md")
            self.assertEqual([], result["diagnostics"])
