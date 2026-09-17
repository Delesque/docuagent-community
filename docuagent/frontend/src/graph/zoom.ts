/** Camera math and semantic-zoom band logic.
 *
 *  Kept as pure functions so the hysteresis rules are unit-testable without a DOM,
 *  and so the focus-entry decision has exactly one implementation shared by the
 *  canvas, the keyboard path, and the Outline.
 */

import type { Camera } from "./types";

export const MIN_SCALE = 0.3;
export const MAX_SCALE = 2.0;

/** Schmitt trigger thresholds. A plain dead zone still chatters at the boundary, so
 *  entry and exit use different thresholds and the span between them holds state. */
export const FOCUS_ENTER_SCALE = 1.8;
export const FOCUS_EXIT_SCALE = 1.3;
/** Exiting lands here: outside the entry threshold, so releasing the gesture cannot
 *  immediately re-enter. */
export const FOCUS_RESTORE_SCALE = 1.25;

export const MAP_BAND_MAX = 0.75;

/** Intent gating for focus entry. Without these, a single flick of the wheel
 *  teleports the user into a node they were only passing over. */
export const INTENT_WINDOW_MS = 300;
export const INTENT_MIN_DELTA = 40;

/** Fraction of the viewport the hovered node must cover before entry is allowed.
 *
 *  The requirements document says 0.25, which is unreachable: a 208x116 module covers
 *  10.1% of a 1200x800 viewport even at MAX_SCALE, and the 420x132 conversation node
 *  reaches 23.1%. With 0.25 the gate could never open at any legal scale, so zoom-to-
 *  enter silently did nothing and only double-click worked. The node dimensions are the
 *  later decision, so the threshold is what was stale.
 *
 *  0.02 still separates map-band browsing from deliberate entry, while keeping every
 *  module reachable once the camera is at the 90% focus threshold. `focus.test.ts` pins
 *  the reachability property rather than this number, so resizing a node breaks the
 *  test instead of the feature.
 */
export const INTENT_MIN_COVERAGE = 0.02;

export type ZoomBand = "map" | "graph";

export function clampScale(scale: number): number {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, scale));
}

export function bandFor(scale: number): ZoomBand {
  return scale < MAP_BAND_MAX ? "map" : "graph";
}

/** Visible tail lines of streamed output, interpolated so nothing steps.
 *  Inner content is inverse-scaled to hold a constant physical font size, so a growing
 *  box reveals more lines instead of magnifying the same lines. */
export function tailLineCount(scale: number): number {
  return Math.max(0, Math.min(14, Math.floor((scale - 0.55) * 11)));
}

/** Signature opacity fades out as code fades in, across the same range. */
export function signatureOpacity(scale: number): number {
  if (scale <= 0.75) return 1;
  if (scale >= 1.3) return 0;
  return 1 - (scale - 0.75) / (1.3 - 0.75);
}

export function codeOpacity(scale: number): number {
  if (scale <= 0.8) return 0;
  if (scale >= 1.15) return 1;
  return (scale - 0.8) / (1.15 - 0.8);
}

/** Trackpad pinch arrives as a ctrlKey wheel event too, with a much finer delta than a
 *  mouse wheel, so the two need separate gain or pinch becomes unusable. */
export function scaleStepFor(deltaY: number, deltaMode: number): number {
  if (deltaMode !== 0) return Math.exp(-deltaY * 0.08);
  const magnitude = Math.abs(deltaY);
  const gain = magnitude < 12 ? 0.012 : 0.0022;
  return Math.exp(-deltaY * gain);
}

/** Zoom about the cursor: the graph point under the pointer must not move. */
export function zoomAtPoint(
  camera: Camera,
  nextScale: number,
  pointer: { x: number; y: number },
): Camera {
  const scale = clampScale(nextScale);
  const graphX = (pointer.x - camera.x) / camera.scale;
  const graphY = (pointer.y - camera.y) / camera.scale;
  return {
    scale,
    x: pointer.x - graphX * scale,
    y: pointer.y - graphY * scale,
  };
}

export function screenToGraph(camera: Camera, point: { x: number; y: number }) {
  return { x: (point.x - camera.x) / camera.scale, y: (point.y - camera.y) / camera.scale };
}

export interface IntentSample {
  at: number;
  delta: number;
}

/** Accumulated upward wheel travel inside the intent window. */
export function accumulatedIntent(samples: IntentSample[], now: number): number {
  return samples
    .filter((sample) => now - sample.at <= INTENT_WINDOW_MS && sample.delta < 0)
    .reduce((total, sample) => total + Math.abs(sample.delta), 0);
}

export function pruneIntent(samples: IntentSample[], now: number): IntentSample[] {
  return samples.filter((sample) => now - sample.at <= INTENT_WINDOW_MS);
}

export interface FocusDecisionInput {
  scale: number;
  focusedId: string | null;
  hoveredId: string | null;
  /** Fraction of the viewport the hovered node covers at the current scale. */
  coverage: number;
  intent: number;
  reducedMotion?: boolean;
}

export type FocusDecision =
  | { action: "enter"; id: string }
  | { action: "exit" }
  | { action: "hold" };

/** The one place focus mode is decided.
 *
 *  Entry needs all of: past the upper threshold, a node under the cursor, that node
 *  covering enough of the viewport, and enough accumulated wheel travel. Exit needs
 *  only the lower threshold, because getting out must never feel sticky.
 */
export function decideFocus(input: FocusDecisionInput): FocusDecision {
  if (input.focusedId) {
    return input.scale <= FOCUS_EXIT_SCALE ? { action: "exit" } : { action: "hold" };
  }
  if (input.scale < FOCUS_ENTER_SCALE) return { action: "hold" };
  if (!input.hoveredId) return { action: "hold" };
  if (input.coverage < INTENT_MIN_COVERAGE) return { action: "hold" };
  if (input.intent < INTENT_MIN_DELTA) return { action: "hold" };
  return { action: "enter", id: input.hoveredId };
}

/** Viewport coverage of a node at a given scale, used by the intent gate. */
export function nodeCoverage(
  node: { width: number; height: number },
  scale: number,
  viewport: { width: number; height: number },
): number {
  const area = viewport.width * viewport.height;
  if (area <= 0) return 0;
  return (node.width * scale * (node.height * scale)) / area;
}

/** Fit the whole graph into the viewport with margin, for the "fit" command. */
export function fitCamera(
  bounds: { width: number; height: number },
  viewport: { width: number; height: number },
  margin = 64,
): Camera {
  if (bounds.width <= 0 || bounds.height <= 0) return { x: 0, y: 0, scale: 1 };
  const scale = clampScale(
    Math.min(
      (viewport.width - margin * 2) / bounds.width,
      (viewport.height - margin * 2) / bounds.height,
      1.2,
    ),
  );
  return {
    scale,
    x: (viewport.width - bounds.width * scale) / 2,
    y: (viewport.height - bounds.height * scale) / 2,
  };
}

/** Center a specific node, used by Outline navigation and window-bar jumps so the
 *  keyboard path has a real equivalent to pointing at a node. */
export function centerOn(
  node: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number },
  scale: number,
): Camera {
  const bounded = clampScale(scale);
  return {
    scale: bounded,
    x: viewport.width / 2 - (node.x + node.width / 2) * bounded,
    y: viewport.height / 2 - (node.y + node.height / 2) * bounded,
  };
}
