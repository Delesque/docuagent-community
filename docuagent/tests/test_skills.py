import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bootstrap import ProviderConfig
import skills
from core import WorkspaceError


class SkillsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.source = self.root / "src-skill" / "python-tests"
        self.source.mkdir(parents=True)
        (self.source / "skill.json").write_text(
            json.dumps({
                "name": "python-tests",
                "description": "Write pytest tests for Python modules.",
                "tags": ["python", "pytest", "testing"],
            }),
            encoding="utf-8",
        )
        (self.source / "SKILL.md").write_text(
            "# Python Tests\nPrefer pytest fixtures.\n",
            encoding="utf-8",
        )

    def test_import_skill_copies_into_project(self) -> None:
        result = skills.import_skill(self.root, str(self.source))
        self.assertEqual("python-tests", result["name"])
        self.assertTrue((self.root / ".docuagent" / "skills" / "python-tests").is_dir())

    def test_list_and_match_skills(self) -> None:
        skills.import_skill(self.root, str(self.source))
        listed = skills.list_skills(self.root)
        self.assertEqual(1, len(listed))
        matched = skills.matching_skills(self.root, "pytest testing")
        self.assertEqual("python-tests", matched[0]["name"])
        self.assertIn("Prefer pytest fixtures", matched[0]["content"])

    def test_marketplace_install_from_local_json(self) -> None:
        market = self.root / "marketplace.json"
        market.write_text(
            json.dumps({
                "skills": [
                    {
                        "name": "python-tests",
                        "description": "pytest",
                        "tags": [],
                        "source": str(self.source),
                    }
                ]
            }),
            encoding="utf-8",
        )
        result = skills.install_skill(self.root, str(market), "python-tests")
        self.assertEqual("python-tests", result["name"])

    def test_select_skills_returns_full_content(self) -> None:
        skills.import_skill(self.root, str(self.source))
        provider = ProviderConfig(
            base_url="http://localhost:11434/v1",
            model="local",
            api_key="",
        )
        available = skills.list_skills(self.root)
        with mock.patch(
            "skills.call_model_json",
            return_value={"skill_names": ["python-tests"]},
        ):
            selected = skills.select_skills(provider, "pytest", available)
        self.assertEqual(1, len(selected))
        self.assertIn("Prefer pytest fixtures", selected[0]["content"])

    def test_import_allows_oversized_skill_with_warning(self) -> None:
        oversized = self.root / "oversized"
        oversized.mkdir()
        (oversized / "skill.json").write_text(
            json.dumps({"name": "big", "description": "big", "tags": []}),
            encoding="utf-8",
        )
        (oversized / "SKILL.md").write_text("x" * (skills.MAX_SKILL_CHARS + 1), encoding="utf-8")
        result = skills.import_skill(self.root, str(oversized))
        self.assertEqual("big", result["name"])
        self.assertIn("建议精简", result["warning"])
        listed = skills.list_skills(self.root)[0]
        self.assertEqual(skills.MAX_SKILL_CHARS + 1, len(listed["content"]))

    def test_invalid_skill_source_rejected(self) -> None:
        with self.assertRaises(WorkspaceError):
            skills.import_skill(self.root, str(self.root / "missing"))


if __name__ == "__main__":
    unittest.main()
