import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main_routes_tasks


class BootstrapStreamRoutesTest(unittest.TestCase):
    def test_stream_bootstrap_start_emits_done_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            payload = {
                "path": str(root),
                "name": "Demo",
                "description": "一个小项目",
                "provider": {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:9/v1",
                    "model": "fake",
                    "api_key": "x",
                },
            }
            model_result = {
                "architecture": {"modules": []},
                "ready": False,
                "next_question": {
                    "id": "q1",
                    "title": "目标",
                    "prompt": "目标是什么？",
                    "why": "确认范围",
                    "placeholder": "例如：记账",
                    "options": ["自由描述"],
                },
                "thinking": "",
                "graph_quality_issues": [],
            }
            with patch(
                "main_routes_tasks._bootstrap.stream_architecture_model",
                return_value=iter([{"type": "done", "result": model_result}]),
            ):
                events = list(
                    main_routes_tasks.stream_bootstrap_start_events(payload)
                )
            self.assertEqual("done", events[-1]["type"])
            self.assertEqual("interviewing", events[-1]["state"]["status"])
            self.assertEqual(
                "Demo",
                events[-1]["state"]["project"]["name"],
            )

    def test_stream_bootstrap_answer_emits_done_event(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            from main_routes_tasks import (
                atomic_write_json,
                managed_path,
            )
            atomic_write_json(
                managed_path(root, "bootstrap.json"),
                {
                    "schema_version": 1,
                    "status": "interviewing",
                    "project": {
                        "name": "Demo",
                        "slug": "demo",
                        "root": str(root),
                        "mode": "new",
                    },
                    "answers": {},
                    "user_profile": {},
                    "interview_mode": "guided",
                    "architecture": {},
                    "current_question": {
                        "id": "q1",
                        "title": "目标",
                        "prompt": "目标是什么？",
                        "why": "确认范围",
                        "placeholder": "例如：记账",
                        "options": ["自由描述"],
                    },
                    "progress": 5,
                    "created_at": "2026-08-21T00:00:00+00:00",
                    "updated_at": "2026-08-21T00:00:00+00:00",
                    "model_notice": "",
                    "agent_mode": "ai",
                    "model_name": "fake",
                    "agent_turns": 0,
                },
            )
            payload = {
                "path": str(root),
                "answer": "做一个记账工具",
                "provider": {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:9/v1",
                    "model": "fake",
                    "api_key": "x",
                },
            }
            model_result = {
                "architecture": {"modules": []},
                "ready": False,
                "next_question": {
                    "id": "q2",
                    "title": "平台",
                    "prompt": "运行在哪？",
                    "why": "确认部署",
                    "placeholder": "例如：Web",
                    "options": ["Web", "桌面"],
                },
                "thinking": "",
                "graph_quality_issues": [],
            }
            with patch(
                "main_routes_tasks._bootstrap.stream_architecture_model",
                return_value=iter([{"type": "done", "result": model_result}]),
            ):
                events = list(
                    main_routes_tasks.stream_bootstrap_answer_events(payload)
                )
            self.assertEqual("done", events[-1]["type"])
            self.assertEqual("interviewing", events[-1]["state"]["status"])


if __name__ == "__main__":
    unittest.main()
