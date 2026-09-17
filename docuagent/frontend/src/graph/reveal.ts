/** The camera move that introduces the graph.
 *
 *  When an architecture first arrives the user is reading the conversation, full-screen.
 *  Dropping them onto a 40-node canvas would lose their place; leaving them in the
 *  conversation would hide the thing that was just built. So the camera retreats a
 *  little on its own: the conversation shrinks into its node, the first modules and
 *  edges come into view below it, and the camera stops — the user zooms the rest of the
 *  way themselves if they want to.
 *
 *  This is deliberately NOT streaming. The architecture arrives as one JSON response;
 *  the animation is a presentation choice layered on top, so nothing here needs the
 *  model to emit incremental events.
 *
 *  Pure functions for the usual reason: the phase transitions and the target camera are
 *  what need assertions, and neither needs a DOM. `reveal.test.ts` covers the property
 *  that matters — that the retreat leaves the camera outside the focus-entry threshold,
 *  so the sequence cannot hand back a camera that immediately re-enters focus.
 */

import type { Camera } from "./types";
import { clampScale } from "./zoom";

/** How long the conversation stays full-screen before the camera moves. Long enough to
 *  finish reading a short reply, short enough not to feel stuck. */
export const REVEAL_HOLD_MS = 850;
export const REVEAL_RETREAT_MS = 1150;

/** Where the retreat stops. Close enough that only the first row of blocks and its
 *  edges peek out below the conversation; the rest is for the user to explore by
 *  zooming out. Still below FOCUS_ENTER_SCALE so the camera cannot re-enter focus. */
export const REVEAL_SCALE = 1.55;

/** Fraction of the viewport height the conversation node's top sits at when the retreat
 *  finishes. Not centered: the point is to leave room below it for the graph. */
export const REVEAL_TOP_FRACTION = 0.12;

export type RevealPhase = "idle" | "holding" | "retreating" | "done";

export interface RevealState {
  phase: RevealPhase;
  /** When the current phase began. */
  at: number;
  from: Camera | null;
  to: Camera | null;
}

export function idleReveal(): RevealState {
  return { phase: "idle", at: 0, from: null, to: null };
}

export function beginReveal(now: number): RevealState {
  return { phase: "holding", at: now, from: null, to: null };
}

/** Start the camera move. Called once the hold has elapsed and the layout is known. */
export function beginRetreat(now: number, from: Camera, to: Camera): RevealState {
  return { phase: "retreating", at: now, from, to };
}

export function finishReveal(): RevealState {
  return { phase: "done", at: 0, from: null, to: null };
}

/** True once the hold has elapsed and the camera should start moving. */
export function holdElapsed(state: RevealState, now: number): boolean {
  return state.phase === "holding" && now - state.at >= REVEAL_HOLD_MS;
}

/** Retreat progress, 0 to 1. */
export function retreatProgress(state: RevealState, now: number): number {
  if (state.phase !== "retreating") return 1;
  const t = (now - state.at) / REVEAL_RETREAT_MS;
  return t < 0 ? 0 : t > 1 ? 1 : t;
}

/** Ease-in-out so the camera starts and stops gently. A linear camera move reads as a
 *  jump-cut with extra steps. */
export function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
}

export function lerpCamera(from: Camera, to: Camera, t: number): Camera {
  const eased = easeInOutCubic(t < 0 ? 0 : t > 1 ? 1 : t);
  return {
    x: from.x + (to.x - from.x) * eased,
    y: from.y + (to.y - from.y) * eased,
    scale: from.scale + (to.scale - from.scale) * eased,
  };
}

/** Where the retreat ends: conversation node horizontally centered, near the top, with
 *  the graph visible below it. */
export function revealCamera(
  conversation: { x: number; y: number; width: number; height: number },
  viewport: { width: number; height: number },
  scale: number = REVEAL_SCALE,
): Camera {
  const bounded = clampScale(scale);
  return {
    scale: bounded,
    x: viewport.width / 2 - (conversation.x + conversation.width / 2) * bounded,
    y: viewport.height * REVEAL_TOP_FRACTION - conversation.y * bounded,
  };
}
