import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import agent_tools
import agents
import sandbox
import workspace
from bootstrap import ProviderConfig
from core import WorkspaceError
from command_fixtures import verification_command


class AgentToolsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src").mkdir(parents=True)
        (self.root / "src" / "core.py").write_text("print('core')\n", encoding="utf-8")
        self.task = {
            "id": "core",
            "module_id": "core",
            "summary": "Core",
            "target_files": ["src/core.py"],
            "verification": [verification_command(self.root)],
            "status": "running",
        }
        self.provider = ProviderConfig(
            base_url="http://localhost:11434/v1",
            model="local",
            api_key="",
        )

    def test_read_file_tool_returns_content(self) -> None:
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "read_file", "args": {"path": "src/core.py"}},
            agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["src", "src/core.py", "AI_ARCH.md"]},
        )
        self.assertEqual("print('core')\n", result["content"])

    def test_read_ai_arch_returns_root_to_target_chain(self) -> None:
        (self.root / "AI_ARCH.md").write_text("# Root\n", encoding="utf-8")
        (self.root / "src" / "AI_ARCH.md").write_text("# Source\n", encoding="utf-8")
        (self.root / "src" / "nested").mkdir()
        (self.root / "src" / "nested" / "AI_ARCH.md").write_text(
            "# Nested\n", encoding="utf-8"
        )
        context = {
            "capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"],
            "allowed_read_scope": ["src", "AI_ARCH.md"],
            "navigation_documents": {
                "required_paths": [
                    "AI_ARCH.md",
                    "src/AI_ARCH.md",
                    "src/nested/AI_ARCH.md",
                ],
                "read_paths": [],
                "enforced": True,
            },
        }

        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "read_ai_arch", "args": {"path": "src/nested/core.py"}},
            agent_context=context,
        )

        self.assertEqual(
            ["AI_ARCH.md", "src/AI_ARCH.md", "src/nested/AI_ARCH.md"],
            result["required_paths"],
        )
        self.assertEqual(
            result["required_paths"],
            context["navigation_documents"]["read_paths"],
        )

    def test_write_file_requires_navigation_chain(self) -> None:
        (self.root / "AI_ARCH.md").write_text("# Root\n", encoding="utf-8")
        (self.root / "src" / "AI_ARCH.md").write_text("# Source\n", encoding="utf-8")
        context = {
            "capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"],
            "allowed_read_scope": ["src", "AI_ARCH.md"],
            "allowed_write_scope": ["src"],
            "navigation_documents": {
                "required_paths": ["AI_ARCH.md", "src/AI_ARCH.md"],
                "read_paths": [],
                "enforced": True,
            },
        }
        sb = sandbox.create_sandbox(self.root)
        try:
            with self.assertRaisesRegex(WorkspaceError, "read_ai_arch"):
                agent_tools.execute_tool(
                    self.root,
                    self.task,
                    {
                        "tool": "write_file",
                        "args": {"path": "src/new.py", "content": "print('new')\n"},
                    },
                    sandbox=sb,
                    agent_context=context,
                )
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "read_ai_arch", "args": {"path": "src/new.py"}},
                sandbox=sb,
                agent_context=context,
            )
            agent_tools.execute_tool(
                self.root,
                self.task,
                {
                    "tool": "write_file",
                    "args": {"path": "src/new.py", "content": "print('new')\n"},
                },
                sandbox=sb,
                agent_context=context,
            )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_new_directory_requires_only_existing_ancestor_documents(self) -> None:
        (self.root / "AI_ARCH.md").write_text("# Root\n", encoding="utf-8")
        context = {
            "capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"],
            "allowed_read_scope": ["src", "AI_ARCH.md"],
            "allowed_write_scope": ["src"],
            "navigation_documents": {
                "required_paths": [
                    "AI_ARCH.md",
                    "src/AI_ARCH.md",
                    "src/new/AI_ARCH.md",
                ],
                "read_paths": [],
                "enforced": True,
            },
        }
        sb = sandbox.create_sandbox(self.root)
        try:
            read = agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "read_ai_arch", "args": {"path": "src/new/module.py"}},
                sandbox=sb,
                agent_context=context,
            )
            self.assertEqual(["AI_ARCH.md"], read["required_paths"])
            agent_tools.execute_tool(
                self.root,
                self.task,
                {
                    "tool": "write_file",
                    "args": {"path": "src/new/module.py", "content": "value = 1\n"},
                },
                sandbox=sb,
                agent_context=context,
            )
            self.assertFalse((Path(sb["path"]) / "src" / "new" / "AI_ARCH.md").exists())
        finally:
            sandbox.destroy_sandbox(sb)

    def test_read_file_blocks_env_and_git(self) -> None:
        for path in (".env", ".git/config", "src/../.env"):
            with self.assertRaises(WorkspaceError):
                agent_tools.execute_tool(
                    self.root,
                    self.task,
                    {"tool": "read_file", "args": {"path": path}},
                    agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["src", "AI_ARCH.md"]},
                )

    def test_read_file_rejects_outside_allowed_scope(self) -> None:
        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "read_file", "args": {"path": "src/other.py"}},
                agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["src/core.py", "AI_ARCH.md"]},
            )

    def test_search_files_returns_matching_lines(self) -> None:
        (self.root / "src" / "util.py").write_text(
            "def helper():\n    return 'core'\n", encoding="utf-8"
        )
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "search_files", "args": {"pattern": "core", "path": "src"}},
            agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["src"]},
        )
        paths = {match["path"] for match in result["matches"]}
        self.assertIn("src/core.py", paths)
        self.assertIn("src/util.py", paths)

    def test_search_files_rejects_outside_scope(self) -> None:
        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "search_files", "args": {"pattern": "core", "path": "src"}},
                agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["AI_ARCH.md"]},
            )

    def test_search_files_requires_pattern(self) -> None:
        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "search_files", "args": {"path": "src"}},
                agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["src"]},
            )

    def test_read_module_interface_only_allows_dependencies(self) -> None:
        architecture = {
            "modules": [
                {"id": "core", "name": "Core", "path": "src/core", "depends_on": []},
                {"id": "auth", "name": "Auth", "path": "src/auth", "depends_on": ["core"]},
                {"id": "payments", "name": "Payments", "path": "src/payments", "depends_on": []},
            ]
        }
        context = {
            "capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"],
            "module_contract": {
                "id": "auth",
                "depends_on": ["core"],
            },
        }
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "read_module_interface", "args": {"module_id": "core"}},
            agent_context=context,
            architecture=architecture,
        )
        self.assertEqual("Core", result["name"])
        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "read_module_interface", "args": {"module_id": "payments"}},
                agent_context=context,
                architecture=architecture,
            )

    def test_search_reuse_finds_registry_entries(self) -> None:
        workspace.write_contracts(self.root, {
            "schema_version": 1,
            "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
            "vocabulary": [
                {"term": "invoice_id", "owner": "billing", "kind": "identifier",
                 "type": "str", "format": "^INV-[0-9]+$",
                 "forbidden_aliases": ["inv_id"]},
            ],
            "shared_kernel": [
                {"symbol": "AppError", "kind": "class", "owner": "shared",
                 "path": "src/shared/errors.py", "why": "统一错误模型",
                 "consumers": ["billing", "auth"]},
            ],
            "commands": [
                {"name": "invoice create", "verb": "create", "resource": "invoice",
                 "owner": "billing", "handler": "billing.cli.register",
                 "args": ["customer", "items"]},
            ],
            "modules": [
                {"id": "billing", "path": "src/billing", "depends_on": ["pricing"],
                 "exports": [{"symbol": "create_invoice", "kind": "function",
                              "signature": "(customer_id: str, items: list) -> Invoice",
                 "since": "0.1.0"}],
                 "consumes": []},
                {"id": "pricing", "path": "src/pricing", "depends_on": [],
                 "exports": [], "consumes": []},
                {"id": "auth", "path": "src/auth", "depends_on": [],
                 "exports": [], "consumes": []},
            ],
            "recipes": [
                {"name": "CLI 参数校验", "problem": "统一参数错误格式",
                 "solution": "复用 shared.parse_args", "used_by": ["billing"]},
            ],
            "data_schema": [
                {"name": "Invoice", "owner": "billing", "kind": "entity",
                 "fields": [{"name": "id", "type": "int", "required": True}],
                 "invariants": "id > 0"},
            ],
            "config_policy": [
                {"name": "MAX_INVOICE_ITEMS", "owner": "billing", "kind": "env",
                 "default": "100", "security_boundary": False},
            ],
        })
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "search_reuse", "args": {"term": "invoice"}},
            agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"]},
        )
        self.assertEqual("invoice", result["term"])
        self.assertEqual(["create_invoice"], [e["symbol"] for e in result["exports"]])
        self.assertEqual(1, len(result["commands"]))
        self.assertEqual(["invoice_id"], [v["term"] for v in result["vocabulary"]])
        self.assertEqual(0, len(result["shared_kernel"]))
        self.assertEqual(["Invoice"], [d["name"] for d in result["data_schema"]])
        self.assertEqual(["MAX_INVOICE_ITEMS"], [c["name"] for c in result["config_policy"]])
        self.assertGreaterEqual(result["total"], 5)

    def test_search_reuse_requires_term(self) -> None:
        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "search_reuse", "args": {}},
                agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"]},
            )

    def test_read_architecture_focuses_own_and_neighbors(self) -> None:
        architecture = {
            "modules": [
                {"id": "core", "name": "Core", "path": "src/core", "depends_on": []},
                {"id": "auth", "name": "Auth", "path": "src/auth", "depends_on": ["core"]},
                {"id": "payments", "name": "Payments", "path": "src/payments", "depends_on": ["auth"]},
            ]
        }
        context = {
            "capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"],
            "module_contract": {"id": "auth", "depends_on": ["core"]},
        }
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "read_architecture", "args": {}},
            agent_context=context,
            architecture=architecture,
        )
        ids = {module["id"] for module in result["modules"]}
        self.assertEqual({"core", "auth", "payments"}, ids)

    def test_write_file_requires_sandbox(self) -> None:
        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {
                    "tool": "write_file",
                    "args": {"path": "src/new.py", "content": "print('new')\n"},
                },
                agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["src"]},
            )

    def test_write_without_capability_is_rejected_even_in_sandbox(self) -> None:
        sb = sandbox.create_sandbox(self.root)
        try:
            context = {"allowed_write_scope": ["src"]}
            with self.assertRaisesRegex(WorkspaceError, "没有声明能力范围"):
                agent_tools.execute_tool(
                    self.root,
                    self.task,
                    {
                        "tool": "write_file",
                        "args": {"path": "src/new.py", "content": "print('new')\n"},
                    },
                    sandbox=sb,
                    agent_context=context,
                )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_write_file_rejects_dependency_path_even_when_readable(self) -> None:
        sb = sandbox.create_sandbox(self.root)
        try:
            context = {
                "capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"],
                "allowed_read_scope": ["src/core.py", "src/auth"],
                "allowed_write_scope": ["src/auth"],
            }
            with self.assertRaisesRegex(WorkspaceError, "写入路径不在"):
                agent_tools.execute_tool(
                    self.root,
                    self.task,
                    {
                        "tool": "write_file",
                        "args": {"path": "src/core.py", "content": "print('bad')\n"},
                    },
                    sandbox=sb,
                    agent_context=context,
                )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_root_level_files_are_read_only_for_every_agent(self) -> None:
        """package.json / README.md belong to no module's sandbox, but any
        sub-agent may READ them; dotfiles and sibling modules stay closed."""
        (self.root / "package.json").write_text('{"name": "demo"}', encoding="utf-8")
        (self.root / "README.md").write_text("# Demo", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1", encoding="utf-8")
        (self.root / "src" / "auth").mkdir(parents=True)
        (self.root / "src" / "auth" / "api.py").write_text("def f(): pass\n", encoding="utf-8")
        context = {
            "capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"],
            "allowed_read_scope": ["src/scanner"],
        }
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "read_file", "args": {"path": "package.json"}},
            agent_context=context,
        )
        self.assertIn("demo", result["content"])
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "read_file", "args": {"path": "README.md"}},
            agent_context=context,
        )
        self.assertIn("# Demo", result["content"])
        listing = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "list_dir", "args": {"path": "."}},
            agent_context=context,
        )
        self.assertIn("package.json", [entry["name"] for entry in listing["entries"]])

        with self.assertRaisesRegex(WorkspaceError, "不在当前子 Agent 允许读取"):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "read_file", "args": {"path": ".env"}},
                agent_context=context,
            )
        with self.assertRaisesRegex(WorkspaceError, "不在当前子 Agent 允许读取"):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "read_file", "args": {"path": "src/auth/api.py"}},
                agent_context=context,
            )
        sb = sandbox.create_sandbox(self.root)
        try:
            with self.assertRaisesRegex(WorkspaceError, "写入路径不在"):
                agent_tools.execute_tool(
                    self.root,
                    self.task,
                    {
                        "tool": "write_file",
                        "args": {"path": "package.json", "content": "{}"},
                    },
                    sandbox=sb,
                    agent_context={
                        **context,
                        "allowed_write_scope": ["src/scanner"],
                    },
                )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_write_and_edit_file_only_change_sandbox(self) -> None:
        sb = sandbox.create_sandbox(self.root)
        try:
            context = {"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"], "allowed_read_scope": ["src"]}
            agent_tools.execute_tool(
                self.root,
                self.task,
                {
                    "tool": "write_file",
                    "args": {"path": "src/new.py", "content": "print('new')\n"},
                },
                sandbox=sb,
                agent_context=context,
            )
            sandbox_path = Path(sb["path"])
            self.assertTrue((sandbox_path / "src" / "new.py").exists())
            self.assertFalse((self.root / "src" / "new.py").exists())

            agent_tools.execute_tool(
                self.root,
                self.task,
                {
                    "tool": "edit_file",
                    "args": {
                        "path": "src/new.py",
                        "old_text": "print('new')",
                        "new_text": "print('edited')",
                    },
                },
                sandbox=sb,
                agent_context=context,
            )
            self.assertIn(
                "print('edited')",
                (sandbox_path / "src" / "new.py").read_text(encoding="utf-8"),
            )
        finally:
            sandbox.destroy_sandbox(sb)

    def test_run_verification_requires_declared_command(self) -> None:
        declared = verification_command(self.root)
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "run_verification", "args": {"command": declared}},
            agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"]},
        )
        self.assertEqual(0, result["returncode"])

        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "run_verification", "args": {"command": "rm -rf ."}},
                agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"]},
            )

    def test_read_ui_layout_returns_task_context(self) -> None:
        self.task["ui_context"] = {
            "kind": "layout_delta",
            "selected_element": {"id": "n1", "selector": "[data-testid=\"save\"]"},
            "layout_context": {"ancestors": [], "element": {"id": "n1"}},
        }
        result = agent_tools.execute_tool(
            self.root,
            self.task,
            {"tool": "read_ui_layout", "args": {}},
            agent_context={"capabilities": ["read_project", "read_architecture", "write_sandbox", "run_verification"]},
        )
        self.assertEqual("n1", result["ui_context"]["selected_element"]["id"])
        self.assertEqual("n1", result["ui_context"]["layout_context"]["element"]["id"])

        self.task.pop("ui_context")
        with self.assertRaises(WorkspaceError):
            agent_tools.execute_tool(
                self.root,
                self.task,
                {"tool": "read_ui_layout", "args": {}},
            )

    def test_run_agent_tool_loop_uses_tools_then_returns_files(self) -> None:
        calls = [
            {
                "thinking": "need context",
                "tool_calls": [
                    {"tool": "read_file", "args": {"path": "src/core.py"}}
                ],
            },
            {
                "thinking": "ready",
                "files": [{"path": "src/core.py", "content": "print('new')\n"}],
            },
        ]
        with patch("agent_tools.call_model_json", side_effect=calls):
            result = agent_tools.run_agent_tool_loop(
                self.root,
                self.provider,
                {"modules": []},
                {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                self.task,
            )

        self.assertEqual("print('new')\n", result["files"][0]["content"])

    def test_tool_failure_is_returned_to_the_next_agent_turn(self) -> None:
        inputs = []

        def fake_model(_provider, _prompt, model_input, **_kwargs):
            inputs.append(model_input)
            if len(inputs) == 1:
                return {
                    "tool_calls": [
                        {"tool": "read_file", "args": {"path": "../outside.py"}}
                    ]
                }
            return {
                "files": [{"path": "src/core.py", "content": "print('recovered')\n"}]
            }

        with patch("agent_tools.call_model_json", side_effect=fake_model):
            result = agent_tools.run_agent_tool_loop(
                self.root,
                self.provider,
                {"modules": []},
                {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                self.task,
            )

        self.assertEqual("print('recovered')\n", result["files"][0]["content"])
        history = inputs[1]["tool_history"]
        self.assertFalse(history[0]["results"][0]["result"]["ok"])
        self.assertIn("不在", history[0]["results"][0]["result"]["error"])

    def test_stream_agent_tool_loop_emits_tool_activity(self) -> None:
        calls = [
            {
                "tool_calls": [
                    {"tool": "task_status", "args": {}}
                ]
            },
            {
                "files": [{"path": "src/core.py", "content": "print('new')\n"}]
            },
        ]
        with patch("agent_tools.call_model_json", side_effect=calls):
            events = list(agent_tools.stream_agent_tool_loop(
                self.root,
                self.provider,
                {"modules": []},
                {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                self.task,
            ))

        self.assertTrue(any(event["type"] == "tool_activity" for event in events))
        self.assertEqual("done", events[-1]["type"])

    def test_run_agent_tool_loop_allows_natural_language_finish_after_write(self) -> None:
        calls = [
            {
                "thinking": "I will write the implementation.",
                "tool_calls": [
                    {
                        "tool": "write_file",
                        "args": {"path": "src/core.py", "content": "print('new')\n"},
                    }
                ],
            },
            {"__prose__": "The implementation has been written to src/core.py."},
        ]
        sandbox_obj = sandbox.create_sandbox(self.root)
        try:
            with patch("agent_tools.call_model_json", side_effect=calls):
                result = agent_tools.run_agent_tool_loop(
                    self.root,
                    self.provider,
                    {"modules": []},
                    {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                    self.task,
                    sandbox=sandbox_obj,
                )
        finally:
            sandbox.destroy_sandbox(sandbox_obj)

        self.assertTrue(result["done"])
        self.assertIn("written to src/core.py", result["summary"])

    def test_natural_language_scope_note_after_write_is_not_handoff(self) -> None:
        calls = [
            {
                "thinking": "I will write the audio module.",
                "tool_calls": [
                    {
                        "tool": "write_file",
                        "args": {"path": "src/audio.js", "content": "export function beep() {}\n"},
                    }
                ],
            },
            {
                "__prose__": (
                    "任务完成。已创建 src/audio.js；"
                    "src/main.js 的接线不在本模块范围内，由组合根模块完成。"
                ),
            },
        ]
        audio_task = dict(
            self.task,
            id="audio",
            module_id="audio",
            target_files=["src/audio.js"],
        )
        sandbox_obj = sandbox.create_sandbox(self.root)
        try:
            with patch("agent_tools.call_model_json", side_effect=calls):
                result = agent_tools.run_agent_tool_loop(
                    self.root,
                    self.provider,
                    {"modules": []},
                    {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                    audio_task,
                    sandbox=sandbox_obj,
                )
        finally:
            sandbox.destroy_sandbox(sandbox_obj)

        self.assertTrue(result["done"])
        self.assertNotIn("needs_handoff", result)

    def test_tool_loop_accepts_structured_handoff_without_write(self) -> None:
        with patch(
            "agent_tools.call_model_json",
            side_effect=[{"needs_handoff": "This change requires another module."}],
        ):
            result = agent_tools.run_agent_tool_loop(
                self.root,
                self.provider,
                {"modules": []},
                {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                self.task,
            )

        self.assertTrue(result["needs_handoff"])
        self.assertIn("another module", result["needs_handoff"])

    def test_tool_loop_rejects_natural_language_finish_without_write(self) -> None:
        # First prose earns one forced-pen nudge; a second prose without any
        # write then fails the task (previously the first prose failed at once).
        with patch(
            "agent_tools.call_model_json",
            side_effect=[
                {"__prose__": "I think we are done here."},
                {"__prose__": "Still nothing to write."},
            ],
        ):
            with self.assertRaisesRegex(WorkspaceError, "没有调用 write_file"):
                agent_tools.run_agent_tool_loop(
                    self.root,
                    self.provider,
                    {"modules": []},
                    {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                    self.task,
                    max_turns=3,
                )

    def test_tool_loop_nudge_leads_to_write_then_finish(self) -> None:
        with patch(
            "agent_tools.call_model_json",
            side_effect=[
                {"__prose__": "I think we are done here."},
                {"thinking": "Right, I must write the file.",
                 "files": [{"path": "src/core.py", "content": "print('new')\n"}]},
            ],
        ):
            result = agent_tools.run_agent_tool_loop(
                self.root,
                self.provider,
                {"modules": []},
                {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                self.task,
                max_turns=3,
            )
        self.assertEqual("print('new')\n", result["files"][0]["content"])

    def test_run_agent_tool_loop_uses_native_tool_calls(self) -> None:
        native_calls = [
            {
                "content": "I will write the implementation.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "write_file",
                        "args": {"path": "src/core.py", "content": "print('new')\n"},
                    }
                ],
            },
            {
                "content": "The implementation has been written.",
                "tool_calls": [],
            },
        ]
        sandbox_obj = sandbox.create_sandbox(self.root)
        try:
            with patch("agent_tools.call_model_chat", side_effect=native_calls):
                result = agent_tools.run_agent_tool_loop(
                    self.root,
                    self.provider,
                    {"modules": []},
                    {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                    self.task,
                    sandbox=sandbox_obj,
                    use_native_tools=True,
                )
        finally:
            sandbox.destroy_sandbox(sandbox_obj)

        self.assertTrue(result["done"])
        self.assertIn("has been written", result["summary"])

    def test_native_tool_loop_rejects_finish_without_write(self) -> None:
        with patch(
            "agent_tools.call_model_chat",
            return_value={"content": "Done, nothing was needed.", "tool_calls": []},
        ):
            with self.assertRaisesRegex(WorkspaceError, "没有调用 write_file"):
                agent_tools.run_agent_tool_loop(
                    self.root,
                    self.provider,
                    {"modules": []},
                    {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                    self.task,
                    use_native_tools=True,
                )

    def test_native_tool_loop_nudge_leads_to_write_then_finish(self) -> None:
        """The first write-less prose earns a forced-pen nudge, not a failure."""
        calls = [
            {"content": "I believe the module is already done.", "tool_calls": []},
            {
                "content": "Writing the implementation now.",
                "tool_calls": [
                    {
                        "id": "call_1",
                        "name": "write_file",
                        "args": {"path": "src/core.py", "content": "print('new')\n"},
                    }
                ],
            },
            {"content": "Implementation is written.", "tool_calls": []},
        ]
        sandbox_obj = sandbox.create_sandbox(self.root)
        try:
            with patch("agent_tools.call_model_chat", side_effect=calls):
                result = agent_tools.run_agent_tool_loop(
                    self.root,
                    self.provider,
                    {"modules": []},
                    {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                    self.task,
                    sandbox=sandbox_obj,
                    use_native_tools=True,
                )
        finally:
            sandbox.destroy_sandbox(sandbox_obj)
        self.assertTrue(result["done"])

    def test_native_tool_loop_falls_back_to_json_protocol(self) -> None:
        from bootstrap import ToolCallingNotSupported

        calls = [
            {
                "thinking": "using fallback",
                "files": [{"path": "src/core.py", "content": "print('new')\n"}],
            }
        ]
        with patch(
            "agent_tools.call_model_chat",
            side_effect=ToolCallingNotSupported("no tools"),
        ), patch("agent_tools.call_model_json", side_effect=calls):
            result = agent_tools.run_agent_tool_loop(
                self.root,
                self.provider,
                {"modules": []},
                {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                self.task,
                use_native_tools=True,
            )

        self.assertEqual("print('new')\n", result["files"][0]["content"])

    def test_repeat_tool_reminders_escalate_at_thresholds(self) -> None:
        history = [
            {
                "turn": 1,
                "tool_calls": [{"tool": "read_file", "args": {"path": "src/core.py"}}],
                "results": [{"tool": "read_file", "result": {"ok": True}}],
            },
            {
                "turn": 2,
                "tool_calls": [{"tool": "read_file", "args": {"path": "src/core.py"}}],
                "results": [{"tool": "read_file", "result": {"ok": True}}],
            },
            {
                "turn": 3,
                "tool_calls": [{"tool": "read_file", "args": {"path": "src/core.py"}}],
                "results": [{"tool": "read_file", "result": {"ok": True}}],
            },
        ]

        reminders = agent_tools._repeat_tool_reminders(history)

        self.assertEqual(1, len(reminders))
        self.assertIn("repeating the exact same tool call", reminders[0])

    def test_no_progress_violation_stops_read_only_loop(self) -> None:
        history = [
            {
                "turn": index,
                "tool_calls": [{"tool": "read_file", "args": {"path": f"src/{index}.py"}}],
                "results": [{"tool": "read_file", "result": {"ok": True}}],
            }
            for index in range(agent_tools.READ_ONLY_ROUND_HARD_LIMIT)
        ]

        violation = agent_tools._no_progress_violation(history)

        self.assertIsNotNone(violation)
        self.assertIn("连续", violation)
        self.assertIn("只读", violation)

    def test_no_progress_violation_stops_exact_repeat_chain(self) -> None:
        history = [
            {
                "turn": index,
                "tool_calls": [{"tool": "read_file", "args": {"path": "src/core.py"}}],
                "results": [{"tool": "read_file", "result": {"ok": True}}],
            }
            for index in range(agent_tools.REPEAT_TOOL_HARD_LIMIT)
        ]

        violation = agent_tools._no_progress_violation(history)

        self.assertIsNotNone(violation)
        self.assertIn("重复调用", violation)
        self.assertIn("read_file", violation)

    def test_no_progress_resets_after_a_write(self) -> None:
        history = [
            {
                "turn": index,
                "tool_calls": [{"tool": "read_file", "args": {"path": f"src/{index}.py"}}],
                "results": [{"tool": "read_file", "result": {"ok": True}}],
            }
            for index in range(agent_tools.READ_ONLY_ROUND_HARD_LIMIT)
        ]
        history.append({
            "turn": 99,
            "tool_calls": [{"tool": "write_file", "args": {"path": "src/core.py"}}],
            "results": [{"tool": "write_file", "result": {"ok": True}}],
        })

        self.assertIsNone(agent_tools._no_progress_violation(history))

    def test_loop_injects_repeat_reminder_after_three_identical_calls(self) -> None:
        captured = []

        def fake_call(provider, prompt, model_input, timeout, allow_prose=True):
            captured.append(model_input)
            if len(captured) < 4:
                return {
                    "tool_calls": [{"tool": "task_status", "args": {}}],
                }
            return {
                "files": [{"path": "src/core.py", "content": "print('new')\n"}],
            }

        with patch("agent_tools.call_model_json", side_effect=fake_call):
            agent_tools.run_agent_tool_loop(
                self.root,
                self.provider,
                {"modules": []},
                {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                self.task,
            )

        self.assertEqual(4, len(captured))
        self.assertIn("repeat_reminders", captured[3])

    def test_native_step_log_compacts_old_tool_turns(self) -> None:
        task_context = {"role": "user", "content": '{"task":{"id":"core","target_files":["src/core.py"]}}'}
        messages = [{"role": "system", "content": "system"}, task_context]
        step_log = ["read_file src/a.py → ok", "read_file src/b.py → ok"]
        for index in range(5):
            messages.append({
                "role": "assistant",
                "content": f"turn {index}",
                "tool_calls": [{"id": str(index), "name": "read_file", "args": {}}],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": str(index),
                "content": "detail",
            })

        agent_tools._prune_native_tool_messages(messages)
        agent_tools._refresh_native_step_log(messages, step_log)

        assistant_turns = [
            message for message in messages if message.get("role") == "assistant"
        ]
        self.assertEqual(3, len(assistant_turns))
        self.assertEqual(task_context, messages[1])
        self.assertEqual(
            ["2", "3", "4"],
            [message["tool_call_id"] for message in messages if message.get("role") == "tool"],
        )
        # A later pruning pass must preserve the same task and complete tool pairs.
        messages.extend([
            {"role": "assistant", "tool_calls": [{"id": "5", "name": "read_file", "args": {}}]},
            {"role": "tool", "tool_call_id": "5", "content": "detail"},
        ])
        agent_tools._prune_native_tool_messages(messages)
        self.assertEqual(task_context, messages[1])
        self.assertEqual(
            ["3", "4", "5"],
            [message["tool_call_id"] for message in messages if message.get("role") == "tool"],
        )
        self.assertTrue(
            any(
                message.get("role") == "user"
                and str(message.get("content") or "").startswith(agent_tools.STEP_LOG_MARKER)
                for message in messages
            )
        )

    def test_tool_result_summary_omits_full_file_content(self) -> None:
        summary = agent_tools._tool_result_summary(
            "write_file",
            {"ok": True, "path": "src/core.py", "written": 4096},
        )

        self.assertIn("write_file", summary)
        self.assertIn("4096", summary)
        self.assertNotIn("def main", summary)

    def test_tool_loop_stops_at_max_turns(self) -> None:
        calls = [
            {
                "tool_calls": [
                    {"tool": "task_status", "args": {}}
                ]
            }
            for _ in range(3)
        ]
        with patch("agent_tools.call_model_json", side_effect=calls):
            with self.assertRaisesRegex(WorkspaceError, "调试上限"):
                agent_tools.run_agent_tool_loop(
                    self.root,
                    self.provider,
                    {"modules": []},
                    {"name": "Demo", "slug": "demo", "root": str(self.root), "mode": "new"},
                    self.task,
                    max_turns=3,
                )

    def test_loop_checkpoint_roundtrip(self) -> None:
        history = [{"turn": 1, "tool_calls": [], "results": []}]
        agent_tools.save_loop_checkpoint(self.root, "core", history)
        self.assertEqual(history, agent_tools.load_loop_checkpoint(self.root, "core"))
        agent_tools.clear_loop_checkpoint(self.root, "core")
        self.assertEqual([], agent_tools.load_loop_checkpoint(self.root, "core"))
    def test_model_input_stable_blocks_precede_task(self) -> None:
        project = {
            "name": "Demo",
            "slug": "demo",
            "root": str(self.root),
            "mode": "new",
        }
        task = {
            "id": "core",
            "module_id": "core",
            "summary": "x",
            "target_files": [],
            "verification": [],
            "ui_context": None,
        }
        context = {
            "user_profile": "u",
            "standards": "s",
            "recipes": "r",
            "project_overview": "o",
            "module_contract": {"id": "core"},
            "error_memory": [],
            "work_log_tail": "w",
        }
        payload = agent_tools._model_input(project, task, context, [])
        keys = list(payload.keys())
        self.assertLess(keys.index("project"), keys.index("agent_context"))
        self.assertLess(keys.index("agent_context"), keys.index("task"))
        self.assertLess(keys.index("task"), keys.index("tools"))
        self.assertLess(keys.index("tools"), keys.index("tool_history"))
        serialized = __import__("json").dumps(payload, ensure_ascii=False)
        # The first module-specific key must not appear before the global stable
        # blocks inside the serialized agent_context value.
        self.assertLess(
            serialized.index('"user_profile"'),
            serialized.index('"module_contract"'),
        )

    def test_agent_context_stable_blocks_first(self) -> None:
        architecture = {
            "modules": [{
                "id": "core",
                "name": "Core",
                "path": "src/core",
                "responsibility": "",
                "brief": "",
                "needs_ui": False,
                "depends_on": [],
            }]
        }
        context = agent_tools.build_agent_context(self.root, "core", architecture, {})
        keys = list(context.keys())
        # Phase A of ARCHITECTURE_CONTRACT_SPEC.md: global stable blocks must
        # precede the first module-specific block, and working-state blocks come
        # after both.
        self.assertEqual(
            ["user_profile", "standards", "recipes", "project_overview", "contract_view", "module_contract"],
            keys[:6],
        )
        self.assertLess(keys.index("module_contract"), keys.index("unresolved_attachments"))
        self.assertLess(keys.index("unresolved_attachments"), keys.index("error_memory"))
        self.assertLess(keys.index("error_memory"), keys.index("work_log_tail"))


    def test_read_scope_is_wider_than_write_scope(self) -> None:
        """Reading is how an agent reuses what exists; writing is bounded.

        Pilot finding: `list_dir("src")` was rejected for an agent whose module
        was `src/io` — the read scope stopped exactly at the module path, while
        the architecture and contract registry (where the reusable global
        surface lives) were not openable either. Read now includes the parent
        directory, the architecture document and the contract registry; write
        still contains only the module itself.
        """
        context = agents.build_agent_context(
            Path(self.root),
            "io",
            {"modules": [
                {"id": "io", "name": "IO", "path": "src/io", "depends_on": []},
                {"id": "core", "name": "Core", "path": "src/core", "depends_on": ["io"]},
            ]},
            {"target_files": []},
        )
        read_scope = set(context["allowed_read_scope"])
        self.assertIn("src", read_scope)                    # parent directory
        self.assertIn("src/io", read_scope)                 # own module
        self.assertIn(".docuagent/architecture.json", read_scope)
        self.assertIn(".docuagent/contracts.json", read_scope)

        # The write boundary did not move: no sibling, no registry, no parent.
        write_scope = set(context["allowed_write_scope"])
        self.assertEqual({"src/io"}, write_scope)

    def test_dependency_paths_stay_read_only(self) -> None:
        context = agents.build_agent_context(
            Path(self.root),
            "core",
            {"modules": [
                {"id": "io", "name": "IO", "path": "src/io", "depends_on": []},
                {"id": "core", "name": "Core", "path": "src/core", "depends_on": ["io"]},
            ]},
            {"target_files": []},
        )
        self.assertIn("src/io", set(context["allowed_read_scope"]))
        self.assertNotIn("src/io", set(context["allowed_write_scope"]))


if __name__ == "__main__":
    unittest.main()
