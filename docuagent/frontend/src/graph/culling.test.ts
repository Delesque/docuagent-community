import { describe, expect, it } from "vitest";
import { cullNodes, visibleGraphBounds } from "./culling";
import type { LaidOutNode } from "./layout";

function node(id: string, x: number, y: number): LaidOutNode {
  return {
    id,
    x,
    y,
    width: 200,
    height: 120,
    pinned: false,
  } as LaidOutNode;
}

describe("node culling", () => {
  it("renders everything below the threshold", () => {
    const nodes = new Map([
      ["a", node("a", 0, 0)],
      ["b", node("b", 5000, 5000)],
    ]);
    const visible = cullNodes(
      ["a", "b"],
      nodes,
      { x: 0, y: 0, scale: 1 },
      { width: 1200, height: 800 },
      new Set(),
      60,
    );
    expect(visible).toEqual(new Set(["a", "b"]));
  });

  it("culls off-screen nodes above the threshold", () => {
    const nodes = new Map([
      ["near", node("near", 0, 0)],
      ["far", node("far", 5000, 5000)],
    ]);
    const ids = [
      "near",
      "far",
      ...Array.from({ length: 60 }, (_, index) => `m${index}`),
    ];
    const visible = cullNodes(
      ids,
      nodes,
      { x: 0, y: 0, scale: 1 },
      { width: 1200, height: 800 },
      new Set(["far"]),
      60,
    );
    expect(visible.has("near")).toBe(true);
    expect(visible.has("far")).toBe(true);
  });

  it("keeps always-visible nodes mounted", () => {
    const nodes = new Map([["far", node("far", 9000, 9000)]]);
    const ids = [
      "far",
      ...Array.from({ length: 60 }, (_, index) => `m${index}`),
    ];
    const visible = cullNodes(
      ids,
      nodes,
      { x: 0, y: 0, scale: 1 },
      { width: 1200, height: 800 },
      new Set(["far"]),
      60,
    );
    expect(visible.has("far")).toBe(true);
  });

  it("computes bounds in graph coordinates", () => {
    const bounds = visibleGraphBounds(
      { x: 100, y: 50, scale: 2 },
      { width: 1200, height: 800 },
    );
    expect(bounds.left).toBeLessThan(0);
    expect(bounds.top).toBeLessThan(0);
    expect(bounds.right).toBeGreaterThan(500);
  });
});
