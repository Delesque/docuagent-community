import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docuagent


class OrchestrateTest(unittest.TestCase):
    PROVIDER = {
        "enabled": True,
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key": "secret",
    }

    def architecture(self) -> dict:
        return docuagent.normalize_architecture({
            "summary": "A small project.",
            "platform": "Windows",
            "language": "Python",
            "runtime": "Python 3.12",
            "frameworks": [],
            "stack": ["Python 3.12"],
            "modules": [
                {
                    "id": "core",
                    "name": "Core",
                    "responsibility": "Core logic.",
                    "path": "src/core",
                    "depends_on": [],
                },
                {
                    "id": "auth",
                    "name": "Auth",
                    "responsibility": "Handle login.",
                    "path": "src/auth",
                    "depends_on": ["core"],
                },
            ],
            "data": [],
            "integrations": [],
            "constraints": [],
            "verification": ["python -m unittest"],
            "risks": [],
            "unresolved": [],
        })

    def project(self) -> tuple[Path, dict, dict]:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        project = {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"}
        return root, self.architecture(), project

    def provider(self) -> docuagent.ProviderConfig:
        return docuagent.ProviderConfig(
            base_url=self.PROVIDER["base_url"],
            model=self.PROVIDER["model"],
            api_key=self.PROVIDER["api_key"],
        )

    def test_normalize_orchestration_keeps_coordination_metadata(self) -> None:
        plan = docuagent.normalize_orchestration(
            {
                "summary": "Core first, then auth.",
                "conflicts": ["auth depends on core"],
                "priorities": {"core": "high"},
                "tasks": [
                    {
                        "id": "core",
                        "module_id": "core",
                        "summary": "Implement core.",
                        "target_files": ["src/core.py"],
                        "depends_on": [],
                        "verification": [],
                    },
                    {
                        "id": "auth",
                        "module_id": "auth",
                        "summary": "Implement auth.",
                        "target_files": ["src/auth.py"],
                        "depends_on": ["core"],
                        "verification": [],
                    },
                ],
            },
            ["core", "auth"],
            ["core", "auth"],
        )
        self.assertEqual("Core first, then auth.", plan["orchestration"]["summary"])
        self.assertEqual(["auth depends on core"], plan["orchestration"]["conflicts"])
        self.assertEqual({"core": "high"}, plan["orchestration"]["priorities"])
        self.assertEqual("high", plan["tasks"][0]["priority"])
        self.assertEqual("medium", plan["tasks"][1]["priority"])

    def test_normalize_orchestration_rejects_out_of_scope_module(self) -> None:
        with self.assertRaisesRegex(docuagent.WorkspaceError, "超出.*编排"):
            docuagent.normalize_orchestration(
                {
                    "summary": "Wrong scope.",
                    "tasks": [
                        {
                            "id": "auth",
                            "module_id": "auth",
                            "summary": "Auth.",
                            "target_files": ["src/auth.py"],
                            "depends_on": [],
                            "verification": [],
                        }
                    ],
                },
                ["core", "auth"],
                ["core"],
            )

    def test_orchestrate_tasks_plans_cross_module_and_persists_record(self) -> None:
        root, architecture, project = self.project()
        docuagent.atomic_write_text(
            docuagent.managed_path(root, "recipes.md"),
            "- Reuse the standard library before adding dependencies.\n",
        )
        docuagent.atomic_write_json(
            docuagent.managed_path(root, "tasks.json"),
            {
                "schema_version": 1,
                "task_version": 1,
                "status": "planned",
                "tasks": [
                    {
                        "id": "report",
                        "module_id": "report",
                        "summary": "Report module.",
                        "target_files": ["src/report.py"],
                        "depends_on": [],
                        "verification": [],
                        "status": "pending",
                        "patch": [],
                        "thinking": "",
                        "last_error": "",
                    }
                ],
                "last_error": "",
            },
        )
        result = {
            "summary": "Core first, then auth.",
            "conflicts": [],
            "priorities": {"core": "high"},
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Implement core.",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                },
                {
                    "id": "auth",
                    "module_id": "auth",
                    "summary": "Implement auth.",
                    "target_files": ["src/auth.py"],
                    "depends_on": ["core"],
                    "verification": [],
                },
            ],
        }
        with patch("orchestrate.call_model_json", return_value=result) as mocked:
            plan = docuagent.orchestrate_tasks(
                root,
                self.provider(),
                architecture,
                project,
                module_id="auth",
            )

        self.assertIn(
            "Reuse the standard library",
            mocked.call_args[0][2]["recipes"],
        )
        self.assertEqual(
            {"core", "auth", "report"},
            {task["module_id"] for task in plan["tasks"]},
        )
        self.assertEqual(
            "high",
            next(task for task in plan["tasks"] if task["id"] == "core")["priority"],
        )
        self.assertEqual("Core first, then auth.", plan["orchestration"]["summary"])
        state = docuagent.read_json(docuagent.managed_path(root, "tasks.json"))
        self.assertEqual(
            {"core", "auth", "report"},
            {t["module_id"] for t in state["tasks"]},
        )
        record = docuagent.read_json(
            docuagent.managed_path(root, "orchestrator/latest.json")
        )
        self.assertEqual("auth", record["trigger_module"])
        self.assertTrue(
            docuagent.managed_path(root, "orchestrator/log.md").exists()
        )

    def test_normalize_triage_result_groups_errors_and_order(self) -> None:
        errors = [
            {
                "task_id": "auth",
                "module_id": "auth",
                "title": "Auth import failed",
                "detail": "cannot import core",
            },
            {
                "task_id": "core",
                "module_id": "core",
                "title": "Core syntax error",
                "detail": "invalid syntax",
            },
        ]
        result = docuagent.normalize_triage_result(
            {
                "summary": "Core breaks auth.",
                "dependency_chains": [["core", "auth"]],
                "groups": [
                    {
                        "severity": "critical",
                        "recommendation": "Fix core first.",
                        "errors": ["core", "auth"],
                    }
                ],
                "suggested_order": ["core", "auth"],
            },
            errors,
        )
        self.assertEqual("Core breaks auth.", result["summary"])
        self.assertEqual([["core", "auth"]], result["dependency_chains"])
        self.assertEqual("critical", result["groups"][0]["severity"])
        self.assertEqual(["core", "auth"], result["suggested_order"])
        self.assertEqual(
            "cannot import core",
            result["groups"][0]["errors"][1]["detail"],
        )

    def test_triage_errors_calls_model_and_writes_record(self) -> None:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        errors = [
            {
                "task_id": "core",
                "module_id": "core",
                "title": "Syntax error",
                "detail": "invalid syntax",
            }
        ]
        raw = {
            "summary": "Fix core.",
            "dependency_chains": [],
            "groups": [
                {
                    "severity": "warning",
                    "recommendation": "Fix it.",
                    "errors": ["core"],
                }
            ],
            "suggested_order": ["core"],
        }
        with patch("orchestrate.call_model_json", return_value=raw) as mocked:
            result = docuagent.triage_errors(root, self.provider(), errors)

        self.assertEqual("Fix core.", result["summary"])
        self.assertEqual(["core"], result["suggested_order"])
        self.assertEqual(
            errors[0]["task_id"],
            mocked.call_args[0][2]["errors"][0]["task_id"],
        )
        self.assertTrue(
            docuagent.managed_path(root, "orchestrator/triage-latest.json").exists()
        )

    def test_triage_errors_reuses_same_persisted_summary(self) -> None:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        errors = [{"task_id": "core", "module_id": "core", "title": "Failure", "detail": "boom"}]
        raw = {
            "summary": "Persisted summary.",
            "dependency_chains": [],
            "groups": [{"severity": "critical", "recommendation": "Fix it.", "errors": ["core"]}],
            "suggested_order": ["core"],
        }
        with patch("orchestrate.call_model_json", return_value=raw) as mocked:
            first = docuagent.triage_errors(root, self.provider(), errors)
            second = docuagent.triage_errors(root, self.provider(), errors)

        self.assertEqual(first, second)
        self.assertEqual(1, mocked.call_count)

    def test_stream_orchestrate_tasks_yields_reasoning_and_done(self) -> None:
        root, architecture, project = self.project()
        plan_result = {
            "summary": "Planned.",
            "conflicts": [],
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Implement core.",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                }
            ],
        }

        def fake_stream(*_args, **_kwargs):
            yield {"type": "reasoning", "text": "reading notes"}
            yield {"type": "done", "result": plan_result}

        with patch("orchestrate.stream_json_model", side_effect=fake_stream):
            events = list(
                docuagent.stream_orchestrate_tasks(
                    root,
                    self.provider(),
                    architecture,
                    project,
                    module_id="core",
                )
            )

        self.assertEqual("started", events[0]["type"])
        self.assertEqual("core", events[0]["module_id"])
        self.assertEqual(["auth", "core"], events[0]["scope_module_ids"])
        self.assertEqual("reasoning", events[1]["type"])
        self.assertIn("reading notes", events[1]["text"])
        self.assertEqual("done", events[-1]["type"])
        self.assertEqual("Planned.", events[-1]["tasks"]["orchestration"]["summary"])


if __name__ == "__main__":
    unittest.main()
