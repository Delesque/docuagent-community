import { describe, expect, it } from "vitest";
import { computeAnchorOffsets, layoutGraph } from "./layout";
import { snapPosition, straightSnapCandidates } from "./snap";
import type { GraphEdge, GraphModule } from "./types";

function makeModule(id: string): GraphModule {
  return {
    id,
    name: id,
    brief: "",
    responsibility: "",
    path: `src/${id}`,
    depends_on: [],
    needs_ui: false,
    group: null,
  };
}

function edge(from: string, to: string): GraphEdge {
  return { from, to, kind: "uses", label: "", reason: "" };
}

describe("straight-edge snapping", () => {
  it("snaps a dragged node to the y where its edge runs straight", () => {
    const modules = ["a", "b"].map((id) => makeModule(id));
    const edges = [edge("b", "a")];
    const result = layoutGraph(modules, edges, [], {
      previous: {
        a: { x: 0, y: 0, pinned: true },
        b: { x: 340, y: 100, pinned: true },
      },
    });

    const snapped = snapPosition("b", 340, 10, edges, result.nodes);
    expect(snapped.snapped).toBe(true);
    expect(snapped.y).toBe(0);
    expect(snapped.edgeId).toBe("b|a|uses");
  });

  it("picks the nearest guide when a node has several edges", () => {
    const modules = ["a", "b", "c"].map((id) => makeModule(id));
    const edges = [edge("b", "a"), edge("b", "c")];
    const result = layoutGraph(modules, edges, [], {
      previous: {
        a: { x: 0, y: 0, pinned: true },
        c: { x: 0, y: 200, pinned: true },
        b: { x: 340, y: 300, pinned: true },
      },
    });

    const candidates = straightSnapCandidates("b", edges, result.nodes);
    expect(candidates.length).toBe(2);
    const snapped = snapPosition("b", 340, 50, edges, result.nodes);
    expect(snapped.snapped).toBe(true);
    expect(snapped.edgeId).toBe("b|a|uses");
    expect(snapped.y).toBeCloseTo(40.6, 5);
  });

  it("places the edge's two face anchors at the same y, so the edge runs straight", () => {
    // A multi-edge node has signed anchor offsets; aligning them is exactly what
    // "straight" means. This asserts the semantic rather than a magic y, so a
    // sign swap in the candidate math cannot pass silently.
    const modules = ["a", "b", "c"].map((id) => makeModule(id));
    const edges = [edge("b", "a"), edge("b", "c")];
    const result = layoutGraph(modules, edges, [], {
      previous: {
        a: { x: 0, y: 0, pinned: true },
        c: { x: 0, y: 200, pinned: true },
        b: { x: 340, y: 300, pinned: true },
      },
    });

    const guide = straightSnapCandidates("b", edges, result.nodes).find(
      (candidate) => candidate.edgeId === "b|a|uses",
    );
    expect(guide).toBeDefined();

    const offsets = computeAnchorOffsets(edges, ["a", "b", "c"]);
    const a = result.nodes.get("a")!;
    const b = result.nodes.get("b")!;
    const outY =
      a.y + a.height / 2 + (offsets.outgoing.get("a")!.get("b|a|uses") ?? 0);
    const inY =
      guide!.y + b.height / 2 + (offsets.incoming.get("b")!.get("b|a|uses") ?? 0);
    expect(inY).toBeCloseTo(outY, 5);
  });

  it("ignores a guide that would overlap another node", () => {
    const modules = ["a", "b", "c"].map((id) => makeModule(id));
    const edges = [edge("b", "a")];
    const result = layoutGraph(modules, edges, [], {
      previous: {
        a: { x: 0, y: 0, pinned: true },
        b: { x: 340, y: 100, pinned: true },
        c: { x: 340, y: 0, pinned: true },
      },
    });

    const candidates = straightSnapCandidates("b", edges, result.nodes);
    expect(candidates).toHaveLength(0);
    const snapped = snapPosition("b", 340, 10, edges, result.nodes);
    expect(snapped.snapped).toBe(false);
  });
});
