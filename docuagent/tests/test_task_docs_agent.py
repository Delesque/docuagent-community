import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core import WorkspaceError
from llm_client import ProviderConfig
from task_docs import generate_document_tree


class DocumentAgentTreeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp()) / "project"
        (self.root / "src" / "nested").mkdir(parents=True)
        (self.root / "src" / "nested" / "core.py").write_text(
            "def run():\n    return 1\n", encoding="utf-8"
        )
        self.provider = ProviderConfig(
            base_url="https://example.test/v1", model="test", api_key="secret"
        )

    @staticmethod
    def result_for(payload: dict) -> dict:
        target = payload["target"]
        lines = [f"# {target['directory']}"]
        lines.extend(f"- `{entry['name']}`" for entry in target["entries"])
        if target["path"] == "AI_ARCH.md":
            lines.extend(
                f"{name}/AI_ARCH.md"
                for name in payload["workspace_facts"]["top_level_directories"]
            )
        return {"files": [{"path": target["path"], "content": "\n".join(lines)}]}

    def test_calls_one_directory_at_a_time_with_root_last(self) -> None:
        calls: list[str] = []

        def model(*args, **kwargs):
            calls.append(args[2]["target"]["path"])
            return self.result_for(args[2])

        with patch("task_docs.call_model_json", side_effect=model):
            updated = generate_document_tree(
                self.root, self.provider, {}, {}, all_directories=True
            )

        self.assertEqual(
            ["src/nested/AI_ARCH.md", "src/AI_ARCH.md", "AI_ARCH.md"], calls
        )
        self.assertEqual(set(calls), set(updated))
        self.assertIn(
            "`core.py`",
            (self.root / "src" / "nested" / "AI_ARCH.md").read_text(encoding="utf-8"),
        )

    def test_failure_before_commit_leaves_no_partial_document_tree(self) -> None:
        def model(*args, **kwargs):
            payload = args[2]
            if payload["target"]["path"] == "src/AI_ARCH.md":
                raise RuntimeError("parent document failed")
            return self.result_for(payload)

        with patch("task_docs.call_model_json", side_effect=model):
            with self.assertRaisesRegex(RuntimeError, "parent document failed"):
                generate_document_tree(
                    self.root, self.provider, {}, {}, all_directories=True
                )

        self.assertFalse((self.root / "AI_ARCH.md").exists())
        self.assertFalse((self.root / "src" / "AI_ARCH.md").exists())
        self.assertFalse((self.root / "src" / "nested" / "AI_ARCH.md").exists())

    def test_rejects_program_template_markers(self) -> None:
        def model(*args, **kwargs):
            payload = args[2]
            result = self.result_for(payload)
            result["files"][0]["content"] += "\n写作指南（阅读后删除）"
            return result

        with patch("task_docs.call_model_json", side_effect=model):
            with self.assertRaisesRegex(WorkspaceError, "模板占位"):
                generate_document_tree(
                    self.root, self.provider, {}, {}, all_directories=True
                )

        self.assertFalse((self.root / "AI_ARCH.md").exists())

    def test_retries_one_invalid_directory_result_with_validation_feedback(self) -> None:
        attempts: dict[str, int] = {}
        retry_feedback: list[str] = []

        def model(*args, **kwargs):
            payload = args[2]
            path = payload["target"]["path"]
            attempts[path] = attempts.get(path, 0) + 1
            if path == "AI_ARCH.md" and attempts[path] == 1:
                valid = self.result_for(payload)["files"][0]
                return {"files": [valid, dict(valid)]}
            if path == "AI_ARCH.md":
                retry_feedback.append(payload["previous_sync_error"])
            return self.result_for(payload)

        with patch("task_docs.call_model_json", side_effect=model):
            updated = generate_document_tree(
                self.root, self.provider, {}, {}, all_directories=True
            )

        self.assertEqual(2, attempts["AI_ARCH.md"])
        self.assertIn("必须只返回", retry_feedback[0])
        self.assertEqual(
            {"src/nested/AI_ARCH.md", "src/AI_ARCH.md", "AI_ARCH.md"},
            set(updated),
        )

    def test_second_invalid_result_still_leaves_no_partial_documents(self) -> None:
        def model(*args, **kwargs):
            payload = args[2]
            valid = self.result_for(payload)["files"][0]
            return {"files": [valid, dict(valid)]}

        with patch("task_docs.call_model_json", side_effect=model):
            with self.assertRaisesRegex(WorkspaceError, "必须只返回"):
                generate_document_tree(
                    self.root, self.provider, {}, {}, all_directories=True
                )

        self.assertFalse((self.root / "AI_ARCH.md").exists())
        self.assertFalse((self.root / "src" / "AI_ARCH.md").exists())


if __name__ == "__main__":
    unittest.main()
