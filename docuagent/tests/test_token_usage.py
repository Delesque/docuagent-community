import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import token_usage


class TokenUsageTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.patcher = patch(
            "token_usage._usage_path", return_value=self.tmp / "token-usage.json"
        )
        self.patcher.start()

    def tearDown(self) -> None:
        self.patcher.stop()

    def test_parse_usage_deepseek(self) -> None:
        parsed = token_usage.parse_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "total_tokens": 150,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
        })
        self.assertEqual(100, parsed["prompt_tokens"])
        self.assertEqual(50, parsed["completion_tokens"])
        self.assertEqual(80, parsed["cache_hit_tokens"])
        self.assertEqual(20, parsed["cache_miss_tokens"])

    def test_parse_usage_openai_cached(self) -> None:
        parsed = token_usage.parse_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_tokens_details": {"cached_tokens": 80},
        })
        self.assertEqual(80, parsed["cache_hit_tokens"])
        self.assertEqual(20, parsed["cache_miss_tokens"])
        self.assertEqual(150, parsed["total_tokens"])

    def test_record_and_summary(self) -> None:
        token_usage.record_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_cache_hit_tokens": 80,
            "prompt_cache_miss_tokens": 20,
        })
        token_usage.record_usage({
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
        })
        summary = token_usage.usage_summary()
        self.assertEqual(2, summary["calls"])
        self.assertEqual(200, summary["prompt_tokens"])
        self.assertEqual(100, summary["completion_tokens"])
        self.assertEqual(80, summary["cache_hit_tokens"])
        self.assertEqual(120, summary["cache_miss_tokens"])
        self.assertEqual(0.4, summary["server_hit_rate"])

    def test_record_empty_usage_is_noop(self) -> None:
        self.assertEqual({}, token_usage.record_usage(None))
        self.assertEqual(0, token_usage.usage_summary()["calls"])

    def test_project_module_and_feature_breakdown(self) -> None:
        project = self.tmp / "project"
        with token_usage.usage_scope(
            project,
            module_id="core",
            feature="agent",
        ):
            token_usage.record_usage({
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "prompt_cache_hit_tokens": 60,
                "prompt_cache_miss_tokens": 40,
            })
        with token_usage.usage_scope(project, feature="architecture"):
            token_usage.record_usage({
                "prompt_tokens": 20,
                "completion_tokens": 10,
                "prompt_cache_hit_tokens": 10,
                "prompt_cache_miss_tokens": 10,
            })

        summary = token_usage.usage_summary(project)
        self.assertEqual("project", summary["scope"])
        self.assertEqual(2, summary["calls"])
        self.assertEqual(170, summary["total_tokens"])
        self.assertEqual("core", summary["modules"][0]["module_id"])
        self.assertEqual(140, summary["modules"][0]["total_tokens"])
        self.assertEqual(
            {"agent", "architecture"},
            {item["feature"] for item in summary["features"]},
        )
