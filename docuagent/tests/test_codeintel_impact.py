"""Impact propagation for the contract-registry projection (§3 step4 groundwork).

When a registry entry goes ``stale`` — the interface it declares no longer matches
reality — every module that depends on the owner, directly or transitively, must
re-adapt. These tests pin the propagation: reverse-edge direction, depth ordering,
self-exclusion, and the no-roots case.
"""

import unittest

from codeintel.service import CodeIntelService


def nodes(*specs):
    out = []
    for node_id, owner, status in specs:
        out.append({"id": node_id, "type": "public_api", "owner": owner, "name": node_id, "status": status})
    return out


def edge(source, target, kind="depends_on"):
    return {"from": source, "to": target, "kind": kind, "label": "", "reason": ""}


class ImpactPropagationTest(unittest.TestCase):
    def test_stale_root_propagates_to_direct_and_transitive_dependents(self) -> None:
        # core is stale; api depends on core; ui depends on api; cli depends on ui.
        # The whole chain re-adapts, at increasing depth — 一路向下传播.
        ns = nodes(
            ("core-api", "core", "stale"),
            ("api-api", "api", "active"),
            ("ui-api", "ui", "active"),
            ("cli-api", "cli", "active"),
        )
        es = [
            edge("core-api", "core", "owns"),
            edge("api-api", "core"),          # api depends on core
            edge("ui-api", "api"),            # ui depends on api
            edge("cli-api", "ui"),            # cli depends on ui
        ]

        impact = CodeIntelService._impact_propagation(ns, es)

        self.assertEqual(["core"], impact["roots"])
        self.assertEqual(
            ["api", "ui", "cli"],
            [item["module_id"] for item in impact["affected"]],
        )
        self.assertEqual([1, 2, 3], [item["depth"] for item in impact["affected"]])
        self.assertEqual(3, impact["count"])

    def test_unrelated_module_stays_unaffected(self) -> None:
        # api depends on stale core; cli depends on ui, and ui depends on nothing
        # in the chain — cli must stay out of the impact set.
        ns = nodes(
            ("core-api", "core", "stale"),
            ("api-api", "api", "active"),
            ("ui-api", "ui", "active"),
            ("cli-api", "cli", "active"),
        )
        es = [
            edge("api-api", "core"),
            edge("cli-api", "ui"),
        ]

        impact = CodeIntelService._impact_propagation(ns, es)

        self.assertEqual(["api"], [item["module_id"] for item in impact["affected"]])

    def test_shared_kernel_uses_edge_counts_as_dependency(self) -> None:
        ns = nodes(
            ("kern", "kernel", "stale"),
            ("consumer-api", "consumer", "active"),
        )
        es = [
            edge("kern", "consumer", "uses"),  # consumer consumes kernel's shared entry
            edge("kern", "kernel", "owns"),
        ]

        impact = CodeIntelService._impact_propagation(ns, es)

        self.assertEqual(["consumer"], [item["module_id"] for item in impact["affected"]])

    def test_no_stale_entries_means_no_impact(self) -> None:
        ns = nodes(("a-api", "a", "active"), ("b-api", "b", "active"))
        es = [edge("a-api", "b")]

        impact = CodeIntelService._impact_propagation(ns, es)

        self.assertEqual([], impact["roots"])
        self.assertEqual([], impact["affected"])
        self.assertEqual(0, impact["count"])

    def test_changed_owner_is_not_marked_as_its_own_affected(self) -> None:
        # A module that both owns a stale entry and depends on another stale module
        # is already a root; it must not appear in its own downstream list.
        ns = nodes(
            ("a-api", "a", "stale"),
            ("b-api", "b", "stale"),
            ("x-api", "x", "active"),
        )
        es = [
            edge("b-api", "a"),  # b depends on a -> b is a root AND a dependent of a
            edge("x-api", "b"),  # x depends on b
        ]

        impact = CodeIntelService._impact_propagation(ns, es)

        self.assertEqual(["a", "b"], impact["roots"])
        self.assertEqual(["x"], [item["module_id"] for item in impact["affected"]])

    def test_self_dependency_is_ignored(self) -> None:
        ns = nodes(("a-api", "a", "stale"))
        es = [edge("a-api", "a")]  # a "depends on" itself through its own entry

        impact = CodeIntelService._impact_propagation(ns, es)

        self.assertEqual([], impact["affected"])


if __name__ == "__main__":
    unittest.main()
