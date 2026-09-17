"""Tests for the unified error nodes (P1: errors attached to graph nodes)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import docuagent
from error_nodes import (
    DOCUMENTATION_ERROR_ACTIONS,
    GENERATION_ERROR_ACTIONS,
    VERIFICATION_ERROR_ACTIONS,
    ErrorNodeError,
    active_error_nodes,
    create_or_refresh_error_node,
    error_node_id,
    read_error_nodes,
    reconcile_task_error_nodes,
    resolve_error_nodes,
)


PROVIDER = {
    "enabled": True,
    "base_url": "https://example.test/v1",
    "model": "test-model",
    "api_key": "secret",
}


def _architecture() -> dict:
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
            }
        ],
        "data": [],
        "integrations": [],
        "constraints": [],
        "verification": ["python -m unittest"],
        "risks": [],
        "unresolved": [],
    })


class ErrorNodesTest(unittest.TestCase):
    def _root(self) -> Path:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        return root

    def _done_state(self, root: Path) -> dict:
        return {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "verified",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                    "applied_files": [],
                    "rejected_files": [],
                    "pending_files": [],
                }
            ],
            "last_error": "",
            "documentation_task": {
                "status": "pending",
                "code_task_id": "core",
                "code_task_ids": ["core"],
                "changed_files": ["src/core.py"],
                "affected_modules": ["core"],
            },
            "docs_in_sync": False,
            "docs_sync_required": True,
        }

    def test_create_error_node_has_required_shape(self) -> None:
        root = self._root()
        node, created = create_or_refresh_error_node(
            root,
            owner_node_id="core",
            source="documentation",
            kind="doc-sync",
            severity="critical",
            title="文档同步失败",
            detail="boom",
            retry_count=2,
            max_retries=2,
            actions=DOCUMENTATION_ERROR_ACTIONS,
            code_task_id="core",
            changed_files=["src\\core.py"],
        )
        self.assertTrue(created)
        self.assertEqual("error:documentation:doc-sync:core", node["id"])
        self.assertEqual("error", node["type"])
        self.assertEqual("core", node["owner_node_id"])
        self.assertEqual("documentation", node["source"])
        self.assertEqual("critical", node["severity"])
        self.assertEqual("active", node["status"])
        self.assertEqual(2, node["retry_count"])
        self.assertEqual("src/core.py", node["changed_files"][0])
        action_ids = [action["id"] for action in node["actions"]]
        self.assertEqual(
            [
                "retry_documentation",
                "view_error_detail",
                "open_related_docs",
                "view_code_change",
            ],
            action_ids,
        )
        for banned in ("ignore", "dismiss", "close", "snooze"):
            self.assertNotIn(banned, action_ids)

    def test_reblocking_the_same_problem_refreshes_instead_of_duplicating(self) -> None:
        root = self._root()
        first, created = create_or_refresh_error_node(
            root,
            owner_node_id="core",
            source="documentation",
            kind="doc-sync",
            severity="critical",
            title="文档同步失败",
            detail="attempt one",
            retry_count=2,
            max_retries=2,
            actions=DOCUMENTATION_ERROR_ACTIONS,
        )
        self.assertTrue(created)
        resolve_error_nodes(root, source="documentation", kind="doc-sync")
        second, created_again = create_or_refresh_error_node(
            root,
            owner_node_id="core",
            source="documentation",
            kind="doc-sync",
            severity="critical",
            title="文档同步失败",
            detail="attempt two",
            retry_count=2,
            max_retries=2,
            actions=DOCUMENTATION_ERROR_ACTIONS,
        )
        self.assertTrue(created_again)
        nodes = read_error_nodes(root)
        self.assertEqual(1, len(nodes))
        self.assertEqual("active", nodes[0]["status"])
        self.assertEqual("attempt two", nodes[0]["detail"])
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["created_at"], nodes[0]["created_at"])

    def test_resolve_only_touches_active_matching_nodes(self) -> None:
        root = self._root()
        for owner in ("core", "auth"):
            create_or_refresh_error_node(
                root,
                owner_node_id=owner,
                source="documentation" if owner == "core" else "verification",
                kind="doc-sync" if owner == "core" else "verify",
                severity="critical",
                title="t",
                detail="d",
                retry_count=1,
                max_retries=2,
                actions=DOCUMENTATION_ERROR_ACTIONS,
            )
        resolved = resolve_error_nodes(root, source="documentation", kind="doc-sync")
        self.assertEqual(1, len(resolved))
        nodes = read_error_nodes(root)
        by_owner = {node["owner_node_id"]: node for node in nodes}
        self.assertEqual("resolved", by_owner["core"]["status"])
        self.assertTrue(by_owner["core"]["resolved_at"])
        self.assertEqual("active", by_owner["auth"]["status"])
        self.assertEqual([], resolve_error_nodes(root, source="documentation", kind="doc-sync"))

    def test_invalid_source_and_severity_are_rejected(self) -> None:
        root = self._root()
        for subtest, kwargs in {
            "source": {"source": "nope", "severity": "critical"},
            "severity": {"source": "documentation", "severity": "huge"},
        }.items():
            with self.subTest(subtest):
                with self.assertRaises(ErrorNodeError):
                    create_or_refresh_error_node(
                        root,
                        owner_node_id="core",
                        kind="doc-sync",
                        title="t",
                        detail="d",
                        retry_count=0,
                        max_retries=2,
                        **kwargs,
                    )

    def test_stable_id_helper(self) -> None:
        with self.subTest("format"):
            self.assertEqual(
                "error:documentation:doc-sync:core",
                error_node_id("documentation", "doc-sync", "core"),
            )


class DocumentationErrorNodeLifecycleTest(unittest.TestCase):
    """sync_docs blocked -> error node created; a successful retry resolves it."""

    def setUp(self) -> None:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        self.root = root
        self.architecture = _architecture()
        self.project = {
            "name": "Demo",
            "slug": "demo",
            "root": str(root),
            "mode": "new",
        }
        self.provider = docuagent.ProviderConfig(
            base_url=PROVIDER["base_url"],
            model=PROVIDER["model"],
            api_key=PROVIDER["api_key"],
        )

    def _state(self) -> dict:
        return {
            "schema_version": 1,
            "task_version": 1,
            "status": "confirmed",
            "tasks": [
                {
                    "id": "core",
                    "module_id": "core",
                    "summary": "Core",
                    "target_files": ["src/core.py"],
                    "depends_on": [],
                    "verification": [],
                    "status": "verified",
                    "patch": [],
                    "thinking": "",
                    "last_error": "",
                    "applied_files": [],
                    "rejected_files": [],
                    "pending_files": [],
                }
            ],
            "last_error": "",
            "documentation_task": {
                "status": "pending",
                "code_task_id": "core",
                "code_task_ids": ["core"],
                "changed_files": ["src/core.py"],
                "affected_modules": ["core"],
            },
            "docs_in_sync": False,
            "docs_sync_required": True,
        }

    def test_blocked_sync_creates_one_active_error_node_with_event(self) -> None:
        docuagent.write_task_state(self.root, self._state())
        with patch(
            "tasks.call_model_json",
            side_effect=[
                RuntimeError("doc attempt 1"),
                RuntimeError("doc attempt 2"),
                RuntimeError("doc attempt 3"),
            ],
        ):
            blocked = docuagent.sync_docs(
                self.root, self.provider, self.architecture, self.project
            )

        self.assertEqual("blocked", blocked["documentation_task"]["status"])
        events = blocked.get("error_node_events", [])
        self.assertEqual(1, len(events))
        self.assertEqual("error_node_created", events[0]["event"])
        node = events[0]["node"]
        self.assertEqual("error:documentation:doc-sync:core", node["id"])
        self.assertEqual("core", node["owner_node_id"])
        self.assertEqual("critical", node["severity"])
        self.assertEqual(2, node["retry_count"])
        self.assertIn("doc attempt 3", node["detail"])
        persisted = active_error_nodes(self.root)
        self.assertEqual(1, len(persisted))
        self.assertEqual("documentation", persisted[0]["source"])

    def test_successful_retry_resolves_the_error_node_with_event(self) -> None:
        docuagent.write_task_state(self.root, self._state())
        with patch(
            "tasks.call_model_json",
            side_effect=[
                RuntimeError("doc attempt 1"),
                RuntimeError("doc attempt 2"),
                RuntimeError("doc attempt 3"),
            ],
        ):
            docuagent.sync_docs(
                self.root, self.provider, self.architecture, self.project
            )

        with patch(
            "tasks.call_model_json",
            return_value={"files": [{"path": "AI_ARCH.md", "content": "# Demo\n"}]},
        ):
            recovered = docuagent.retry_documentation(
                self.root, self.provider, self.architecture, self.project
            )

        self.assertEqual("synced", recovered["documentation_task"]["status"])
        events = recovered.get("error_node_events", [])
        self.assertEqual(1, len(events))
        self.assertEqual("error_node_resolved", events[0]["event"])
        nodes = read_error_nodes(self.root)
        self.assertEqual(1, len(nodes))
        self.assertEqual("resolved", nodes[0]["status"])
        self.assertTrue(nodes[0]["resolved_at"])
        self.assertEqual([], active_error_nodes(self.root))


class TaskErrorReconcileTest(unittest.TestCase):
    """reconcile_task_error_nodes: failed tasks -> nodes; recovery -> resolved."""

    def _root(self) -> Path:
        root = Path(tempfile.mkdtemp()) / "proj"
        root.mkdir(parents=True)
        return root

    @staticmethod
    def _failed_task(task_id: str = "core", **overrides) -> dict:
        task = {
            "id": task_id,
            "module_id": "core",
            "status": "failed",
            "last_error": "boom",
            "target_files": ["src/core.py"],
        }
        task.update(overrides)
        return task

    def test_failed_task_without_verification_output_creates_generation_node(self) -> None:
        root = self._root()
        touched = reconcile_task_error_nodes(root, [self._failed_task()])
        self.assertEqual(1, len(touched))
        node = touched[0]
        self.assertEqual("error:generation:task-failed:core", node["id"])
        self.assertEqual("generation", node["source"])
        self.assertEqual("core", node["owner_node_id"])
        self.assertEqual("core", node["code_task_id"])
        self.assertEqual([action["id"] for action in node["actions"]],
                         [action["id"] for action in GENERATION_ERROR_ACTIONS])
        self.assertIn("retry_task", [action["id"] for action in node["actions"]])

    def test_failed_task_with_verification_output_creates_verification_node(self) -> None:
        root = self._root()
        touched = reconcile_task_error_nodes(
            root, [self._failed_task(verification_output={"returncode": 1})]
        )
        self.assertEqual("verification", touched[0]["source"])
        self.assertEqual("error:verification:task-failed:core", touched[0]["id"])
        self.assertIn("verify_task", [action["id"] for action in touched[0]["actions"]])

    def test_recovery_resolves_the_node_and_repeat_calls_are_idempotent(self) -> None:
        root = self._root()
        reconcile_task_error_nodes(root, [self._failed_task()])
        touched = reconcile_task_error_nodes(
            root, [{**self._failed_task(), "status": "verified"}]
        )
        self.assertEqual(1, len(touched))
        self.assertEqual("resolved", touched[0]["status"])
        self.assertTrue(touched[0]["resolved_at"])
        # A third pass over the same healthy state must not touch anything.
        self.assertEqual([], reconcile_task_error_nodes(root, []))

    def test_source_drift_resolves_the_stale_node_and_creates_the_new_one(self) -> None:
        root = self._root()
        reconcile_task_error_nodes(root, [self._failed_task()])
        touched = reconcile_task_error_nodes(
            root, [self._failed_task(verification_output={"returncode": 1})]
        )
        ids = {node["id"] for node in touched}
        self.assertIn("error:verification:task-failed:core", ids)
        nodes = {node["id"]: node for node in read_error_nodes(root)}
        self.assertEqual("resolved", nodes["error:generation:task-failed:core"]["status"])
        self.assertEqual("active", nodes["error:verification:task-failed:core"]["status"])

    def test_relisted_failed_task_revives_the_same_node_without_duplicates(self) -> None:
        root = self._root()
        reconcile_task_error_nodes(root, [self._failed_task()])
        reconcile_task_error_nodes(root, [])
        touched = reconcile_task_error_nodes(root, [self._failed_task(last_error="again")])
        self.assertEqual(1, len(touched))
        nodes = read_error_nodes(root)
        self.assertEqual(1, len(nodes))
        self.assertEqual("active", nodes[0]["status"])
        self.assertEqual("again", nodes[0]["detail"])
        self.assertEqual("", nodes[0]["resolved_at"])

    def test_write_task_state_triggers_the_reconcile(self) -> None:
        root = self._root()
        state = {
            "schema_version": 1,
            "tasks": [self._failed_task()],
        }
        docuagent.write_task_state(root, state)
        persisted = active_error_nodes(root)
        self.assertEqual(1, len(persisted))
        self.assertEqual("generation", persisted[0]["source"])

        state["tasks"][0]["status"] = "verified"
        docuagent.write_task_state(root, state)
        self.assertEqual([], active_error_nodes(root))

    def test_invalid_task_entries_are_skipped(self) -> None:
        root = self._root()
        touched = reconcile_task_error_nodes(root, [None, {"status": "failed"}, {"id": "x", "status": "failed"}])
        self.assertEqual(1, len(touched))
        self.assertEqual("error:generation:task-failed:x", touched[0]["id"])


if __name__ == "__main__":
    unittest.main()
