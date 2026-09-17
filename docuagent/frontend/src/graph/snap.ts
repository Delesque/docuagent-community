/** Shift-drag straight-edge snapping.
 *
 *  Each connected edge contributes a candidate y: the position at which that edge's two
 *  face anchors align and the edge can run straight. A node with several edges offers
 *  several guides; the pointer snaps to the nearest candidate that does not overlap
 *  another node. Pure functions so the candidate math is testable without a DOM.
 */

import { computeAnchorOffsets, edgeKey, type LaidOutNode } from "./layout";
import type { GraphEdge } from "./types";

export interface SnapCandidate {
  edgeId: string;
  y: number;
}

export interface SnapResult {
  x: number;
  y: number;
  snapped: boolean;
  edgeId: string | null;
  candidates: SnapCandidate[];
}

function overlapsOther(nodeId: string, y: number, nodes: Map<string, LaidOutNode>): boolean {
  const self = nodes.get(nodeId);
  if (!self) return true;
  for (const [id, node] of nodes) {
    if (id === nodeId) continue;
    if (
      self.x < node.x + node.width &&
      self.x + self.width > node.x &&
      y < node.y + node.height &&
      y + self.height > node.y
    ) {
      return true;
    }
  }
  return false;
}

function presentEdges(edges: GraphEdge[], nodes: Map<string, LaidOutNode>): GraphEdge[] {
  const known = new Set(nodes.keys());
  const seen = new Set<string>();
  const present: GraphEdge[] = [];
  for (const edge of edges) {
    if (!known.has(edge.from) || !known.has(edge.to)) continue;
    const key = edgeKey(edge);
    if (seen.has(key)) continue;
    seen.add(key);
    present.push(edge);
  }
  return present;
}

export function straightSnapCandidates(
  nodeId: string,
  edges: GraphEdge[],
  nodes: Map<string, LaidOutNode>,
): SnapCandidate[] {
  const node = nodes.get(nodeId);
  if (!node) return [];
  const present = presentEdges(edges, nodes);
  const offsets = computeAnchorOffsets(present, nodes.keys());
  const candidates: SnapCandidate[] = [];

  for (const edge of present) {
    if (edge.from === edge.to) continue;
    let neighborId: string | null = null;
    if (edge.from === nodeId) neighborId = edge.to;
    if (edge.to === nodeId) neighborId = edge.from;
    if (!neighborId) continue;
    const neighbor = nodes.get(neighborId);
    if (!neighbor) continue;
    const key = edgeKey(edge);

    let candidateY: number;
    if (node.layer === neighbor.layer) {
      candidates.push({ edgeId: key, y: neighbor.y + neighbor.height });
      candidates.push({ edgeId: key, y: neighbor.y - node.height });
      continue;
    }

    // Straight = the edge's two face anchors share a y. Anchors sit at center +
    // offset. An edge leaves the `to` node's right face (`outgoing[to]`) and enters
    // the `from` node's left face (`incoming[from]`); aligning them solves for the
    // dragged node's y.
    if (edge.to === nodeId) {
      // node is the upstream (`to`) endpoint: edge leaves node's right face and
      // enters neighbor's left face.
      const out = offsets.outgoing.get(nodeId)?.get(key) ?? 0;
      const inn = offsets.incoming.get(neighborId)?.get(key) ?? 0;
      candidateY = neighbor.y + neighbor.height / 2 + inn - out - node.height / 2;
    } else {
      // node is the downstream (`from`) endpoint: edge leaves neighbor's right face
      // and enters node's left face.
      const out = offsets.outgoing.get(neighborId)?.get(key) ?? 0;
      const inn = offsets.incoming.get(nodeId)?.get(key) ?? 0;
      candidateY = neighbor.y + neighbor.height / 2 + out - inn - node.height / 2;
    }
    candidates.push({ edgeId: key, y: candidateY });
  }

  return candidates.filter(
    (candidate) => !overlapsOther(nodeId, candidate.y, nodes),
  );
}

export function snapPosition(
  nodeId: string,
  x: number,
  y: number,
  edges: GraphEdge[],
  nodes: Map<string, LaidOutNode>,
  threshold = 18,
): SnapResult {
  const candidates = straightSnapCandidates(nodeId, edges, nodes);
  if (candidates.length === 0) {
    return { x, y, snapped: false, edgeId: null, candidates };
  }
  const closest = [...candidates].sort((a, b) =>
    Math.abs(a.y - y) === Math.abs(b.y - y)
      ? a.edgeId.localeCompare(b.edgeId)
      : Math.abs(a.y - y) - Math.abs(b.y - y),
  )[0]!;
  if (Math.abs(closest.y - y) <= threshold) {
    return { x, y: closest.y, snapped: true, edgeId: closest.edgeId, candidates };
  }
  return { x, y, snapped: false, edgeId: null, candidates };
}
