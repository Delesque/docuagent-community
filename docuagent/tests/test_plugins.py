import json
import tempfile
import unittest
from pathlib import Path

import plugins
from core import WorkspaceError


class PluginsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.source = self.root / "src-plugin" / "hello"
        self.source.mkdir(parents=True)
        (self.source / "plugin.json").write_text(
            json.dumps({
                "name": "hello",
                "version": "1.0.0",
                "description": "Hello plugin",
                "entry": "main.py",
                "tools": [
                    {
                        "name": "hello",
                        "description": "Say hello",
                        "args": {"name": "string"},
                    }
                ],
                "prompts": [
                    {"target": "subagent", "content": "Always greet politely."}
                ],
            }),
            encoding="utf-8",
        )
        (self.source / "main.py").write_text(
            "def tool_hello(project_root, args):\n"
            "    return {'hello': args.get('name', 'world')}\n",
            encoding="utf-8",
        )

    def test_install_toggle_and_call_plugin_tool(self) -> None:
        installed = plugins.install_plugin(self.root, str(self.source))
        self.assertEqual("hello", installed["name"])
        self.assertFalse(installed["enabled"])

        plugins.toggle_plugin(self.root, "hello", True)
        tools = plugins.list_plugin_tools(self.root)
        self.assertEqual("hello", tools[0]["name"])
        result = plugins.call_plugin_tool(
            self.root,
            "hello",
            {"name": "DocuAgent"},
        )
        self.assertEqual("DocuAgent", result["hello"])
        self.assertIn(
            "Always greet politely.",
            plugins.list_plugin_prompts(self.root, "subagent"),
        )

    def test_install_records_source_and_hash(self) -> None:
        installed = plugins.install_plugin(self.root, str(self.source))
        self.assertEqual(str(self.source), installed["installed_from"])
        self.assertEqual(64, len(installed["sha256"]))
        listed = next(item for item in plugins.list_plugins(self.root) if item["name"] == "hello")
        self.assertEqual(installed["sha256"], listed["sha256"])
        self.assertEqual(str(self.source), listed["installed_from"])

    def test_disabled_plugin_tools_are_not_exposed(self) -> None:
        plugins.install_plugin(self.root, str(self.source))
        self.assertEqual([], plugins.list_plugin_tools(self.root))

    def test_uninstall_removes_plugin(self) -> None:
        plugins.install_plugin(self.root, str(self.source))
        plugins.uninstall_plugin(self.root, "hello")
        with self.assertRaises(WorkspaceError):
            plugins.toggle_plugin(self.root, "hello", True)


if __name__ == "__main__":
    unittest.main()
