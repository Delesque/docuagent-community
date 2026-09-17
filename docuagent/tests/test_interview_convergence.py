"""Interview convergence guards.

The real-model pilot showed the architecture agent looping on one topic as
infinite id variants (extractor-lexing-approach → -strategy-15 → -policy-18)
and never setting ready=true. These tests pin the escalation ladder and the
model_input injection.
"""

import unittest
from unittest.mock import patch

import bootstrap
from llm_client import ProviderConfig


# identity/goal are ordinary answered topics; the extractor-* ids are the exact
# variant loop observed in the pilot.
VARIANTS = [
    "identity",
    "goal",
    "extractor-lexing-approach",
    "extractor-lexing-strategy",
    "extractor-lexing-strategy-15",
    "extractor-lexing-policy-final",
    "extractor-lexing-strategy-17",
]


def _answers(count: int) -> dict:
    return {question_id: "答" for question_id in VARIANTS[:count]}


class RepetitionNoticeTest(unittest.TestCase):
    def test_below_threshold_returns_none(self) -> None:
        self.assertIsNone(bootstrap.repetition_notice(_answers(4)))
        self.assertIsNone(bootstrap.repetition_notice(None))
        self.assertIsNone(bootstrap.repetition_notice({}))

    def test_three_variants_ask_to_move_on(self) -> None:
        notice = bootstrap.repetition_notice(_answers(5))
        self.assertIsNotNone(notice)
        self.assertIn("extractor-lexing", notice)
        self.assertIn("下一个不同方面", notice)

    def test_four_variants_get_stern_warning(self) -> None:
        notice = bootstrap.repetition_notice(_answers(6))
        self.assertIn("不许再问", notice)

    def test_five_variants_get_hard_stop(self) -> None:
        notice = bootstrap.repetition_notice(_answers(7))
        self.assertIn("禁止再提出任何问题", notice)
        self.assertIn("ready=true", notice)


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
    "thinking": "",
    "graph_quality_issues": [],
}


class SystemFactsPromptTest(unittest.TestCase):
    """The architecture prompt carries the execution facts that stop it from
    designing unfulfillable verification commands and overlapping modules."""

    def test_system_facts_section_is_present(self) -> None:
        self.assertIn("SYSTEM FACTS", bootstrap.ARCHITECTURE_SYSTEM_PROMPT)
        self.assertIn("each module becomes ONE bounded sub-agent", bootstrap.ARCHITECTURE_SYSTEM_PROMPT)
        self.assertIn("Verification commands are promises", bootstrap.ARCHITECTURE_SYSTEM_PROMPT)
        self.assertIn("read_files", bootstrap.ARCHITECTURE_SYSTEM_PROMPT)
        self.assertIn("overlap two modules' file scopes", bootstrap.ARCHITECTURE_SYSTEM_PROMPT)

    def test_stale_never_finish_early_removed(self) -> None:
        self.assertNotIn("do not finish early", bootstrap.ARCHITECTURE_SYSTEM_PROMPT)

    def test_coverage_floor_wording_kept(self) -> None:
        self.assertIn("coverage floor", bootstrap.ARCHITECTURE_SYSTEM_PROMPT)


def _state(answers: dict) -> dict:
    return {
        "project": {"name": "Demo"},
        "user_profile": {},
        "interview_mode": "guided",
        "answers": answers,
        "architecture": {},
        "agent_turns": len(answers),
    }


class ModelInputInjectionTest(unittest.TestCase):
    def test_notice_is_injected_into_model_input(self) -> None:
        captured: dict = {}

        def fake_call(config, system_prompt, model_input, **kwargs):
            captured.update(model_input)
            return dict(MODEL_RESULT)

        with patch.object(bootstrap, "call_model_json", fake_call):
            bootstrap.call_architecture_model(
                _state(_answers(5)),
                {"question_id": "x", "answer": "y"},
                ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
            )
        self.assertIn("repetition_notice", captured)
        self.assertIn("extractor-lexing", captured["repetition_notice"])

    def test_no_notice_below_threshold(self) -> None:
        captured: dict = {}

        def fake_call(config, system_prompt, model_input, **kwargs):
            captured.update(model_input)
            return dict(MODEL_RESULT)

        with patch.object(bootstrap, "call_model_json", fake_call):
            bootstrap.call_architecture_model(
                _state(_answers(4)),
                {"question_id": "x", "answer": "y"},
                ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
            )
        self.assertNotIn("repetition_notice", captured)
