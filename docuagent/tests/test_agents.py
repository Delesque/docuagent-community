import tempfile
import unittest
from pathlib import Path

import docuagent
import workspace


class AgentsTest(unittest.TestCase):
    def test_register_list_update_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)

            entry = docuagent.register_agent(
                root,
                "core",
                "core-task",
                ["src/core.py"],
                "running",
            )
            self.assertEqual("core", entry["module_id"])
            self.assertEqual("running", entry["status"])
            self.assertIn("work_log", entry)
            self.assertIn("error_memory", entry)
            self.assertTrue(
                (root / ".docuagent" / "agents" / "core" / "work_log.md").exists()
            )
            self.assertTrue(
                (root / ".docuagent" / "agents" / "core" / "error_memory.json").exists()
            )

            agents = docuagent.list_agents(root)
            self.assertEqual(1, len(agents))
            self.assertEqual("core", agents[0]["module_id"])

            updated = docuagent.update_agent_status(root, "core", "review")
            self.assertEqual("review", updated["status"])
            self.assertEqual("review", docuagent.list_agents(root)[0]["status"])

    def test_workspace_locks_conflict_on_shared_file(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)

            granted = docuagent.acquire_workspace_locks(root, [
                ("a", ["src/core/a.py"]),
                ("b", ["src/core/a.py"]),
                ("c", ["src/other/c.py"]),
            ])

            self.assertEqual(["a", "c"], granted)
            docuagent.release_workspace_locks(root, ["a", "c"])
            granted_again = docuagent.acquire_workspace_locks(root, [
                ("b", ["src/core/a.py"]),
            ])
            self.assertEqual(["b"], granted_again)

    def test_work_log_and_error_memory_persist(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            docuagent.register_agent(root, "core", "core-task", ["src/core.py"])

            docuagent.append_work_log(
                root, "core", "生成完成", "core-task", "文件：src/core.py"
            )
            docuagent.append_error_memory(
                root,
                "core",
                "verification",
                "验证命令失败",
                task_id="core-task",
                related_files=["src/core.py"],
            )

            detail = docuagent.agent_detail(root, "core")
            self.assertIn("生成完成", detail["work_log"])
            self.assertTrue(detail["work_log_exists"])
            self.assertTrue(detail["error_memory_exists"])
            self.assertEqual(1, len(detail["error_memory"]))
            self.assertEqual("验证命令失败", detail["error_memory"][0]["error"])
            self.assertEqual("verification", detail["error_memory"][0]["source"])

    def test_agent_message_forwarding_and_error_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            docuagent.register_agent(root, "core", "core-task", ["src/core.py"])
            docuagent.append_error_memory(
                root, "core", "verification", "超时", task_id="core-task"
            )

            sent = docuagent.send_agent_message(root, "core", "请重试一次")
            self.assertIn("请重试一次", sent["content"])
            self.assertIn("user message", sent["content"])

            cleared = docuagent.clear_error_memory(root, "core")
            self.assertEqual([], cleared)
            self.assertEqual(
                [],
                docuagent.agent_detail(root, "core")["error_memory"],
            )

    def test_build_agent_context_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            docuagent.atomic_write_text(
                docuagent.managed_path(root, "project.md"),
                "# Project Purpose\n\nStable overview.",
            )
            docuagent.atomic_write_text(
                docuagent.managed_path(root, "standards.md"),
                "- Prefer stdlib over dependencies.",
            )
            docuagent.atomic_write_text(
                docuagent.managed_path(root, "recipes.md"),
                "- Use dataclasses for immutable value objects.",
            )
            architecture = {
                "modules": [
                    {
                        "id": "core",
                        "name": "Core",
                        "responsibility": "Core logic.",
                        "brief": "Core logic.",
                        "path": "src/core",
                        "needs_ui": False,
                        "depends_on": [],
                    }
                ]
            }

            context = docuagent.build_agent_context(
                root,
                "core",
                architecture,
                {
                    "target_files": ["src/core.py"],
                    "ui_context": {"kind": "layout_delta", "module_id": "core"},
                },
            )

            self.assertEqual("Core logic.", context["module_contract"]["responsibility"])
            self.assertIn("Stable overview.", context["project_overview"])
            self.assertIn("stdlib", context["standards"])
            self.assertIn("dataclasses", context["recipes"])
            self.assertIn("src/core.py", context["allowed_read_scope"])
            self.assertEqual(
                {"read_project", "read_architecture", "write_sandbox", "run_verification"},
                set(context["capabilities"]),
            )
            self.assertEqual("layout_delta", context["ui_context"]["kind"])
            self.assertNotIn("architecture", context)
            self.assertNotIn("conversation", context)

    def test_build_agent_context_includes_directory_document_chain(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "src" / "core").mkdir(parents=True)
        (root / "src" / "AI_ARCH.md").write_text(
            "# Purpose\n\nCore source.\n", encoding="utf-8"
        )
        architecture = {
            "modules": [{
                "id": "core",
                "name": "Core",
                "path": "src/core",
                "responsibility": "Core logic",
                "depends_on": [],
            }]
        }

        context = docuagent.build_agent_context(root, "core", architecture, {
            "target_files": ["src/core/main.py"],
        })

        required = context["directory_documents"]["required_paths"]
        self.assertNotIn("AI_ARCH.md", required)
        self.assertIn("src/AI_ARCH.md", required)
        self.assertNotIn("src/core/AI_ARCH.md", required)
        self.assertIn("src/AI_ARCH.md", context["allowed_read_scope"])
        self.assertIs(context["directory_documents"], context["navigation_documents"])

    def test_build_agent_context_injects_contract_view(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            workspace.write_contracts(root, {
                "schema_version": 1,
                "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
                "vocabulary": [
                    {"term": "invoice_id", "owner": "billing", "kind": "identifier",
                     "type": "str", "format": "^INV-[0-9]+$",
                     "forbidden_aliases": ["inv_id"]},
                ],
                "shared_kernel": [],
                "commands": [],
                "modules": [
                    {"id": "billing", "path": "src/billing", "depends_on": ["pricing"],
                     "exports": [{"symbol": "create_invoice", "kind": "function"}],
                     "consumes": []},
                    {"id": "pricing", "path": "src/pricing", "depends_on": [],
                     "exports": [{"symbol": "calculate_price", "kind": "function"}],
                     "consumes": []},
                ],
                "recipes": [],
            })
            architecture = {
                "modules": [
                    {"id": "billing", "name": "Billing", "responsibility": "Invoice creation.",
                     "brief": "", "path": "src/billing", "needs_ui": False,
                     "depends_on": ["pricing"]},
                    {"id": "pricing", "name": "Pricing", "responsibility": "Price calculation.",
                     "brief": "", "path": "src/pricing", "needs_ui": False,
                     "depends_on": []},
                ]
            }
            context = docuagent.build_agent_context(root, "billing", architecture, {"summary": "create invoice"})
            view = context["contract_view"]
            self.assertEqual(["create_invoice"], [e["symbol"] for e in view["own_exports"]])
            self.assertEqual(
                [("pricing", "calculate_price")],
                [(entry["module_id"], entry["exports"][0]["symbol"])
                 for entry in view["dependency_exports"]],
            )
            self.assertEqual(["invoice_id"], [v["term"] for v in view["vocabulary"]])

    def test_build_agent_context_adds_dependency_paths_to_read_only_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            architecture = {
                "modules": [
                    {"id": "core", "name": "Core", "path": "src/core", "depends_on": []},
                    {"id": "auth", "name": "Auth", "path": "src/auth", "depends_on": ["core"]},
                ]
            }
            context = docuagent.build_agent_context(
                root,
                "auth",
                architecture,
                {"target_files": ["src/auth/api.py"]},
            )
            self.assertIn("src/core", context["allowed_read_scope"])
            self.assertNotIn("src/core", context["allowed_write_scope"])
            self.assertIn("src/auth/api.py", context["allowed_write_scope"])
            self.assertIn("src/auth", context["allowed_write_scope"])

    def test_build_agent_context_injects_forwarded_session_tail(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            docuagent.send_agent_message(root, "core", "允许你修改 src/main.js 完成接线。")
            architecture = {
                "modules": [
                    {
                        "id": "core",
                        "name": "Core",
                        "responsibility": "Core logic.",
                        "brief": "Core logic.",
                        "path": "src/core",
                        "needs_ui": False,
                        "depends_on": [],
                    }
                ]
            }

            context = docuagent.build_agent_context(
                root,
                "core",
                architecture,
                {"target_files": ["src/core.py"]},
            )

            self.assertIn("允许你修改 src/main.js", context["session_tail"])


    def test_build_agent_context_falls_back_to_default_standards(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            architecture = {
                "modules": [
                    {
                        "id": "core",
                        "name": "Core",
                        "responsibility": "Core logic.",
                        "brief": "Core logic.",
                        "path": "src/core",
                        "needs_ui": False,
                        "depends_on": [],
                    }
                ]
            }

            context = docuagent.build_agent_context(
                root,
                "core",
                architecture,
                {"target_files": ["src/core.py"]},
            )

            self.assertIn("Project Standards", context["standards"])
            self.assertIn("Never declare placeholders", context["standards"])

    def test_agent_detail_rejects_unknown_agent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            with self.assertRaisesRegex(docuagent.WorkspaceError, "找不到子 Agent"):
                docuagent.agent_detail(root, "ghost")


if __name__ == "__main__":
    unittest.main()
