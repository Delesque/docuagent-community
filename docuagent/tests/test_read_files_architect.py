"""Architecture agent file reads (plan item 2, scheme A).

The architecture agent may return `read_files`; the system reads the requested
files into the next turn's input under `file_reads`. Tests pin the request
normalisation, path confinement and round-trip injection.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bootstrap
from llm_client import ProviderConfig


def _project(root_path: str) -> tuple[dict, Path]:
    root = Path(root_path)
    (root / "src").mkdir()
    (root / "src" / "scanner.js").write_text("const scan = 1;\n", encoding="utf-8")
    (root / ".docuagent").mkdir()
    (root / ".docuagent" / "state.json").write_text("{}", encoding="utf-8")
    return {"project": {"root": str(root)}, "architecture": {}, "agent_mode": "ai"}, root


MODEL_RESULT = {
    "architecture": {"modules": []},
    "ready": False,
    "next_question": {
        "id": "next-topic",
        "title": "t",
        "prompt": "p",
        "why": "w",
        "placeholder": "x",
        "options": ["自由描述"],
    },
    "mode_offer": None,
    "thinking": "",
    "graph_quality_issues": [],
    "graph_diagnostics": [],
    "read_files": ["src/scanner.js"],
}


class ReadRequestNormalizeTest(unittest.TestCase):
    def test_normalizes_and_caps(self) -> None:
        result = {"read_files": ["a.js", " b.js ", "a.js", 3, None, "c.js", "d.js", "e.js", "f.js"]}
        self.assertEqual(
            bootstrap._normalize_read_requests(result),
            ["a.js", "b.js", "c.js", "d.js", "e.js"],
        )

    def test_non_list_ignored(self) -> None:
        self.assertEqual(bootstrap._normalize_read_requests({"read_files": "x"}), [])


class ReadFilesForArchitectTest(unittest.TestCase):
    def test_reads_real_file_and_rejects_others(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state, root = _project(temp_dir)
            entries = bootstrap.read_files_for_architect(root, [
                "src/scanner.js",
                "src/missing.js",
                "../outside.js",
                ".docuagent/state.json",
            ])
            by_path = {e["path"]: e for e in entries}
            self.assertIn("const scan", by_path["src/scanner.js"]["content"])
            self.assertIn("error", by_path["src/missing.js"])
            self.assertIn("error", by_path["../outside.js"])
            self.assertIn("error", by_path[".docuagent/state.json"])


class RoundTripTest(unittest.TestCase):
    def test_model_input_carries_file_reads(self) -> None:
        captured: dict = {}

        def fake_call(config, system_prompt, model_input, **kwargs):
            captured.update(model_input)
            return dict(MODEL_RESULT)

        with tempfile.TemporaryDirectory() as temp_dir:
            state, root = _project(temp_dir)
            state["file_reads"] = [{"path": "src/scanner.js", "content": "const scan = 1;"}]
            with patch.object(bootstrap, "call_model_json", fake_call):
                bootstrap.call_architecture_model(
                    state,
                    {"question_id": "q", "answer": "a"},
                    ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
                )
        self.assertEqual(captured["file_reads"][0]["path"], "src/scanner.js")

    def test_apply_captures_read_requests_into_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state, root = _project(temp_dir)
            bootstrap.apply_architecture_model_result(
                state,
                MODEL_RESULT,
                ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
            )
            self.assertIn("file_reads", state)
            self.assertEqual(state["file_reads"][0]["path"], "src/scanner.js")
            self.assertIn("const scan", state["file_reads"][0]["content"])

    def test_validate_passes_read_files_through(self) -> None:
        with patch.object(bootstrap, "call_model_json", return_value=dict(MODEL_RESULT)):
            out = bootstrap.validate_architecture_model_result(dict(MODEL_RESULT), {})
        self.assertEqual(out["read_files"], ["src/scanner.js"])
