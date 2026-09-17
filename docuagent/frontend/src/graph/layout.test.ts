import { describe, expect, it } from "vitest";
import {
  assignLayers,
  GROUP_HEADER,
  GROUP_PADDING,
  layoutGraph,
  NODE_HEIGHT,
  NODE_WIDTH,
} from "./layout";
import {
  CONVERSATION_NODE_ID,
  conversationModule,
  withConversationNode,
} from "./conversationNode";
import type { GraphEdge, GraphGroup, GraphModule } from "./types";

function makeModule(id: string, group: string | null = null): GraphModule {
  return {
    id,
    name: id.toUpperCase(),
    brief: `${id} does one thing.`,
    responsibility: `${id} responsibility`,
    path: `src/${id}`,
    depends_on: [],
    needs_ui: false,
    group,
  };
}

function edge(from: string, to: string, kind: GraphEdge["kind"] = "uses"): GraphEdge {
  return { from, to, kind, label: "", reason: "" };
}

describe("assignLayers", () => {
  it("places a dependency before its dependent", () => {
    const layers = assignLayers(["a", "b"], [edge("b", "a")]);

    expect(layers.get("a")).toBe(0);
    expect(layers.get("b")).toBe(1);
  });

  it("uses longest path so a diamond ends on one layer", () => {
    const edges = [edge("b", "a"), edge("c", "a"), edge("d", "b"), edge("d", "c")];
    const layers = assignLayers(["a", "b", "c", "d"], edges);

    expect(layers.get("a")).toBe(0);
    expect(layers.get("b")).toBe(1);
    expect(layers.get("c")).toBe(1);
    expect(layers.get("d")).toBe(2);
  });

  it("ignores event edges when layering", () => {
    const withEvent = assignLayers(["a", "b"], [edge("b", "a", "event")]);

    expect(withEvent.get("a")).toBe(0);
    expect(withEvent.get("b")).toBe(0);
  });

  it("does not hang on an event cycle", () => {
    const layers = assignLayers(
      ["a", "b"],
      [edge("a", "b", "event"), edge("b", "a", "event")],
    );

    expect(layers.get("a")).toBe(0);
    expect(layers.get("b")).toBe(0);
  });

  it("survives a build-order cycle instead of looping forever", () => {
    const layers = assignLayers(["a", "b", "c"], [edge("a", "b"), edge("b", "c"), edge("c", "a")]);

    expect(layers.size).toBe(3);
  });

  it("handles a deep chain without stack overflow", () => {
    const count = 900;
    const ids = Array.from({ length: count }, (_, index) => `n${index}`);
    const edges = ids.slice(1).map((id, index) => edge(id, `n${index}`));

    const layers = assignLayers(ids, edges);

    expect(layers.get("n0")).toBe(0);
    expect(layers.get(`n${count - 1}`)).toBe(count - 1);
  });
});

describe("layoutGraph determinism", () => {
  const modules = ["a", "b", "c", "d", "e"].map((id) => makeModule(id));
  const edges = [edge("b", "a"), edge("c", "a"), edge("d", "b"), edge("e", "c")];

  it("produces identical output across runs", () => {
    const first = layoutGraph(modules, edges, []);
    const second = layoutGraph(modules, edges, []);

    for (const [id, node] of first.nodes) {
      expect(second.nodes.get(id)).toEqual(node);
    }
    expect(first.edges.map((item) => item.path)).toEqual(second.edges.map((item) => item.path));
  });

  it("is independent of module input order", () => {
    const forward = layoutGraph(modules, edges, []);
    const reversed = layoutGraph([...modules].reverse(), edges, []);

    for (const [id, node] of forward.nodes) {
      expect(reversed.nodes.get(id)!.layer).toBe(node.layer);
    }
  });

  it("advances layers along x", () => {
    const result = layoutGraph(modules, edges, []);
    const a = result.nodes.get("a")!;
    const b = result.nodes.get("b")!;

    expect(a.x).toBe(0);
    expect(b.x).toBeGreaterThan(a.x + NODE_WIDTH);
  });

  it("does not overlap nodes within a layer", () => {
    const result = layoutGraph(modules, edges, []);
    const byLayer = new Map<number, Array<{ y: number }>>();
    for (const node of result.nodes.values()) {
      if (!byLayer.has(node.layer)) byLayer.set(node.layer, []);
      byLayer.get(node.layer)!.push(node);
    }
    for (const bucket of byLayer.values()) {
      const sorted = bucket.map((node) => node.y).sort((x, y) => x - y);
      for (let index = 1; index < sorted.length; index += 1) {
        expect(sorted[index]! - sorted[index - 1]!).toBeGreaterThanOrEqual(NODE_HEIGHT);
      }
    }
  });
});

describe("layout inertia", () => {
  const modules = [makeModule("a"), makeModule("b")];
  const edges = [edge("b", "a")];

  it("keeps a pinned node exactly where it was", () => {
    const result = layoutGraph(modules, edges, [], {
      previous: { a: { x: 999, y: -250, pinned: true } },
    });

    expect(result.nodes.get("a")!.x).toBe(999);
    expect(result.nodes.get("a")!.y).toBe(-250);
    expect(result.nodes.get("a")!.pinned).toBe(true);
  });

  it("blends an unpinned node toward its former position", () => {
    const fresh = layoutGraph(modules, edges, []);
    const blended = layoutGraph(modules, edges, [], {
      previous: { a: { x: 400, y: 400, pinned: false } },
      inertia: 0.5,
    });
    const target = fresh.nodes.get("a")!;
    const moved = blended.nodes.get("a")!;

    expect(moved.x).toBeCloseTo(target.x + (400 - target.x) * 0.5);
    expect(moved.y).toBeCloseTo(target.y + (400 - target.y) * 0.5);
  });

  it("ignores stale entries for nodes that no longer exist", () => {
    const result = layoutGraph(modules, edges, [], {
      previous: { ghost: { x: 10, y: 10, pinned: true } },
    });

    expect(result.nodes.has("ghost")).toBe(false);
    expect(result.nodes.size).toBe(2);
  });

  it("reduces how far existing nodes move when new ones arrive", () => {
    // The point of inertia: nodes arriving mid-stream must not relocate everything the
    // user has already read. Adding two siblings to `a`'s dependents makes the layer
    // taller, and vertical centering pulls `a` down — that is the drift to damp.
    const before = layoutGraph(modules, edges, []);
    const settled: Record<string, { x: number; y: number; pinned: boolean }> = {};
    for (const [id, node] of before.nodes) {
      settled[id] = { x: node.x, y: node.y, pinned: false };
    }

    const grown = [...modules, makeModule("c"), makeModule("d")];
    const grownEdges = [...edges, edge("c", "a"), edge("d", "a")];
    const withInertia = layoutGraph(grown, grownEdges, [], { previous: settled });
    const withoutInertia = layoutGraph(grown, grownEdges, []);

    const drift = (result: ReturnType<typeof layoutGraph>, id: string): number =>
      Math.abs(result.nodes.get(id)!.y - before.nodes.get(id)!.y);

    expect(drift(withoutInertia, "a")).toBeGreaterThan(0);
    expect(drift(withInertia, "a")).toBeLessThan(drift(withoutInertia, "a"));
  });

  it("holds a pinned node steady while its neighbours rearrange", () => {
    const grown = [...modules, makeModule("c")];
    const grownEdges = [...edges, edge("c", "a")];
    const result = layoutGraph(grown, grownEdges, [], {
      previous: { b: { x: 640, y: 320, pinned: true } },
    });

    expect(result.nodes.get("b")!.x).toBe(640);
    expect(result.nodes.get("b")!.y).toBe(320);
  });

  it("keeps a free node out of a pinned node's row", () => {
    // `a` and `b` share layer 1 and both depend on `x`; `a` is hand-placed at y=100,
    // which sits inside `b`'s straightening desire (aligning its edge to `x`). The
    // layout must not let `b` settle on top of the pinned `a`.
    const modules = [makeModule("x"), makeModule("a"), makeModule("b")];
    const edges = [edge("a", "x"), edge("b", "x")];
    const result = layoutGraph(modules, edges, [], {
      previous: { a: { x: 0, y: 100, pinned: true } },
    });

    const a = result.nodes.get("a")!;
    const b = result.nodes.get("b")!;
    const overlaps = a.y < b.y + b.height && a.y + a.height > b.y;
    expect(overlaps).toBe(false);
  });
});

describe("groups as compound nodes", () => {
  const group: GraphGroup = {
    id: "fastapi",
    label: "FastAPI",
    kind: "framework",
    members: ["b", "c"],
  };
  const modules = [makeModule("a"), makeModule("b", "fastapi"), makeModule("c", "fastapi")];
  const edges = [edge("b", "a"), edge("c", "a")];

  it("wraps members in a container", () => {
    const result = layoutGraph(modules, edges, [group]);
    const container = result.groups.find((item) => item.id === "fastapi")!;
    const b = result.nodes.get("b")!;

    expect(container).toBeDefined();
    expect(container.x).toBeLessThan(b.x);
    expect(container.y).toBeLessThan(b.y);
    expect(container.width).toBeGreaterThan(b.width);
  });

  it("skips a group whose members are all missing", () => {
    const result = layoutGraph(modules, edges, [
      { id: "ghost", label: "Ghost", kind: "framework", members: ["nope"] },
    ]);

    expect(result.groups.find((item) => item.id === "ghost")).toBeUndefined();
  });

  it("keeps members adjacent within their layer", () => {
    const extra = [...modules, makeModule("d"), makeModule("e")];
    const extraEdges = [...edges, edge("d", "a"), edge("e", "a")];
    const result = layoutGraph(extra, extraEdges, [group]);
    const ys = ["b", "c"].map((id) => result.nodes.get(id)!.y).sort((x, y) => x - y);

    expect(ys[1]! - ys[0]!).toBe(NODE_HEIGHT + 32);
  });

  it("contains a pinned member dragged upward after conversation placement", () => {
    const arch = withConversationNode({
      summary: "",
      platform: "",
      language: "",
      runtime: "",
      frameworks: [],
      stack: [],
      groups: [{ id: "fastapi", label: "FastAPI", kind: "framework", members: ["b", "c"] }],
      data: [],
      integrations: [],
      constraints: [],
      verification: [],
      risks: [],
      unresolved: [],
      modules,
      edges,
    })!;

    const result = layoutGraph(arch.modules, arch.edges, arch.groups, {
      previous: {
        b: { x: 0, y: -260, pinned: true },
      },
    });
    const container = result.groups.find((item) => item.id === "fastapi")!;
    const b = result.nodes.get("b")!;

    expect(container.x).toBeLessThanOrEqual(b.x);
    expect(container.y).toBeLessThanOrEqual(b.y - GROUP_HEADER);
    expect(container.x + container.width).toBeGreaterThanOrEqual(b.x + b.width + GROUP_PADDING);
    expect(container.y + container.height).toBeGreaterThanOrEqual(b.y + b.height + GROUP_PADDING);
  });
});

describe("the conversation node sits above the graph", () => {
  const projected = () =>
    withConversationNode(
      {
        summary: "", platform: "", language: "", runtime: "", frameworks: [],
        stack: [], groups: [], data: [], integrations: [], constraints: [],
        verification: [], risks: [], unresolved: [],
        modules: [makeModule("a"), makeModule("b"), makeModule("c")],
        edges: [edge("b", "a"), edge("c", "a")],
      },
    )!;

  it("is placed above every module, not in a layer beside them", () => {
    // Layers advance on x, so participating in layering would put it at the far left.
    const arch = projected();
    const result = layoutGraph(arch.modules, arch.edges, arch.groups);
    const conversation = result.nodes.get(CONVERSATION_NODE_ID)!;

    for (const node of result.nodes.values()) {
      if (node.id === CONVERSATION_NODE_ID) continue;
      expect(node.y).toBeGreaterThanOrEqual(conversation.y + conversation.height);
    }
  });

  it("does not occupy a layer slot or shift module layers", () => {
    const arch = projected();
    const withConv = layoutGraph(arch.modules, arch.edges, arch.groups);
    const withoutConv = layoutGraph(
      [makeModule("a"), makeModule("b"), makeModule("c")],
      [edge("b", "a"), edge("c", "a")],
      [],
    );

    for (const id of ["a", "b", "c"]) {
      expect(withConv.nodes.get(id)!.layer).toBe(withoutConv.nodes.get(id)!.layer);
      expect(withConv.nodes.get(id)!.x).toBe(withoutConv.nodes.get(id)!.x);
    }
    expect(withConv.nodes.get(CONVERSATION_NODE_ID)!.layer).toBe(-1);
  });

  it("is horizontally centered over the module span", () => {
    const arch = projected();
    const result = layoutGraph(arch.modules, arch.edges, arch.groups);
    const conversation = result.nodes.get(CONVERSATION_NODE_ID)!;
    const modules = [...result.nodes.values()].filter((n) => n.id !== CONVERSATION_NODE_ID);
    const left = Math.min(...modules.map((n) => n.x));
    const right = Math.max(...modules.map((n) => n.x + n.width));

    expect(conversation.x + conversation.width / 2).toBeCloseTo((left + right) / 2);
  });

  it("does not draw synthetic edges to or from the conversation", () => {
    const arch = projected();
    const result = layoutGraph(arch.modules, arch.edges, arch.groups);
    const conversationEdges = result.edges.filter(
      (e) => e.from === CONVERSATION_NODE_ID || e.to === CONVERSATION_NODE_ID,
    );
    expect(conversationEdges).toHaveLength(0);
  });

  it("keeps everything inside the reported bounds", () => {
    const arch = projected();
    const result = layoutGraph(arch.modules, arch.edges, arch.groups);

    for (const node of result.nodes.values()) {
      expect(result.width).toBeGreaterThanOrEqual(node.x + node.width);
      expect(result.height).toBeGreaterThanOrEqual(node.y + node.height);
    }
  });

  it("leaves a pinned module where the user put it", () => {
    // Clearing room for the conversation must not override an explicit placement.
    const arch = projected();
    const result = layoutGraph(arch.modules, arch.edges, arch.groups, {
      previous: { a: { x: 700, y: 640, pinned: true } },
    });

    expect(result.nodes.get("a")!.x).toBe(700);
    expect(result.nodes.get("a")!.y).toBe(640);
  });

  it("lays out a graph that is only the conversation", () => {
    const result = layoutGraph([conversationModule()], [], []);

    expect(result.nodes.size).toBe(1);
    expect(result.nodes.get(CONVERSATION_NODE_ID)!.layer).toBe(-1);
  });

  it("stays deterministic with the conversation attached", () => {
    const arch = projected();
    const first = layoutGraph(arch.modules, arch.edges, arch.groups);
    const second = layoutGraph(arch.modules, arch.edges, arch.groups);

    for (const [id, node] of first.nodes) {
      expect(second.nodes.get(id)).toEqual(node);
    }
    expect(first.edges.map((e) => e.path)).toEqual(second.edges.map((e) => e.path));
  });
});

describe("edge routing", () => {
  const modules = [makeModule("a"), makeModule("b")];

  it("emits one path per unique typed edge", () => {
    const result = layoutGraph(modules, [edge("b", "a"), edge("b", "a")], []);

    expect(result.edges).toHaveLength(1);
    expect(result.edges[0]!.path.startsWith("M ")).toBe(true);
  });

  it("keeps distinct kinds between the same pair", () => {
    const result = layoutGraph(modules, [edge("b", "a"), edge("b", "a", "event")], []);

    expect(result.edges).toHaveLength(2);
  });

  it("drops edges pointing at unknown nodes", () => {
    const result = layoutGraph(modules, [edge("b", "missing")], []);

    expect(result.edges).toHaveLength(0);
  });

  it("draws a straight line between adjacent same-layer nodes", () => {
    const result = layoutGraph(modules, [edge("b", "a", "event")], []);

    expect(result.edges[0]!.path).toContain("L ");
    expect(result.edges[0]!.path).not.toContain("C ");
  });

  it("bows same-layer edges when a node stands between them", () => {
    const three = ["a", "b", "c"].map((id) => makeModule(id));
    const result = layoutGraph(three, [edge("c", "a", "event")], []);

    expect(result.edges[0]!.path).toContain("C ");
  });

  it("reports bounds covering every node", () => {
    const result = layoutGraph(modules, [edge("b", "a")], []);

    for (const node of result.nodes.values()) {
      expect(result.width).toBeGreaterThanOrEqual(node.x + node.width);
      expect(result.height).toBeGreaterThanOrEqual(node.y + node.height);
    }
  });
});
