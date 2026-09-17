import tempfile
import unittest
from pathlib import Path

import docuagent
import task_docs


def _architecture():
    return {
        "summary": "demo",
        "platform": "web",
        "language": "python",
        "runtime": "python3",
        "frameworks": [],
        "stack": [],
        "modules": [
            {
                "id": "core",
                "name": "Core",
                "brief": "core",
                "responsibility": "Core logic.",
                "path": "src/core",
                "depends_on": [],
                "needs_ui": False,
                "group": None,
                "verification": [],
                "target_files": [],
            },
            {
                "id": "api",
                "name": "API",
                "brief": "api",
                "responsibility": "HTTP API.",
                "path": "src/api",
                "depends_on": ["core"],
                "needs_ui": False,
                "group": None,
                "verification": [],
                "target_files": [],
            },
        ],
        "groups": [],
        "edges": [],
        "data": [],
        "integrations": [],
        "constraints": [],
        "verification": [],
        "risks": [],
        "unresolved": [],
    }


class ArchitectureTaskSyncTest(unittest.TestCase):
    def test_applied_task_files_and_verification_flow_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            architecture = _architecture()
            document = {
                "schema_version": 1,
                "architecture_version": 1,
                "generated_at": docuagent.utc_now(),
                "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
                **architecture,
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "architecture.json"), document)
            state = {
                "schema_version": 1,
                "status": "initialized",
                "project": document["project"],
                "answers": {},
                "architecture": architecture,
                "current_question": None,
                "progress": 100,
                "updated_at": docuagent.utc_now(),
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "bootstrap.json"), state)

            updated = task_docs.sync_architecture_from_tasks(
                root,
                architecture,
                [
                    {
                        "id": "core",
                        "module_id": "core",
                        "status": "verified",
                        "target_files": ["src/core.py", "src/core_test.py"],
                        "verification": ["python -m unittest"],
                    },
                    {
                        "id": "api",
                        "module_id": "api",
                        "status": "running",
                        "target_files": ["src/api.py"],
                        "verification": ["python -m unittest"],
                    },
                ],
            )

            self.assertEqual(["core"], updated)
            saved_document = docuagent.read_json(docuagent.managed_path(root, "architecture.json"))
            core = next(item for item in saved_document["modules"] if item["id"] == "core")
            self.assertEqual(["src/core.py", "src/core_test.py"], core["target_files"])
            self.assertEqual(["python -m unittest"], core["verification"])
            saved_state = docuagent.read_json(docuagent.managed_path(root, "bootstrap.json"))
            core_state = next(item for item in saved_state["architecture"]["modules"] if item["id"] == "core")
            self.assertEqual(["src/core.py", "src/core_test.py"], core_state["target_files"])

    def test_pending_or_running_tasks_do_not_write_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            architecture = _architecture()
            document = {
                "schema_version": 1,
                "architecture_version": 1,
                "generated_at": docuagent.utc_now(),
                "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
                **architecture,
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "architecture.json"), document)

            updated = task_docs.sync_architecture_from_tasks(
                root,
                architecture,
                [{
                    "id": "core",
                    "module_id": "core",
                    "status": "pending",
                    "target_files": ["src/core.py"],
                    "verification": ["python -m unittest"],
                }],
            )

            self.assertEqual([], updated)
            saved_document = docuagent.read_json(docuagent.managed_path(root, "architecture.json"))
            core = next(item for item in saved_document["modules"] if item["id"] == "core")
            self.assertEqual([], core["target_files"])


if __name__ == "__main__":
    unittest.main()
