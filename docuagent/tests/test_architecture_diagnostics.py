"""Structured diagnostics receipts (B2).

The architecture validate gates and graph-quality review now carry a machine-readable
receipt alongside the human message: stable rule codes, exact subjects, measured
evidence. These tests pin the contract: rule codes, severity, evidence values, and
full backwards compatibility of the legacy Chinese-string surfaces.
"""

import tempfile
import unittest
from pathlib import Path

import bootstrap
import main_routes_bootstrap
import docuagent


def valid_architecture() -> dict:
    return {
        "summary": "A local document-first coding workspace.",
        "platform": "Windows",
        "language": "Python",
        "runtime": "Python 3.12",
        "frameworks": [],
        "stack": ["Python 3.12"],
        "modules": [
            {"id": "workspace", "name": "Workspace", "responsibility": "Manage project state and files.", "path": "src/workspace", "depends_on": []},
            {"id": "graph", "name": "Graph", "responsibility": "Render the architecture graph.", "path": "src/graph", "depends_on": ["workspace"]},
            {"id": "tasks", "name": "Tasks", "responsibility": "Break work into a task DAG.", "path": "src/tasks", "depends_on": ["workspace"]},
        ],
        "edges": [
            {"from": "graph", "to": "workspace", "kind": "uses", "reason": "Reads project state."},
            {"from": "tasks", "to": "workspace", "kind": "uses", "reason": "Reads module list."},
        ],
        "data": [],
        "integrations": [],
        "constraints": ["No chat history"],
        "verification": ["Run pytest"],
        "risks": [],
        "unresolved": [],
    }


def ready_result(**overrides) -> dict:
    result = {
        "architecture": valid_architecture(),
        "ready": True,
        "next_question": None,
        "provenance": [],
        "thinking": "",
    }
    result.update(overrides)
    return result


class GraphDiagnosticsTest(unittest.TestCase):
    def test_messages_match_legacy_strings_exactly(self) -> None:
        architecture = {
            "modules": [
                {"id": "app", "name": "App", "responsibility": "Short.", "path": "a.js"},
                *[
                    {"id": f"m{i}", "name": f"M{i}", "responsibility": "Owns a real capability end to end.", "path": f"m{i}.js"}
                    for i in range(1, 5)
                ],
            ],
            "edges": [
                {"from": f"m{i}", "to": f"m{j}", "kind": "uses", "reason": "real"}
                for i in range(1, 5)
                for j in range(1, 5)
                if i != j
            ],
        }

        diagnostics = docuagent.architecture_graph_diagnostics(architecture)
        issues = docuagent.architecture_graph_quality_issues(architecture)

        self.assertEqual(issues, [item["message"] for item in diagnostics])
        self.assertTrue(any(item["rule"] == "density/ratio-high" for item in diagnostics))
        density = next(item for item in diagnostics if item["rule"] == "density/ratio-high")
        self.assertEqual(density["severity"], "warning")
        self.assertEqual(density["evidence"]["edges"], 12)
        self.assertEqual(density["evidence"]["modules"], 5)

    def test_invalid_shape_keeps_legacy_message(self) -> None:
        architecture = {"modules": "nope", "edges": []}
        self.assertEqual(
            docuagent.architecture_graph_quality_issues(architecture),
            ["架构模块或边格式不正确。"],
        )
        self.assertEqual(
            docuagent.architecture_graph_diagnostics(architecture)[0]["rule"],
            "graph/shape-invalid",
        )

    def test_thin_leaf_subject_carries_module_id(self) -> None:
        architecture = {
            "modules": [
                {"id": "entry", "name": "Entry", "responsibility": "DOM shell markup.", "path": "index.html"},
                {"id": "core", "name": "Core", "responsibility": "Owns the domain model.", "path": "core.js"},
                {"id": "leaf", "name": "Leaf", "responsibility": "Tiny helper.", "path": "leaf.js"},
            ],
            "edges": [
                {"from": "entry", "to": "core", "kind": "uses", "reason": "boots"},
                {"from": "core", "to": "leaf", "kind": "uses", "reason": "reads"},
            ],
        }

        diagnostics = docuagent.architecture_graph_diagnostics(architecture)
        rules = {item["rule"] for item in diagnostics}

        self.assertIn("module/thin-leaf", rules)
        thin = next(item for item in diagnostics if item["rule"] == "module/thin-leaf")
        self.assertEqual(thin["subject"]["id"], "leaf")


class ReadinessDiagnosticsTest(unittest.TestCase):
    def test_missing_fields_map_to_stable_rules(self) -> None:
        diagnostics = docuagent.architecture_readiness_diagnostics({})

        rules = {item["rule"] for item in diagnostics}
        self.assertEqual(
            rules,
            {
                "required/summary",
                "required/stack",
                "required/runtime",
                "required/modules",
                "required/constraints",
                "required/verification",
            },
        )
        for item in diagnostics:
            self.assertEqual(item["severity"], "error")

    def test_unresolved_decision_keeps_text_and_id(self) -> None:
        diagnostics = docuagent.architecture_readiness_diagnostics(
            {"unresolved": [{"id": "auth-choice", "question": "登录态放哪？"}]}
        )

        item = next(d for d in diagnostics if d["rule"] == "readiness/unresolved-decision")
        self.assertEqual(item["subject"]["id"], "auth-choice")
        self.assertEqual(item["message"], "登录态放哪？")

    def test_legacy_issues_surface_unchanged(self) -> None:
        architecture = {"summary": "", "unresolved": ["存储方案未定"]}
        issues = docuagent.architecture_readiness_issues(architecture)
        self.assertIn("项目目标", issues)
        self.assertIn("存储方案未定", issues)


class EdgeReasonDiagnosticsTest(unittest.TestCase):
    def test_missing_reasons_reported_with_preview_evidence(self) -> None:
        architecture = {
            "edges": [
                {"from": "a", "to": "b", "kind": "uses"},
                {"from": "b", "to": "c", "kind": "uses", "reason": "calls c"},
                {"from": "c", "to": "d", "kind": "uses"},
                {"from": "d", "to": "e", "kind": "uses"},
                {"from": "e", "to": "f", "kind": "uses"},
                {"from": "f", "to": "g", "kind": "uses"},
            ],
        }

        diagnostics = docuagent.architecture_edge_reason_diagnostics(architecture)

        self.assertEqual(len(diagnostics), 1)
        item = diagnostics[0]
        self.assertEqual(item["rule"], "edge/reason-missing")
        self.assertEqual(item["severity"], "error")
        self.assertEqual(item["evidence"]["missing_total"], 5)
        self.assertEqual(len(item["evidence"]["preview"]), 5)
        self.assertEqual(
            docuagent.architecture_edge_reason_issues(architecture),
            [item["message"]],
        )


class WorkspaceErrorPayloadTest(unittest.TestCase):
    def test_plain_raise_has_no_payload(self) -> None:
        error = docuagent.WorkspaceError("普通错误。")
        self.assertIsNone(getattr(error, "payload", None))

    def test_diagnostics_receipt_envelope(self) -> None:
        receipt = docuagent.diagnostics_receipt([{"rule": "x", "severity": "error", "subject": {}, "evidence": {}, "message": "m"}])
        self.assertEqual(receipt["schema_version"], 1)
        self.assertEqual(len(receipt["diagnostics"]), 1)


class ValidateReceiptTest(unittest.TestCase):
    def test_ready_with_inferred_provenance_is_not_a_model_failure(self) -> None:
        """Deliberate behavior change (depcheck-lite pilot).

        A model returning the finished architecture with its inferences used to
        raise here, which the UI reported as "AI 模型调用失败，请重试" — sending
        the user to their API key for a rule that retrying cannot satisfy. The
        gate now lives at confirmation (see InferenceGatePlacementTest), where
        the provenance ledger — with accept-all — is one click away.
        """
        raw = ready_result(
            provenance=[{"id": "claim-1", "source": "inferred", "text": "推测的边界"}]
        )

        result = bootstrap.validate_architecture_model_result(raw, {})

        self.assertTrue(result["ready"])
        self.assertEqual(
            ["claim-1"],
            [item["id"] for item in result["architecture"]["provenance"]],
        )

    def test_early_ready_collects_all_blocking_rules(self) -> None:
        raw = ready_result(
            architecture={
                "summary": "",
                "platform": "",
                "language": "",
                "runtime": "",
                "stack": [],
                "modules": [
                    {"id": "a", "name": "A", "responsibility": "Owns a capability.", "path": "a.js"},
                    {"id": "b", "name": "B", "responsibility": "Owns a capability.", "path": "b.js"},
                ],
                "edges": [{"from": "a", "to": "b", "kind": "uses"}],
                "constraints": [],
                "verification": [],
                "unresolved": [],
            }
        )

        with self.assertRaises(docuagent.WorkspaceError) as caught:
            bootstrap.validate_architecture_model_result(raw, {})

        rules = {item["rule"] for item in caught.exception.payload["diagnostics"]}
        self.assertIn("required/summary", rules)
        self.assertIn("required/stack", rules)
        self.assertIn("required/constraints", rules)
        self.assertIn("required/verification", rules)
        self.assertIn("count/below-first-version", rules)
        self.assertIn("edge/reason-missing", rules)
        # The human message still leads with the aggregate Chinese sentence.
        self.assertIn("过早结束", str(caught.exception))

    def test_module_count_exceeds_carries_measured_evidence(self) -> None:
        architecture = valid_architecture()
        for i in range(10):
            architecture["modules"].append(
                {"id": f"extra{i}", "name": f"Extra{i}", "responsibility": "Owns a capability.", "path": f"e{i}.js", "depends_on": []}
            )
        raw = ready_result(architecture=architecture)

        with self.assertRaises(docuagent.WorkspaceError) as caught:
            bootstrap.validate_architecture_model_result(raw, {})

        item = next(
            d for d in caught.exception.payload["diagnostics"]
            if d["rule"] == "count/exceeds-first-version"
        )
        self.assertEqual(item["evidence"]["max"], bootstrap.MAX_FIRST_VERSION_MODULES)
        self.assertEqual(item["evidence"]["modules"], 13)

    def test_successful_result_includes_graph_diagnostics(self) -> None:
        result = bootstrap.validate_architecture_model_result(ready_result(), {})

        self.assertIn("graph_diagnostics", result)
        self.assertIn("graph_quality_issues", result)
        self.assertEqual(result["graph_quality_issues"], [item["message"] for item in result["graph_diagnostics"]])


class ProvenanceAnchorTest(unittest.TestCase):
    """Claims may anchor to a module so confirmations can follow the design."""

    def test_anchor_kept_when_module_exists(self) -> None:
        claims = [
            {"id": "c1", "text": "配置存本地。", "source": "inferred", "module_id": "graph"},
            {"id": "c2", "text": "全局事实。", "source": "confirmed", "module_id": ""},
            {"id": "c3", "text": "无锚点事实。", "source": "confirmed"},
        ]

        normalized = bootstrap.normalize_provenance(claims, module_ids={"graph", "core"})

        self.assertEqual(normalized[0]["module_id"], "graph")
        self.assertNotIn("module_id", normalized[1])
        self.assertNotIn("module_id", normalized[2])

    def test_anchor_dropped_when_module_does_not_exist(self) -> None:
        claims = [{"id": "c1", "text": "幻觉锚点。", "source": "inferred", "module_id": "ghost"}]

        normalized = bootstrap.normalize_provenance(claims, module_ids={"core"})

        self.assertNotIn("module_id", normalized[0])
        # Without a whitelist (legacy callers) the anchor survives untouched.
        permissive = bootstrap.normalize_provenance(claims)
        self.assertEqual(permissive[0]["module_id"], "ghost")

    def test_validate_anchors_against_real_modules(self) -> None:
        raw = ready_result(
            provenance=[
                {"id": "c1", "text": "图模块读工作区。", "source": "inferred", "module_id": "workspace"},
                {"id": "c2", "text": "指向不存在模块。", "source": "inferred", "module_id": "phantom"},
            ]
        )
        raw["ready"] = False
        raw["next_question"] = {"id": "q", "title": "t", "prompt": "p", "why": "", "placeholder": ""}

        result = bootstrap.validate_architecture_model_result(raw, {})

        by_id = {claim["id"]: claim for claim in result["architecture"]["provenance"]}
        self.assertEqual(by_id["c1"]["module_id"], "workspace")
        self.assertNotIn("module_id", by_id["c2"])


if __name__ == "__main__":
    unittest.main()



def _architecture_with_claim(claims: list[dict]) -> dict:
    """A minimal ready-check-passing architecture carrying provenance claims.

    `normalize_architecture` does not carry `provenance`, so it is attached after
    normalization — the same order `validate_architecture_model_result` uses.
    """
    architecture = docuagent.normalize_architecture({
        "summary": "S", "platform": "CLI", "language": "JS", "runtime": "Node",
        "frameworks": [], "stack": ["JS"],
        "modules": [{"id": "app", "name": "App", "responsibility": "Does a thing.",
                     "path": "src/app", "depends_on": []}],
        "groups": [], "edges": [], "data": [], "integrations": [],
        "constraints": ["No third-party runtime deps"],
        "verification": ["node --test passes"],
        "risks": [], "unresolved": [],
    })
    architecture["provenance"] = claims
    return architecture


class InferenceGatePlacementTest(unittest.TestCase):
    """Where the "unconfirmed inferences" gate is enforced.

    Found by the depcheck-lite pilot: models routinely return the finished
    architecture together with their inferences. Raising there surfaced a product
    rule as "AI 模型调用失败，请重试", which points at the API key for something
    retrying cannot fix. The gate belongs at confirmation, where the ledger
    (with accept-all) is one click away.
    """

    def test_ready_with_inferences_reaches_review_instead_of_failing(self) -> None:
        result = bootstrap.validate_architecture_model_result(
            ready_result(provenance=[
                {"id": "c1", "text": "运行环境是 Node 18。", "source": "inferred", "module_id": "graph"},
            ]),
            {},
        )

        self.assertTrue(result["ready"])
        self.assertEqual(
            ["inferred"],
            [item["source"] for item in result["architecture"]["provenance"]],
        )
        # Anchoring survived: the claim is attached to a real module.
        self.assertEqual("graph", result["architecture"]["provenance"][0]["module_id"])

    def test_other_ready_violations_still_raise(self) -> None:
        # A finished architecture with no modules is a real violation; the
        # relaxation covers the inference case only.
        broken = ready_result()
        broken["architecture"]["constraints"] = []
        broken["architecture"]["verification"] = []

        with self.assertRaises(docuagent.WorkspaceError) as caught:
            bootstrap.validate_architecture_model_result(broken, {})
        self.assertIn("过早结束", str(caught.exception))

    def test_confirmation_gate_names_the_ledger_and_how_to_clear_it(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            state = {
                "schema_version": 1,
                "status": "review",
                "project": {"name": "P", "slug": "p", "root": str(root), "mode": "new"},
                "architecture": _architecture_with_claim(
                    [{"id": "c1", "text": "推测", "source": "inferred"}]
                ),
                "answers": {},
            }
            docuagent.atomic_write_json(docuagent.managed_path(root, "bootstrap.json"), state)

            with self.assertRaises(docuagent.WorkspaceError) as caught:
                main_routes_bootstrap.confirm_bootstrap({"path": str(root)})

            self.assertIn("来源确认", str(caught.exception))
            self.assertIn("1", str(caught.exception))
            # Structured diagnostics ride along, same shape as the other gates.
            payload = caught.exception.payload
            self.assertIsNotNone(payload)
            inferred = next(
                item for item in payload["diagnostics"]
                if item["rule"] == "provenance/inferred-remaining"
            )
            self.assertEqual(inferred["evidence"]["ids"], ["c1"])
            self.assertEqual(inferred["evidence"]["count"], 1)

            # Accepting the claim clears the gate: the same call then succeeds.
            main_routes_bootstrap.update_provenance(
                {"path": str(root), "claim_id": "c1", "action": "accept"}
            )
            confirmed = main_routes_bootstrap.confirm_bootstrap({"path": str(root)})
            self.assertEqual("ready", confirmed["status"])
