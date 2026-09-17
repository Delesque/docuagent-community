import tempfile
import unittest
from pathlib import Path

import docuagent


class NodeAttachmentsTest(unittest.TestCase):
    def test_add_read_resolve_attachment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)

            attachments = docuagent.add_node_attachment(
                root, "auth", "note", "需要支持 OAuth2"
            )
            attachment_id = attachments["auth"][0]["id"]
            self.assertEqual("note", attachments["auth"][0]["type"])

            attachments = docuagent.resolve_node_attachment(root, "auth", attachment_id)
            self.assertTrue(attachments["auth"][0]["resolved"])

            attachments = docuagent.add_node_attachment(
                root, "auth", "option", "用数据库索引还是缓存"
            )
            option_id = attachments["auth"][1]["id"]
            self.assertEqual("option", attachments["auth"][1]["type"])

            attachments = docuagent.add_node_attachment(
                root, "auth", "error_memory", "曾经超时失败"
            )
            error_id = attachments["auth"][2]["id"]
            self.assertEqual("error_memory", attachments["auth"][2]["type"])

            attachments = docuagent.archive_node_attachment(root, "auth", option_id)
            self.assertTrue(attachments["auth"][1]["archived"])
            attachments = docuagent.archive_node_attachment(root, "auth", error_id)
            self.assertTrue(attachments["auth"][2]["archived"])

            read = docuagent.read_node_attachments(root)
            self.assertTrue(read["auth"][0]["resolved"])
