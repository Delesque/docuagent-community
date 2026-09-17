import { describe, expect, it } from "vitest";
import {
  edgeIndicatorFor,
  mergeEdgeIndicators,
  worldToScreen,
  type ViewportSize,
} from "./errorEdgeIndicators";

const VIEWPORT: ViewportSize = { width: 800, height: 600 };

describe("worldToScreen", () => {
  it("applies screen = world * scale + camera", () => {
    expect(worldToScreen(5, 7, { x: 10, y: 20, scale: 2 })).toEqual({ x: 20, y: 34 });
    expect(worldToScreen(0, 0, { x: -30, y: 40, scale: 1 })).toEqual({ x: -30, y: 40 });
  });
});

describe("edgeIndicatorFor", () => {
  it("returns null while the point is inside the viewport", () => {
    expect(edgeIndicatorFor({ x: 400, y: 300 }, VIEWPORT)).toBeNull();
    expect(edgeIndicatorFor({ x: 12, y: 300 }, VIEWPORT)).toBeNull();
    expect(edgeIndicatorFor({ x: 788, y: 300 }, VIEWPORT)).toBeNull();
  });

  it("pins an off-screen point to the right edge", () => {
    const indicator = edgeIndicatorFor({ x: 1200, y: 300 }, VIEWPORT);
    expect(indicator).not.toBeNull();
    expect(indicator!.side).toBe("right");
    expect(indicator!.x).toBe(800 - 20);
    expect(indicator!.y).toBe(300);
  });

  it("picks the side the ray actually crosses first", () => {
    // Steeply above-left: the top bound is crossed before the left bound.
    const indicator = edgeIndicatorFor({ x: -500, y: -1000 }, VIEWPORT);
    expect(indicator!.side).toBe("top");
    expect(indicator!.x).toBeLessThan(400);
    // Far left at mid height: left edge, vertically centred.
    expect(edgeIndicatorFor({ x: -500, y: 300 }, VIEWPORT)!.side).toBe("left");
  });

  it("keeps the anchor inside the viewport bounds", () => {
    const indicator = edgeIndicatorFor({ x: 400, y: 5000 }, VIEWPORT);
    expect(indicator!.side).toBe("bottom");
    expect(indicator!.y).toBe(600 - 20);
    expect(indicator!.x).toBe(400);
  });
});

describe("mergeEdgeIndicators", () => {
  it("merges one side into a single reminder with a count and worst severity", () => {
    const merged = mergeEdgeIndicators([
      { side: "right", x: 780, y: 100, severity: "warning", owner: "a" },
      { side: "right", x: 780, y: 300, severity: "critical", owner: "b" },
      { side: "top", x: 400, y: 20, severity: "info", owner: "c" },
    ]);
    expect(merged).toHaveLength(2);
    const right = merged.find((item) => item.side === "right")!;
    expect(right.count).toBe(2);
    expect(right.worst).toBe("critical");
    // Critical sorts first so the click target is the most severe owner.
    expect(right.owners).toEqual(["b", "a"]);
    expect(right.y).toBe(200);
    expect(merged.find((item) => item.side === "top")!.count).toBe(1);
  });
});
