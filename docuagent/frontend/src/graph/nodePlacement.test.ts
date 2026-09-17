/** Hand-placing a node.
 *
 *  The store owns "is this node placed by hand"; the canvas owns where. These cover the
 *  store half plus the layout contract it depends on — that a pinned node stays exactly
 *  where it was put, which is the whole reason dropping a node also pins it.
 */

import { describe, expect, it } from "vitest";
import { layoutGraph } from "./layout";
import type { GraphEdge, GraphModule } from "./types";

function makeModule(id: string): GraphModule {
  return {
    id,
    name: id.toUpperCase(),
    brief: "",
    responsibility: "r",
    path: `src/${id}`,
    depends_on: [],
    needs_ui: false,
    group: null,
  };
}

function edge(from: string, to: string): GraphEdge {
  return { from, to, kind: "uses", label: "", reason: "" };
}

describe("a dropped node stays where it was dropped", () => {
  const modules = [makeModule("a"), makeModule("b")];
  const edges = [edge("b", "a")];

  it("holds the exact coordinates, with no inertia blend", () => {
    // Why dropping a node pins it in the same command: an unpinned node blends toward
    // its computed slot, so a drag without a pin visibly undoes itself.
    const result = layoutGraph(modules, edges, [], {
      previous: { a: { x: 617, y: -84, pinned: true } },
    });
    expect(result.nodes.get("a")!.x).toBe(617);
    expect(result.nodes.get("a")!.y).toBe(-84);
  });

  it("drifts back toward its slot when not pinned", () => {
    // The contrast that makes the pin necessary rather than merely tidy.
    const dropped = { x: 617, y: -84 };
    const result = layoutGraph(modules, edges, [], {
      previous: { a: { ...dropped, pinned: false } },
    });
    expect(result.nodes.get("a")!.x).not.toBe(dropped.x);
  });

  it("reports the node as pinned so the UI can mark it", () => {
    const result = layoutGraph(modules, edges, [], {
      previous: { a: { x: 10, y: 20, pinned: true } },
    });
    expect(result.nodes.get("a")!.pinned).toBe(true);
  });

  it("keeps a placed node still while its neighbours re-layout around it", () => {
    const grown = [...modules, makeModule("c"), makeModule("d")];
    const grownEdges = [...edges, edge("c", "a"), edge("d", "a")];
    const result = layoutGraph(grown, grownEdges, [], {
      previous: { b: { x: 900, y: 300, pinned: true } },
    });
    expect(result.nodes.get("b")!.x).toBe(900);
    expect(result.nodes.get("b")!.y).toBe(300);
  });
});

describe("edges follow a node that was moved", () => {
  it("re-routes to the new position rather than the computed one", () => {
    // The reason a drag goes through the layout engine instead of just offsetting CSS:
    // an edge left attached to where a node used to be is worse than an immovable node.
    const modules = [makeModule("a"), makeModule("b")];
    const edges = [edge("b", "a")];

    const settled = layoutGraph(modules, edges, []);
    const moved = layoutGraph(modules, edges, [], {
      previous: { a: { x: 120, y: 640, pinned: true } },
    });

    expect(moved.edges[0]!.path).not.toBe(settled.edges[0]!.path);
    // The path starts on the moved node's right face.
    const a = moved.nodes.get("a")!;
    expect(moved.edges[0]!.path).toContain(`M ${a.x + a.width}`);
  });
});

describe("releasing a node returns it to the layout engine", () => {
  it("lands back on the computed slot once the pin is gone", () => {
    // What "重排布局" and R have to produce: dropping the coordinate is not enough on its
    // own, since a pinned entry with no tracked coordinate persists as {0,0}.
    const modules = [makeModule("a"), makeModule("b")];
    const edges = [edge("b", "a")];

    const fresh = layoutGraph(modules, edges, []);
    const released = layoutGraph(modules, edges, [], { previous: {} });

    expect(released.nodes.get("a")).toEqual(fresh.nodes.get("a"));
  });
});
