import tempfile
import unittest
from pathlib import Path

import docuagent


class SnapshotsTest(unittest.TestCase):
    def test_create_and_list_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            (root / "hello.txt").write_text("v1", encoding="utf-8")

            docuagent.create_snapshot(root, "第一回合")
            listed = docuagent.list_snapshots(root)

            self.assertEqual(1, len(listed))
            self.assertEqual("第一回合", listed[0]["reason"])
            self.assertGreater(listed[0]["file_count"], 0)
            self.assertNotIn("files", listed[0])

    def test_restore_rolls_files_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            target = root / "src" / "core.py"
            target.parent.mkdir(parents=True)
            target.write_text("print('v1')\n", encoding="utf-8")

            docuagent.create_snapshot(root, "v1 时刻")
            target.write_text("print('v2')\n", encoding="utf-8")
            snapshot_id = docuagent.list_snapshots(root)[0]["id"]

            result = docuagent.restore_snapshot(root, snapshot_id)

            self.assertEqual("v1 时刻", result["reason"])
            self.assertGreater(result["restored_files"], 0)
            self.assertEqual("print('v1')\n", target.read_text(encoding="utf-8"))

    def test_restore_missing_snapshot_raises(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            with self.assertRaises(LookupError):
                docuagent.restore_snapshot(root, "nope")

    def test_snapshot_store_is_not_embedded_in_later_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            (root / "hello.txt").write_text("v1", encoding="utf-8")

            first = docuagent.create_snapshot(root, "第一回合")
            second = docuagent.create_snapshot(root, "第二回合")

            import json

            raw = json.loads(
                (root / ".docuagent" / "snapshots.json").read_text(encoding="utf-8")
            )
            entries = {entry["id"]: entry for entry in raw["entries"]}
            self.assertNotIn(
                ".docuagent/snapshots.json",
                entries[second["id"]]["files"],
            )
            self.assertIn("hello.txt", entries[first["id"]]["files"])

    def test_snapshot_records_skipped_files_and_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            (root / "hello.txt").write_text("v1", encoding="utf-8")
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            (root / "large.txt").write_text("x" * 200_001, encoding="utf-8")

            created = docuagent.create_snapshot(root, "带跳过项")

            self.assertTrue(created["truncated"])
            self.assertEqual(2, created["skipped_count"])
            listed = docuagent.list_snapshots(root)[0]
            self.assertTrue(listed["truncated"])
            self.assertEqual(2, listed["skipped_count"])
            raw = (root / ".docuagent" / "snapshots.json").read_text(encoding="utf-8")
            self.assertNotIn("TOKEN=secret", raw)
            self.assertNotIn("x" * 200_001, raw)

    def test_restore_reports_extra_files_without_deleting_them(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "proj"
            root.mkdir(parents=True)
            (root / "base.txt").write_text("base", encoding="utf-8")
            snapshot_id = docuagent.create_snapshot(root, "base")["id"]
            (root / "new.txt").write_text("new", encoding="utf-8")

            result = docuagent.restore_snapshot(root, snapshot_id)

            self.assertEqual(1, result["extra_file_count"])
            self.assertIn("new.txt", result["extra_files"])
            self.assertEqual("new", (root / "new.txt").read_text(encoding="utf-8"))
