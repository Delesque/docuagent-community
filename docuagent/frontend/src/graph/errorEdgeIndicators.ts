/** Viewport-edge indicators for off-screen error nodes (P1-2).
 *
 *  When an error's owner node sits outside the current viewport, a directional
 *  reminder pins to the corresponding viewport edge. Pure geometry here; the
 *  React layer just renders the merged indicators.
 */

import type { Camera } from "./types";
import type { ErrorNodeSeverity } from "../api/errorNodes";

export type EdgeSide = "top" | "right" | "bottom" | "left";

export interface ViewportSize {
  width: number;
  height: number;
}

/** Camera transform is `screen = world * scale + camera.xy` (origin top-left). */
export function worldToScreen(
  x: number,
  y: number,
  camera: Camera,
): { x: number; y: number } {
  return { x: x * camera.scale + camera.x, y: y * camera.scale + camera.y };
}

/** Padding keeps the indicator fully clickable inside the viewport. */
export const EDGE_INDICATOR_PADDING = 20;

/** Nodes at least this far inside the viewport count as visible. */
const VISIBLE_MARGIN = 8;

export interface EdgeIndicator {
  side: EdgeSide;
  /** Screen-space anchor of the indicator, already inset by the padding. */
  x: number;
  y: number;
}

/** Where the off-screen reminder for one screen point pins to, or null when the
 *  point is (close enough to be) visible. Casts a ray from the viewport centre
 *  toward the point and intersects it with the inset viewport rectangle. Pure. */
export function edgeIndicatorFor(
  point: { x: number; y: number },
  viewport: ViewportSize,
): EdgeIndicator | null {
  const pad = EDGE_INDICATOR_PADDING;
  if (
    point.x >= VISIBLE_MARGIN &&
    point.x <= viewport.width - VISIBLE_MARGIN &&
    point.y >= VISIBLE_MARGIN &&
    point.y <= viewport.height - VISIBLE_MARGIN
  ) {
    return null;
  }
  const centerX = viewport.width / 2;
  const centerY = viewport.height / 2;
  const dx = point.x - centerX;
  const dy = point.y - centerY;

  // Ray t > 0 at which each bound is crossed; pick the smallest positive.
  let bestT = Infinity;
  let side: EdgeSide = "top";
  if (dx < 0) {
    const t = (pad - centerX) / dx; // dx negative, pad < center ⇒ t positive
    if (t < bestT) { bestT = t; side = "left"; }
  } else if (dx > 0) {
    const t = (viewport.width - pad - centerX) / dx;
    if (t < bestT) { bestT = t; side = "right"; }
  }
  if (dy < 0) {
    const t = (pad - centerY) / dy;
    if (t < bestT) { bestT = t; side = "top"; }
  } else if (dy > 0) {
    const t = (viewport.height - pad - centerY) / dy;
    if (t < bestT) { bestT = t; side = "bottom"; }
  }
  if (!Number.isFinite(bestT)) return null;

  const x = clamp(centerX + dx * bestT, pad, viewport.width - pad);
  const y = clamp(centerY + dy * bestT, pad, viewport.height - pad);
  return { side, x, y };
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export interface MergedEdgeIndicator {
  side: EdgeSide;
  x: number;
  y: number;
  count: number;
  /** Worst severity in the group; a merged reminder may not hide a critical. */
  worst: ErrorNodeSeverity;
  /** Owner ids behind the reminder, ordered critical-first for click targeting. */
  owners: string[];
}

const SEVERITY_RANK: Record<ErrorNodeSeverity, number> = {
  critical: 0,
  warning: 1,
  info: 2,
};

/** Merge one-side indicators into a single reminder with a count. Pure. */
export function mergeEdgeIndicators(
  items: Array<EdgeIndicator & { severity: ErrorNodeSeverity; owner: string }>,
): MergedEdgeIndicator[] {
  const bySide = new Map<EdgeSide, Array<EdgeIndicator & { severity: ErrorNodeSeverity; owner: string }>>();
  for (const item of items) {
    const list = bySide.get(item.side) ?? [];
    list.push(item);
    bySide.set(item.side, list);
  }
  const merged: MergedEdgeIndicator[] = [];
  for (const [side, list] of bySide) {
    if (list.length === 0) continue;
    const ordered = [...list].sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
    merged.push({
      side,
      x: list.reduce((sum, item) => sum + item.x, 0) / list.length,
      y: list.reduce((sum, item) => sum + item.y, 0) / list.length,
      count: list.length,
      worst: ordered[0]!.severity,
      owners: ordered.map((item) => item.owner),
    });
  }
  return merged;
}

export const SIDE_LABELS: Record<EdgeSide, string> = {
  top: "上方",
  right: "右侧",
  bottom: "下方",
  left: "左侧",
};
