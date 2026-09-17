/** Client-side impact propagation for the contract-registry graph.
 *
 *  Mirrors `contract_registry.impact_for_roots` on the backend so hovering a
 *  contract entry can compute its downstream spread locally — no round trip, no
 *  server state. Edge directions differ and are normalized here exactly as the
 *  backend does: `depends_on` runs entry(owner A) → module B (A depends on B);
 *  `uses` runs entry(owner S) → consumer C (C depends on S).
 */

import type { RegistryEdge, RegistryNode } from "../codeintel/api";

export interface ContractImpact {
  /** The hovered entry's owner plus every module downstream of it, by depth. */
  affected: Array<{ module_id: string; depth: number }>;
}

export function impactForRoots(
  nodes: RegistryNode[],
  edges: RegistryEdge[],
  roots: string[],
): ContractImpact {
  const ownerOf = new Map<string, string>();
  for (const node of nodes) {
    if (node.id) ownerOf.set(node.id, node.owner);
  }
  const cleanRoots = Array.from(new Set(roots.filter(Boolean))).sort();

  const dependents = new Map<string, Set<string>>();
  for (const edge of edges) {
    const sourceOwner = ownerOf.get(edge.from) ?? "";
    const target = edge.to ?? "";
    if (!sourceOwner || !target || sourceOwner === target) continue;
    if (edge.kind === "depends_on") {
      const set = dependents.get(target) ?? new Set<string>();
      set.add(sourceOwner);
      dependents.set(target, set);
    } else if (edge.kind === "uses") {
      const set = dependents.get(sourceOwner) ?? new Set<string>();
      set.add(target);
      dependents.set(sourceOwner, set);
    }
  }

  const depth = new Map<string, number>();
  for (const root of cleanRoots) depth.set(root, 0);
  const queue = [...cleanRoots];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const dependent of dependents.get(current) ?? []) {
      if (!depth.has(dependent)) {
        depth.set(dependent, (depth.get(current) ?? 0) + 1);
        queue.push(dependent);
      }
    }
  }

  const affected = Array.from(depth.entries())
    .filter(([, level]) => level > 0)
    .sort((a, b) => a[1] - b[1] || a[0].localeCompare(b[0]))
    .map(([module_id, level]) => ({ module_id, depth: level }));
  return { affected };
}

/** The module set to keep visible while hovering one contract entry: its owner
 *  plus everything downstream. Everything else may dim. */
export function highlightModulesForContract(
  nodes: RegistryNode[],
  edges: RegistryEdge[],
  contractId: string | null,
): Set<string> | null {
  if (!contractId) return null;
  const node = nodes.find((item) => item.id === contractId);
  if (!node?.owner) return null;
  const impact = impactForRoots(nodes, edges, [node.owner]);
  return new Set([node.owner, ...impact.affected.map((item) => item.module_id)]);
}

/** Upstream and downstream reach of one architecture module over its own
 *  dependency edges. Both directions in one walk: the caller decides which to
 *  display. Self is always included. */
export function reachModules(
  architectureEdges: Array<{ from: string; to: string }>,
  rootId: string,
): { upstream: Set<string>; downstream: Set<string> } {
  const downstream = new Set<string>([rootId]);
  const upstream = new Set<string>([rootId]);
  let queue = [rootId];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const edge of architectureEdges) {
      if (edge.from === current && !downstream.has(edge.to)) {
        downstream.add(edge.to);
        queue.push(edge.to);
      }
    }
  }
  queue = [rootId];
  while (queue.length > 0) {
    const current = queue.shift()!;
    for (const edge of architectureEdges) {
      if (edge.to === current && !upstream.has(edge.from)) {
        upstream.add(edge.from);
        queue.push(edge.from);
      }
    }
  }
  return { upstream, downstream };
}

/** The combined highlight set for reach mode: the selected module plus its full
 *  reach. Everything else may dim while the mode is on. */
export function reachHighlightSet(
  architectureEdges: Array<{ from: string; to: string }>,
  selectedId: string | null,
): Set<string> | null {
  if (!selectedId) return null;
  const { upstream, downstream } = reachModules(architectureEdges, selectedId);
  return new Set([...upstream, ...downstream]);
}
