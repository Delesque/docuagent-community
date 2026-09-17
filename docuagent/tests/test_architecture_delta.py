"""Architecture delta comparison (B1).

Canonical compare of two architecture documents with the four-way classification:
added / removed / changed / moved. The critical property under test is the split:
presentation-only edits (path, group, target_files) classify as `moved` and must
never surface as semantic warnings — the same guarantee later reuse needs for
concurrent contract-registry deltas.
"""

import unittest

import docuagent


def architecture(**overrides) -> dict:
    base = {
        "summary": "A local coding workspace.",
        "platform": "Windows",
        "language": "Python",
        "runtime": "Python 3.12",
        "frameworks": [],
        "stack": ["Python 3.12"],
        "modules": [
            {"id": "core", "name": "Core", "brief": "logic", "responsibility": "Owns the domain.",
             "path": "src/core", "depends_on": [], "needs_ui": False, "group": None},
            {"id": "graph", "name": "Graph", "brief": "render", "responsibility": "Draws the graph.",
             "path": "src/graph", "depends_on": ["core"], "needs_ui": False, "group": None},
        ],
        "groups": [],
        "edges": [
            {"from": "graph", "to": "core", "kind": "uses", "label": "读取状态", "reason": "Reads project state."},
        ],
        "data": [], "integrations": [], "constraints": ["No chat history"],
        "verification": ["Run pytest"], "risks": [], "unresolved": [],
    }
    base.update(overrides)
    return base


def by_kind(delta: dict, kind: str) -> list[dict]:
    return [change for change in delta["changes"] if change["kind"] == kind]


class CanonicalEqualityTest(unittest.TestCase):
    def test_key_order_and_list_order_do_not_matter(self) -> None:
        source = architecture()
        shuffled = {
            # Every field present, keys and lists reordered.
            "verification": source["verification"],
            "constraints": source["constraints"],
            "modules": [
                {key: source["modules"][1][key] for key in reversed(source["modules"][1])},
                {key: source["modules"][0][key] for key in reversed(source["modules"][0])},
            ],
            "edges": [
                {key: source["edges"][0][key] for key in reversed(source["edges"][0])},
            ],
        }
        for key, value in source.items():
            shuffled.setdefault(key, value)
        # Reorder the remaining top-level keys so dict insertion order differs too.
        shuffled = {key: shuffled[key] for key in sorted(shuffled, reverse=True)}

        delta = docuagent.compare_architectures(architecture(), shuffled)

        self.assertEqual(
            {"added": 0, "removed": 0, "changed": 0, "moved": 0},
            delta["counts"],
        )
        self.assertEqual([], delta["changes"])

    def test_document_envelope_fields_are_ignored(self) -> None:
        before = architecture()
        after = {
            "schema_version": 1,
            "architecture_version": 9,
            "generated_at": "later",
            "project": {"name": "X"},
            **architecture(),
            "provenance": [{"id": "p1", "source": "inferred"}],
        }

        delta = docuagent.compare_architectures(before, after)

        self.assertEqual({"added": 0, "removed": 0, "changed": 0, "moved": 0}, delta["counts"])


class ModuleClassificationTest(unittest.TestCase):
    def test_added_and_removed_modules(self) -> None:
        before = architecture()
        after = architecture()
        after["modules"] = [m for m in after["modules"] if m["id"] != "graph"]
        after["modules"].append(
            {"id": "tasks", "name": "Tasks", "brief": "dag", "responsibility": "Plans tasks.",
             "path": "src/tasks", "depends_on": ["core"], "needs_ui": False, "group": None}
        )

        delta = docuagent.compare_architectures(before, after)

        added = by_kind(delta, "added")
        removed = by_kind(delta, "removed")
        self.assertEqual([{"type": "module", "id": "tasks"}], [c["subject"] for c in added])
        self.assertEqual([{"type": "module", "id": "graph"}], [c["subject"] for c in removed])
        self.assertIn("Tasks", added[0]["message"])
        self.assertEqual({"added": 1, "removed": 1, "changed": 0, "moved": 0}, delta["counts"])

    def test_responsibility_change_is_changed(self) -> None:
        after = architecture()
        after["modules"][0]["responsibility"] = "Owns everything else."

        delta = docuagent.compare_architectures(architecture(), after)

        changed = by_kind(delta, "changed")
        self.assertEqual(1, len(changed))
        self.assertEqual({"type": "module", "id": "core"}, changed[0]["subject"])
        self.assertEqual(["responsibility"], changed[0]["fields"])
        self.assertIn("职责", changed[0]["message"])

    def test_path_and_group_only_changes_are_moved_not_changed(self) -> None:
        after = architecture()
        after["modules"][0]["path"] = "src/core/domain"
        after["modules"][0]["group"] = "backend"
        after["modules"][0]["target_files"] = ["src/core/domain/__init__.py"]

        delta = docuagent.compare_architectures(architecture(), after)

        self.assertEqual([], by_kind(delta, "changed"))
        moved = by_kind(delta, "moved")
        self.assertEqual(1, len(moved))
        self.assertEqual({"type": "module", "id": "core"}, moved[0]["subject"])
        self.assertEqual(["path", "group", "target_files"], moved[0]["fields"])
        self.assertEqual({"added": 0, "removed": 0, "changed": 0, "moved": 1}, delta["counts"])

    def test_semantic_change_wins_over_presentation_change(self) -> None:
        after = architecture()
        after["modules"][0]["path"] = "src/core/domain"
        after["modules"][0]["responsibility"] = "Owns everything else."

        delta = docuagent.compare_architectures(architecture(), after)

        self.assertEqual(1, len(by_kind(delta, "changed")))
        self.assertEqual([], by_kind(delta, "moved"))


class EdgeAndTopFieldTest(unittest.TestCase):
    def test_edge_add_remove_and_reason_change(self) -> None:
        before = architecture()
        after = architecture()
        after["edges"][0]["reason"] = "Polls project state."
        after["edges"].append(
            {"from": "core", "to": "graph", "kind": "blocks", "label": "先建", "reason": "Build order."}
        )

        delta = docuagent.compare_architectures(before, after)

        added = by_kind(delta, "added")
        changed = by_kind(delta, "changed")
        self.assertEqual({"type": "edge", "from": "core", "to": "graph", "kind": "blocks"}, added[0]["subject"])
        self.assertEqual(["reason"], changed[0]["fields"])
        self.assertEqual({"type": "edge", "from": "graph", "to": "core", "kind": "uses"}, changed[0]["subject"])

    def test_edge_accepted_flag_is_not_semantic(self) -> None:
        after = architecture()
        after["edges"][0]["accepted"] = True

        delta = docuagent.compare_architectures(architecture(), after)

        self.assertEqual({"added": 0, "removed": 0, "changed": 0, "moved": 0}, delta["counts"])

    def test_top_level_constraint_change(self) -> None:
        after = architecture()
        after["constraints"] = ["No chat history", "Keys never on disk"]

        delta = docuagent.compare_architectures(architecture(), after)

        changed = by_kind(delta, "changed")
        self.assertEqual(1, len(changed))
        self.assertEqual({"type": "architecture", "id": "constraints"}, changed[0]["subject"])

    def test_unchanged_documents_produce_zero_counts(self) -> None:
        delta = docuagent.compare_architectures(architecture(), architecture())
        self.assertEqual({"added": 0, "removed": 0, "changed": 0, "moved": 0}, delta["counts"])
        self.assertIsNone(docuagent.format_delta_summary(delta))


class FormattingTest(unittest.TestCase):
    def test_lines_and_summary(self) -> None:
        after = architecture()
        after["modules"].append(
            {"id": "tasks", "name": "Tasks", "brief": "dag", "responsibility": "Plans tasks.",
             "path": "src/tasks", "depends_on": ["core"], "needs_ui": False, "group": None}
        )
        after["modules"][0]["path"] = "src/core/domain"
        after["edges"][0]["reason"] = "Polls project state."

        delta = docuagent.compare_architectures(architecture(), after)
        lines = docuagent.format_delta(delta)
        summary = docuagent.format_delta_summary(delta)

        self.assertIn("新增模块 Tasks。", lines)
        self.assertIn("模块 Core 仅位置调整：路径。", lines)
        self.assertIn("连线 graph → core（uses）更新：原因。", lines)
        self.assertEqual("新增 1 · 移除 0 · 更新 1 · 仅位置调整 1", summary)


class EditRouteDeltaTest(unittest.TestCase):
    """The edit route must return the computed delta, not just model prose."""

    PROVIDER = {
        "enabled": True,
        "base_url": "https://example.test/v1",
        "model": "test-model",
        "api_key": "secret",
    }

    def edit_result(self, changes: list[str]) -> dict:
        return docuagent.validate_architecture_edit_result({
            "architecture": docuagent.normalize_architecture({
                "summary": "An editable architecture.",
                "platform": "Windows",
                "language": "Python",
                "runtime": "Python 3.12",
                "frameworks": [],
                "stack": ["Python 3.12"],
                "modules": [
                    {"id": "core", "name": "Core", "responsibility": "core does one thing.",
                     "path": "src/core", "depends_on": []},
                    {"id": "auth", "name": "Auth", "responsibility": "auth does one thing.",
                     "path": "src/auth", "depends_on": []},
                ],
                "data": [], "integrations": [],
                "constraints": ["No chat history"], "verification": ["Run tests"],
                "risks": [], "unresolved": [],
            }),
            "thinking": "拆出认证。",
            "changes": changes,
        })

    def test_edit_returns_computed_delta(self) -> None:
        import json
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            docuagent.atomic_write_json(
                docuagent.managed_path(root, "architecture.json"),
                {
                    "schema_version": 1,
                    "architecture_version": 3,
                    "generated_at": "T",
                    "project": {"name": "Edited", "slug": "edited", "root": str(root), "mode": "new"},
                    **docuagent.normalize_architecture({
                        "summary": "An editable architecture.",
                        "platform": "Windows",
                        "language": "Python",
                        "runtime": "Python 3.12",
                        "frameworks": [],
                        "stack": ["Python 3.12"],
                        "modules": [
                            {"id": "core", "name": "Core", "responsibility": "core does one thing.",
                             "path": "src/core", "depends_on": []},
                        ],
                        "data": [], "integrations": [],
                        "constraints": ["No chat history"], "verification": ["Run tests"],
                        "risks": [], "unresolved": [],
                    }),
                },
            )

            with patch(
                "docuagent.call_architecture_edit_model",
                return_value=self.edit_result(["新增 auth 模块"]),
            ):
                result = docuagent.edit_architecture({
                    "path": str(root),
                    "request": "把认证拆成独立模块",
                    "provider": self.PROVIDER,
                })

            self.assertEqual({"added": 1, "removed": 0, "changed": 0, "moved": 0}, result["delta"]["counts"])
            self.assertIn("新增模块 Auth。", result["delta_lines"])
            self.assertEqual("新增 1 · 移除 0 · 更新 0 · 仅位置调整 0", result["delta_summary"])
            # The document on disk is untouched by comparison: still the new version.
            written = json.loads((root / ".docuagent" / "architecture.json").read_text(encoding="utf-8"))
            self.assertEqual(4, written["architecture_version"])


if __name__ == "__main__":
    unittest.main()
