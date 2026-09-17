import tempfile
import unittest
from pathlib import Path
from unittest import mock

import ui_layout
from core import WorkspaceError
from workspace import atomic_write_json


def sample_snapshot() -> dict:
    return {
        "schema_version": 1,
        "captured_at": "2026-08-13T00:00:00+00:00",
        "url": "http://localhost:3000/",
        "title": "Demo",
        "viewport": {"width": 1280, "height": 800},
        "root": {
            "id": "n0",
            "tag": "html",
            "selector": "html",
            "role": "",
            "text": "",
            "value": "",
            "aria_label": "",
            "source_hint": "",
            "rect": {"x": 0, "y": 0, "width": 1280, "height": 800},
            "layout": {
                "display": "block",
                "position": "static",
                "direction": "ltr",
                "visibility": "visible",
                "opacity": 1,
                "box_sizing": "content-box",
                "flex_direction": "row",
                "flex_wrap": "nowrap",
                "align_items": "normal",
                "align_content": "normal",
                "justify_content": "normal",
                "gap": "normal",
                "row_gap": "normal",
                "column_gap": "normal",
                "padding": [0, 0, 0, 0],
                "margin": [0, 0, 0, 0],
                "grid_template_columns": "none",
                "grid_template_rows": "none",
                "grid_auto_flow": "row",
                "grid_column": "",
                "grid_row": "",
                "flex_grow": "0",
                "flex_shrink": "1",
                "flex_basis": "auto",
                "order": "0",
                "align_self": "auto",
                "overflow": "",
                "z_index": None,
                "width": 1280,
                "height": 800,
            },
            "frame": None,
            "children": [
                {
                    "id": "n1",
                    "tag": "button",
                    "selector": "button",
                    "role": "button",
                    "text": "Save",
                    "value": "",
                    "aria_label": "",
                    "source_hint": "btn-primary",
                    "rect": {"x": 10, "y": 10, "width": 80, "height": 32},
                    "layout": {
                        "display": "inline-block",
                        "position": "static",
                        "direction": "ltr",
                        "visibility": "visible",
                        "opacity": 1,
                        "box_sizing": "border-box",
                        "flex_direction": "row",
                        "flex_wrap": "nowrap",
                        "align_items": "normal",
                        "align_content": "normal",
                        "justify_content": "normal",
                        "gap": "normal",
                        "row_gap": "normal",
                        "column_gap": "normal",
                        "padding": [4, 8, 4, 8],
                        "margin": [0, 0, 0, 0],
                        "grid_template_columns": "none",
                        "grid_template_rows": "none",
                        "grid_auto_flow": "row",
                        "grid_column": "",
                        "grid_row": "",
                        "flex_grow": "0",
                        "flex_shrink": "1",
                        "flex_basis": "auto",
                        "order": "0",
                        "align_self": "auto",
                        "overflow": "",
                        "z_index": None,
                        "width": 80,
                        "height": 32,
                    },
                    "frame": None,
                    "children": [],
                }
            ],
        },
        "meta": {"node_count": 2, "pruned": 0, "truncated": False},
    }


class UiLayoutTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())

    def test_layout_path_is_slugified(self) -> None:
        path = ui_layout.layout_path(self.root, "My Module!")
        self.assertEqual("my-module.json", path.name)
        self.assertEqual("layouts", path.parent.name)

    def test_read_missing_layout_raises(self) -> None:
        with self.assertRaises(WorkspaceError):
            ui_layout.read_layout_snapshot(self.root, "core")

    def test_validate_accepts_minimal_snapshot(self) -> None:
        payload = ui_layout.validate_layout_snapshot(sample_snapshot())
        self.assertEqual(2, payload["meta"]["node_count"])

    def test_validate_rejects_duplicate_node_ids(self) -> None:
        payload = sample_snapshot()
        payload["root"]["children"][0]["id"] = "n0"
        with self.assertRaises(WorkspaceError):
            ui_layout.validate_layout_snapshot(payload)

    def test_validate_rejects_empty_root(self) -> None:
        payload = sample_snapshot()
        payload["root"] = {}
        with self.assertRaises(WorkspaceError):
            ui_layout.validate_layout_snapshot(payload)

    def test_normalize_payload_adds_metadata(self) -> None:
        raw = sample_snapshot()
        payload = ui_layout.normalize_layout_payload(raw, "core", "http://example.test")
        self.assertEqual("core", payload["module_id"])
        self.assertEqual("http://localhost:3000/", payload["url"])
        self.assertIn("captured_at", payload)

    def test_capture_persists_snapshot(self) -> None:
        with mock.patch("ui_layout._run_cdp_capture", return_value=sample_snapshot()):
            result = ui_layout.capture_layout_snapshot(
                self.root,
                "http://localhost:3000/",
                "core",
            )
        self.assertEqual("core", result["module_id"])
        self.assertEqual(2, result["node_count"])
        self.assertTrue(ui_layout.layout_path(self.root, "core").exists())
        self.assertEqual(2, ui_layout.read_layout_snapshot(self.root, "core")["meta"]["node_count"])

    def test_capture_rejects_non_http_url(self) -> None:
        with self.assertRaises(WorkspaceError):
            ui_layout.capture_layout_snapshot(self.root, "file:///tmp/a.html", "core")

    def test_read_rejects_unsupported_schema_version(self) -> None:
        target = ui_layout.layout_path(self.root, "core")
        atomic_write_json(target, {"schema_version": 99, "root": sample_snapshot()["root"]})
        with self.assertRaises(WorkspaceError):
            ui_layout.read_layout_snapshot(self.root, "core")


    def test_element_signature(self) -> None:
        button = sample_snapshot()["root"]["children"][0]
        sig = ui_layout.element_signature(button)
        self.assertEqual("button", sig["tag"])
        self.assertEqual("button", sig["role"])
        self.assertEqual("Save", sig["text"])
        self.assertEqual(80, sig["width"])

    def test_find_element_by_signature_ignores_selector(self) -> None:
        snapshot = sample_snapshot()
        button = snapshot["root"]["children"][0]
        sig = ui_layout.element_signature(button)
        button["selector"] = "div:nth-of-type(3) > span"
        found = ui_layout.find_element_by_signature(snapshot, sig)
        self.assertIsNotNone(found)
        self.assertEqual("n1", found["id"])
    def test_schema_documents_core_fields(self) -> None:
        schema = ui_layout.layout_snapshot_schema()
        self.assertEqual(1, schema["schema_version"])
        self.assertIn("layout", schema["root"])
        self.assertIn("grid_template_columns", schema["root"]["layout"])


if __name__ == "__main__":
    unittest.main()
