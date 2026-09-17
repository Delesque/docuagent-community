import sys
import unittest
from unittest import mock

import mcp_sdk_adapter
from core import WorkspaceError


class McpSdkAdapterTest(unittest.TestCase):
    def test_requires_official_sdk_when_missing(self) -> None:
        with mock.patch.dict(sys.modules, {"mcp": None}):
            with self.assertRaisesRegex(WorkspaceError, "pip install mcp"):
                mcp_sdk_adapter.run_sdk_server()


if __name__ == "__main__":
    unittest.main()
