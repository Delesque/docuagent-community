"""Structured interview plan (scheme B).

The model authors its own coverage plan (`interview_plan`) up front and every
question belongs to one aspect (`next_question.aspect_id`). These tests pin the
plan normalisation, per-aspect budget counting, steering injection, state
persistence, and the public_state progress that finally speaks the same
vocabulary as the model's actual questions.
"""

import unittest
from unittest.mock import patch

import bootstrap
from llm_client import ProviderConfig


class NormalizePlanTest(unittest.TestCase):
    def test_valid_plan_is_normalized(self) -> None:
        plan = bootstrap.normalize_plan([
            {"id": "User Goals", "title": "项目目标", "why": "边界"},
            {"id": "stack", "title": "技术栈"},
            {"junk": "no id"},
            {"id": "stack", "title": "重复"},
            "",
        ])
        self.assertIsNotNone(plan)
        self.assertEqual([a["id"] for a in plan], ["user-goals", "stack"])

    def test_invalid_plan_returns_none(self) -> None:
        self.assertIsNone(bootstrap.normalize_plan(None))
        self.assertIsNone(bootstrap.normalize_plan("nope"))
        self.assertIsNone(bootstrap.normalize_plan([]))
        self.assertIsNone(bootstrap.normalize_plan([{"title": "no id"}]))


def _plan() -> list[dict[str, str]]:
    return [
        {"id": "goal", "title": "项目目标"},
        {"id": "stack", "title": "技术栈"},
    ]


class AspectCountTest(unittest.TestCase):
    def test_counts_from_question_aspects_mapping(self) -> None:
        question_aspects = {"q1": "goal", "q2": "goal", "q3": "stack"}
        count = bootstrap.aspect_question_count(
            _plan(), "goal", {}, question_aspects
        )
        self.assertEqual(count, 2)

    def test_empty_mapping_counts_zero(self) -> None:
        count = bootstrap.aspect_question_count(_plan(), "goal", {}, {})
        self.assertEqual(count, 0)

    def test_fallback_prefix_when_mapping_absent(self) -> None:
        answers = {"goal-1": "a", "goal-2": "b", "stack-choice": "c"}
        count = bootstrap.aspect_question_count(_plan(), "goal", answers, None)
        self.assertEqual(count, 2)

    def test_none_mapping_means_legacy(self) -> None:
        answers = {"goal": "a"}
        count = bootstrap.aspect_question_count(_plan(), "goal", answers, None)
        self.assertEqual(count, 1)


class AspectSteeringTest(unittest.TestCase):
    def test_no_plan_asks_model_to_establish_one(self) -> None:
        notice = bootstrap.aspect_steering(None, {}, None)
        self.assertIsNotNone(notice)
        self.assertIn("interview_plan", notice)

    def test_exhausted_aspect_is_named(self) -> None:
        question_aspects = {"q1": "goal", "q2": "goal", "q3": "goal"}
        notice = bootstrap.aspect_steering(_plan(), {}, question_aspects)
        self.assertIsNotNone(notice)
        self.assertIn("项目目标", notice)
        # stack is not exhausted, so it is not named as a blocked aspect.
        self.assertNotIn("技术栈", notice)

    def test_no_exhaustion_returns_none(self) -> None:
        question_aspects = {"q1": "goal"}
        self.assertIsNone(bootstrap.aspect_steering(_plan(), {}, question_aspects))


class InterviewPlanInjectionTest(unittest.TestCase):
    def _state(self, plan=None, question_aspects=None, answers=None) -> dict:
        return {
            "project": {"name": "Demo"},
            "user_profile": {},
            "interview_mode": "guided",
            "answers": answers or {},
            "architecture": {},
            "agent_turns": 0,
            "interview_plan": plan,
            "question_aspects": question_aspects,
        }

    def test_plan_with_budget_is_injected(self) -> None:
        captured: dict = {}

        def fake_call(config, system_prompt, model_input, **kwargs):
            captured.update(model_input)
            return {
                "architecture": {"modules": []},
                "ready": False,
                "next_question": {"id": "next", "title": "t", "prompt": "p", "options": ["自由描述"], "aspect_id": "stack"},
                "thinking": "",
                "graph_quality_issues": [],
            }

        with patch.object(bootstrap, "call_model_json", fake_call):
            bootstrap.call_architecture_model(
                # goal already has three questions -> exhausted
                self._state(_plan(), {"q1": "goal", "q2": "goal", "q3": "goal"}),
                {"question_id": "x", "answer": "y"},
                ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
            )
        self.assertIn("interview_plan", captured)
        by_id = {item["id"]: item for item in captured["interview_plan"]}
        self.assertEqual(by_id["goal"]["asked"], 3)
        self.assertEqual(by_id["goal"]["remaining"], 0)
        self.assertEqual(by_id["stack"]["remaining"], 3)
        self.assertIn("aspect_steering", captured)
        self.assertIn("项目目标", captured["aspect_steering"])

    def test_no_plan_no_steering(self) -> None:
        captured: dict = {}

        def fake_call(config, system_prompt, model_input, **kwargs):
            captured.update(model_input)
            return {
                "architecture": {"modules": []},
                "ready": False,
                "next_question": {"id": "q", "title": "t", "prompt": "p", "options": ["自由描述"]},
                "thinking": "",
                "graph_quality_issues": [],
            }

        with patch.object(bootstrap, "call_model_json", fake_call):
            bootstrap.call_architecture_model(
                self._state(None, None),
                {"question_id": "x", "answer": "y"},
                ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
            )
        self.assertNotIn("interview_plan", captured)
        self.assertNotIn("aspect_steering", captured)


class ApplyPlanPersistenceTest(unittest.TestCase):
    def test_apply_stores_plan_and_question_aspect(self) -> None:
        state = {"answers": {}, "architecture": {}, "agent_turns": 0}
        model_result = {
            "architecture": {"modules": []},
            "ready": False,
            "next_question": {
                "id": "stack-pick",
                "title": "选型",
                "prompt": "p",
                "options": ["自由描述"],
                "aspect_id": "stack",
            },
            "thinking": "",
            "graph_quality_issues": [],
            "interview_plan": _plan(),
        }
        bootstrap.apply_architecture_model_result(
            state,
            model_result,
            ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
        )
        self.assertEqual(state["interview_plan"], _plan())
        self.assertEqual(state["question_aspects"], {"stack-pick": "stack"})

    def test_apply_keeps_existing_plan_when_model_omits_it(self) -> None:
        state = {
            "answers": {},
            "architecture": {},
            "agent_turns": 0,
            "interview_plan": _plan(),
            "question_aspects": {"a": "goal"},
        }
        model_result = {
            "architecture": {"modules": []},
            "ready": True,
            "next_question": None,
            "thinking": "",
            "graph_quality_issues": [],
            # no interview_plan key -> None -> keep state plan
            "interview_plan": None,
        }
        bootstrap.apply_architecture_model_result(
            state,
            model_result,
            ProviderConfig(base_url="https://x/v1", model="m", api_key="k"),
        )
        self.assertEqual(state["interview_plan"], _plan())
        self.assertEqual(state["question_aspects"], {"a": "goal"})


class ValidateResultPlanTest(unittest.TestCase):
    def test_model_plan_wins_over_state_plan(self) -> None:
        raw = {
            "architecture": {"modules": []},
            "ready": False,
            "next_question": {"id": "q", "title": "t", "prompt": "p", "options": ["自由描述"]},
            "interview_plan": [{"id": "fresh", "title": "新方面"}],
        }
        result = bootstrap.validate_architecture_model_result(
            raw, {}, interview_plan=_plan()
        )
        self.assertEqual([a["id"] for a in result["interview_plan"]], ["fresh"])

    def test_invalid_model_plan_keeps_state_plan(self) -> None:
        raw = {
            "architecture": {"modules": []},
            "ready": False,
            "next_question": {"id": "q", "title": "t", "prompt": "p", "options": ["自由描述"]},
            "interview_plan": "garbage",
        }
        result = bootstrap.validate_architecture_model_result(
            raw, {}, interview_plan=_plan()
        )
        self.assertEqual(result["interview_plan"], _plan())

    def test_aspect_id_survives_normalization(self) -> None:
        raw = {
            "architecture": {"modules": []},
            "ready": False,
            "next_question": {
                "id": "q1",
                "title": "t",
                "prompt": "p",
                "options": ["自由描述"],
                "aspect_id": "goal",
            },
        }
        result = bootstrap.validate_architecture_model_result(raw, {}, interview_plan=_plan())
        self.assertEqual(result["next_question"]["aspect_id"], "goal")


class PublicStateProgressTest(unittest.TestCase):
    """Progress display finally matches what the model actually asks."""

    def _public(self, state: dict) -> dict:
        import main_routes_bootstrap as routes

        return routes.public_state(state) or {}

    def test_plan_driven_remaining(self) -> None:
        state = {
            "schema_version": 1,
            "status": "interviewing",
            "project": {"name": "Demo", "slug": "demo", "root": "/tmp/x", "mode": "new"},
            "architecture": {},
            "answers": {"q1": "回答1", "q2": "回答2"},
            "current_question": {"id": "q3", "title": "t", "prompt": "p"},
            "progress": 50,
            "agent_mode": "ai",
            "model_name": None,
            "model_notice": None,
            "thinking": "",
            "interview_plan": _plan(),
            "question_aspects": {"q1": "goal", "q2": "goal"},
        }
        result = self._public(state)
        # goal already has 2 questions; only stack is not started.
        self.assertEqual(result["topics_remaining"], ["技术栈"])
        self.assertEqual(result["topics_total"], 2)
        by_id = {item["id"]: item for item in result["interview_plan"]}
        self.assertEqual(by_id["goal"]["asked"], 2)

    def test_fixed_topics_fallback_without_plan(self) -> None:
        state = {
            "schema_version": 1,
            "status": "interviewing",
            "project": {"name": "Demo", "slug": "demo", "root": "/tmp/x", "mode": "new"},
            "architecture": {},
            "answers": {"goal": "已答"},  # only the fixed goal topic is covered
            "current_question": {"id": "q3", "title": "t", "prompt": "p"},
            "progress": 50,
            "agent_mode": "ai",
            "model_name": None,
            "model_notice": None,
            "thinking": "",
        }
        result = self._public(state)
        self.assertNotIn("项目目标", result["topics_remaining"])
        self.assertEqual(result["topics_total"], len(bootstrap.INTERVIEW_TOPICS))


if __name__ == "__main__":
    unittest.main()
