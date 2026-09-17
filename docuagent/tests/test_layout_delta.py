import unittest

from core import WorkspaceError
import layout_delta


def base_node(node_id: str = "n1", selector: str = "[data-testid=\"save\"]") -> dict:
    return {
        "id": node_id,
        "selector": selector,
        "source_hint": "btn-primary",
        "rect": {"x": 10, "y": 10, "width": 80, "height": 32},
        "layout": {
            "display": "inline-block",
            "flex_direction": "row",
            "gap": "8px",
            "align_items": "center",
            "justify_content": "flex-start",
            "padding": [4, 8, 4, 8],
            "margin": [0, 0, 0, 0],
            "min_width": "none",
            "max_width": "none",
            "min_height": "none",
            "max_height": "none",
            "flex_grow": "0",
            "flex_shrink": "1",
            "flex_basis": "auto",
            "order": "0",
            "align_self": "auto",
            "grid_template_columns": "none",
            "grid_template_rows": "none",
            "grid_column": "",
            "grid_row": "",
        },
        "children": [],
    }


def changed_node() -> dict:
    node = base_node()
    node["rect"] = {"x": 10, "y": 10, "width": 120, "height": 40}
    node["layout"].update({
        "display": "flex",
        "flex_direction": "column",
        "gap": "12px",
        "align_items": "center",
        "justify_content": "center",
        "min_width": "100px",
        "max_width": "none",
        "flex_grow": "1",
        "align_self": "center",
        "grid_template_columns": "repeat(2, 1fr)",
        "grid_column": "1 / 3",
    })
    return node


class LayoutDeltaTest(unittest.TestCase):
    def test_normalize_requires_reason(self) -> None:
        with self.assertRaises(WorkspaceError):
            layout_delta.normalize_layout_delta(
                {
                    "schema_version": 1,
                    "module_id": "core",
                    "reason": "",
                    "container": {},
                    "element": {},
                    "grid": {},
                }
            )



    def test_build_from_target_attributes(self) -> None:
        delta = layout_delta.build_layout_delta_from_target(
            base_node(),
            {"element": {"width": 140, "x": 20}, "container": {"gap": "16px"}},
            module_id="core",
            reason="让按钮更大",
        )
        self.assertEqual(140, delta["element"]["width"])
        self.assertEqual(20, delta["element"]["x"])
        self.assertEqual("16px", delta["container"]["gap"])
        self.assertIn("element.width", delta["changed"])
        self.assertIn("element.x", delta["changed"])
        self.assertIn("container.gap", delta["changed"])

    def test_build_from_target_filters_unknown_fields(self) -> None:
        delta = layout_delta.build_layout_delta_from_target(
            base_node(),
            {"element": {"width": 100, "bogus": 123}},
            module_id="core",
            reason="过滤未知字段",
        )
        self.assertEqual(100, delta["element"]["width"])
        self.assertNotIn("bogus", delta["element"])
    def test_diff_detects_position_shift(self) -> None:
        moved = base_node()
        moved["rect"] = {"x": 30, "y": 40, "width": 80, "height": 32}
        moved["layout"]["position"] = "absolute"
        delta = layout_delta.build_layout_delta_from_nodes(
            base_node(),
            moved,
            module_id="core",
            reason="移动按钮",
        )
        self.assertEqual(30, delta["element"]["x"])
        self.assertEqual(40, delta["element"]["y"])
        self.assertEqual("absolute", delta["element"]["position"])
        self.assertIn("element.x", delta["changed"])
        self.assertIn("element.y", delta["changed"])
        self.assertIn("element.position", delta["changed"])
    def test_diff_is_deterministic_and_structured(self) -> None:
        first = layout_delta.build_layout_delta_from_nodes(
            base_node(),
            changed_node(),
            module_id="core",
            reason="让按钮更明显",
        )
        second = layout_delta.build_layout_delta_from_nodes(
            base_node(),
            changed_node(),
            module_id="core",
            reason="让按钮更明显",
        )
        self.assertEqual(first, second)
        self.assertEqual("flex", first["container"]["display"])
        self.assertEqual("column", first["container"]["direction"])
        self.assertEqual("12px", first["container"]["gap"])
        self.assertEqual(120, first["element"]["width"])
        self.assertEqual(100, first["element"]["min_width"])
        self.assertEqual(1, first["element"]["flex_grow"])
        self.assertEqual(2, first["grid"]["column_span"])
        self.assertIn("container.display", first["changed"])
        self.assertIn("element.width", first["changed"])
        self.assertIn("grid.template_columns", first["changed"])

    def test_same_node_produces_empty_delta(self) -> None:
        delta = layout_delta.build_layout_delta_from_nodes(
            base_node(),
            base_node(),
            module_id="core",
            reason="未变化",
        )
        self.assertEqual({}, delta["container"])
        self.assertEqual({}, delta["element"])
        self.assertEqual({}, delta["grid"])
        self.assertEqual([], delta["changed"])

    def test_request_text_is_stable(self) -> None:
        delta = layout_delta.build_layout_delta_from_nodes(
            base_node(),
            changed_node(),
            module_id="core",
            reason="让按钮更明显",
        )
        first = layout_delta.layout_delta_to_request_text(delta, "按钮太小")
        second = layout_delta.layout_delta_to_request_text(delta, "按钮太小")
        self.assertEqual(first, second)
        self.assertIn("[data-testid=\"save\"]", first)
        self.assertIn("让按钮更明显", first)
        self.assertIn("- display = flex", first)
        self.assertIn("- width = 120", first)
        self.assertIn("- template_columns = repeat(2, 1fr)", first)

    def test_snapshots_fallback_to_selector_when_id_changes(self) -> None:
        before = {"module_id": "core", "root": base_node("n1")}
        after = {"module_id": "core", "root": changed_node().copy()}
        after["root"]["id"] = "n7"
        delta = layout_delta.build_layout_delta_from_snapshots(
            before,
            after,
            selector="[data-testid=\"save\"]",
            reason="让按钮更明显",
        )
        self.assertEqual("n7", delta["element_id"])
        self.assertIn("element.width", delta["changed"])

    def test_schema_documents_sections(self) -> None:
        schema = layout_delta.layout_delta_schema()
        self.assertEqual(1, schema["schema_version"])
        self.assertIn("container", schema)
        self.assertIn("flex_grow", schema["element"])
        self.assertIn("column_span", schema["grid"])

    def test_layout_context_includes_ancestors_and_compact_children(self) -> None:
        root = {
            "id": "n0",
            "tag": "main",
            "selector": "main",
            "rect": {"x": 0, "y": 0, "width": 400, "height": 200},
            "layout": {"display": "flex"},
            "children": [
                {
                    "id": "n1",
                    "tag": "button",
                    "selector": "[data-testid=\"save\"]",
                    "rect": {"x": 10, "y": 10, "width": 80, "height": 32},
                    "layout": {"display": "inline-block"},
                    "children": [
                        {
                            "id": "n2",
                            "tag": "span",
                            "selector": "span",
                            "rect": {"x": 10, "y": 10, "width": 40, "height": 20},
                            "layout": {"display": "inline"},
                            "children": [],
                        }
                    ],
                }
            ],
        }
        snapshot = {
            "module_id": "core",
            "url": "http://localhost",
            "title": "Demo",
            "captured_at": "2026-08-13T00:00:00+00:00",
            "viewport": {"width": 800, "height": 600},
            "root": root,
            "meta": {"node_count": 3, "pruned": 0, "truncated": False},
        }
        context = layout_delta.layout_context_for_element(
            snapshot,
            element_id="n1",
            max_children=10,
        )
        self.assertEqual("n0", context["ancestors"][0]["id"])
        self.assertEqual("n1", context["element"]["id"])
        self.assertEqual("n2", context["element"]["children"][0]["id"])
        self.assertEqual([], context["element"]["children"][0]["children"])


if __name__ == "__main__":
    unittest.main()
