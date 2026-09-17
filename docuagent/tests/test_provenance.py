import tempfile
import unittest
from pathlib import Path

import docuagent


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
                "brief": "logic",
                "responsibility": "",
                "path": "src",
                "depends_on": [],
                "needs_ui": False,
                "group": None,
            }
        ],
        "groups": [],
        "edges": [],
        "data": [],
        "integrations": [],
        "constraints": [],
        "verification": [],
        "risks": [],
        "unresolved": [],
        "provenance": [
            {"id": "claim-1", "text": "模型推测需要缓存", "source": "inferred"},
            {"id": "claim-2", "text": "建议使用 SQLite", "source": "recommended"},
        ],
    }


def _write_bootstrap(root: Path):
    state = {
        "schema_version": 1,
        "status": "review",
        "project": {"name": "Demo", "slug": "demo", "root": str(root), "mode": "new"},
        "answers": {"goal": "demo"},
        "architecture": _architecture(),
        "current_question": None,
        "progress": 95,
        "updated_at": docuagent.utc_now(),
    }
    docuagent.atomic_write_json(docuagent.managed_path(root, "bootstrap.json"), state)


class ProvenanceActionTest(unittest.TestCase):
    def test_accept_marks_claim_confirmed_and_persists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            _write_bootstrap(root)

            result = docuagent.update_provenance(
                {"path": str(root), "claim_id": "claim-1", "action": "accept"}
            )

            claims = result["state"]["architecture"]["provenance"]
            self.assertEqual("confirmed", claims[0]["source"])
            saved = docuagent.read_json(docuagent.managed_path(root, "bootstrap.json"))
            self.assertEqual("confirmed", saved["architecture"]["provenance"][0]["source"])

    def test_modify_rewrites_text_as_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            _write_bootstrap(root)

            result = docuagent.update_provenance(
                {
                    "path": str(root),
                    "claim_id": "claim-1",
                    "action": "modify",
                    "text": "用户确认：需要 Redis 缓存",
                }
            )

            claim = result["state"]["architecture"]["provenance"][0]
            self.assertEqual("用户确认：需要 Redis 缓存", claim["text"])
            self.assertEqual("confirmed", claim["source"])

    def test_unknown_and_reject_relabel_without_rewriting_text(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            _write_bootstrap(root)

            unknown = docuagent.update_provenance(
                {"path": str(root), "claim_id": "claim-1", "action": "unknown"}
            )
            self.assertEqual("unknown", unknown["state"]["architecture"]["provenance"][0]["source"])

            rejected = docuagent.update_provenance(
                {"path": str(root), "claim_id": "claim-2", "action": "reject"}
            )
            self.assertEqual("rejected", rejected["state"]["architecture"]["provenance"][1]["source"])

    def test_updates_architecture_json_after_finalize(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            document = {
                "schema_version": 1,
                "architecture_version": 1,
                "generated_at": docuagent.utc_now(),
                "project": {"name": "Demo"},
                **_architecture(),
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "architecture.json"), document)

            result = docuagent.update_provenance(
                {"path": str(root), "claim_id": "claim-2", "action": "accept"}
            )

            self.assertIn("architecture", result)
            self.assertEqual("confirmed", result["architecture"]["provenance"][1]["source"])
            saved = docuagent.read_json(docuagent.managed_path(root, "architecture.json"))
            self.assertEqual("confirmed", saved["provenance"][1]["source"])

    def test_rejects_unknown_claim_and_action(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            _write_bootstrap(root)

            with self.assertRaises(docuagent.WorkspaceError):
                docuagent.update_provenance(
                    {"path": str(root), "claim_id": "missing", "action": "accept"}
                )
            with self.assertRaises(docuagent.WorkspaceError):
                docuagent.update_provenance(
                    {"path": str(root), "claim_id": "claim-1", "action": "delete"}
                )
