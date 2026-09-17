/** Overview of the whole graph with the current viewport marked.
 *
 *  Necessary once panning exists: at graph scale a 40-node layout is several viewports
 *  wide, and without this the user has no way to know what is off-screen.
 */

import { memo } from "react";
import type { LayoutResult } from "../graph/layout";
import { EDGE_STYLES, type Camera } from "../graph/types";

interface MinimapProps {
  layout: LayoutResult;
  camera: Camera;
  viewport: { width: number; height: number };
  selectedNodeId: string | null;
  onJump: (point: { x: number; y: number }) => void;
}

const WIDTH = 168;
const HEIGHT = 112;

export const Minimap = memo(function Minimap({
  layout,
  camera,
  viewport,
  selectedNodeId,
  onJump,
}: MinimapProps) {
  if (layout.nodes.size === 0) return null;

  const padding = 24;
  const scale = Math.min(
    (WIDTH - padding) / Math.max(layout.width, 1),
    (HEIGHT - padding) / Math.max(layout.height, 1),
  );
  const offsetX = (WIDTH - layout.width * scale) / 2;
  const offsetY = (HEIGHT - layout.height * scale) / 2;

  const view = {
    x: (-camera.x / camera.scale) * scale + offsetX,
    y: (-camera.y / camera.scale) * scale + offsetY,
    width: (viewport.width / camera.scale) * scale,
    height: (viewport.height / camera.scale) * scale,
  };

  return (
    <div
      className="absolute bottom-3 right-3 overflow-hidden rounded border border-ink-ghost bg-paper-raise/92"
      style={{ width: WIDTH, height: HEIGHT }}
    >
      <svg
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label="架构缩略图"
        className="cursor-crosshair"
        onPointerDown={(event) => {
          const rect = event.currentTarget.getBoundingClientRect();
          onJump({
            x: (event.clientX - rect.left - offsetX) / scale,
            y: (event.clientY - rect.top - offsetY) / scale,
          });
        }}
      >
        <g transform={`translate(${offsetX}, ${offsetY}) scale(${scale})`}>
          {layout.edges.map((edge) => (
            <path
              key={edge.id}
              d={edge.path}
              stroke={EDGE_STYLES[edge.kind].color}
              strokeWidth={1 / scale}
              fill="none"
              opacity={0.4}
            />
          ))}
          {[...layout.nodes.values()].map((node) => (
            <rect
              key={node.id}
              x={node.x}
              y={node.y}
              width={node.width}
              height={node.height}
              rx={6}
              fill={node.id === selectedNodeId ? "#FF5C63" : "#2FBF70"}
              opacity={node.id === selectedNodeId ? 0.95 : 0.55}
            />
          ))}
        </g>
        <rect
          x={view.x}
          y={view.y}
          width={view.width}
          height={view.height}
          fill="rgba(228, 233, 242, 0.07)"
          stroke="rgba(228, 233, 242, 0.55)"
          strokeWidth={1}
        />
      </svg>
    </div>
  );
});
