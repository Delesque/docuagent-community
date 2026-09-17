import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docuagent


class MemoryTest(unittest.TestCase):
    PROVIDER = docuagent.ProviderConfig(
        base_url="https://example.test/v1",
        model="test-model",
        api_key="secret",
    )

    def test_suggest_apply_reject_revert(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            docuagent.atomic_write_text(
                docuagent.managed_path(root, "standards.md"),
                "Old\n",
            )
            docuagent.register_agent(root, "core", "core-task", ["src/core.py"])
            docuagent.append_work_log(
                root, "core", "生成完成", "core-task", "用户偏好 Python 标准库"
            )

            def fake_call(*_args, **_kwargs):
                return {
                    "candidates": [
                        {
                            "target": "standards",
                            "action": "append",
                            "title": "Standard Library",
                            "content": "- Prefer the Python standard library.",
                            "source": "core/work_log.md",
                            "reason": "Reusable project convention.",
                        },
                        {
                            "target": "user-profile",
                            "action": "append",
                            "title": "Language",
                            "content": "- Prefers Python 3.12.",
                            "source": "core/work_log.md",
                            "reason": "Stable user preference.",
                        },
                        {
                            "target": "recipe",
                            "action": "append",
                            "title": "Stdlib First",
                            "content": "- Reuse the Python standard library before adding dependencies.",
                            "source": "core/work_log.md",
                            "reason": "Repeated practice that keeps projects dependency-light.",
                        },
                    ]
                }

            with patch("memory.call_model_json", side_effect=fake_call):
                result = docuagent.suggest_memory_updates(root, self.PROVIDER)

            candidates = result["candidates"]
            self.assertEqual(3, len(candidates))
            self.assertEqual({"pending"}, {c["status"] for c in candidates})

            applied = docuagent.apply_memory_candidate(root, candidates[0]["id"])
            self.assertEqual("applied", applied["status"])
            standards = (
                root / ".docuagent" / "standards.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Standard Library", standards)
            self.assertIn("Python standard library", standards)

            reverted = docuagent.revert_memory_candidate(root, candidates[0]["id"])
            self.assertEqual("reverted", reverted["status"])
            self.assertEqual(
                "Old\n",
                (root / ".docuagent" / "standards.md").read_text(encoding="utf-8"),
            )

            rejected = docuagent.reject_memory_candidate(root, candidates[1]["id"])
            self.assertEqual("rejected", rejected["status"])

            recipe_applied = docuagent.apply_memory_candidate(root, candidates[2]["id"])
            self.assertEqual("applied", recipe_applied["status"])
            recipes = (
                root / ".docuagent" / "recipes.md"
            ).read_text(encoding="utf-8")
            self.assertIn("Stdlib First", recipes)
            self.assertIn("standard library", recipes)

            recipe_reverted = docuagent.revert_memory_candidate(
                root, candidates[2]["id"]
            )
            self.assertEqual("reverted", recipe_reverted["status"])
            self.assertEqual(
                ["reverted", "rejected", "reverted"],
                [c["status"] for c in docuagent.read_memory_candidates(root)],
            )


if __name__ == "__main__":
    unittest.main()
