/** Rendered-geometry quality gate (B3).
 *
 *  Semantic lint asks "is the graph well-formed?"; this asks the harder question
 *  "does the picture we actually draw lie?" Two checks, both pure geometry over
 *  the routed polylines — no DOM, no rendering, so they run in vitest:
 *
 *  1. 标签压线 — an edge label box sits too close to another edge's route, so the
 *     reader attributes the label to the wrong line.
 *  2. 视觉假分支 — two *unrelated* edges share a straight lane for 8px or more and
 *     read as a merge/join that does not exist.
 *
 *  This is a gate, not a fixer: it reports, the human repositions (or tunes a
 *  layout parameter). Auto-re-routing is explicitly out of scope — Archify rejects
 *  auto-layout for the same reason we do: the layout is the user's artifact.
 */

import { samplePathPoints } from "./layout";
import type { RoutedEdge } from "./layout";

export interface Point {
  x: number;
  y: number;
}

export interface AuditSegment {
  edgeId: string;
  from: string;
  to: string;
  a: Point;
  b: Point;
  /** Orthogonal lanes are what the false-branch check looks at. */
  axis: "horizontal" | "vertical" | "diagonal";
}

export interface GeometryViolation {
  rule: "label-clearance" | "false-branch";
  severity: "error" | "warning" | "info";
  message: string;
  /** Edge ids involved. */
  subjects: string[];
  evidence: Record<string, number | string>;
}

/** EdgeLayer draws its hover label as a 60x18 rect centred on the midpoint. The
 *  audit uses the same box even for unhovered edges — worst case, because a label
 *  only appears on hover and must be legible whenever it does. */
export const LABEL_BOX = { width: 60, height: 18 };

/** Distance thresholds in graph units. Overlap is an outright error (the label
 *  sits on the line); a hairline gap is a warning; a near miss is informational
 *  and is what catches layouts that degrade as the graph grows. */
export const CLEARANCE_THRESHOLDS = { error: 0, warning: 2, info: 4 };

/** Two unrelated edges sharing at least this much straight lane read as a fork. */
export const FALSE_BRANCH_MIN_SHARED = 8;

export interface LabelBox {
  edgeId: string;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export function segmentsOf(edges: RoutedEdge[]): AuditSegment[] {
  const segments: AuditSegment[] = [];
  for (const edge of edges) {
    const points = samplePathPoints(edge.path);
    for (let index = 1; index < points.length; index += 1) {
      const a = points[index - 1]!;
      const b = points[index]!;
      const dx = Math.abs(b.x - a.x);
      const dy = Math.abs(b.y - a.y);
      segments.push({
        edgeId: edge.id,
        from: edge.from,
        to: edge.to,
        a,
        b,
        axis: dx < 0.5 && dy >= 0.5 ? "vertical" : dy < 0.5 && dx >= 0.5 ? "horizontal" : "diagonal",
      });
    }
  }
  return segments;
}

export function labelBoxes(edges: RoutedEdge[]): LabelBox[] {
  return edges.map((edge) => ({
    edgeId: edge.id,
    left: edge.midpoint.x - LABEL_BOX.width / 2,
    right: edge.midpoint.x + LABEL_BOX.width / 2,
    top: edge.midpoint.y - LABEL_BOX.height / 2,
    bottom: edge.midpoint.y + LABEL_BOX.height / 2,
  }));
}

function distancePointToSegment(point: Point, a: Point, b: Point): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return Math.hypot(point.x - a.x, point.y - a.y);
  let t = ((point.x - a.x) * dx + (point.y - a.y) * dy) / lengthSquared;
  t = Math.max(0, Math.min(1, t));
  return Math.hypot(point.x - (a.x + t * dx), point.y - (a.y + t * dy));
}

function distanceSegmentToSegment(p1: Point, p2: Point, q1: Point, q2: Point): number {
  return Math.min(
    distancePointToSegment(p1, q1, q2),
    distancePointToSegment(p2, q1, q2),
    distancePointToSegment(q1, p1, p2),
    distancePointToSegment(q2, p1, p2),
  );
}

function segmentDistanceToBox(segment: AuditSegment, box: LabelBox): number {
  const corners: Point[] = [
    { x: box.left, y: box.top },
    { x: box.right, y: box.top },
    { x: box.right, y: box.bottom },
    { x: box.left, y: box.bottom },
  ];
  const edges: Array<[Point, Point]> = [
    [corners[0]!, corners[1]!],
    [corners[1]!, corners[2]!],
    [corners[2]!, corners[3]!],
    [corners[3]!, corners[0]!],
  ];
  let best = Infinity;
  for (const [a, b] of edges) {
    best = Math.min(best, distanceSegmentToSegment(segment.a, segment.b, a, b));
  }
  // A route running through the label is worse than any measured gap: report it
  // as -1 so it sorts as an overlap rather than as "1px away".
  const inside =
    Math.max(segment.a.x, segment.b.x) >= box.left &&
    Math.min(segment.a.x, segment.b.x) <= box.right &&
    Math.max(segment.a.y, segment.b.y) >= box.top &&
    Math.min(segment.a.y, segment.b.y) <= box.bottom;
  return inside ? -1 : best;
}

export function auditLabelClearance(edges: RoutedEdge[]): GeometryViolation[] {
  const segments = segmentsOf(edges);
  const boxes = labelBoxes(edges);
  const violations: GeometryViolation[] = [];
  for (const box of boxes) {
    let closest: { distance: number; edgeId: string } | null = null;
    for (const segment of segments) {
      if (segment.edgeId === box.edgeId) continue;
      const distance = segmentDistanceToBox(segment, box);
      if (closest === null || distance < closest.distance) {
        closest = { distance, edgeId: segment.edgeId };
      }
    }
    if (!closest) continue;
    const severity =
      closest.distance <= CLEARANCE_THRESHOLDS.error
        ? "error"
        : closest.distance < CLEARANCE_THRESHOLDS.warning
          ? "warning"
          : closest.distance < CLEARANCE_THRESHOLDS.info
            ? "info"
            : null;
    if (!severity) continue;
    violations.push({
      rule: "label-clearance",
      severity,
      message:
        severity === "error"
          ? `连线标签压在另一条连线上（${box.edgeId} 与 ${closest.edgeId}）`
          : `连线标签离另一条连线过近：${closest.distance.toFixed(1)}px（${box.edgeId} 与 ${closest.edgeId}）`,
      subjects: [box.edgeId, closest.edgeId],
      evidence: { distance: Number(closest.distance.toFixed(2)) },
    });
  }
  return violations;
}

/** Overlap length of two collinear orthogonal segments on the same lane. */
function sharedLaneLength(a: AuditSegment, b: AuditSegment): number {
  if (a.axis !== b.axis || a.axis === "diagonal") return 0;
  if (a.axis === "horizontal") {
    if (Math.abs(a.a.y - b.a.y) > 0.5) return 0;
    const overlap =
      Math.min(Math.max(a.a.x, a.b.x), Math.max(b.a.x, b.b.x)) -
      Math.max(Math.min(a.a.x, a.b.x), Math.min(b.a.x, b.b.x));
    return overlap > 0 ? overlap : 0;
  }
  if (Math.abs(a.a.x - b.a.x) > 0.5) return 0;
  const overlap =
    Math.min(Math.max(a.a.y, a.b.y), Math.max(b.a.y, b.b.y)) -
    Math.max(Math.min(a.a.y, a.b.y), Math.min(b.a.y, b.b.y));
  return overlap > 0 ? overlap : 0;
}

export function auditFalseBranches(
  edges: RoutedEdge[],
  minShared: number = FALSE_BRANCH_MIN_SHARED,
): GeometryViolation[] {
  const segments = segmentsOf(edges);
  const violations: GeometryViolation[] = [];
  for (let i = 0; i < segments.length; i += 1) {
    for (let j = i + 1; j < segments.length; j += 1) {
      const a = segments[i]!;
      const b = segments[j]!;
      if (a.edgeId === b.edgeId) continue;
      // Edges meeting at a node legitimately share the approach lane: that is a
      // real junction, not a false one.
      if (a.from === b.from || a.from === b.to || a.to === b.from || a.to === b.to) continue;
      const shared = sharedLaneLength(a, b);
      if (shared < minShared) continue;
      violations.push({
        rule: "false-branch",
        severity: "error",
        message: `两条不相关连线共享 ${Math.round(shared)}px 同一车道，看起来像分叉（${a.edgeId} 与 ${b.edgeId}）`,
        subjects: [a.edgeId, b.edgeId],
        evidence: { shared: Number(shared.toFixed(2)), axis: a.axis },
      });
    }
  }
  return violations;
}

export function auditGeometry(
  edges: RoutedEdge[],
  minShared: number = FALSE_BRANCH_MIN_SHARED,
): GeometryViolation[] {
  return [...auditLabelClearance(edges), ...auditFalseBranches(edges, minShared)];
}

export function auditErrors(violations: GeometryViolation[]): GeometryViolation[] {
  return violations.filter((violation) => violation.severity === "error");
}
