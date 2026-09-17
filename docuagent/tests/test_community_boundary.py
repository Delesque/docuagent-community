import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main
import onboard
from core import WorkspaceError
from workspace import atomic_write_json, managed_path


class CommunityBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DOCUAGENT_ENABLE_COMMERCIAL": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_core_imports_without_private_package(self):
        self.assertFalse(onboard.available())
        self.assertTrue(callable(main.start_bootstrap))

    def test_paid_routes_fail_before_scanning_or_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "untouched"
            for route in (main.scan_import_route, main.start_onboard_route):
                with self.subTest(route=route.__name__):
                    with self.assertRaises(WorkspaceError) as caught:
                        route({"path": str(root)})
                    self.assertEqual(caught.exception.payload["code"], "extension_unavailable")
                    self.assertFalse(root.exists())
            with self.assertRaises(WorkspaceError):
                list(main.stream_onboard_events({"path": str(root)}))
            self.assertFalse(root.exists())

    def test_workspace_reports_actual_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            info = main.inspect_workspace(tmp)
            self.assertFalse(info["capabilities"]["project_reconstruction"])

    def test_bootstrap_cannot_fall_back_to_import_analysis(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('existing')\n", encoding="utf-8")
            with self.assertRaises(WorkspaceError) as caught:
                main.start_bootstrap({"path": str(root)})
            self.assertEqual(caught.exception.payload["code"], "extension_unavailable")
            self.assertFalse(managed_path(root, "bootstrap.json").exists())

    def test_generated_new_project_can_reopen_with_existing_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "main.py").write_text("print('new')\n", encoding="utf-8")
            atomic_write_json(managed_path(root, "bootstrap.json"), {
                "status": "initialized", "project": {"mode": "new"},
                "architecture": {"modules": [{"id": "core"}]},
            })
            state = main.start_bootstrap({"path": str(root)})
            self.assertEqual(state["status"], "initialized")

    def test_enabling_flag_does_not_supply_missing_private_code(self):
        with patch.dict(os.environ, {"DOCUAGENT_ENABLE_COMMERCIAL": "1"}):
            with patch("onboard.importlib.util.find_spec", return_value=None):
                with self.assertRaises(WorkspaceError):
                    onboard.call_onboard(None, {})

    def test_unknown_extension_function_is_not_exposed(self):
        with self.assertRaises(AttributeError):
            getattr(onboard, "arbitrary_entry")
