import unittest

from bootstrap import (
    ARCHITECTURE_EDIT_SYSTEM_PROMPT,
    ARCHITECTURE_SYSTEM_PROMPT,
)
from standards import DEFAULT_STANDARDS_MD
from tasks import (
    DOC_SYNC_PROMPT,
    IMPLEMENTATION_PROMPT,
    TASK_PLAN_PROMPT,
)


class PromptsTest(unittest.TestCase):
    def test_architecture_prompts_include_integrity_rules(self) -> None:
        self.assertIn("Architecture integrity rules", ARCHITECTURE_SYSTEM_PROMPT)
        self.assertIn("Architecture integrity rules", ARCHITECTURE_EDIT_SYSTEM_PROMPT)

    def test_architecture_prompts_include_engineering_graph_discipline(self) -> None:
        self.assertIn("Engineering graph discipline", ARCHITECTURE_SYSTEM_PROMPT)
        self.assertIn("Engineering graph discipline", ARCHITECTURE_EDIT_SYSTEM_PROMPT)
        self.assertIn("must NOT fan out", ARCHITECTURE_SYSTEM_PROMPT)
        self.assertIn("merge it into its consumer", ARCHITECTURE_SYSTEM_PROMPT)

    def test_task_planning_prompt_includes_integrity_rules(self) -> None:
        self.assertIn("Task integrity rules", TASK_PLAN_PROMPT)

    def test_implementation_prompt_includes_safety_rules(self) -> None:
        self.assertIn("Software safety and engineering standards", IMPLEMENTATION_PROMPT)
        self.assertIn("Never reference undeclared variables", IMPLEMENTATION_PROMPT)
        self.assertIn("project recipes", IMPLEMENTATION_PROMPT)

    def test_doc_sync_prompt_includes_integrity_rules(self) -> None:
        self.assertIn("Document integrity rules", DOC_SYNC_PROMPT)

    def test_default_standards_cover_nonexistent_names(self) -> None:
        self.assertIn("Never declare placeholders", DEFAULT_STANDARDS_MD)
        self.assertIn("Every name", DEFAULT_STANDARDS_MD)


if __name__ == "__main__":
    unittest.main()
