import { describe, expect, it } from "vitest";
import {
  accumulatedIntent,
  bandFor,
  centerOn,
  clampScale,
  codeOpacity,
  decideFocus,
  fitCamera,
  FOCUS_ENTER_SCALE,
  FOCUS_EXIT_SCALE,
  FOCUS_RESTORE_SCALE,
  INTENT_MIN_COVERAGE,
  INTENT_MIN_DELTA,
  MAX_SCALE,
  MIN_SCALE,
  nodeCoverage,
  pruneIntent,
  scaleStepFor,
  screenToGraph,
  signatureOpacity,
  tailLineCount,
  zoomAtPoint,
} from "./zoom";

describe("scale clamping", () => {
  it("bounds scale to the configured range", () => {
    expect(clampScale(0.01)).toBe(MIN_SCALE);
    expect(clampScale(99)).toBe(MAX_SCALE);
    expect(clampScale(1.1)).toBe(1.1);
  });
});

describe("semantic zoom bands", () => {
  it("switches from map to graph at the band edge", () => {
    expect(bandFor(0.5)).toBe("map");
    expect(bandFor(0.74)).toBe("map");
    expect(bandFor(0.75)).toBe("graph");
    expect(bandFor(1.6)).toBe("graph");
  });

  it("reveals more tail lines as scale grows, never stepping backward", () => {
    let previous = -1;
    for (let scale = MIN_SCALE; scale <= MAX_SCALE; scale += 0.05) {
      const lines = tailLineCount(scale);
      expect(lines).toBeGreaterThanOrEqual(previous);
      previous = lines;
    }
  });

  it("shows no code in the map band", () => {
    expect(tailLineCount(0.4)).toBe(0);
    expect(codeOpacity(0.4)).toBe(0);
  });

  it("caps tail lines so a node cannot grow unbounded", () => {
    expect(tailLineCount(MAX_SCALE)).toBeLessThanOrEqual(14);
  });

  it("crossfades signature and code in opposite directions", () => {
    expect(signatureOpacity(0.7)).toBe(1);
    expect(signatureOpacity(1.4)).toBe(0);
    expect(codeOpacity(0.7)).toBe(0);
    expect(codeOpacity(1.4)).toBe(1);
    const mid = 1.0;
    expect(signatureOpacity(mid)).toBeGreaterThan(0);
    expect(signatureOpacity(mid)).toBeLessThan(1);
    expect(codeOpacity(mid)).toBeGreaterThan(0);
    expect(codeOpacity(mid)).toBeLessThan(1);
  });
});

describe("cursor-anchored zoom", () => {
  it("keeps the graph point under the cursor fixed", () => {
    const camera = { x: 120, y: -40, scale: 0.9 };
    const pointer = { x: 300, y: 220 };
    const before = screenToGraph(camera, pointer);

    const next = zoomAtPoint(camera, 1.6, pointer);
    const after = screenToGraph(next, pointer);

    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });

  it("still anchors when the requested scale is clamped", () => {
    const camera = { x: 0, y: 0, scale: 1.9 };
    const pointer = { x: 400, y: 300 };
    const before = screenToGraph(camera, pointer);

    const after = screenToGraph(zoomAtPoint(camera, 50, pointer), pointer);

    expect(after.x).toBeCloseTo(before.x, 6);
    expect(after.y).toBeCloseTo(before.y, 6);
  });
});

describe("wheel gain", () => {
  it("gives trackpad pinch a larger gain than a mouse wheel", () => {
    const pinch = scaleStepFor(-4, 0);
    const wheel = scaleStepFor(-100, 0);

    expect(pinch).toBeGreaterThan(1);
    expect(wheel).toBeGreaterThan(1);
    // Per unit of delta, pinch must move more or a pinch gesture feels dead.
    expect(Math.log(pinch) / 4).toBeGreaterThan(Math.log(wheel) / 100);
  });

  it("zooms out on positive delta", () => {
    expect(scaleStepFor(100, 0)).toBeLessThan(1);
  });

  it("handles line-mode deltas", () => {
    expect(scaleStepFor(-3, 1)).toBeGreaterThan(1);
  });
});

describe("focus hysteresis", () => {
  const entering = {
    scale: FOCUS_ENTER_SCALE,
    focusedId: null,
    hoveredId: "auth",
    coverage: 0.4,
    intent: INTENT_MIN_DELTA,
  };

  it("enters when every gate is satisfied", () => {
    expect(decideFocus(entering)).toEqual({ action: "enter", id: "auth" });
  });

  it("never oscillates strictly inside the hysteresis band", () => {
    // The band is open at the bottom: at exactly FOCUS_EXIT_SCALE, exiting is correct.
    for (let scale = FOCUS_EXIT_SCALE + 0.01; scale < FOCUS_ENTER_SCALE; scale += 0.05) {
      expect(decideFocus({ ...entering, scale }).action).toBe("hold");
      expect(
        decideFocus({ ...entering, scale, focusedId: "auth" }).action,
      ).toBe("hold");
    }
  });

  it("treats the exit threshold itself as an exit", () => {
    expect(
      decideFocus({ ...entering, scale: FOCUS_EXIT_SCALE, focusedId: "auth" }).action,
    ).toBe("exit");
  });

  it("restores to a scale outside the entry threshold", () => {
    expect(FOCUS_RESTORE_SCALE).toBeLessThan(FOCUS_ENTER_SCALE);
    expect(decideFocus({ ...entering, scale: FOCUS_RESTORE_SCALE }).action).toBe("hold");
  });

  it("refuses entry on a flick with insufficient accumulated travel", () => {
    expect(decideFocus({ ...entering, intent: INTENT_MIN_DELTA - 1 }).action).toBe("hold");
  });

  it("refuses entry when the node is too small on screen", () => {
    expect(
      decideFocus({ ...entering, coverage: INTENT_MIN_COVERAGE - 0.01 }).action,
    ).toBe("hold");
  });

  it("refuses entry with no node under the cursor", () => {
    expect(decideFocus({ ...entering, hoveredId: null }).action).toBe("hold");
  });

  it("exits at the lower threshold without needing intent", () => {
    expect(
      decideFocus({
        scale: FOCUS_EXIT_SCALE,
        focusedId: "auth",
        hoveredId: null,
        coverage: 0,
        intent: 0,
      }),
    ).toEqual({ action: "exit" });
  });

  it("holds focus while still above the exit threshold", () => {
    expect(
      decideFocus({
        scale: 1.4,
        focusedId: "auth",
        hoveredId: null,
        coverage: 0,
        intent: 0,
      }).action,
    ).toBe("hold");
  });
});

describe("intent accumulation", () => {
  it("counts only upward travel inside the window", () => {
    const now = 1000;
    const samples = [
      { at: 990, delta: -60 },
      { at: 995, delta: -60 },
      { at: 999, delta: 200 },
      { at: 100, delta: -500 },
    ];

    expect(accumulatedIntent(samples, now)).toBe(120);
  });

  it("prunes samples older than the window", () => {
    const pruned = pruneIntent([{ at: 0, delta: -10 }, { at: 950, delta: -10 }], 1000);

    expect(pruned).toHaveLength(1);
  });
});

describe("coverage", () => {
  it("grows with scale", () => {
    const node = { width: 208, height: 116 };
    const viewport = { width: 1200, height: 800 };

    expect(nodeCoverage(node, 2, viewport)).toBeGreaterThan(
      nodeCoverage(node, 1, viewport),
    );
  });

  it("returns zero for a degenerate viewport", () => {
    expect(nodeCoverage({ width: 10, height: 10 }, 1, { width: 0, height: 0 })).toBe(0);
  });
});

describe("camera helpers", () => {
  it("fits bounds inside the viewport", () => {
    const camera = fitCamera({ width: 2400, height: 1200 }, { width: 1200, height: 800 });

    expect(camera.scale).toBeLessThan(1);
    expect(camera.scale).toBeGreaterThanOrEqual(MIN_SCALE);
  });

  it("returns a neutral camera for an empty graph", () => {
    expect(fitCamera({ width: 0, height: 0 }, { width: 800, height: 600 })).toEqual({
      x: 0,
      y: 0,
      scale: 1,
    });
  });

  it("centers a node in the viewport", () => {
    const viewport = { width: 1000, height: 600 };
    const node = { x: 500, y: 300, width: 200, height: 100 };

    const camera = centerOn(node, viewport, 1);
    const centerX = node.x * camera.scale + camera.x + (node.width * camera.scale) / 2;
    const centerY = node.y * camera.scale + camera.y + (node.height * camera.scale) / 2;

    expect(centerX).toBeCloseTo(viewport.width / 2);
    expect(centerY).toBeCloseTo(viewport.height / 2);
  });
});
