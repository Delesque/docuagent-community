import tempfile
import unittest
from pathlib import Path

import context_cache


def _context(**overrides):
    base = {
        "user_profile": "喜欢简洁的 UI",
        "standards": "遵循 PEP8",
        "recipes": "用 dataclass 定义模型",
        "project_overview": "一个小项目",
        "contract_view": {"vocabulary": [], "own_exports": []},
        "module_contract": {"id": "core", "name": "Core"},
        "unresolved_attachments": [{"id": "a1", "text": "待定"}],
        "error_memory": [],
        "work_log_tail": "首次生成",
    }
    base.update(overrides)
    return base


class ContextCacheTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_first_record_is_cold_miss(self) -> None:
        context_cache.record_context(self.root, _context())
        summary = context_cache.cache_summary(self.root)
        self.assertEqual(0, summary["total_hits"])
        self.assertEqual(9, summary["total_misses"])
        self.assertIsNone(summary["overall_hit_rate"] or None)

    def test_same_context_hits_on_second_call(self) -> None:
        context_cache.record_context(self.root, _context())
        context_cache.record_context(self.root, _context())
        summary = context_cache.cache_summary(self.root)
        self.assertEqual(9, summary["total_hits"])
        self.assertEqual(9, summary["total_misses"])
        self.assertEqual(0.5, summary["overall_hit_rate"])

    def test_changed_block_counts_miss_only_for_that_block(self) -> None:
        context_cache.record_context(self.root, _context())
        context_cache.record_context(self.root, _context(standards="新规范"))
        summary = context_cache.cache_summary(self.root)
        by_key = {block["key"]: block for block in summary["blocks"]}
        self.assertEqual(0, by_key["standards"]["hits"])
        self.assertEqual(2, by_key["standards"]["misses"])
        self.assertEqual(1, by_key["user_profile"]["hits"])
        self.assertEqual(1, by_key["user_profile"]["misses"])

    def test_blocks_ordered_by_stability(self) -> None:
        context_cache.record_context(self.root, _context())
        summary = context_cache.cache_summary(self.root)
        keys = [block["key"] for block in summary["blocks"]]
        self.assertEqual(list(context_cache.BLOCK_KEYS), keys)

    def test_module_cache_rate_is_independent(self) -> None:
        context_cache.record_context(self.root, _context(), module_id="core")
        context_cache.record_context(self.root, _context(), module_id="core")
        context_cache.record_context(
            self.root,
            _context(standards="另一个模块"),
            module_id="api",
        )

        summary = context_cache.cache_summary(self.root)
        by_id = {item["module_id"]: item for item in summary["modules"]}
        self.assertEqual(0.5, by_id["core"]["overall_hit_rate"])
        self.assertEqual(0.0, by_id["api"]["overall_hit_rate"])
