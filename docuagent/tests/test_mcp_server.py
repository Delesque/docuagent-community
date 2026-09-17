import json
import tempfile
import unittest
from pathlib import Path

import mcp_server


class McpServerTest(unittest.TestCase):
    def test_initialize_returns_capabilities(self) -> None:
        response = mcp_server.handle_message({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {},
        })
        self.assertEqual(1, response["id"])
        self.assertIn("tools", response["result"]["capabilities"])
        self.assertEqual("docuagent-mcp", response["result"]["serverInfo"]["name"])

    def test_tools_list_contains_core_tools(self) -> None:
        response = mcp_server.handle_message({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
        })
        names = {tool["name"] for tool in response["result"]["tools"]}
        self.assertIn("inspect_workspace", names)
        self.assertIn("list_tasks", names)
        self.assertIn("git_status", names)

    def test_call_inspect_workspace(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "core.py").write_text("print('x')\n", encoding="utf-8")
        response = mcp_server.handle_message({
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "inspect_workspace",
                "arguments": {"path": str(root)},
            },
        })
        text = response["result"]["content"][0]["text"]
        payload = json.loads(text)
        self.assertEqual(str(root), payload["path"])
        self.assertFalse(response["result"]["isError"])

    def test_call_unknown_tool_returns_error(self) -> None:
        response = mcp_server.handle_message({
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "not_a_tool", "arguments": {}},
        })
        self.assertTrue(response["result"]["isError"])

    def test_ping(self) -> None:
        response = mcp_server.handle_message({
            "jsonrpc": "2.0",
            "id": 5,
            "method": "ping",
        })
        self.assertEqual({}, response["result"])


if __name__ == "__main__":
    unittest.main()
