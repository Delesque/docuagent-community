import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import mcp_client


FAKE_SERVER = r'''
import json, sys
for line in sys.stdin:
    msg = json.loads(line)
    method = msg.get("method")
    if method == "initialize":
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"protocolVersion":"2025-03-26","capabilities":{"tools":{}},"serverInfo":{"name":"fake"}}}) + "\n")
    elif method == "notifications/initialized":
        pass
    elif method == "tools/list":
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"tools":[{"name":"echo","description":"echo","inputSchema":{"type":"object","properties":{}}}]}}) + "\n")
    elif method == "tools/call":
        sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":msg["id"],"result":{"content":[{"type":"text","text":"ok"}]}}) + "\n")
    sys.stdout.flush()
'''


class McpClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.script = self.root / "fake_server.py"
        self.script.write_text(FAKE_SERVER, encoding="utf-8")

    def test_client_lists_and_calls_tool(self) -> None:
        client = mcp_client.McpClient(sys.executable, [str(self.script)])
        try:
            client.start()
            tools = client.list_tools()
            self.assertEqual("echo", tools[0]["name"])
            result = client.call_tool("echo", {})
            self.assertEqual("ok", result["content"][0]["text"])
        finally:
            client.stop()

    def test_server_requires_approval_before_starting(self) -> None:
        project = self.root / "proj"
        (project / ".docuagent").mkdir(parents=True)
        (project / ".docuagent" / "mcp.json").write_text(
            json.dumps({
                "servers": [{
                    "name": "fake",
                    "command": sys.executable,
                    "args": [str(self.script)],
                    "approved": False,
                }]
            }),
            encoding="utf-8",
        )

        servers = mcp_client.configured_servers(project)
        self.assertFalse(servers[0]["approved"])
        with self.assertRaisesRegex(mcp_client.WorkspaceError, "尚未确认启动命令"):
            mcp_client.list_server_tools(project, "fake")

        approved = mcp_client.approve_mcp_server(project, "fake")
        self.assertTrue(approved[0]["approved"])
        tools = mcp_client.list_server_tools(project, "fake")
        self.assertEqual("echo", tools[0]["name"])

    def test_save_preserves_approval_only_for_unchanged_command(self) -> None:
        project = self.root / "proj"
        (project / ".docuagent").mkdir(parents=True)
        mcp_client.save_mcp_servers(project, [{
            "name": "fake",
            "command": sys.executable,
            "args": [str(self.script)],
        }])
        mcp_client.approve_mcp_server(project, "fake")

        mcp_client.save_mcp_servers(project, [{
            "name": "fake",
            "command": sys.executable,
            "args": [str(self.script)],
        }])
        self.assertTrue(mcp_client.configured_servers(project)[0]["approved"])

        mcp_client.save_mcp_servers(project, [{
            "name": "fake",
            "command": sys.executable,
            "args": [str(self.script), "--changed"],
        }])
        self.assertFalse(mcp_client.configured_servers(project)[0]["approved"])

    def test_load_mcp_servers_merges_project_config(self) -> None:
        home = self.root / "home"
        (home / ".docuagent").mkdir(parents=True)
        (home / ".docuagent" / "mcp.json").write_text(
            json.dumps({"servers": [{"name": "global", "command": "python", "args": ["a"]}]}),
            encoding="utf-8",
        )
        project = self.root / "proj"
        (project / ".docuagent").mkdir(parents=True)
        (project / ".docuagent" / "mcp.json").write_text(
            json.dumps({"servers": [{"name": "project", "command": "python", "args": ["b"]}]}),
            encoding="utf-8",
        )
        with mock.patch("pathlib.Path.home", return_value=home):
            servers = mcp_client.load_mcp_servers(project)
        self.assertEqual({"global", "project"}, set(servers["servers"]))


if __name__ == "__main__":
    unittest.main()
