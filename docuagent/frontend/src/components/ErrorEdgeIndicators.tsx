/** Viewport-edge reminders for off-screen error nodes (P1-2).
 *
 *  Rendered in screen space (outside the camera transform), so the reminders
 *  stay pinned to the viewport edges while the graph pans and zooms under
 *  them. Each merged reminder shows an icon, a count and a direction word —
 *  never color alone — and is a real ≥44px button with keyboard focus, so
 *  clicking (or Enter) flies the camera to the most severe owner behind it.
 */

import { useMemo } from "react";
import type { Camera } from "../graph/types";
import {
  EDGE_INDICATOR_PADDING,
  SIDE_LABELS,
  edgeIndicatorFor,
  mergeEdgeIndicators,
  worldToScreen,
  type ViewportSize,
} from "../graph/errorEdgeIndicators";
import { ERROR_SEVERITY_COLORS } from "./ErrorNodeOverlay";
import type { ErrorNode } from "../api/errorNodes";

interface ErrorEdgeIndicatorsProps {
  errorNodes: ErrorNode[];
  /** Graph-coordinate owner boxes (same source the badge overlay uses). */
  moduleBoxes: Record<string, { x: number; y: number; width: number; height: number }>;
  camera: Camera;
  viewport: ViewportSize;
  /** Fly the camera to this owner node. */
  onFocusOwner: (ownerNodeId: string) => void;
}

/** World-coordinate fallback for owners without a graph node; must match the
 *  badge overlay's stacking so both views agree on where "project" errors live. */
function fallbackOrigin(index: number): { x: number; y: number } {
  return { x: 24, y: 24 + index * 44 };
}

export function ErrorEdgeIndicators({
  errorNodes,
  moduleBoxes,
  camera,
  viewport,
  onFocusOwner,
}: ErrorEdgeIndicatorsProps) {
  const reminders = useMemo(() => {
    const items: Array<
      ReturnType<typeof edgeIndicatorFor> & {
        severity: ErrorNode["severity"];
        owner: string;
      }
    > = [];
    let fallbackIndex = 0;
    for (const node of errorNodes) {
      if (node.status !== "active") continue;
      const box = moduleBoxes[node.owner_node_id];
      const origin = box
        ? { x: box.x, y: box.y }
        : fallbackOrigin(fallbackIndex++);
      const screen = worldToScreen(origin.x, origin.y, camera);
      const indicator = edgeIndicatorFor(screen, viewport);
      if (indicator) {
        items.push({ ...indicator, severity: node.severity, owner: node.owner_node_id });
      }
    }
    return mergeEdgeIndicators(items);
  }, [errorNodes, moduleBoxes, camera, viewport]);

  if (reminders.length === 0) return null;

  return (
    <>
      {reminders.map((reminder) => {
        const color = ERROR_SEVERITY_COLORS[reminder.worst];
        const style: React.CSSProperties =
          reminder.side === "top"
            ? { top: EDGE_INDICATOR_PADDING, left: reminder.x, transform: "translate(-50%, 0)" }
            : reminder.side === "bottom"
              ? { bottom: EDGE_INDICATOR_PADDING, left: reminder.x, transform: "translate(-50%, 0)" }
              : reminder.side === "left"
                ? { left: EDGE_INDICATOR_PADDING, top: reminder.y, transform: "translate(0, -50%)" }
                : { right: EDGE_INDICATOR_PADDING, top: reminder.y, transform: "translate(0, -50%)" };
        return (
          <button
            key={`err-edge-${reminder.side}`}
            type="button"
            className="absolute z-50 flex min-h-[44px] items-center gap-1.5 rounded-full border px-3 font-mono text-[11px] leading-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
            style={{
              ...style,
              borderColor: color,
              background: "rgba(17, 22, 29, 0.95)",
              color,
              cursor: "pointer",
            }}
            aria-label={`视口${SIDE_LABELS[reminder.side]}有 ${reminder.count} 个错误，点击定位`}
            onClick={() => {
              const owner = reminder.owners[0];
              if (owner) onFocusOwner(owner);
            }}
          >
            <span aria-hidden>⚠</span>
            <span>{reminder.count}</span>
            <span>{SIDE_LABELS[reminder.side]}</span>
          </button>
        );
      })}
    </>
  );
}
