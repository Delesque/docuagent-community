import type { LaidOutNode } from "./layout";
import type { Camera } from "./types";

export interface Viewport {
  width: number;
  height: number;
}

export function visibleGraphBounds(
  camera: Camera,
  viewport: Viewport,
  margin = 240,
): { left: number; top: number; right: number; bottom: number } {
  const halfMargin = margin / camera.scale;
  return {
    left: -camera.x / camera.scale - halfMargin,
    top: -camera.y / camera.scale - halfMargin,
    right: (viewport.width - camera.x) / camera.scale + halfMargin,
    bottom: (viewport.height - camera.y) / camera.scale + halfMargin,
  };
}

/** Which module ids should actually mount DOM nodes.
 *
 *  Below the threshold every node renders (small graphs are cheap and never flicker).
 *  Above it, off-screen nodes are culled; focused/selected/hovered nodes stay mounted
 *  no matter where the camera is, so keyboard and drag gestures cannot lose their
 *  target mid-interaction. Memoized GraphNode instances with stable keys act as the
 *  node pool, so panning reuses DOM rather than recreating it.
 */
export function cullNodes(
  moduleIds: string[],
  nodes: Map<string, LaidOutNode>,
  camera: Camera,
  viewport: Viewport,
  always: Set<string> = new Set(),
  threshold = 60,
): Set<string> {
  if (moduleIds.length <= threshold) return new Set(moduleIds);
  const bounds = visibleGraphBounds(camera, viewport);
  const visible = new Set<string>();
  for (const id of moduleIds) {
    if (always.has(id)) {
      visible.add(id);
      continue;
    }
    const node = nodes.get(id);
    if (!node) continue;
    if (
      node.x + node.width >= bounds.left &&
      node.x <= bounds.right &&
      node.y + node.height >= bounds.top &&
      node.y <= bounds.bottom
    ) {
      visible.add(id);
    }
  }
  return visible;
}
