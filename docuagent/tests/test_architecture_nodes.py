import tempfile
import unittest
from pathlib import Path

import docuagent


def module(module_id: str, deps: list[str] | None = None, path: str | None = None) -> dict:
    return {
        "id": module_id,
        "name": module_id.title(),
        "responsibility": f"{module_id} does one thing.",
        "brief": f"{module_id} does one thing.",
        "path": path or f"src/{module_id}",
        "depends_on": deps or [],
        "needs_ui": False,
        "group": None,
    }


def architecture(ids: list[str], edges: list[dict] | None = None) -> dict:
    return {
        "summary": "A reviewable architecture.",
        "platform": "Web",
        "language": "Python",
        "runtime": "Python 3.12",
        "frameworks": [],
        "stack": ["Python 3.12"],
        "modules": [module(module_id) for module_id in ids],
        "groups": [],
        "edges": edges or [],
        "data": [],
        "integrations": [],
        "constraints": ["Keep boundaries"],
        "verification": ["Run tests"],
        "risks": [],
        "unresolved": [],
    }


def write_review(root: Path, arch: dict) -> None:
    state = {
        "schema_version": 1,
        "status": "review",
        "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
        "answers": {"goal": "demo"},
        "architecture": arch,
        "current_question": None,
        "progress": 95,
        "updated_at": docuagent.utc_now(),
    }
    docuagent.atomic_write_json(docuagent.managed_path(root, "bootstrap.json"), state)


class ArchitectureNodeOperationTest(unittest.TestCase):
    def test_rename_and_responsibility_edit_review_draft(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            write_review(root, architecture(["core", "api", "worker"]))

            renamed = docuagent.update_architecture_nodes({
                "path": str(root), "action": "rename", "module_id": "core", "name": "Domain Core",
            })
            self.assertEqual("Domain Core", renamed["architecture"]["modules"][0]["name"])
            self.assertEqual(0, renamed["architecture_version"])

            changed = docuagent.update_architecture_nodes({
                "path": str(root), "action": "responsibility", "module_id": "core",
                "text": "Own all domain rules and invariants.",
            })
            core = next(item for item in changed["architecture"]["modules"] if item["id"] == "core")
            self.assertEqual("Own all domain rules and invariants.", core["responsibility"])

            saved = docuagent.read_json(docuagent.managed_path(root, "bootstrap.json"))
            self.assertEqual("Domain Core", saved["architecture"]["modules"][0]["name"])

    def test_mark_module_uncertain(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            write_review(root, architecture(["core", "api", "worker"]))

            result = docuagent.update_architecture_nodes({
                "path": str(root), "action": "uncertain", "module_id": "api",
                "reason": "还不确定是否需要独立 API 层。",
            })
            api = next(item for item in result["architecture"]["modules"] if item["id"] == "api")
            self.assertTrue(api["uncertain"])
            self.assertEqual("还不确定是否需要独立 API 层。", api["uncertain_reason"])

    def test_delete_rejects_below_three_modules_in_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            write_review(root, architecture(["core", "api", "worker"]))

            with self.assertRaisesRegex(docuagent.WorkspaceError, "至少需要 3 个模块"):
                docuagent.update_architecture_nodes({
                    "path": str(root), "action": "delete", "module_id": "worker",
                })

    def test_merge_redirects_dependencies_and_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            arch = architecture(
                ["core", "api", "worker", "cache"],
                edges=[
                    {"from": "api", "to": "core", "kind": "uses", "label": "调用", "reason": "API 调用核心逻辑。"},
                    {"from": "worker", "to": "cache", "kind": "uses", "label": "读写", "reason": "Worker 使用缓存。"},
                ],
            )
            arch["modules"][1]["depends_on"] = ["core"]
            arch["modules"][2]["depends_on"] = ["cache"]
            write_review(root, arch)

            result = docuagent.update_architecture_nodes({
                "path": str(root), "action": "merge", "module_id": "core", "source_ids": ["cache"],
            })
            modules = result["architecture"]["modules"]
            self.assertEqual(3, len(modules))
            self.assertNotIn("cache", {item["id"] for item in modules})
            worker = next(item for item in modules if item["id"] == "worker")
            self.assertEqual(["core"], worker["depends_on"])
            self.assertTrue(all(edge["from"] != "cache" and edge["to"] != "cache" for edge in result["architecture"]["edges"]))

    def test_split_inherits_dependencies_and_edges(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            arch = architecture(
                ["core", "api", "worker"],
                edges=[
                    {"from": "api", "to": "core", "kind": "uses", "label": "调用", "reason": "API 调用核心逻辑。"},
                ],
            )
            arch["modules"][0]["depends_on"] = []
            arch["modules"][1]["depends_on"] = ["core"]
            write_review(root, arch)

            result = docuagent.update_architecture_nodes({
                "path": str(root), "action": "split", "module_id": "core",
                "name_a": "Domain", "name_b": "Persistence",
                "responsibility_a": "Domain rules.",
                "responsibility_b": "Save and load state.",
            })
            modules = result["architecture"]["modules"]
            self.assertEqual(4, len(modules))
            ids = {item["id"] for item in modules}
            self.assertIn("domain", ids)
            self.assertIn("persistence", ids)
            self.assertTrue(any(edge["from"] == "api" and edge["to"] == "domain" for edge in result["architecture"]["edges"]))
            self.assertTrue(any(edge["from"] == "api" and edge["to"] == "persistence" for edge in result["architecture"]["edges"]))

    def test_confirmed_edit_bumps_version_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            document = {
                "schema_version": 1,
                "architecture_version": 3,
                "generated_at": docuagent.utc_now(),
                "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
                **architecture(["core", "api", "worker"]),
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "architecture.json"), document)

            result = docuagent.update_architecture_nodes({
                "path": str(root), "action": "rename", "module_id": "api", "name": "API Gateway",
            })

            self.assertEqual(4, result["architecture_version"])
            self.assertEqual(1, result["history_remaining"])
            self.assertEqual("API Gateway", result["architecture"]["modules"][1]["name"])


if __name__ == "__main__":
    unittest.main()


class ArchitectureEdgeOperationTest(unittest.TestCase):
    def review_with_edge(self, root: Path) -> dict:
        arch = architecture(
            ["core", "api", "worker"],
            edges=[
                {"from": "api", "to": "core", "kind": "uses", "label": "调用", "reason": "API 调用核心逻辑。"},
            ],
        )
        arch["modules"][1]["depends_on"] = ["core"]
        write_review(root, arch)
        return arch

    def test_accept_and_change_edge_type(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            self.review_with_edge(root)

            accepted = docuagent.update_architecture_edges({
                "path": str(root), "action": "accept", "from": "api", "to": "core",
            })
            edge = next(item for item in accepted["architecture"]["edges"] if item["from"] == "api" and item["to"] == "core")
            self.assertTrue(edge["accepted"])

            changed = docuagent.update_architecture_edges({
                "path": str(root), "action": "type", "from": "api", "to": "core", "kind": "data",
            })
            edge = next(item for item in changed["architecture"]["edges"] if item["from"] == "api" and item["to"] == "core")
            self.assertEqual("data", edge["kind"])

    def test_update_reason_and_delete_edge(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            self.review_with_edge(root)

            changed = docuagent.update_architecture_edges({
                "path": str(root), "action": "reason", "from": "api", "to": "core",
                "reason": "API 读取核心提供的领域模型。",
            })
            edge = next(item for item in changed["architecture"]["edges"] if item["from"] == "api" and item["to"] == "core")
            self.assertEqual("API 读取核心提供的领域模型。", edge["reason"])

            deleted = docuagent.update_architecture_edges({
                "path": str(root), "action": "delete", "from": "api", "to": "core",
            })
            self.assertFalse(any(item["from"] == "api" and item["to"] == "core" for item in deleted["architecture"]["edges"]))


class ReopenReviewTest(unittest.TestCase):
    def test_ready_state_returns_to_review(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            state = {
                "schema_version": 1,
                "status": "ready",
                "architecture_version": 1,
                "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
                "answers": {"goal": "demo"},
                "architecture": architecture(["core", "api", "worker"]),
                "current_question": None,
                "progress": 100,
                "updated_at": docuagent.utc_now(),
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "bootstrap.json"), state)

            result = docuagent.reopen_architecture_review({"path": str(root)})

            self.assertEqual("review", result["status"])
            self.assertIsNone(result["current_question"])
            saved = docuagent.read_json(docuagent.managed_path(root, "bootstrap.json"))
            self.assertEqual("review", saved["status"])

    def test_initialized_state_uses_latest_document(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            state = {
                "schema_version": 1,
                "status": "initialized",
                "architecture_version": 1,
                "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
                "answers": {"goal": "demo"},
                "architecture": architecture(["old", "api", "worker"]),
                "current_question": None,
                "progress": 100,
                "updated_at": docuagent.utc_now(),
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "bootstrap.json"), state)
            document = {
                "schema_version": 1,
                "architecture_version": 2,
                "generated_at": docuagent.utc_now(),
                "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
                **architecture(["core", "api", "worker"]),
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "architecture.json"), document)

            result = docuagent.reopen_architecture_review({"path": str(root)})

            self.assertEqual("review", result["status"])
            self.assertEqual({"core", "api", "worker"}, {item["id"] for item in result["architecture"]["modules"]})
