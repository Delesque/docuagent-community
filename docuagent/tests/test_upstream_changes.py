"""Upstream contract-change delivery (§3 step4 groundwork).

`upstream_changes_for_module` answers one question for a module: did an interface
it builds against move? The task-boundary notice (`_deliver_upstream_change_notice`)
turns that answer into an unresolved attachment when a task finishes — never
mid-run, because a running agent's context was frozen at start.
"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import contract_registry
import main_routes_tasks


def contracts(core_stale: bool) -> dict:
    return {
        "modules": [
            {
                "id": "core",
                "path": "src/core",
                "depends_on": [],
                "exports": [
                    {
                        "id": "core-score",
                        "symbol": "score",
                        "kind": "function",
                        "source_ref": "src/core/score.py",
                        "line": 10,
                        **({"status": "stale"} if core_stale else {}),
                    }
                ],
            },
            {"id": "api", "path": "src/api", "depends_on": ["core"], "exports": [{"id": "api-run", "symbol": "run", "kind": "function", "source_ref": "src/api/run.py", "line": 5}]},
            {"id": "ui", "path": "src/ui", "depends_on": ["api"], "exports": [{"id": "ui-view", "symbol": "view", "kind": "function", "source_ref": "src/ui/view.py", "line": 8}]},
            {"id": "cli", "path": "src/cli", "depends_on": [], "exports": []},
        ],
        "shared_kernel": [],
        "commands": [],
        "vocabulary": [],
        "data_schema": [],
        "config_policy": [],
    }


class UpstreamChangesForModuleTest(unittest.TestCase):
    def test_direct_and_transitive_dependents_get_depth(self) -> None:
        registry = contracts(core_stale=True)

        api = contract_registry.upstream_changes_for_module(registry, "api")
        ui = contract_registry.upstream_changes_for_module(registry, "ui")
        cli = contract_registry.upstream_changes_for_module(registry, "cli")

        self.assertEqual(1, api["depth"])
        self.assertEqual("core", api["roots"][0])
        self.assertEqual("score", api["changed"][0]["name"])
        self.assertEqual("src/core/score.py", api["changed"][0]["file"])
        self.assertEqual(10, api["changed"][0]["line"])
        self.assertEqual(2, ui["depth"])
        self.assertEqual({}, cli)

    def test_owner_of_stale_entry_reports_its_own_change(self) -> None:
        registry = contracts(core_stale=True)

        own = contract_registry.upstream_changes_for_module(registry, "core")

        self.assertEqual(0, own["depth"])
        self.assertEqual("score", own["changed"][0]["name"])

    def test_no_stale_entries_means_no_change(self) -> None:
        registry = contracts(core_stale=False)

        for module_id in ("core", "api", "ui"):
            self.assertEqual({}, contract_registry.upstream_changes_for_module(registry, module_id))


class TaskBoundaryNoticeTest(unittest.TestCase):
    def _project_with_task(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        (root / ".docuagent").mkdir(exist_ok=True)
        (root / ".docuagent" / "tasks.json").write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "tasks": [
                        {"id": "task-1", "module_id": "api", "status": "applied"}
                    ],
                }
            ),
            encoding="utf-8",
        )
        (root / ".docuagent" / "contracts.json").write_text(
            json.dumps(contracts(core_stale=True)),
            encoding="utf-8",
        )

    def test_apply_task_writes_unresolved_notice_for_affected_module(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self._project_with_task(root)

            with patch("main_routes_tasks._tasks.apply_task_patch", return_value={}):
                main_routes_tasks.apply_task({"path": str(root), "task_id": "task-1"})

            attachments = main_routes_tasks.read_node_attachments(root).get("api", [])
            self.assertEqual(1, len(attachments))
            self.assertFalse(attachments[0]["resolved"])
            self.assertIn("上游接口已变更（直接依赖）", attachments[0]["text"])
            self.assertIn("score", attachments[0]["text"])

    def test_notice_is_deduplicated_by_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            self._project_with_task(root)

            for _ in range(2):
                with patch("main_routes_tasks._tasks.apply_task_patch", return_value={}):
                    main_routes_tasks.apply_task({"path": str(root), "task_id": "task-1"})

            attachments = main_routes_tasks.read_node_attachments(root).get("api", [])
            self.assertEqual(1, len(attachments))

    def test_unaffected_module_gets_no_notice(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            (root / ".docuagent").mkdir()
            (root / ".docuagent" / "tasks.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "tasks": [{"id": "task-2", "module_id": "cli", "status": "applied"}],
                    }
                ),
                encoding="utf-8",
            )
            (root / ".docuagent" / "contracts.json").write_text(
                json.dumps(contracts(core_stale=True)),
                encoding="utf-8",
            )

            with patch("main_routes_tasks._tasks.apply_task_patch", return_value={}):
                main_routes_tasks.apply_task({"path": str(root), "task_id": "task-2"})

            self.assertEqual([], main_routes_tasks.read_node_attachments(root).get("cli", []))


if __name__ == "__main__":
    unittest.main()
