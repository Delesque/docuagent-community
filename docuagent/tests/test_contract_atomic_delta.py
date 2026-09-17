"""Tests for §3 step4: multi-agent atomic contract deltas.

Covers the pure merge (`apply_contract_delta`) for every registry type and the
concurrency-safe writer (`apply_deltas_atomically`): no lost updates under
parallel agents, and an explicit optimistic-concurrency conflict when an agent's
view of the registry has gone stale.

These run on the system Python 3.12 + pytest (managed 3.13 has no pytest). No
subprocess, no network — pure stdlib threading against a temp file.
"""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import contract_registry
import core


def _seed(path: Path, extra: dict | None = None) -> dict:
    """Write a normalized contracts.json and return its content-hash."""
    reg = {
        "schema_version": 1,
        "project": {"name": "demo", "language": "Python", "runtime": "3.12"},
        "vocabulary": [],
        "shared_kernel": [],
        "commands": [],
        "data_schema": [],
        "config_policy": [],
        "modules": [{
            "id": "billing", "path": "src/billing", "depends_on": [],
            "exports": [], "consumes": [],
        }],
        "recipes": [],
    }
    if extra:
        reg.update(extra)
    normalized = core.normalize_contracts(reg)
    path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    return contract_registry.contracts_hash(normalized)


class ApplyDeltaPureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.reg = core.normalize_contracts({
            "schema_version": 1,
            "project": {"name": "d"},
            "modules": [{"id": "m", "path": "a.py", "exports": []}],
        })

    def _items(self, reg):
        return contract_registry.flatten_registry(reg)

    def test_add_all_six_types(self) -> None:
        deltas = [
            {"op": "add", "type": "public_api", "owner": "m", "name": "run"},
            {"op": "add", "type": "data_schema", "owner": "m", "name": "Invoice",
             "data": {"kind": "entity"}},
            {"op": "add", "type": "commands_events", "owner": "m", "name": "create"},
            {"op": "add", "type": "config_policy", "owner": "m", "name": "MAX",
             "data": {"kind": "env", "default": "3"}},
            {"op": "add", "type": "shared_kernel", "owner": "shared", "name": "AppError"},
            {"op": "add", "type": "vocabulary", "owner": "m", "name": "invoice_id"},
        ]
        out = self.reg
        for d in deltas:
            out = contract_registry.apply_contract_delta(out, d)
        items = {i["type"]: i for i in self._items(out)}
        self.assertEqual(items["public_api"]["name"], "run")
        self.assertEqual(items["data_schema"]["owner"], "m")
        self.assertEqual(items["config_policy"]["status"], "active")
        self.assertEqual(items["shared_kernel"]["owner"], "shared")
        self.assertEqual(items["vocabulary"]["term"], "invoice_id")

    def test_add_is_idempotent(self) -> None:
        once = contract_registry.apply_contract_delta(
            self.reg, {"op": "add", "type": "config_policy", "owner": "m",
                       "name": "MAX", "data": {"default": "3"}})
        twice = contract_registry.apply_contract_delta(
            once, {"op": "add", "type": "config_policy", "owner": "m",
                   "name": "MAX", "data": {"default": "9"}})
        policies = [c for c in twice["config_policy"] if c["name"] == "MAX"]
        self.assertEqual(len(policies), 1)
        self.assertEqual(policies[0]["default"], "9")

    def test_update_merges_and_preserves_bookkeeping(self) -> None:
        added = contract_registry.apply_contract_delta(
            self.reg, {"op": "add", "type": "config_policy", "owner": "m",
                       "name": "MAX", "data": {"default": "3", "source_hash": "h1"}})
        updated = contract_registry.apply_contract_delta(
            added, {"op": "update", "type": "config_policy", "owner": "m",
                    "name": "MAX", "data": {"default": "5"}})
        target = next(c for c in updated["config_policy"] if c["name"] == "MAX")
        self.assertEqual(target["default"], "5")
        self.assertEqual(target["source_hash"], "h1")  # bookkeeping preserved

    def test_update_missing_is_conflict(self) -> None:
        with self.assertRaises(contract_registry.ContractConflict):
            contract_registry.apply_contract_delta(
                self.reg, {"op": "update", "type": "config_policy",
                           "owner": "m", "name": "GHOST"})

    def test_remove_deletes(self) -> None:
        added = contract_registry.apply_contract_delta(
            self.reg, {"op": "add", "type": "config_policy", "owner": "m",
                       "name": "MAX", "data": {"default": "3"}})
        removed = contract_registry.apply_contract_delta(
            added, {"op": "remove", "type": "config_policy", "owner": "m", "name": "MAX"})
        self.assertEqual([c for c in removed["config_policy"] if c["name"] == "MAX"], [])

    def test_remove_missing_is_noop(self) -> None:
        out = contract_registry.apply_contract_delta(
            self.reg, {"op": "remove", "type": "config_policy", "owner": "m", "name": "GHOST"})
        # normalize_contracts must still succeed (no corruption)
        self.assertIsInstance(out, dict)


class ApplyDeltasAtomicTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp())
        self.path = self.dir / "contracts.json"

    def test_timeout_preserves_other_writers_lock_and_releases_thread_lock(self) -> None:
        lock_path = Path(f"{self.path}.delta.lock")
        lock_path.write_text("other writer", encoding="utf-8")
        local_lock = threading.Lock()
        with patch("contract_registry.time.monotonic", side_effect=[0, 31]):
            with self.assertRaises(contract_registry.ContractConflict):
                with contract_registry._acquire_lock(self.path, local_lock):
                    self.fail("must not enter another writer's critical section")
        self.assertEqual(lock_path.read_text(encoding="utf-8"), "other writer")
        self.assertFalse(local_lock.locked())

    @unittest.skipUnless(os.name == "nt", "Windows lock deletion behavior")
    def test_transient_windows_access_denied_is_retried(self) -> None:
        original_open = os.open
        attempts = 0

        def open_after_deletion(*args, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise PermissionError("lock deletion in progress")
            return original_open(*args, **kwargs)

        lock_path = Path(f"{self.path}.delta.lock")
        with patch("contract_registry.os.open", side_effect=open_after_deletion):
            with contract_registry._acquire_lock(self.path, None):
                self.assertTrue(lock_path.exists())
        self.assertEqual(attempts, 2)
        self.assertFalse(lock_path.exists())

    @unittest.skipUnless(os.name == "nt", "Windows lock deletion behavior")
    def test_persistent_access_denied_has_a_bounded_timeout(self) -> None:
        with patch("contract_registry.os.open", side_effect=PermissionError("denied")):
            with patch("contract_registry.time.monotonic", side_effect=[0, 31]):
                with self.assertRaises(contract_registry.ContractConflict):
                    with contract_registry._acquire_lock(self.path, None):
                        self.fail("must not enter without a lock")

    def test_atomic_write_is_valid_json(self) -> None:
        _seed(self.path)
        contract_registry.apply_deltas_atomically(
            self.path,
            {"op": "add", "type": "config_policy", "owner": "billing",
             "name": "MAX", "data": {"default": "3"}})
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertIn("config_policy", data)
        self.assertTrue(any(c["name"] == "MAX" for c in data["config_policy"]))

    def test_expected_hash_mismatch_is_conflict(self) -> None:
        h = _seed(self.path)
        # someone else writes first
        contract_registry.apply_deltas_atomically(
            self.path,
            {"op": "add", "type": "config_policy", "owner": "billing",
             "name": "OTHER", "data": {"default": "1"}})
        with self.assertRaises(contract_registry.ContractConflict):
            contract_registry.apply_deltas_atomically(
                self.path,
                {"op": "add", "type": "config_policy", "owner": "billing",
                 "name": "MAX", "data": {"default": "3"}},
                expected_hash=h)

    def test_optimistic_merge_no_lost_updates(self) -> None:
        """N agents each register a distinct config_policy; with optimistic
        re-read-before-write all N must survive (no clobber)."""
        _seed(self.path)
        n = 12
        errors: list[BaseException] = []

        def worker(i: int) -> None:
            for _ in range(20):  # bounded retry on transient conflict
                try:
                    current = contract_registry._read_contracts(self.path)
                    h = contract_registry.contracts_hash(current)
                    contract_registry.apply_deltas_atomically(
                        self.path,
                        {"op": "add", "type": "config_policy", "owner": "billing",
                         "name": f"K{i}", "data": {"default": str(i)}},
                        expected_hash=h)
                    return
                except contract_registry.ContractConflict as exc:
                    errors.append(exc)
            errors.append(RuntimeError(f"agent {i} gave up"))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        data = json.loads(self.path.read_text(encoding="utf-8"))
        names = {c["name"] for c in data["config_policy"]}
        self.assertEqual({f"K{i}" for i in range(n)} & names, {f"K{i}" for i in range(n)})
        self.assertEqual(len(names), n)  # no duplicates, no loss

    def test_concurrent_stale_update_raises_one_conflict(self) -> None:
        """Two agents read the same stale hash, then both try to update the SAME
        entry with different values. Exactly one wins; the other gets a conflict
        and the stored value equals the winner's — never a torn write."""
        _seed(self.path, extra={
            "config_policy": [{"name": "MAX", "owner": "billing", "kind": "env",
                               "default": "0", "status": "active"}]})
        h = contract_registry.contracts_hash(
            contract_registry._read_contracts(self.path))

        results: list[str] = []
        lock = threading.Lock()

        def worker(value: str, tag: str) -> None:
            try:
                contract_registry.apply_deltas_atomically(
                    self.path,
                    {"op": "update", "type": "config_policy", "owner": "billing",
                     "name": "MAX", "data": {"default": value}},
                    expected_hash=h, lock=lock)
                results.append(f"ok:{tag}")
            except contract_registry.ContractConflict:
                results.append(f"conflict:{tag}")

        a = threading.Thread(target=worker, args=("3", "A"))
        b = threading.Thread(target=worker, args=("9", "B"))
        a.start()
        b.start()
        a.join()
        b.join()

        self.assertEqual(results.count("conflict:A") + results.count("conflict:B"), 1)
        self.assertEqual(results.count("ok:A") + results.count("ok:B"), 1)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        final = next(c for c in data["config_policy"] if c["name"] == "MAX")
        self.assertIn(final["default"], ("3", "9"))


if __name__ == "__main__":
    unittest.main()
