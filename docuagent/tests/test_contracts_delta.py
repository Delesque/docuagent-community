"""contracts_delta: classified diff of two contract registries.

Same four-way classification as architecture_delta, same critical property:
presentation-only differences (source_ref / source_hash / last_seen) classify as
`moved` and must never flood the semantic change channel — a concurrent reconcile
that merely re-locates an unchanged contract is not a design change.
"""

import unittest

import contract_registry


def registry(core_symbol: str = "score", core_status: str | None = None,
             core_ref: str = "src/core/score.py", api_deps: list[str] | None = None) -> dict:
    exports: list[dict] = [
        {
            "id": "core-score",
            "symbol": core_symbol,
            "kind": "function",
            "source_ref": core_ref,
            "line": 10,
        }
    ]
    if core_status:
        exports[0]["status"] = core_status
    return {
        "modules": [
            {"id": "core", "path": "src/core", "depends_on": [], "exports": exports},
            {"id": "api", "path": "src/api", "depends_on": api_deps or [], "exports": []},
        ],
        "shared_kernel": [],
        "commands": [],
        "vocabulary": [],
        "data_schema": [],
        "config_policy": [],
    }


def by_kind(delta: dict, kind: str) -> list[dict]:
    return [change for change in delta["changes"] if change["kind"] == kind]


class ContractsDeltaTest(unittest.TestCase):
    def test_identical_registries_produce_zero_counts(self) -> None:
        delta = contract_registry.contracts_delta(registry(), registry())
        self.assertEqual(
            {"added": 0, "removed": 0, "changed": 0, "moved": 0}, delta["counts"]
        )
        self.assertEqual([], delta["changes"])

    def test_key_order_and_list_order_do_not_matter(self) -> None:
        before = registry()
        after = registry()
        after["modules"] = list(reversed(after["modules"]))
        after["shared_kernel"], after["commands"] = after["commands"], after["shared_kernel"]

        delta = contract_registry.contracts_delta(before, after)

        self.assertEqual(
            {"added": 0, "removed": 0, "changed": 0, "moved": 0}, delta["counts"]
        )

    def test_added_and_removed_entries(self) -> None:
        after = registry()
        after["commands"] = [
            {"id": "cmd-build", "owner": "core", "symbol": "build", "kind": "cli"}
        ]

        delta = contract_registry.contracts_delta(registry(), after)

        added = by_kind(delta, "added")
        self.assertEqual(1, len(added))
        self.assertEqual({"type": "contract", "id": "cmd-build", "owner": "core"}, added[0]["subject"])
        self.assertIn("build", added[0]["message"])

    def test_signature_change_is_changed(self) -> None:
        before = registry(core_symbol="score")
        after = registry(core_symbol="score_v2")

        delta = contract_registry.contracts_delta(before, after)

        changed = by_kind(delta, "changed")
        self.assertEqual(1, len(changed))
        self.assertEqual(["symbol"], changed[0]["fields"])
        self.assertIn("符号", changed[0]["message"])

    def test_source_ref_only_change_is_moved_not_changed(self) -> None:
        before = registry()
        after = registry(core_ref="src/core/domain/score.py")

        delta = contract_registry.contracts_delta(before, after)

        self.assertEqual([], by_kind(delta, "changed"))
        moved = by_kind(delta, "moved")
        self.assertEqual(1, len(moved))
        self.assertEqual(["source_ref"], moved[0]["fields"])
        self.assertIn("仅位置调整", moved[0]["message"])

    def test_stale_status_change_is_semantic(self) -> None:
        before = registry()
        after = registry(core_status="stale")

        delta = contract_registry.contracts_delta(before, after)

        changed = by_kind(delta, "changed")
        self.assertEqual(1, len(changed))
        self.assertIn("status", changed[0]["fields"])

    def test_dependency_change_is_semantic_at_module_level(self) -> None:
        after = registry(api_deps=["core"])

        delta = contract_registry.contracts_delta(registry(), after)

        changed = by_kind(delta, "changed")
        self.assertEqual(1, len(changed))
        self.assertEqual({"type": "module", "id": "api"}, changed[0]["subject"])
        self.assertEqual(["depends_on"], changed[0]["fields"])

    def test_format_lines(self) -> None:
        after = registry(api_deps=["core"])
        lines = contract_registry.format_contracts_delta(
            contract_registry.contracts_delta(registry(), after)
        )
        self.assertEqual(["模块 api 更新：依赖。"], lines)


if __name__ == "__main__":
    unittest.main()
