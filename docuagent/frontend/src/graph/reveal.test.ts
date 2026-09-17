import { describe, expect, it } from "vitest";
import {
  REVEAL_HOLD_MS,
  REVEAL_RETREAT_MS,
  REVEAL_SCALE,
  REVEAL_TOP_FRACTION,
  beginRetreat,
  beginReveal,
  easeInOutCubic,
  finishReveal,
  holdElapsed,
  idleReveal,
  lerpCamera,
  retreatProgress,
  revealCamera,
} from "./reveal";
import {
  FOCUS_ENTER_SCALE,
  FOCUS_RESTORE_SCALE,
  MAP_BAND_MAX,
  decideFocus,
} from "./zoom";
import { CONVERSATION_HEIGHT, CONVERSATION_WIDTH } from "./layout";

const VIEWPORT = { width: 1200, height: 800 };
const CONVERSATION = {
  x: 0,
  y: 0,
  width: CONVERSATION_WIDTH,
  height: CONVERSATION_HEIGHT,
};
const T0 = 5_000;

describe("where the retreat stops", () => {
  it("lands in the graph band, not the map band", () => {
    // Below MAP_BAND_MAX the nodes collapse to status dots, which would defeat the
    // point of revealing them.
    expect(REVEAL_SCALE).toBeGreaterThan(MAP_BAND_MAX);
  });

  it("lands outside the focus-entry threshold", () => {
    // The sequence hands control back to the user. If it stopped at or above the entry
    // threshold, the next wheel notch would read as re-entering the conversation.
    expect(REVEAL_SCALE).toBeLessThan(FOCUS_ENTER_SCALE);
  });

  it("hands back a camera that does not immediately re-enter focus", () => {
    const camera = revealCamera(CONVERSATION, VIEWPORT);
    const decision = decideFocus({
      scale: camera.scale,
      focusedId: null,
      hoveredId: "__conversation__",
      coverage: 1,
      intent: 10_000,
    });
    expect(decision.action).toBe("hold");
  });

  it("stops close enough that zooming out is the user's exploration step", () => {
    expect(REVEAL_SCALE).toBeGreaterThan(FOCUS_RESTORE_SCALE);
  });

  it("centers the conversation horizontally", () => {
    const camera = revealCamera(CONVERSATION, VIEWPORT);
    const centerX =
      camera.x + (CONVERSATION.x + CONVERSATION.width / 2) * camera.scale;
    expect(centerX).toBeCloseTo(VIEWPORT.width / 2);
  });

  it("puts the conversation near the top, leaving room for the graph below", () => {
    // Centering it would hide the thing the retreat exists to show.
    const camera = revealCamera(CONVERSATION, VIEWPORT);
    const topY = camera.y + CONVERSATION.y * camera.scale;
    expect(topY).toBeCloseTo(VIEWPORT.height * REVEAL_TOP_FRACTION);
    expect(topY).toBeLessThan(VIEWPORT.height / 2);
  });

  it("leaves most of the viewport for the graph", () => {
    const camera = revealCamera(CONVERSATION, VIEWPORT);
    const bottom =
      camera.y + (CONVERSATION.y + CONVERSATION.height) * camera.scale;
    const remaining = VIEWPORT.height - bottom;
    expect(remaining).toBeGreaterThan(VIEWPORT.height * 0.5);
  });

  it("clamps a scale outside the camera band", () => {
    expect(revealCamera(CONVERSATION, VIEWPORT, 99).scale).toBeLessThanOrEqual(2);
    expect(revealCamera(CONVERSATION, VIEWPORT, 0.01).scale).toBeGreaterThanOrEqual(0.3);
  });
});

describe("phase transitions", () => {
  it("starts idle and does nothing", () => {
    const state = idleReveal();
    expect(state.phase).toBe("idle");
    expect(holdElapsed(state, T0 + 10_000)).toBe(false);
  });

  it("holds before moving the camera", () => {
    // The user is mid-sentence when the architecture lands; yanking the camera
    // immediately would lose their place.
    const state = beginReveal(T0);
    expect(state.phase).toBe("holding");
    expect(holdElapsed(state, T0)).toBe(false);
    expect(holdElapsed(state, T0 + REVEAL_HOLD_MS - 1)).toBe(false);
    expect(holdElapsed(state, T0 + REVEAL_HOLD_MS)).toBe(true);
  });

  it("runs the retreat from 0 to 1", () => {
    const from = { x: 0, y: 0, scale: FOCUS_ENTER_SCALE };
    const to = revealCamera(CONVERSATION, VIEWPORT);
    const state = beginRetreat(T0, from, to);

    expect(retreatProgress(state, T0)).toBe(0);
    expect(retreatProgress(state, T0 + REVEAL_RETREAT_MS / 2)).toBeCloseTo(0.5);
    expect(retreatProgress(state, T0 + REVEAL_RETREAT_MS)).toBe(1);
  });

  it("clamps progress rather than overshooting", () => {
    const state = beginRetreat(T0, { x: 0, y: 0, scale: 2 }, { x: 0, y: 0, scale: 1 });
    expect(retreatProgress(state, T0 - 500)).toBe(0);
    expect(retreatProgress(state, T0 + REVEAL_RETREAT_MS * 4)).toBe(1);
  });

  it("reports finished once done, so the sequence never replays", () => {
    const state = finishReveal();
    expect(state.phase).toBe("done");
    expect(holdElapsed(state, T0 + 10_000)).toBe(false);
    expect(retreatProgress(state, T0 + 10_000)).toBe(1);
  });
});

/** The canvas's arming decision, extracted so its branches are testable.
 *
 *  Mirrors the effect in GraphCanvas. A race here was invisible until probed: the shell
 *  dispatches `focusConversation` in the same commit that first supplies an
 *  architecture, so reading focus on the first pass sees null and skipped the
 *  introduction permanently.
 */
function armReveal(input: {
  focusedId: string | null;
  reducedMotion: boolean;
  nodeCount: number;
}): { armed: boolean; phase: string; fits: boolean } {
  if (input.nodeCount === 0) return { armed: false, phase: "idle", fits: false };
  if (input.focusedId === null) {
    // Undecided: wait rather than committing to either path.
    return { armed: false, phase: "idle", fits: false };
  }
  if (input.reducedMotion || input.focusedId !== "__conversation__") {
    return { armed: true, phase: "done", fits: true };
  }
  return { armed: true, phase: "holding", fits: false };
}

describe("arming the introduction", () => {
  it("waits while focus is undecided", () => {
    // The bug this guards: arming on the first pass saw null and finished immediately.
    const result = armReveal({ focusedId: null, reducedMotion: false, nodeCount: 4 });
    expect(result.armed).toBe(false);
    expect(result.phase).toBe("idle");
  });

  it("plays the retreat once focus lands on the conversation", () => {
    const result = armReveal({
      focusedId: "__conversation__",
      reducedMotion: false,
      nodeCount: 4,
    });
    expect(result.phase).toBe("holding");
    // The reveal owns the camera, so no fit.
    expect(result.fits).toBe(false);
  });

  it("fits instead when the user is already elsewhere", () => {
    // Opening a finished project cold: nothing to introduce, just frame the graph.
    const result = armReveal({ focusedId: "core", reducedMotion: false, nodeCount: 4 });
    expect(result.phase).toBe("done");
    expect(result.fits).toBe(true);
  });

  it("fits rather than travelling under reduced motion", () => {
    const result = armReveal({
      focusedId: "__conversation__",
      reducedMotion: true,
      nodeCount: 4,
    });
    expect(result.phase).toBe("done");
    expect(result.fits).toBe(true);
  });

  it("does nothing before a graph exists", () => {
    const result = armReveal({
      focusedId: "__conversation__",
      reducedMotion: false,
      nodeCount: 0,
    });
    expect(result.armed).toBe(false);
  });
});

describe("camera interpolation", () => {
  const from = { x: 0, y: 0, scale: FOCUS_ENTER_SCALE };
  const to = revealCamera(CONVERSATION, VIEWPORT);

  it("starts at `from` and ends at `to`", () => {
    expect(lerpCamera(from, to, 0)).toEqual(from);
    const end = lerpCamera(from, to, 1);
    expect(end.scale).toBeCloseTo(to.scale);
    expect(end.x).toBeCloseTo(to.x);
    expect(end.y).toBeCloseTo(to.y);
  });

  it("moves monotonically toward the target", () => {
    let previous = from.scale;
    for (const t of [0.2, 0.4, 0.6, 0.8, 1]) {
      const scale = lerpCamera(from, to, t).scale;
      expect(scale).toBeLessThan(previous);
      previous = scale;
    }
  });

  it("eases in and out rather than moving linearly", () => {
    // A linear camera move reads as a jump cut with extra steps.
    expect(easeInOutCubic(0)).toBe(0);
    expect(easeInOutCubic(1)).toBe(1);
    expect(easeInOutCubic(0.5)).toBeCloseTo(0.5);
    // Slow at the start: quarter of the way through time, less than a quarter of the way
    // through the distance.
    expect(easeInOutCubic(0.25)).toBeLessThan(0.25);
    expect(easeInOutCubic(0.75)).toBeGreaterThan(0.75);
  });

  it("clamps outside the 0..1 range", () => {
    expect(lerpCamera(from, to, -1)).toEqual(lerpCamera(from, to, 0));
    expect(lerpCamera(from, to, 2).scale).toBeCloseTo(to.scale);
  });
});
