/** All edges render into one SVG element rather than one per edge.
 *
 *  Type is encoded three ways at once — color, stroke pattern, and a hover label —
 *  because roughly 8% of men cannot reliably separate the red and green ends of a
 *  palette, and a graph whose semantics live only in hue is unreadable to them.
 */

import { memo } from "react";
import { EDGE_STYLES } from "../graph/types";
import type { RoutedEdge } from "../graph/layout";

interface EdgeLayerProps {
  edges: RoutedEdge[];
  width: number;
  height: number;
  selectedEdgeId: string | null;
  hoveredEdgeId: string | null;
  highlightedNodeId: string | null;
  /** Module-id set from contract-impact hover: edges touching these modules stay
   *  lit, everything else temporarily dims. Null restores the neutral state. */
  highlightedModuleIds?: Set<string> | null;
  onSelect: (id: string | null) => void;
  onHover: (id: string | null) => void;
  /** Draw-in progress per edge, 0 to 1. Absent or 1 means fully drawn. */
  drawProgress?: (edgeId: string) => number;
}

const PADDING = 400;

export const EdgeLayer = memo(function EdgeLayer({
  edges,
  width,
  height,
  selectedEdgeId,
  hoveredEdgeId,
  highlightedNodeId,
  highlightedModuleIds = null,
  onSelect,
  onHover,
  drawProgress,
}: EdgeLayerProps) {
  return (
    <svg
      aria-hidden
      className="pointer-events-none absolute overflow-visible"
      style={{ left: -PADDING, top: -PADDING }}
      width={width + PADDING * 2}
      height={height + PADDING * 2}
    >
      <defs>
        {Object.entries(EDGE_STYLES).map(([kind, style]) => (
          <marker
            key={kind}
            id={`arrow-${kind}`}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M 0 1 L 7 4 L 0 7 z" fill={style.color} />
          </marker>
        ))}
      </defs>
      <g transform={`translate(${PADDING}, ${PADDING})`}>
        {edges.map((edge) => {
          const style = EDGE_STYLES[edge.kind];
          const active = edge.id === selectedEdgeId || edge.id === hoveredEdgeId;
          const nodeConnected =
            highlightedNodeId !== null &&
            (edge.from === highlightedNodeId || edge.to === highlightedNodeId);
          const moduleConnected =
            highlightedModuleIds !== null &&
            (highlightedModuleIds.has(edge.from) || highlightedModuleIds.has(edge.to));
          const connected = nodeConnected || moduleConnected === true;
          const dimmed =
            (highlightedNodeId !== null && !nodeConnected && moduleConnected !== true) ||
            (highlightedModuleIds !== null && !moduleConnected);

          // Draw-in cannot reuse strokeDasharray, which already carries the edge type.
          // `pathLength={1}` renormalizes the geometry so a dash pattern expressed in
          // those units is independent of the real path length, and the arrowhead is
          // withheld until the line reaches its target.
          const progress = drawProgress ? drawProgress(edge.id) : 1;
          const drawing = progress < 1;

          return (
            <g
              key={edge.id}
              className="pointer-events-auto cursor-pointer"
              onPointerEnter={() => onHover(edge.id)}
              onPointerLeave={() => onHover(null)}
              onPointerDown={(event) => {
                event.stopPropagation();
                onSelect(edge.id);
              }}
            >
              {/* Invisible fat stroke so thin dashed edges are still clickable. */}
              <path d={edge.path} stroke="transparent" strokeWidth={14} fill="none" />
              <path
                d={edge.path}
                stroke={style.color}
                strokeWidth={active ? 2.4 : style.doubled ? 1 : 1.5}
                strokeLinecap="round"
                fill="none"
                opacity={dimmed ? 0.18 : active ? 1 : connected ? 0.95 : 0.62}
                {...(drawing
                  ? {
                      pathLength: 1,
                      strokeDasharray: 1,
                      strokeDashoffset: 1 - progress,
                    }
                  : {
                      strokeDasharray: style.dash,
                      markerEnd: `url(#arrow-${edge.kind})`,
                    })}
              />
              {style.doubled ? (
                <path
                  d={edge.path}
                  stroke={style.color}
                  strokeWidth={1}
                  fill="none"
                  opacity={dimmed ? 0.12 : 0.5}
                  transform="translate(0, 3)"
                />
              ) : null}
              {active ? (
                <g transform={`translate(${edge.midpoint.x}, ${edge.midpoint.y})`}>
                  <rect
                    x={-30}
                    y={-9}
                    width={60}
                    height={18}
                    rx={4}
                    fill="#0E1119"
                    stroke={style.color}
                    strokeWidth={0.75}
                    opacity={0.96}
                  />
                  <text
                    x={0}
                    y={4}
                    textAnchor="middle"
                    fill="#E9FFF1"
                    fontSize={9.5}
                    fontFamily="'Cascadia Code', 'SFMono-Regular', Consolas, monospace"
                  >
                    {edge.label || style.label}
                  </text>
                </g>
              ) : null}
            </g>
          );
        })}
      </g>
    </svg>
  );
});
