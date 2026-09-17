import unittest

from bootstrap import (
    INTERVIEW_MODES,
    MODE_GUIDANCE,
    PROFILE_QUESTIONS,
    normalize_interview_mode,
    profile_complete,
    profile_question,
    validate_architecture_model_result,
)


class UserProfileTest(unittest.TestCase):
    def test_exposes_three_interview_modes(self) -> None:
        self.assertEqual(("beginner", "guided", "professional"), INTERVIEW_MODES)
        self.assertEqual(set(INTERVIEW_MODES), set(MODE_GUIDANCE))

    def test_profile_questions_are_the_five_documented_facts(self) -> None:
        self.assertEqual(
            ["identity", "coding_experience", "desired_help", "explanation_preference", "constraints"],
            [item["id"] for item in PROFILE_QUESTIONS],
        )
        self.assertEqual("identity", profile_question(0)["id"])
        self.assertIsNone(profile_question(5))

    def test_mode_normalization_defaults_to_guided(self) -> None:
        self.assertEqual("beginner", normalize_interview_mode("BEGINNER"))
        self.assertEqual("guided", normalize_interview_mode("unknown"))
        self.assertEqual("guided", normalize_interview_mode(None))

    def test_mode_offer_accepts_only_a_complete_valid_shape(self) -> None:
        result = validate_architecture_model_result(
            {
                "architecture": {"summary": "x", "modules": [], "edges": [], "data": [], "integrations": [], "constraints": [], "verification": [], "risks": [], "unresolved": []},
                "ready": False,
                "next_question": {"id": "goal", "title": "目标", "prompt": "目标？", "options": ["自由描述"]},
                "mode_offer": {"target_mode": "beginner", "label": "是否让我之后说得更简单？", "confirm_label": "说得更简单"},
            },
            {},
        )
        self.assertEqual("beginner", result["mode_offer"]["target_mode"])

    def test_profile_completion_requires_all_five_answers(self) -> None:
        profile = {item["id"]: "不确定" for item in PROFILE_QUESTIONS}
        self.assertTrue(profile_complete(profile))
        profile.pop("constraints")
        self.assertFalse(profile_complete(profile))


if __name__ == "__main__":
    unittest.main()
