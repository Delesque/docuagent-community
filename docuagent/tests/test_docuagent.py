import json
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, patch

import bootstrap
import docuagent


def document_model_result(*args, **kwargs) -> dict:
    payload = args[2]
    target = payload["target"]
    lines = [f"# {target['directory']}"]
    lines.extend(f"- `{entry['name']}`" for entry in target["entries"])
    if target["path"] == "AI_ARCH.md":
        facts = payload["workspace_facts"]
        lines.extend(f"{name}/AI_ARCH.md" for name in facts["top_level_directories"])
        if facts.get("requires_python"):
            lines.append(facts["requires_python"])
    return {"files": [{"path": target["path"], "content": "\n".join(lines)}]}


class BootstrapTest(unittest.TestCase):
    def valid_architecture(self) -> dict:
        return {
            "summary": "A local document-first coding workspace.",
            "platform": "Windows",
            "language": "Python",
            "runtime": "Python 3.12",
            "frameworks": [],
            "stack": ["Python 3.12", "HTML", "CSS", "JavaScript"],
            "modules": [
                {
                    "id": "workspace",
                    "name": "Workspace",
                    "responsibility": "Manage project state and files.",
                    "path": "src/workspace",
                    "depends_on": [],
                },
                {
                    "id": "workbench",
                    "name": "Workbench",
                    "responsibility": "Present architecture decisions.",
                    "path": "web",
                    "depends_on": ["workspace"],
                },
                {
                    "id": "verification",
                    "name": "Verification",
                    "responsibility": "Run project checks and report results.",
                    "path": "tests",
                    "depends_on": [],
                },
            ],
            "data": ["Project overview", "Feature requirements"],
            "integrations": ["OpenAI-compatible API"],
            "constraints": ["No chat history", "API keys are not persisted"],
            "verification": ["Run unit tests"],
            "risks": ["Malformed model output"],
            "unresolved": [],
        }

    def valid_model_result(self, ready: bool = True) -> dict:
        return {
            "architecture": self.valid_architecture(),
            "ready": ready,
            "next_question": (
                None
                if ready
                else {
                    "id": "deployment",
                    "title": "部署方式",
                    "prompt": "第一版需要怎样分发？",
                    "why": "分发方式影响项目边界。",
                    "placeholder": "本地 Python 服务",
                }
            ),
        }

    # A configured model is mandatory — there is no offline interview to fall back on —
    # so tests that need a completed interview stub the one model call instead of
    # walking a canned question list.
    TEST_PROVIDER = {
        "enabled": True,
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key": "secret",
    }

    def ready_architecture(self) -> dict:
        """A draft complete enough to pass `architecture_readiness_issues`."""
        architecture = self.valid_architecture()
        architecture["constraints"] = [
            "No chat history",
            "Do not overwrite existing files",
        ]
        architecture["verification"] = [
            "Run unit tests",
            "Run the generated entry point",
        ]
        architecture["stack"] = ["Python 3.12", "standard library"]
        architecture["language"] = "Python"
        return architecture

    def answer_all_questions(self, root: Path) -> dict:
        """Complete the five profile turns, then stub the architecture model."""
        state = docuagent.start_bootstrap({
            "path": str(root), "name": "Sample Tool",
            "description": "A local Python tool for structured project work.",
            "provider": self.TEST_PROVIDER, "interview_mode": "professional",
        })
        for index, question in enumerate(bootstrap.PROFILE_QUESTIONS):
            self.assertEqual(question["id"], state["current_question"]["id"])
            payload = {"path": str(root), "answer": f"profile-{index}", "provider": self.TEST_PROVIDER}
            if index == len(bootstrap.PROFILE_QUESTIONS) - 1:
                ready = docuagent.validate_architecture_model_result(
                    {"architecture": self.ready_architecture(), "ready": True, "next_question": None}, {}
                )
                with patch("docuagent.call_architecture_model", return_value=ready) as model_call:
                    state = docuagent.answer_bootstrap(payload)
                self.assertEqual(1, model_call.call_count)
            else:
                with patch("docuagent.call_architecture_model") as model_call:
                    state = docuagent.answer_bootstrap(payload)
                self.assertEqual(0, model_call.call_count)
        self.assertEqual("review", state["status"])
        self.assertEqual({item["id"] for item in bootstrap.PROFILE_QUESTIONS}, set(state["user_profile"]))
        self.assertEqual("professional", state["interview_mode"])
        return state

    def test_new_python_project_generates_memory_docs_and_scaffold(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sample-tool"
            state = self.answer_all_questions(root)

            self.assertEqual("review", state["status"])
            state = docuagent.confirm_bootstrap({"path": str(root)})
            self.assertEqual("ready", state["status"])
            with patch("tasks.call_model_json", side_effect=document_model_result):
                result = docuagent.finalize_bootstrap({
                    "path": str(root), "provider": self.TEST_PROVIDER,
                })

            self.assertEqual("initialized", result["status"])
            self.assertTrue((root / ".docuagent" / "project.md").exists())
            self.assertTrue((root / ".docuagent" / "standards.md").exists())
            self.assertTrue((root / ".docuagent" / "architecture.json").exists())
            self.assertTrue((root / "AI_ARCH.md").exists())
            self.assertTrue((root / "src" / "sample-tool".replace("-", "_") / "main.py").exists())
            self.assertTrue((root / "tests" / "AI_ARCH.md").exists())
            self.assertIn(
                'requires-python = ">=3.12"',
                (root / "pyproject.toml").read_text(encoding="utf-8"),
            )

            overview = (root / ".docuagent" / "project.md").read_text(encoding="utf-8")
            self.assertIn("No chat history", overview)

    def test_existing_business_file_is_not_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "existing"
            root.mkdir()
            main_file = root / "main.py"
            main_file.write_text("print('keep me')\n", encoding="utf-8")

            with patch("onboard.require_available"):
                state = self.answer_all_questions(root)
            self.assertEqual("imported", state["project"]["mode"])
            docuagent.confirm_bootstrap({"path": str(root)})
            with patch("tasks.call_model_json", side_effect=document_model_result):
                docuagent.finalize_bootstrap({
                    "path": str(root), "provider": self.TEST_PROVIDER,
                })

            self.assertEqual("print('keep me')\n", main_file.read_text(encoding="utf-8"))
            self.assertTrue((root / "AI_ARCH.md").exists())
            self.assertFalse((root / "src").exists())

    def test_python_scaffold_follows_root_package_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "filesentinel"
            state = {
                "project": {
                    "mode": "new",
                    "slug": "filesentinel",
                    "name": "FileSentinel",
                },
                "answers": {"goal": "Audit a local directory."},
                "architecture": {
                    "summary": "Audit a local directory.",
                    "runtime": "Python 3.12",
                    "stack": ["Python 3.12 standard library"],
                    "modules": [
                        {
                            "path": "filesentinel/scanner.py",
                            "target_files": [
                                "filesentinel/scanner.py",
                                "tests/test_scanner.py",
                            ],
                        }
                    ],
                },
            }

            created = docuagent.create_scaffold(root, state)

            self.assertIn(str(Path("filesentinel") / "__init__.py"), created)
            self.assertTrue((root / "filesentinel" / "main.py").is_file())
            self.assertFalse((root / "src" / "filesentinel").exists())
            self.assertIn(
                '$env:PYTHONPATH = "."',
                (root / "README.md").read_text(encoding="utf-8"),
            )
            self.assertIn(
                'requires-python = ">=3.12"',
                (root / "pyproject.toml").read_text(encoding="utf-8"),
            )

    def test_python_scaffold_uses_confirmed_newer_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "sample-tool"
            state = {
                "project": {
                    "mode": "new",
                    "slug": "sample-tool",
                    "name": "Sample Tool",
                },
                "answers": {},
                "architecture": {
                    "summary": "A sample tool.",
                    "runtime": "CPython / Python 3.13",
                    "stack": ["Python"],
                    "modules": [
                        {
                            "path": "src/sample_tool/core.py",
                            "target_files": ["src/sample_tool/core.py"],
                        }
                    ],
                },
            }

            docuagent.create_scaffold(root, state)

            self.assertTrue((root / "src" / "sample_tool" / "main.py").is_file())
            self.assertIn(
                'requires-python = ">=3.13"',
                (root / "pyproject.toml").read_text(encoding="utf-8"),
            )

    def test_bootstrap_resumes_from_managed_state(self) -> None:
        interviewing = docuagent.validate_architecture_model_result(
            self.valid_model_result(ready=False),
            {},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "resume"
            with patch(
                "docuagent.call_architecture_model",
                return_value=interviewing,
            ) as model_call:
                first = docuagent.start_bootstrap({
                    "path": str(root),
                    "name": "Resume",
                    "description": "A resumable project.",
                    "provider": self.TEST_PROVIDER,
                })
                resumed = docuagent.start_bootstrap({
                    "path": str(root),
                    "name": "Ignored",
                    "description": "Ignored",
                    "provider": self.TEST_PROVIDER,
                })

            self.assertEqual(first["project"]["name"], resumed["project"]["name"])
            self.assertEqual(
                first["current_question"]["id"], resumed["current_question"]["id"]
            )
            # Profile-first resume reads persisted state and makes no model call.
            self.assertEqual(0, model_call.call_count)

    def test_folder_picker_returns_workspace_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "picked"
            root.mkdir()
            (root / "main.py").write_text("print('existing')\n", encoding="utf-8")

            with patch("docuagent.ask_directory", return_value=str(root)):
                result = docuagent.choose_workspace({"initial_path": ""})

            self.assertFalse(result["cancelled"])
            self.assertEqual(str(root.resolve()), result["workspace"]["path"])
            self.assertEqual("imported", result["workspace"]["mode"])

    def test_folder_picker_cancel_does_not_select_workspace(self) -> None:
        with patch("docuagent.ask_directory", return_value=""):
            result = docuagent.choose_workspace({"initial_path": ""})

        self.assertTrue(result["cancelled"])
        self.assertNotIn("workspace", result)

    def test_valid_architecture_model_result_is_normalized(self) -> None:
        result = docuagent.validate_architecture_model_result(
            self.valid_model_result(),
            {},
        )

        self.assertTrue(result["ready"])
        self.assertIsNone(result["next_question"])
        self.assertEqual(["workspace"], result["architecture"]["modules"][1]["depends_on"])

    def test_ready_requires_at_least_three_modules(self) -> None:
        result = self.valid_model_result()
        result["architecture"]["modules"] = result["architecture"]["modules"][:2]

        with self.assertRaisesRegex(docuagent.WorkspaceError, "至少需要 3 个模块"):
            docuagent.validate_architecture_model_result(result, {})

    def test_more_than_eight_modules_are_rejected(self) -> None:
        result = self.valid_model_result(ready=False)
        result["architecture"]["modules"] = [
            {
                "id": f"m{index}",
                "name": f"Module {index}",
                "responsibility": f"Responsibility {index}.",
                "path": f"src/m{index}",
                "depends_on": [],
            }
            for index in range(9)
        ]

        with self.assertRaisesRegex(docuagent.WorkspaceError, "最多 8 个模块"):
            docuagent.validate_architecture_model_result(result, {})

    def test_ready_edge_without_reason_is_rejected(self) -> None:
        result = self.valid_model_result()
        result["architecture"]["edges"] = [
            {
                "from": "workspace",
                "to": "verification",
                "kind": "uses",
                "label": "读取验证结果",
            }
        ]

        with self.assertRaisesRegex(docuagent.WorkspaceError, "连线缺少原因"):
            docuagent.validate_architecture_model_result(result, {})

    def test_duplicate_module_ids_are_rejected(self) -> None:
        result = self.valid_model_result()
        duplicate = dict(result["architecture"]["modules"][0])
        duplicate["name"] = "Duplicate"
        result["architecture"]["modules"].append(duplicate)

        with self.assertRaisesRegex(docuagent.WorkspaceError, "重复模块 ID"):
            docuagent.validate_architecture_model_result(result, {})

    def test_unknown_module_dependency_is_rejected(self) -> None:
        result = self.valid_model_result()
        result["architecture"]["modules"][1]["depends_on"] = ["missing"]

        with self.assertRaisesRegex(docuagent.WorkspaceError, "依赖未知模块"):
            docuagent.validate_architecture_model_result(result, {})

    def test_absolute_module_path_is_rejected(self) -> None:
        result = self.valid_model_result()
        result["architecture"]["modules"][0]["path"] = "/outside/project"

        with self.assertRaisesRegex(docuagent.WorkspaceError, "不安全的模块路径"):
            docuagent.validate_architecture_model_result(result, {})

    def test_cyclic_module_dependency_is_broken_deterministically(self) -> None:
        result = self.valid_model_result()
        result["architecture"]["modules"][0]["depends_on"] = ["workbench"]

        normalized = docuagent.validate_architecture_model_result(result, {})
        edges = normalized["architecture"]["edges"]
        self.assertEqual(1, len(edges))
        self.assertIsNone(
            docuagent.find_edge_cycle(
                docuagent.build_order_graph(
                    {module["id"] for module in normalized["architecture"]["modules"]},
                    edges,
                )
            )
        )

    def test_ready_must_be_boolean(self) -> None:
        result = self.valid_model_result()
        result["ready"] = "true"

        with self.assertRaisesRegex(docuagent.WorkspaceError, "必须是布尔值"):
            docuagent.validate_architecture_model_result(result, {})

    def test_provider_connection_uses_structured_model_call(self) -> None:
        with patch(
            "docuagent.call_model_json",
            return_value={"ok": True, "capability": "structured JSON"},
        ) as model_call:
            result = docuagent.test_provider_connection(
                {
                    "provider": {
                        "enabled": True,
                        "base_url": "https://example.test/v1",
                        "model": "test-model",
                        "api_key": "secret",
                    }
                }
            )

        self.assertTrue(result["connected"])
        self.assertEqual("test-model", result["model"])
        self.assertEqual(1, model_call.call_count)
        config = model_call.call_args.args[0]
        self.assertEqual(
            "https://example.test/v1/chat/completions",
            config.endpoint,
        )
        self.assertIs(True, model_call.call_args.kwargs["json_mode"])
        self.assertEqual(0, model_call.call_args.kwargs["temperature"])

    def test_provider_reachable_normalizes_chat_completions_base_url(self) -> None:
        with patch("main.fetch_provider_models", return_value={"data": []}) as fetch:
            docuagent.test_provider_reachable(
                {
                    "provider": {
                        "enabled": True,
                        "base_url": "https://example.test/v1/chat/completions",
                        "model": "test-model",
                        "api_key": "secret",
                    }
                }
            )
        fetch.assert_called_once_with("https://example.test/v1", "secret")

    def test_provider_reachable_allows_local_without_key(self) -> None:
        with patch("main.fetch_provider_models", return_value={"data": []}) as fetch:
            docuagent.test_provider_reachable(
                {
                    "provider": {
                        "enabled": True,
                        "base_url": "http://localhost:11434/v1",
                        "model": "local-model",
                        "api_key": "",
                    }
                }
            )
        fetch.assert_called_once_with("http://localhost:11434/v1", "")

    def test_provider_probe_reuses_saved_key_when_requested(self) -> None:
        with patch("main.read_global_config", return_value={"api_key": "saved-secret"}), patch(
            "main.fetch_provider_models", return_value=[]
        ) as fetch:
            docuagent.test_provider_reachable(
                {
                    "provider": {
                        "enabled": True,
                        "base_url": "https://example.test/v1",
                        "model": "test-model",
                        "api_key": "",
                        "use_saved_key": True,
                    }
                }
            )
        fetch.assert_called_once_with("https://example.test/v1", "saved-secret")

    def test_provider_connection_allows_local_without_key(self) -> None:
        with patch(
            "docuagent.call_model_json",
            return_value={"ok": True, "capability": "local structured"},
        ) as model_call:
            result = docuagent.test_provider_connection(
                {
                    "provider": {
                        "enabled": True,
                        "base_url": "http://127.0.0.1:11434/v1",
                        "model": "local-model",
                        "api_key": "",
                    }
                }
            )
        self.assertTrue(result["connected"])
        self.assertEqual("", model_call.call_args.args[0].api_key)
        self.assertIs(True, model_call.call_args.kwargs["json_mode"])
        self.assertEqual(0, model_call.call_args.kwargs["temperature"])

    def test_confirm_bootstrap_opens_finalization_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "confirm"
            review = self.answer_all_questions(root)
            confirmed = docuagent.confirm_bootstrap({"path": str(root)})

            self.assertEqual("review", review["status"])
            self.assertEqual("ready", confirmed["status"])
            self.assertIsNotNone(confirmed["confirmed_at"])

    def test_architecture_revision_uses_model_and_returns_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "revise"
            self.answer_all_questions(root)
            provider = {
                "enabled": True,
                "base_url": "https://example.test/v1",
                "model": "test-model",
                "api_key": "secret",
            }
            normalized = docuagent.validate_architecture_model_result(
                self.valid_model_result(),
                {},
            )

            with patch(
                "docuagent.call_architecture_model",
                return_value=normalized,
            ) as model_call:
                revised = docuagent.revise_bootstrap(
                    {
                        "path": str(root),
                        "feedback": "Make the workbench depend on the workspace module.",
                        "provider": provider,
                    }
                )

            self.assertEqual("review", revised["status"])
            self.assertEqual("ai", revised["agent_mode"])
            self.assertEqual("test-model", revised["model_name"])
            self.assertEqual(1, model_call.call_count)
            persisted = (root / ".docuagent" / "bootstrap.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("secret", persisted)


    def test_inferred_claim_blocks_generation_until_resolved(self) -> None:
        """The gate moved from the answer path to the confirmation gate.

        Raising while validating the model's reply made the product report a rule
        as "AI 模型调用失败，请重试" — the user goes looking at their API key for
        something retrying cannot fix (depcheck-lite pilot). The architecture now
        reaches review, and confirmation refuses until the claim is settled.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "claims"
            state = docuagent.start_bootstrap({"path": str(root), "name": "Claims", "description": "x", "provider": self.TEST_PROVIDER})
            result = {"architecture": self.ready_architecture(), "ready": True, "next_question": None, "provenance": [{"id": "users", "text": "用户熟悉 Python", "source": "inferred"}]}
            # Patched at `bootstrap.call_model_json` — the name bootstrap actually
            # resolves — so the real validation still runs. Patching
            # `call_architecture_model` instead would skip validation, and validation
            # is where provenance gets attached to the architecture.
            # Guided mode asks its fixed topics one per turn before it accepts `ready`,
            # so the same reply is replayed until the draft is complete.
            with patch("bootstrap.call_model_json", return_value=result):
                for _ in range(12):
                    state = docuagent.answer_bootstrap(
                        {"path": str(root), "answer": "profile", "provider": self.TEST_PROVIDER}
                    )
                    if state["status"] == "review":
                        break
            self.assertEqual("review", state["status"])

            confirm = docuagent.POST_ROUTES["/api/bootstrap/confirm"]
            with self.assertRaisesRegex(docuagent.WorkspaceError, "AI 推测未确认"):
                confirm({"path": str(root)})

            docuagent.POST_ROUTES["/api/provenance/update"]({"path": str(root), "claim_id": "users", "action": "accept"})
            self.assertEqual("ready", confirm({"path": str(root)})["status"])

    def test_mode_offer_is_persisted_and_confirmation_switches_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "mode-offer"
            state = docuagent.start_bootstrap({"path": str(root), "name": "Mode Offer", "description": "x", "provider": self.TEST_PROVIDER})
            for index in range(5):
                payload = {"path": str(root), "answer": f"profile-{index}", "provider": self.TEST_PROVIDER}
                if index == 4:
                    ready = docuagent.validate_architecture_model_result({"architecture": self.ready_architecture(), "ready": True, "next_question": None, "mode_offer": {"target_mode": "professional", "label": "是否让我之后直接给技术方案？", "confirm_label": "直接给技术方案"}}, {})
                    with patch("docuagent.call_architecture_model", return_value=ready):
                        state = docuagent.answer_bootstrap(payload)
                else:
                    state = docuagent.answer_bootstrap(payload)
            self.assertEqual("professional", state["mode_offer"]["target_mode"])
            switched = docuagent.POST_ROUTES["/api/bootstrap/mode"]({"path": str(root), "interview_mode": "professional"})
            self.assertEqual("professional", switched["interview_mode"])
            self.assertIsNone(switched["mode_offer"])
            persisted = docuagent.read_json(root / ".docuagent" / "bootstrap.json")
            self.assertEqual("professional", persisted["interview_mode"])


class NoOfflineModeTest(unittest.TestCase):
    """A configured model is required; there is no deterministic interview.

    These pin a product decision rather than an implementation detail. Reintroducing a
    fallback would make DocuAgent answer with an architecture the user never designed,
    which reads as agent reasoning but is locally invented text.
    """

    def test_start_requires_a_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(docuagent.WorkspaceError, "需要配置 AI 模型"):
                docuagent.start_bootstrap(
                    {"path": temp_dir, "name": "No Key", "description": "x"}
                )

    def test_start_writes_nothing_without_a_provider(self) -> None:
        # The check precedes state construction, so a rejected start must not leave a
        # half-initialized project behind for the next attempt to resume from.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "untouched"
            with self.assertRaises(docuagent.WorkspaceError):
                docuagent.start_bootstrap(
                    {"path": str(root), "name": "No Key", "description": "x"}
                )
            self.assertFalse((root / ".docuagent" / "bootstrap.json").exists())

    def test_answer_requires_a_provider(self) -> None:
        interviewing = docuagent.validate_architecture_model_result(
            BootstrapTest().valid_model_result(ready=False),
            {},
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "midway"
            with patch("docuagent.call_architecture_model", return_value=interviewing):
                docuagent.start_bootstrap({
                    "path": str(root),
                    "name": "Midway",
                    "description": "x",
                    "provider": BootstrapTest.TEST_PROVIDER,
                })
            state = docuagent.answer_bootstrap({"path": str(root), "answer": "A real answer."})
            self.assertEqual("coding_experience", state["current_question"]["id"])

    def test_profile_answers_do_not_call_model_before_completion(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "profile-gate"
            state = docuagent.start_bootstrap({"path": str(root), "name": "Gate", "description": "x", "provider": BootstrapTest.TEST_PROVIDER})
            for index in range(4):
                with patch("docuagent.call_architecture_model") as model_call:
                    state = docuagent.answer_bootstrap({"path": str(root), "answer": f"fact-{index}", "provider": BootstrapTest.TEST_PROVIDER})
                self.assertEqual(0, model_call.call_count)
            self.assertEqual("constraints", state["current_question"]["id"])

    def test_legacy_bootstrap_without_profile_fields_is_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "legacy"
            managed = root / ".docuagent"
            managed.mkdir(parents=True)
            docuagent.atomic_write_json(managed / "bootstrap.json", {"status": "interviewing", "project": {"root": str(root)}, "answers": {}, "current_question": {"id": "goal", "title": "Goal", "prompt": "Goal?"}, "graph_quality_issues": ["图密度偏高"]})
            state = docuagent.public_state(docuagent.read_json(managed / "bootstrap.json"))
            self.assertEqual({}, state["user_profile"])
            self.assertEqual("guided", state["interview_mode"])
            self.assertEqual(0, state["profile_progress"])
            self.assertEqual(["图密度偏高"], state["graph_quality_issues"])

    def test_the_fallback_interview_is_gone(self) -> None:
        for name in (
            "QUESTIONS",
            "question_for",
            "advance_fallback",
            "build_architecture",
        ):
            self.assertFalse(
                hasattr(docuagent, name),
                f"{name} came back; see the module docstring in bootstrap.py",
            )

    def test_topic_coverage_is_advisory_only(self) -> None:
        # The topics exist for the progress display. They must not gate readiness —
        # that is the model's `ready` flag plus architecture_readiness_issues.
        self.assertEqual(9, len(docuagent.INTERVIEW_TOPICS))
        self.assertEqual(9, len(docuagent.uncovered_topics({})))
        self.assertEqual([], docuagent.uncovered_topics(
            {topic["id"]: "answered" for topic in docuagent.INTERVIEW_TOPICS}
        ))


class ConversationNodeTest(unittest.TestCase):
    """The conversation renders as a graph node but is not a module.

    It is projected in by the frontend (`graph/conversationNode.ts`), never by
    `normalize_architecture`: the architecture dict is echoed back to the model as
    `current_architecture` every turn, so a synthetic module there would read as one the
    agent had designed and would be assigned a path and dependencies.

    The backend's only job is to let its id survive layout-state persistence.
    """

    def test_architecture_never_contains_the_conversation(self) -> None:
        architecture = docuagent.normalize_architecture({
            "summary": "s", "platform": "Windows", "language": "Python",
            "runtime": "3.12", "frameworks": [], "stack": ["Python"],
            "modules": [{"id": "core", "name": "Core", "responsibility": "r",
                         "path": "src/core", "depends_on": []}],
            "data": [], "integrations": [], "constraints": ["c"],
            "verification": ["v"], "risks": [], "unresolved": [],
        })
        ids = [module["id"] for module in architecture["modules"]]
        self.assertEqual(["core"], ids)
        for edge in architecture["edges"]:
            self.assertNotEqual(docuagent.CONVERSATION_NODE_ID, edge["from"])
            self.assertNotEqual(docuagent.CONVERSATION_NODE_ID, edge["to"])

    def test_reserved_id_survives_slugification(self) -> None:
        # `optional_slug` strips leading/trailing separators, which would turn
        # `__conversation__` into `conversation` and break the key the canvas uses.
        self.assertEqual(
            "conversation", docuagent.optional_slug(docuagent.CONVERSATION_NODE_ID)
        )
        self.assertEqual(
            docuagent.CONVERSATION_NODE_ID,
            docuagent.normalize_node_id(docuagent.CONVERSATION_NODE_ID),
        )
        # Ordinary ids still get slugified.
        self.assertEqual("my-mod", docuagent.normalize_node_id("  My Mod  "))

    def test_conversation_layout_state_round_trips(self) -> None:
        state = docuagent.normalize_ui_state({
            "nodes": {
                docuagent.CONVERSATION_NODE_ID: {"x": 5, "y": 6, "pinned": True},
                "core": {"x": 1, "y": 2, "pinned": False},
            },
            "window_bar": [docuagent.CONVERSATION_NODE_ID, "core"],
            "outline_expanded": [docuagent.CONVERSATION_NODE_ID],
        })
        self.assertIn(docuagent.CONVERSATION_NODE_ID, state["nodes"])
        self.assertEqual(5.0, state["nodes"][docuagent.CONVERSATION_NODE_ID]["x"])
        self.assertIn(docuagent.CONVERSATION_NODE_ID, state["window_bar"])
        self.assertIn(docuagent.CONVERSATION_NODE_ID, state["outline_expanded"])

    def test_pruning_keeps_the_conversation_but_drops_dead_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            (root / ".docuagent").mkdir(parents=True)
            docuagent.write_ui_state(root, {
                "nodes": {
                    docuagent.CONVERSATION_NODE_ID: {"x": 5, "y": 6, "pinned": True},
                    "alive": {"x": 1, "y": 2, "pinned": True},
                    "deleted": {"x": 3, "y": 4, "pinned": True},
                },
                "window_bar": [docuagent.CONVERSATION_NODE_ID, "alive", "deleted"],
                "outline_expanded": [docuagent.CONVERSATION_NODE_ID, "deleted"],
            })

            pruned = docuagent.prune_ui_state(root, {"alive"})

            # The conversation is never in module_ids, so a plain filter would lose it.
            self.assertIn(docuagent.CONVERSATION_NODE_ID, pruned["nodes"])
            self.assertIn("alive", pruned["nodes"])
            self.assertNotIn("deleted", pruned["nodes"])
            self.assertEqual(
                [docuagent.CONVERSATION_NODE_ID, "alive"], pruned["window_bar"]
            )
            self.assertEqual(
                [docuagent.CONVERSATION_NODE_ID], pruned["outline_expanded"]
            )

    def test_finalize_does_not_write_the_conversation_into_architecture(self) -> None:
        architecture = docuagent.normalize_architecture({
            "summary": "s", "platform": "Windows", "language": "Python",
            "runtime": "3.12", "frameworks": [], "stack": ["Python"],
            "modules": [{"id": "core", "name": "Core", "responsibility": "r",
                         "path": "src/core", "depends_on": []}],
            "data": [], "integrations": [], "constraints": ["c"],
            "verification": ["v"], "risks": [], "unresolved": [],
        })
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "final"
            root.mkdir(parents=True)
            docuagent.atomic_write_json(
                docuagent.managed_path(root, "bootstrap.json"),
                {
                    "schema_version": 1, "status": "ready",
                    "project": {"name": "F", "slug": "f", "root": str(root),
                                "mode": "new"},
                    "answers": {"goal": "g"}, "architecture": architecture,
                    "current_question": None, "progress": 100,
                    "created_at": "T", "updated_at": "T", "confirmed_at": "T",
                    "model_notice": "", "agent_mode": "ai", "model_name": "m",
                    "agent_turns": 1,
                },
            )
            with patch("tasks.call_model_json", side_effect=document_model_result):
                docuagent.finalize_bootstrap({
                    "path": str(root), "provider": BootstrapTest.TEST_PROVIDER,
                })

            written = json.loads(
                (root / ".docuagent" / "architecture.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                docuagent.CONVERSATION_NODE_ID,
                [module["id"] for module in written["modules"]],
            )


class ConversationPersistenceTest(unittest.TestCase):
    def test_read_drops_synthetic_restore_lines_and_consecutive_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            (root / ".docuagent").mkdir(parents=True)
            docuagent.write_conversation_history(root, [
                {"role": "assistant", "content": "DocuAgent: 我们的项目地址是？"},
                {"role": "assistant", "content": "DocuAgent: 我们的项目地址是？"},
                {"role": "assistant", "content": "已恢复 3 条对话记录。"},
                {"role": "assistant", "content": "这是一个已有项目：demo。"},
                {"role": "user", "content": "做一个工具"},
            ])

            history = docuagent.read_conversation_history(root)

            self.assertEqual(
                [
                    {"role": "assistant", "content": "DocuAgent: 我们的项目地址是？"},
                    {"role": "assistant", "content": "这是一个已有项目：demo。"},
                    {"role": "user", "content": "做一个工具"},
                ],
                history,
            )

    def test_normalize_keeps_distinct_repeated_phrases(self) -> None:
        messages = docuagent.normalize_conversation_messages([
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "好的。"},
            {"role": "user", "content": "继续"},
        ])

        self.assertEqual(3, len(messages))


class EdgeSemanticsTest(unittest.TestCase):
    def architecture_with_edges(self, edges: list[dict]) -> dict:
        return {
            "summary": "Graph schema fixture.",
            "platform": "Windows",
            "language": "Python",
            "runtime": "Python 3.12",
            "stack": ["Python 3.12"],
            "constraints": ["No chat history"],
            "verification": ["Run unit tests"],
            "modules": [
                {
                    "id": "alpha",
                    "name": "Alpha",
                    "responsibility": "First module.",
                    "path": "src/alpha",
                    "depends_on": [],
                },
                {
                    "id": "beta",
                    "name": "Beta",
                    "responsibility": "Second module.",
                    "path": "src/beta",
                    "depends_on": [],
                },
            ],
            "edges": edges,
        }

    def test_event_edges_may_form_a_cycle(self) -> None:
        architecture = docuagent.normalize_architecture(
            self.architecture_with_edges(
                [
                    {"from": "alpha", "to": "beta", "kind": "event"},
                    {"from": "beta", "to": "alpha", "kind": "event"},
                ]
            )
        )

        self.assertEqual(2, len(architecture["edges"]))
        self.assertTrue(all(edge["kind"] == "event" for edge in architecture["edges"]))

    def test_self_dependency_is_dropped_not_rejected(self) -> None:
        raw = self.architecture_with_edges(
            [{"from": "alpha", "to": "alpha", "kind": "uses"}]
        )
        raw["modules"][0]["depends_on"] = ["alpha"]

        architecture = docuagent.normalize_architecture(raw)

        self.assertEqual([], architecture["modules"][0]["depends_on"])
        self.assertEqual([], architecture["edges"])

    def test_build_order_edge_cycles_are_broken_deterministically(self) -> None:
        for kind in ("uses", "data", "extends", "blocks"):
            with self.subTest(kind=kind):
                architecture = docuagent.normalize_architecture(
                    self.architecture_with_edges(
                        [
                            {"from": "alpha", "to": "beta", "kind": kind},
                            {"from": "beta", "to": "alpha", "kind": kind},
                        ]
                    )
                )
                self.assertEqual(1, len(architecture["edges"]))
                self.assertIsNone(
                    docuagent.find_edge_cycle(
                        docuagent.build_order_graph(
                            {"alpha", "beta"},
                            architecture["edges"],
                        )
                    )
                )

    def test_mixed_cycle_through_event_edge_is_allowed(self) -> None:
        architecture = docuagent.normalize_architecture(
            self.architecture_with_edges(
                [
                    {"from": "alpha", "to": "beta", "kind": "uses"},
                    {"from": "beta", "to": "alpha", "kind": "event"},
                ]
            )
        )

        self.assertEqual(2, len(architecture["edges"]))

    def test_unknown_edge_type_degrades_to_uses(self) -> None:
        self.assertEqual("uses", docuagent.normalize_edge_type("loopback JSON API"))
        self.assertEqual("uses", docuagent.normalize_edge_type("in-process call"))
        self.assertEqual("data", docuagent.normalize_edge_type("Shared Database"))
        self.assertEqual("event", docuagent.normalize_edge_type("pubsub"))
        self.assertEqual("uses", docuagent.normalize_edge_type(""))
        self.assertEqual("uses", docuagent.normalize_edge_type(None))

    def test_edge_referencing_unknown_module_is_rejected(self) -> None:
        with self.assertRaisesRegex(docuagent.WorkspaceError, "引用未知模块"):
            docuagent.normalize_architecture(
                self.architecture_with_edges(
                    [{"from": "alpha", "to": "missing", "kind": "uses"}]
                )
            )

    def test_edge_missing_endpoint_is_rejected(self) -> None:
        with self.assertRaisesRegex(docuagent.WorkspaceError, "缺少 from 或 to"):
            docuagent.normalize_architecture(
                self.architecture_with_edges([{"from": "alpha", "kind": "uses"}])
            )

    def test_legacy_depends_on_becomes_uses_edges(self) -> None:
        architecture = self.architecture_with_edges([])
        del architecture["edges"]
        architecture["modules"][1]["depends_on"] = ["alpha"]

        normalized = docuagent.normalize_architecture(architecture)

        self.assertEqual(
            [{"from": "beta", "to": "alpha", "kind": "uses", "label": "", "reason": "模块 beta 声明依赖模块 alpha。", "accepted": False}],
            normalized["edges"],
        )

    def test_brief_is_one_sentence_and_capped(self) -> None:
        long_text = "第一句话。" + "补充说明" * 80
        brief = docuagent.normalize_brief(long_text)

        self.assertEqual("第一句话。", brief)
        self.assertLessEqual(len(docuagent.normalize_brief("补充说明" * 80)), docuagent.BRIEF_MAX_CHARS)

    def test_brief_falls_back_to_responsibility(self) -> None:
        architecture = docuagent.normalize_architecture(self.architecture_with_edges([]))

        self.assertEqual("First module.", architecture["modules"][0]["brief"])

    def test_needs_ui_must_be_boolean(self) -> None:
        architecture = self.architecture_with_edges([])
        architecture["modules"][0]["needs_ui"] = "yes"

        with self.assertRaisesRegex(docuagent.WorkspaceError, "必须是布尔值"):
            docuagent.normalize_architecture(architecture)

    def test_group_membership_is_validated(self) -> None:
        architecture = self.architecture_with_edges([])
        architecture["modules"][0]["group"] = "fastapi"

        with self.assertRaisesRegex(docuagent.WorkspaceError, "引用未知分组"):
            docuagent.normalize_architecture(architecture)

        architecture["groups"] = [
            {"id": "fastapi", "label": "FastAPI", "kind": "framework", "members": ["alpha"]}
        ]
        normalized = docuagent.normalize_architecture(architecture)

        self.assertEqual("fastapi", normalized["modules"][0]["group"])
        self.assertEqual(["alpha"], normalized["groups"][0]["members"])

    def test_absent_group_stays_absent(self) -> None:
        architecture = docuagent.normalize_architecture(self.architecture_with_edges([]))

        self.assertIsNone(architecture["modules"][0]["group"])


class EngineeringGraphTest(unittest.TestCase):
    def modules(self) -> list[dict]:
        return [
            {"id": "index-html", "name": "Index HTML", "path": "index.html"},
            {"id": "main", "name": "Main", "path": "src/main.js"},
            {"id": "snake", "name": "Snake", "path": "src/snake.js"},
            {"id": "renderer", "name": "Renderer", "path": "src/renderer.js"},
        ]

    def test_simplify_removes_entry_load_fanout_behind_composer(self) -> None:
        edges = [
            {"from": "index-html", "to": "main", "kind": "uses", "label": "启动应用"},
            {"from": "index-html", "to": "snake", "kind": "uses", "label": "按顺序加载脚本"},
            {"from": "index-html", "to": "renderer", "kind": "blocks", "label": "按顺序加载脚本"},
            {"from": "main", "to": "snake", "kind": "uses", "label": "创建蛇实例"},
            {"from": "main", "to": "renderer", "kind": "uses", "label": "创建渲染器"},
            {"from": "renderer", "to": "snake", "kind": "uses", "label": "绘制蛇"},
        ]

        simplified = docuagent.simplify_architecture_edges(self.modules(), edges)

        pairs = {(edge["from"], edge["to"]) for edge in simplified}
        self.assertIn(("index-html", "main"), pairs)
        self.assertNotIn(("index-html", "snake"), pairs)
        self.assertNotIn(("index-html", "renderer"), pairs)
        self.assertIn(("main", "snake"), pairs)
        self.assertIn(("renderer", "snake"), pairs)

    def test_simplify_keeps_direct_entry_binding_without_composer_path(self) -> None:
        edges = [
            {"from": "index-html", "to": "renderer", "kind": "uses", "label": "获取 canvas DOM"},
            {"from": "renderer", "to": "snake", "kind": "uses", "label": "绘制蛇"},
        ]

        simplified = docuagent.simplify_architecture_edges(self.modules(), edges)

        self.assertIn(("index-html", "renderer"), {(e["from"], e["to"]) for e in simplified})

    def test_simplify_keeps_one_edge_per_pair_with_most_informative_kind(self) -> None:
        edges = [
            {"from": "main", "to": "snake", "kind": "blocks", "label": "load order"},
            {"from": "main", "to": "snake", "kind": "uses", "label": "create snake"},
        ]

        simplified = docuagent.simplify_architecture_edges(self.modules(), edges)

        self.assertEqual(1, len(simplified))
        self.assertEqual("uses", simplified[0]["kind"])

    def test_architecture_validation_simplifies_redundant_entry_edges(self) -> None:
        raw = {
            "architecture": {
                "summary": "Small web game.",
                "platform": "Browser",
                "language": "JavaScript",
                "runtime": "Browser",
                "frameworks": [],
                "stack": ["JavaScript"],
                "modules": [
                    {"id": "index-html", "name": "Index", "responsibility": "DOM shell.", "path": "index.html", "depends_on": []},
                    {"id": "main", "name": "Main", "responsibility": "Wire modules.", "path": "src/main.js", "depends_on": []},
                    {"id": "snake", "name": "Snake", "responsibility": "Own snake body.", "path": "src/snake.js", "depends_on": []},
                ],
                "edges": [
                    {"from": "index-html", "to": "main", "kind": "uses", "label": "启动", "reason": "入口加载组合根。"},
                    {"from": "index-html", "to": "snake", "kind": "uses", "label": "加载脚本", "reason": "入口直接加载游戏模块。"},
                    {"from": "main", "to": "snake", "kind": "uses", "label": "创建蛇", "reason": "组合根创建游戏实例。"},
                ],
                "data": [],
                "integrations": [],
                "constraints": ["No frameworks"],
                "verification": ["Open index.html"],
                "risks": [],
                "unresolved": [],
            },
            "ready": True,
            "thinking": "ok",
            "next_question": None,
        }

        result = docuagent.validate_architecture_model_result(raw, {})

        pairs = {(edge["from"], edge["to"]) for edge in result["architecture"]["edges"]}
        self.assertNotIn(("index-html", "snake"), pairs)
        self.assertIn(("index-html", "main"), pairs)
        self.assertIn(("main", "snake"), pairs)
        self.assertIn("graph_quality_issues", result)

    def test_graph_quality_flags_entry_fanout_and_density(self) -> None:
        architecture = {
            "modules": [
                *self.modules(),
                {"id": "score", "name": "Score", "path": "src/score.js"},
            ],
            "edges": [
                {"from": "index-html", "to": "main", "kind": "uses"},
                {"from": "index-html", "to": "snake", "kind": "uses"},
                {"from": "index-html", "to": "renderer", "kind": "uses"},
                {"from": "index-html", "to": "score", "kind": "uses"},
                {"from": "main", "to": "snake", "kind": "uses"},
                {"from": "main", "to": "renderer", "kind": "uses"},
                {"from": "main", "to": "score", "kind": "uses"},
                {"from": "renderer", "to": "snake", "kind": "uses"},
                {"from": "renderer", "to": "score", "kind": "uses"},
                {"from": "game-state", "to": "score", "kind": "uses"},
                {"from": "game-state", "to": "snake", "kind": "uses"},
            ],
        }

        issues = docuagent.architecture_graph_quality_issues(architecture)

        self.assertTrue(any("入口模块" in issue for issue in issues))
        self.assertTrue(any("密度" in issue for issue in issues))


class UiStateTest(unittest.TestCase):
    def test_camera_scale_is_clamped(self) -> None:
        state = docuagent.normalize_ui_state({"camera": {"x": 5, "y": -3, "scale": 99}})

        self.assertEqual(docuagent.MAX_SCALE, state["camera"]["scale"])
        self.assertEqual(5.0, state["camera"]["x"])
        self.assertEqual(-3.0, state["camera"]["y"])

    def test_corrupt_ui_state_falls_back_to_defaults(self) -> None:
        state = docuagent.normalize_ui_state({"camera": "broken", "nodes": 7})

        self.assertEqual(1.0, state["camera"]["scale"])
        self.assertEqual({}, state["nodes"])

    def test_non_finite_camera_values_are_rejected(self) -> None:
        state = docuagent.normalize_ui_state(
            {"camera": {"x": float("inf"), "y": float("nan"), "scale": True}}
        )

        self.assertEqual(0.0, state["camera"]["x"])
        self.assertEqual(0.0, state["camera"]["y"])
        self.assertEqual(1.0, state["camera"]["scale"])

    def test_window_bar_is_deduplicated_and_capped(self) -> None:
        state = docuagent.normalize_ui_state(
            {"window_bar": [f"node-{index}" for index in range(20)] + ["node-0"]}
        )

        self.assertEqual(docuagent.WINDOW_BAR_SLOTS, len(state["window_bar"]))
        self.assertEqual(len(set(state["window_bar"])), len(state["window_bar"]))

    def test_ui_state_round_trips_through_disk(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "ui"
            (root / ".docuagent").mkdir(parents=True)
            docuagent.write_ui_state(
                root,
                {
                    "camera": {"x": 12.5, "y": -40.25, "scale": 1.4},
                    "nodes": {"alpha": {"x": 100, "y": 200, "pinned": True}},
                    "window_bar": ["alpha"],
                },
            )

            reloaded = docuagent.read_ui_state(root)

            self.assertEqual(1.4, reloaded["camera"]["scale"])
            self.assertEqual(12.5, reloaded["camera"]["x"])
            self.assertEqual(100.0, reloaded["nodes"]["alpha"]["x"])
            self.assertTrue(reloaded["nodes"]["alpha"]["pinned"])
            self.assertEqual(["alpha"], reloaded["window_bar"])

    def test_missing_ui_state_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            state = docuagent.read_ui_state(Path(temp_dir))

            self.assertEqual(docuagent.UI_STATE_VERSION, state["ui_state_version"])
            self.assertEqual({}, state["nodes"])

    def test_prune_drops_entries_for_removed_modules(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "prune"
            (root / ".docuagent").mkdir(parents=True)
            docuagent.write_ui_state(
                root,
                {
                    "nodes": {"alpha": {"x": 1, "y": 2}, "gone": {"x": 3, "y": 4}},
                    "window_bar": ["alpha", "gone"],
                    "outline_expanded": ["gone"],
                },
            )

            pruned = docuagent.prune_ui_state(root, {"alpha"})

            self.assertEqual(["alpha"], list(pruned["nodes"]))
            self.assertEqual(["alpha"], pruned["window_bar"])
            self.assertEqual([], pruned["outline_expanded"])

    def test_save_ui_state_requires_initialized_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(docuagent.WorkspaceError, "尚未开始初始化"):
                docuagent.save_ui_state({"path": temp_dir, "ui_state": {}})


class MigrationTest(unittest.TestCase):
    def legacy_architecture(self) -> dict:
        return {
            "schema_version": 1,
            "architecture_version": 1,
            "modules": [
                {"id": "orchestrator", "responsibility": "Own workflow state"},
                {"id": "workbench", "responsibility": "Present state"},
            ],
            "dependencies": [
                {"from": "workbench", "to": "orchestrator", "type": "loopback JSON API"},
                {"from": "orchestrator", "to": "workbench", "type": "in-process call"},
            ],
        }

    def test_legacy_dependencies_are_converted_to_typed_edges(self) -> None:
        migrated, changed = docuagent.migrate_architecture_shape(self.legacy_architecture())

        self.assertTrue(changed)
        self.assertEqual(2, len(migrated["edges"]))
        self.assertTrue(all(edge["kind"] == "uses" for edge in migrated["edges"]))
        self.assertEqual([], migrated["groups"])

    def test_migration_adds_graph_fields_to_modules(self) -> None:
        migrated, _ = docuagent.migrate_architecture_shape(self.legacy_architecture())

        module = migrated["modules"][0]
        self.assertEqual("Own workflow state", module["brief"])
        self.assertFalse(module["needs_ui"])
        self.assertIsNone(module["group"])
        self.assertEqual([], module["depends_on"])

    def test_migration_is_idempotent(self) -> None:
        once, first_changed = docuagent.migrate_architecture_shape(self.legacy_architecture())
        twice, second_changed = docuagent.migrate_architecture_shape(once)

        self.assertTrue(first_changed)
        self.assertFalse(second_changed)
        self.assertEqual(once, twice)

    def test_migrated_legacy_cycle_is_preserved_not_rejected(self) -> None:
        """Migration must not raise on legacy data that would now fail validation."""
        migrated, _ = docuagent.migrate_architecture_shape(self.legacy_architecture())

        self.assertEqual(2, len(migrated["edges"]))

    def test_migrate_managed_directory_creates_ui_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "legacy"
            managed = root / ".docuagent"
            managed.mkdir(parents=True)
            docuagent.atomic_write_json(
                managed / "architecture.json", self.legacy_architecture()
            )

            changed = docuagent.migrate_managed_directory(root)

            self.assertIn("architecture.json", changed)
            self.assertIn("ui-state.json", changed)
            self.assertTrue((managed / "ui-state.json").exists())
            self.assertEqual([], docuagent.migrate_managed_directory(root))

    def test_missing_module_name_is_derived_from_id(self) -> None:
        """Hand-authored files omit `name`; nodes would otherwise render blank."""
        migrated, changed = docuagent.migrate_architecture_shape(self.legacy_architecture())

        self.assertTrue(changed)
        self.assertEqual("Orchestrator", migrated["modules"][0]["name"])

    def test_inspect_reads_architecture_without_bootstrap(self) -> None:
        """A finalized or hand-authored project has no bootstrap.json, but the canvas
        must still render its graph."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "no-bootstrap"
            managed = root / ".docuagent"
            managed.mkdir(parents=True)
            docuagent.atomic_write_json(
                managed / "architecture.json", self.legacy_architecture()
            )

            result = docuagent.inspect_workspace(str(root))

            self.assertIsNone(result["bootstrap"])
            self.assertIsNotNone(result["architecture"])
            self.assertEqual(2, len(result["architecture"]["modules"]))
            self.assertEqual(2, len(result["architecture"]["edges"]))

    def test_inspect_prefers_live_bootstrap_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "both"
            managed = root / ".docuagent"
            managed.mkdir(parents=True)
            docuagent.atomic_write_json(
                managed / "architecture.json", self.legacy_architecture()
            )
            docuagent.atomic_write_json(
                managed / "bootstrap.json",
                {
                    "schema_version": 1,
                    "status": "review",
                    "project": {"name": "Live", "slug": "live", "root": str(root), "mode": "new"},
                    "answers": {},
                    "architecture": {
                        "modules": [
                            {
                                "id": "only",
                                "name": "Only",
                                "responsibility": "Sole module",
                                "depends_on": [],
                            }
                        ]
                    },
                },
            )

            result = docuagent.inspect_workspace(str(root))

            self.assertEqual(1, len(result["architecture"]["modules"]))
            self.assertEqual("only", result["architecture"]["modules"][0]["id"])
            self.assertEqual("Live", result["project_name"])

    def test_inspect_returns_no_architecture_for_empty_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            result = docuagent.inspect_workspace(temp_dir)

            self.assertIsNone(result["architecture"])
            self.assertIsNone(result["bootstrap"])

    def test_migration_skips_project_without_managed_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual([], docuagent.migrate_managed_directory(Path(temp_dir)))

    def test_dogfood_architecture_file_migrates(self) -> None:
        """The repository's own architecture.json is a real legacy case.

        Asserts the migrated shape, not the `changed` flag: this file may already have
        been migrated in place by a prior `inspect_workspace` call, and re-migrating it
        correctly reports no change.
        """
        source = Path(docuagent.__file__).resolve().parent / ".docuagent" / "architecture.json"
        if not source.exists():
            self.skipTest("dogfood architecture.json not present")
        payload = json.loads(source.read_text(encoding="utf-8"))

        migrated, _ = docuagent.migrate_architecture_shape(payload)

        self.assertEqual(2, len(migrated["edges"]))
        for edge in migrated["edges"]:
            self.assertIn(edge["kind"], docuagent.EDGE_TYPES)
        for module in migrated["modules"]:
            self.assertIn("brief", module)
            self.assertIn("needs_ui", module)


class ArchitectureEditTest(unittest.TestCase):
    """Editing an architecture that already exists.

    The gap this closes: `answer_bootstrap` returns early once status is `initialized`
    and `revise_bootstrap` only accepts `review`, so a generated project had no route
    that could change its architecture. Hand-authored projects had none either, having
    no bootstrap state at all.
    """

    PROVIDER = {
        "enabled": True,
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key": "secret",
    }

    def architecture(self, module_ids: list[str]) -> dict:
        return docuagent.normalize_architecture({
            "summary": "An editable architecture.",
            "platform": "Windows",
            "language": "Python",
            "runtime": "Python 3.12",
            "frameworks": [],
            "stack": ["Python 3.12"],
            "modules": [
                {
                    "id": module_id,
                    "name": module_id.title(),
                    "responsibility": f"{module_id} does one thing.",
                    "path": f"src/{module_id}",
                    "depends_on": [],
                }
                for module_id in module_ids
            ],
            "data": [],
            "integrations": [],
            "constraints": ["No chat history"],
            "verification": ["Run unit tests"],
            "risks": [],
            "unresolved": [],
        })

    def project_with_architecture(self, root: Path, module_ids: list[str]) -> None:
        """A finished project: architecture.json only, no live interview."""
        root.mkdir(parents=True, exist_ok=True)
        docuagent.atomic_write_json(
            docuagent.managed_path(root, "architecture.json"),
            {
                "schema_version": 1,
                "architecture_version": 3,
                "generated_at": "T",
                "project": {"name": "Edited", "slug": "edited", "root": str(root),
                            "mode": "new"},
                **self.architecture(module_ids),
            },
        )

    def edit_result(self, module_ids: list[str], changes: list[str]) -> dict:
        return docuagent.validate_architecture_edit_result({
            "architecture": self.architecture(module_ids),
            "thinking": "我把模块拆开了。",
            "changes": changes,
        })

    def test_edit_replaces_the_architecture_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core", "auth"], ["新增 auth 模块"]),
            ):
                result = docuagent.edit_architecture({
                    "path": str(root),
                    "request": "把认证拆成独立模块",
                    "provider": self.PROVIDER,
                })

            self.assertEqual(
                ["auth", "core"],
                sorted(module["id"] for module in result["architecture"]["modules"]),
            )
            written = json.loads(
                (root / ".docuagent" / "architecture.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                ["auth", "core"],
                sorted(module["id"] for module in written["modules"]),
            )

    def test_edit_increments_architecture_version(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core"], ["调整职责"]),
            ):
                result = docuagent.edit_architecture({
                    "path": str(root),
                    "request": "改一下职责描述",
                    "provider": self.PROVIDER,
                })

            self.assertEqual(4, result["architecture_version"])

    def test_edit_pushes_history_and_undo_restores(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core", "auth"], ["新增 auth 模块"]),
            ):
                docuagent.edit_architecture({
                    "path": str(root),
                    "request": "把认证拆成独立模块",
                    "provider": self.PROVIDER,
                })

            self.assertTrue(
                (root / ".docuagent" / "architecture-history.json").exists()
            )
            undone = docuagent.undo_architecture({"path": str(root)})

            written = json.loads(
                (root / ".docuagent" / "architecture.json").read_text(encoding="utf-8")
            )
            self.assertEqual(["core"], sorted(m["id"] for m in written["modules"]))
            self.assertEqual(3, undone["architecture_version"])
            self.assertEqual(0, undone["history_remaining"])

    def test_edit_marks_changed_modules_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core", "auth"], ["新增 auth 模块"]),
            ):
                docuagent.edit_architecture({
                    "path": str(root),
                    "request": "新增 auth",
                    "provider": self.PROVIDER,
                })

            stale = docuagent.read_stale_modules(root)
            self.assertIn("auth", stale)

    def test_undo_without_history_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with self.assertRaisesRegex(docuagent.WorkspaceError, "没有可以撤销"):
                docuagent.undo_architecture({"path": str(root)})

    def test_undo_keeps_bootstrap_in_step(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])
            docuagent.atomic_write_json(
                docuagent.managed_path(root, "bootstrap.json"),
                {
                    "schema_version": 1,
                    "status": "initialized",
                    "project": {"name": "Edited", "slug": "edited",
                                "root": str(root), "mode": "new"},
                    "answers": {"goal": "g"},
                    "architecture": self.architecture(["core"]),
                },
            )

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core", "auth"], ["新增 auth 模块"]),
            ):
                docuagent.edit_architecture({
                    "path": str(root),
                    "request": "把认证拆成独立模块",
                    "provider": self.PROVIDER,
                })
            docuagent.undo_architecture({"path": str(root)})

            bootstrap = json.loads(
                (root / ".docuagent" / "bootstrap.json").read_text(encoding="utf-8")
            )
            self.assertEqual(["core"], sorted(m["id"] for m in bootstrap["architecture"]["modules"]))

    def test_edit_works_on_a_finished_project(self) -> None:
        """The case the interview routes reject outright."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])
            docuagent.atomic_write_json(
                docuagent.managed_path(root, "bootstrap.json"),
                {
                    "schema_version": 1,
                    "status": "initialized",
                    "project": {"name": "Edited", "slug": "edited",
                                "root": str(root), "mode": "new"},
                    "answers": {"goal": "g"},
                    "architecture": self.architecture(["core"]),
                    "current_question": None,
                    "progress": 100,
                    "created_at": "T",
                    "updated_at": "T",
                    "model_notice": "",
                    "agent_mode": "ai",
                    "model_name": "m",
                    "agent_turns": 2,
                },
            )

            # The old routes refuse: this is exactly why editing needed its own.
            with self.assertRaises(docuagent.WorkspaceError):
                docuagent.revise_bootstrap({
                    "path": str(root), "feedback": "加一个模块",
                    "provider": self.PROVIDER,
                })

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core", "export"], ["新增导出"]),
            ):
                result = docuagent.edit_architecture({
                    "path": str(root),
                    "request": "加一个导出功能",
                    "provider": self.PROVIDER,
                })

            self.assertIn(
                "export",
                [module["id"] for module in result["architecture"]["modules"]],
            )
            # bootstrap.json is kept in step, or reopening would show the old graph:
            # inspect_workspace prefers the interview draft over the document.
            state = json.loads(
                (root / ".docuagent" / "bootstrap.json").read_text(encoding="utf-8")
            )
            self.assertIn(
                "export",
                [module["id"] for module in state["architecture"]["modules"]],
            )

    def test_edit_survives_a_project_with_no_bootstrap_state(self) -> None:
        """Hand-authored projects have architecture.json and nothing else."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])
            self.assertFalse(
                (root / ".docuagent" / "bootstrap.json").exists()
            )

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core", "api"], ["新增 api"]),
            ):
                result = docuagent.edit_architecture({
                    "path": str(root),
                    "request": "加一个 api 模块",
                    "provider": self.PROVIDER,
                })

            self.assertEqual(2, len(result["architecture"]["modules"]))

    def test_edit_prunes_layout_for_removed_modules_but_keeps_survivors(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core", "legacy"])
            docuagent.write_ui_state(root, {
                "ui_state_version": 1,
                "camera": {"x": 0.0, "y": 0.0, "scale": 1.0},
                "nodes": {
                    "core": {"x": 700.0, "y": 120.0, "pinned": True},
                    "legacy": {"x": 40.0, "y": 40.0, "pinned": True},
                },
                "window_bar": [],
                "outline_expanded": [],
            })

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core"], ["删除 legacy"]),
            ):
                docuagent.edit_architecture({
                    "path": str(root),
                    "request": "删掉 legacy 模块",
                    "provider": self.PROVIDER,
                })

            ui_state = docuagent.read_ui_state(root)
            # A module that survived the edit keeps the position the user gave it.
            self.assertEqual(700.0, ui_state["nodes"]["core"]["x"])
            self.assertNotIn("legacy", ui_state["nodes"])

    def test_update_module_requirement_marks_dependents_stale(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["a", "b", "c"])
            architecture = self.architecture(["a", "b", "c"])
            architecture["edges"] = [
                {"id": "b-a", "from": "b", "to": "a", "kind": "uses"},
                {"id": "c-b", "from": "c", "to": "b", "kind": "uses"},
            ]
            docuagent.atomic_write_json(
                docuagent.managed_path(root, "architecture.json"),
                {
                    "schema_version": 1,
                    "architecture_version": 3,
                    "generated_at": "T",
                    "project": {"name": "Edited", "slug": "edited", "root": str(root), "mode": "new"},
                    **architecture,
                },
            )

            result = docuagent.update_module_requirement({
                "path": str(root),
                "module_id": "a",
                "requirement": "新的需求：a 负责核心计算。",
            })

            self.assertEqual(4, result["architecture_version"])
            self.assertEqual({"a", "b", "c"}, set(result["stale_modules"]))
            self.assertEqual(
                "新的需求：a 负责核心计算。",
                result["architecture"]["modules"][0]["brief"],
            )
            persisted = docuagent.read_json(
                docuagent.managed_path(root, "architecture.json")
            )
            self.assertEqual(
                "新的需求：a 负责核心计算。",
                persisted["modules"][0]["brief"],
            )

    def test_edit_requires_a_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with self.assertRaises(docuagent.WorkspaceError):
                docuagent.edit_architecture({
                    "path": str(root), "request": "改点东西",
                    "provider": {"enabled": False},
                })

    def test_edit_requires_an_existing_architecture(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "empty"
            root.mkdir(parents=True)

            with self.assertRaises(docuagent.WorkspaceError):
                docuagent.edit_architecture({
                    "path": str(root), "request": "改点东西",
                    "provider": self.PROVIDER,
                })

    def test_edit_requires_a_request(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with self.assertRaises(docuagent.WorkspaceError):
                docuagent.edit_architecture({
                    "path": str(root), "request": " ", "provider": self.PROVIDER,
                })

    def test_a_revision_that_breaks_readiness_is_rejected(self) -> None:
        """A revision must not drop the project below the bar that let it generate."""
        with self.assertRaises(docuagent.WorkspaceError):
            docuagent.validate_architecture_edit_result({
                "architecture": {
                    "summary": "",
                    "platform": "",
                    "language": "",
                    "runtime": "",
                    "frameworks": [],
                    "stack": [],
                    "modules": [],
                    "data": [],
                    "integrations": [],
                    "constraints": [],
                    "verification": [],
                    "risks": [],
                    "unresolved": [],
                },
                "changes": [],
            })

    def test_a_failed_edit_leaves_the_architecture_untouched(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])
            before = (root / ".docuagent" / "architecture.json").read_text(
                encoding="utf-8"
            )

            with patch(
                "docuagent.call_architecture_edit_model",
                side_effect=docuagent.WorkspaceError("模型返回值不是有效的 JSON 对象。"),
            ):
                with self.assertRaises(docuagent.WorkspaceError):
                    docuagent.edit_architecture({
                        "path": str(root), "request": "改点东西",
                        "provider": self.PROVIDER,
                    })

            self.assertEqual(
                before,
                (root / ".docuagent" / "architecture.json").read_text(encoding="utf-8"),
            )

    def test_edit_does_not_require_interview_fields(self) -> None:
        """An edit is not an interview turn: no `ready`, no `next_question`."""
        result = docuagent.validate_architecture_edit_result({
            "architecture": self.architecture(["core"]),
            "changes": ["调整"],
        })
        self.assertEqual(["调整"], result["changes"])
        self.assertNotIn("ready", result)
        self.assertNotIn("next_question", result)

    def test_edit_rejects_an_unsafe_module_path(self) -> None:
        with self.assertRaises(docuagent.WorkspaceError):
            docuagent.validate_architecture_edit_result({
                "architecture": {
                    **self.architecture(["core"]),
                    "modules": [{
                        "id": "core", "name": "Core", "responsibility": "r",
                        "path": "../../etc/passwd", "depends_on": [],
                    }],
                },
                "changes": [],
            })

    def test_edit_broken_cycle_still_passes(self) -> None:
        result = docuagent.validate_architecture_edit_result({
            "architecture": {
                **self.architecture(["a", "b"]),
                "modules": [
                    {"id": "a", "name": "A", "responsibility": "r",
                     "path": "src/a", "depends_on": ["b"]},
                    {"id": "b", "name": "B", "responsibility": "r",
                     "path": "src/b", "depends_on": ["a"]},
                ],
            },
            "changes": [],
        })
        self.assertEqual(1, len(result["architecture"]["edges"]))

    def test_edit_caps_the_change_list(self) -> None:
        result = docuagent.validate_architecture_edit_result({
            "architecture": self.architecture(["core"]),
            "changes": [f"change {index}" for index in range(40)],
        })
        self.assertEqual(12, len(result["changes"]))

    def test_edit_never_writes_the_conversation_into_architecture(self) -> None:
        """The conversation is a view concept; it must not reach architecture.json."""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self.project_with_architecture(root, ["core"])

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["core"], ["调整"]),
            ):
                docuagent.edit_architecture({
                    "path": str(root), "request": "改一下",
                    "provider": self.PROVIDER,
                })

            written = json.loads(
                (root / ".docuagent" / "architecture.json").read_text(encoding="utf-8")
            )
            self.assertNotIn(
                docuagent.CONVERSATION_NODE_ID,
                [module["id"] for module in written["modules"]],
            )

    def test_the_edit_route_is_registered(self) -> None:
        """Guards a handler that exists but is unreachable by a runtime-only failure."""
        self.assertIn("/api/architecture/edit", docuagent.POST_ROUTES)
        self.assertIn("/api/architecture/stream-edit", docuagent.STREAM_ROUTE_PATHS)


class CycleDetectionTest(unittest.TestCase):
    def test_deep_chain_does_not_overflow(self) -> None:
        count = 4000
        graph = {f"n{index}": [f"n{index + 1}"] for index in range(count)}
        graph[f"n{count}"] = []

        self.assertIsNone(docuagent.find_edge_cycle(graph))

    def test_cycle_path_is_reported(self) -> None:
        cycle = docuagent.find_edge_cycle({"a": ["b"], "b": ["c"], "c": ["a"]})

        self.assertIsNotNone(cycle)
        self.assertEqual(cycle[0], cycle[-1])

    def test_diamond_is_not_a_cycle(self) -> None:
        graph = {"a": ["b", "c"], "b": ["d"], "c": ["d"], "d": []}

        self.assertIsNone(docuagent.find_edge_cycle(graph))


class _FakeStreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def __iter__(self):
        return iter([line.encode("utf-8") for line in self.lines])


class _FakeJsonResponse:
    def __init__(self, content: str) -> None:
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        body = {"choices": [{"message": {"content": self.content}}]}
        return json.dumps(body, ensure_ascii=False).encode("utf-8")


class _FakeToolCallResponse:
    """SSE stream version of a chat completion with native tool calls."""

    def __init__(self, content: str, tool_calls: list[dict] | None = None) -> None:
        self.content = content
        self.tool_calls = tool_calls or []

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def __iter__(self):
        events = [{
            "choices": [{
                "delta": {
                    "content": self.content,
                    "tool_calls": self.tool_calls,
                },
                "finish_reason": None,
            }],
        }, {
            "choices": [{
                "delta": {},
                "finish_reason": "tool_calls" if self.tool_calls else "stop",
            }],
        }]
        lines = [
            f"data: {json.dumps(event, ensure_ascii=False)}".encode("utf-8")
            for event in events
        ]
        lines.append(b"data: [DONE]")
        return iter(lines)


class _FakeModelsResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None

    def read(self) -> bytes:
        return b'{"data": [{"id": "m1"}, {"id": "m2"}]}'


class StreamingModelTest(unittest.TestCase):
    def test_stream_architecture_model_yields_reasoning_and_done(self) -> None:
        model_result = BootstrapTest().valid_model_result(ready=False)
        content = json.dumps(model_result, ensure_ascii=False)
        lines = [
            'data: {"choices":[{"delta":{"reasoning_content":"我在思考模块边界。"}}]}',
            f'data: {{"choices":[{{"delta":{{"content":{json.dumps(content)}}}}}]}}',
            "data: [DONE]",
        ]
        provider = docuagent.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeStreamResponse(lines),
        ):
            events = list(
                docuagent.stream_architecture_model(
                    {
                        "project": {"name": "Demo"},
                        "answers": {},
                        "architecture": {},
                        "agent_turns": 0,
                    },
                    {"question_id": "start", "answer": "做一个工具"},
                    provider,
                )
            )

        self.assertEqual("reasoning", events[0]["type"])
        self.assertIn("模块边界", events[0]["text"])
        self.assertEqual("done", events[-1]["type"])
        self.assertTrue(events[-1]["result"]["architecture"]["modules"])


class ProxyFallbackTest(unittest.TestCase):
    def test_winerror_10013_retries_without_system_proxy(self) -> None:
        blocked = urllib.error.URLError(OSError(10013, "Permission denied"))
        response = object()
        opener = MagicMock()
        opener.open.return_value = response
        request = MagicMock()

        with patch(
            "urllib.request.urlopen",
            side_effect=[blocked],
        ), patch(
            "urllib.request.build_opener",
            return_value=opener,
        ) as build_opener:
            result = bootstrap.urlopen_with_proxy_fallback(request, 12)

        self.assertIs(response, result)
        build_opener.assert_called_once()
        proxy_handler = build_opener.call_args.args[0]
        self.assertIsInstance(proxy_handler, urllib.request.ProxyHandler)
        self.assertEqual({}, proxy_handler.proxies)
        opener.open.assert_called_once_with(request, timeout=12)

    def test_other_socket_errors_are_not_swallowed(self) -> None:
        refused = urllib.error.URLError(OSError(10061, "Connection refused"))

        with patch(
            "urllib.request.urlopen",
            side_effect=[refused],
        ), patch(
            "urllib.request.build_opener",
        ) as build_opener:
            with self.assertRaises(urllib.error.URLError):
                bootstrap.urlopen_with_proxy_fallback(MagicMock(), 5)

        build_opener.assert_not_called()

    def test_fetch_provider_models_uses_proxy_fallback(self) -> None:
        blocked = urllib.error.URLError(OSError(10013, "Permission denied"))
        opener = MagicMock()
        opener.open.return_value = _FakeModelsResponse()

        with patch(
            "urllib.request.urlopen",
            side_effect=[blocked],
        ), patch(
            "urllib.request.build_opener",
            return_value=opener,
        ):
            models = docuagent.fetch_provider_models(
                "https://example.test/v1",
                "secret",
            )

        self.assertEqual(["m1", "m2"], models)


class JsonModeTest(unittest.TestCase):
    def test_call_model_json_sends_json_mode_and_temperature(self) -> None:
        provider = docuagent.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeJsonResponse('{"ok":true,"capability":"structured JSON"}'),
        ) as urlopen:
            result = docuagent.call_model_json(
                provider,
                "Return JSON only.",
                {"instruction": "confirm"},
                json_mode=True,
                temperature=0,
            )

        self.assertTrue(result["ok"])
        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual({"type": "json_object"}, payload["response_format"])
        self.assertEqual(0, payload["temperature"])

    def test_call_model_json_drops_json_mode_on_http_400(self) -> None:
        provider = docuagent.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )
        rejected = urllib.error.HTTPError(
            "https://example.test/v1/chat/completions",
            400,
            "Bad Request",
            {},
            None,
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=[rejected, _FakeJsonResponse('{"ok":true}')],
        ) as urlopen:
            result = docuagent.call_model_json(
                provider,
                "Return JSON only.",
                {"instruction": "confirm"},
                json_mode=True,
                temperature=0,
            )

        self.assertTrue(result["ok"])
        first = json.loads(urlopen.call_args_list[0].args[0].data.decode("utf-8"))
        second = json.loads(urlopen.call_args_list[1].args[0].data.decode("utf-8"))
        self.assertIn("response_format", first)
        self.assertNotIn("response_format", second)

    def test_call_model_json_retries_a_non_json_reply(self) -> None:
        provider = docuagent.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _FakeJsonResponse("抱歉，我来给你写一段代码"),
                _FakeJsonResponse('{"ok":true,"answer":42}'),
            ],
        ) as urlopen:
            result = docuagent.call_model_json(
                provider,
                "Return JSON only.",
                {"instruction": "confirm"},
            )

        self.assertTrue(result["ok"])
        self.assertEqual(42, result["answer"])
        self.assertEqual(2, urlopen.call_count)

    def test_call_model_json_extracts_json_block_from_prose(self) -> None:
        provider = docuagent.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )
        content = (
            "I checked the task and the target file does not exist yet.\n"
            "I will create it now.\n"
            '```json\n'
            '{"tool_calls":[{"tool":"write_file","args":{"path":"src/core.js","content":"..."}}]}\n'
            "```"
        )

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeJsonResponse(content),
        ):
            result = docuagent.call_model_json(
                provider,
                "You may answer in natural language.",
                {"instruction": "implement"},
            )

        self.assertEqual(["write_file"], [call["tool"] for call in result["tool_calls"]])

    def test_call_model_json_allows_pure_prose_for_agent_loop(self) -> None:
        provider = docuagent.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeJsonResponse("Done. All files were written through tools."),
        ):
            result = docuagent.call_model_json(
                provider,
                "You may answer in natural language.",
                {"instruction": "implement"},
                allow_prose=True,
            )

        self.assertEqual(
            "Done. All files were written through tools.", result["__prose__"]
        )

    def test_call_model_json_gives_up_after_json_retries(self) -> None:
        provider = docuagent.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )

        with patch(
            "urllib.request.urlopen",
            side_effect=[
                _FakeJsonResponse("还是非 JSON"),
                _FakeJsonResponse("依旧非 JSON"),
                _FakeJsonResponse("依然非 JSON"),
            ],
        ) as urlopen:
            with self.assertRaises(Exception):
                docuagent.call_model_json(
                    provider,
                    "Return JSON only.",
                    {"instruction": "confirm"},
                )

        self.assertEqual(3, urlopen.call_count)


class NativeToolCallingTest(unittest.TestCase):
    def test_call_model_chat_sends_tools_and_parses_tool_calls(self) -> None:
        provider = bootstrap.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )
        tools = bootstrap.openai_tool_schemas([
            {"name": "write_file", "description": "Write a sandbox file.", "args": {
                "path": "relative path",
                "content": "file content",
            }},
        ])

        with patch(
            "urllib.request.urlopen",
            return_value=_FakeToolCallResponse(
                "I will write the file.",
                [{
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({
                            "path": "src/core.js",
                            "content": "console.log('ok')",
                        }),
                    },
                }],
            ),
        ) as urlopen:
            result = bootstrap.call_model_chat(
                provider,
                [{"role": "system", "content": "Implement."}],
                tools=tools,
            )

        payload = json.loads(urlopen.call_args.args[0].data.decode("utf-8"))
        self.assertEqual("write_file", payload["tools"][0]["function"]["name"])
        self.assertEqual(["path", "content"], payload["tools"][0]["function"]["parameters"]["required"])
        self.assertEqual("write_file", result["tool_calls"][0]["name"])
        self.assertEqual("src/core.js", result["tool_calls"][0]["args"]["path"])

    def test_call_model_chat_falls_back_marker_on_http_400(self) -> None:
        provider = bootstrap.ProviderConfig(
            base_url="https://example.test/v1",
            model="test-model",
            api_key="secret",
        )
        rejected = urllib.error.HTTPError(
            "https://example.test/v1/chat/completions",
            400,
            "Bad Request",
            {},
            None,
        )
        tools = bootstrap.openai_tool_schemas([
            {"name": "write_file", "description": "Write a sandbox file.", "args": {
                "path": "relative path",
            }},
        ])

        with patch("urllib.request.urlopen", side_effect=rejected):
            with self.assertRaises(bootstrap.ToolCallingNotSupported):
                bootstrap.call_model_chat(
                    provider,
                    [{"role": "user", "content": "Implement."}],
                    tools=tools,
                )


if __name__ == "__main__":
    unittest.main()
