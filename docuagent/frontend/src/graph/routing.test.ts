/** Edge readability as geometry, not as pixels.
 *
 *  These assert properties — "no edge passes through a node", "two edges do not share a
 *  vertical run" — rather than expected path strings. A path-string assertion breaks
 *  whenever a corner radius changes and says nothing about whether the drawing is
 *  legible, which is the thing that was actually wrong.
 */

import { describe, expect, it } from "vitest";
import { layoutGraph, NODE_HEIGHT, NODE_WIDTH } from "./layout";
import type { GraphEdge, GraphModule } from "./types";

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

/** Sample an SVG path into points, so geometry can be checked without a path parser.
 *
 *  Only handles the commands this layout emits (M, L, A) and treats an arc as a straight
 *  hop to its endpoint. That approximation is conservative for these assertions: an arc
 *  never bulges outside the corner its two segments already define, so a chord that
 *  clears a node means the real curve does too.
 */
function samplePath(path: string): Array<{ x: number; y: number }> {
  const points: Array<{ x: number; y: number }> = [];
  const tokens = path.trim().split(/\s+/);
  let index = 0;
  while (index < tokens.length) {
    const command = tokens[index];
    if (command === "M" || command === "L") {
      points.push({ x: Number(tokens[index + 1]), y: Number(tokens[index + 2]) });
      index += 3;
    } else if (command === "A") {
      // rx ry rotation large-arc sweep x y
      points.push({ x: Number(tokens[index + 6]), y: Number(tokens[index + 7]) });
      index += 8;
    } else if (command === "C") {
      points.push({ x: Number(tokens[index + 5]), y: Number(tokens[index + 6]) });
      index += 7;
    } else {
      index += 1;
    }
  }
  return points;
}

/** Densify a polyline so segment interiors are tested, not only its vertices. */
function densify(points: Array<{ x: number; y: number }>, step = 6) {
  const dense: Array<{ x: number; y: number }> = [];
  for (let index = 1; index < points.length; index += 1) {
    const from = points[index - 1]!;
    const to = points[index]!;
    const length = Math.hypot(to.x - from.x, to.y - from.y);
    const count = Math.max(1, Math.ceil(length / step));
    for (let sample = 0; sample <= count; sample += 1) {
      dense.push({
        x: from.x + ((to.x - from.x) * sample) / count,
        y: from.y + ((to.y - from.y) * sample) / count,
      });
    }
  }
  return dense;
}

describe("long edges do not pass through the nodes they skip", () => {
  // The visible symptom of the old router: an edge spanning 2+ layers was one dog-leg
  // straight across the intervening columns, over any node standing there.
  it("routes around a node standing in the way", () => {
    // a is layer 0; b, c, d are layer 1; e is layer 2 and depends on a directly, so its
    // edge must cross layer 1 where three nodes are stacked.
    const modules = ["a", "b", "c", "d", "e"].map((id) => makeModule(id));
    const edges = [
      edge("b", "a"),
      edge("c", "a"),
      edge("d", "a"),
      edge("e", "b"),
      edge("e", "a"),
    ];
    const result = layoutGraph(modules, edges, []);

    const long = result.edges.find((item) => item.from === "e" && item.to === "a")!;
    expect(long).toBeDefined();

    const samples = densify(samplePath(long.path));
    for (const id of ["b", "c", "d"]) {
      const node = result.nodes.get(id)!;
      const inside = samples.filter(
        (point) =>
          point.x > node.x + 2 &&
          point.x < node.x + node.width - 2 &&
          point.y > node.y + 2 &&
          point.y < node.y + node.height - 2,
      );
      expect(inside, `edge e→a cuts through ${id}`).toHaveLength(0);
    }
  });

  it("keeps a long edge clear of every node in a wide graph", () => {
    // A chain plus a shortcut spanning the whole chain: the shortcut crosses every
    // intervening layer, which is the worst case for this.
    const ids = ["n0", "n1", "n2", "n3", "n4"];
    const modules = ids.map((id) => makeModule(id));
    const edges = ids.slice(1).map((id, index) => edge(id, `n${index}`));
    edges.push(edge("n4", "n0"));

    const result = layoutGraph(modules, edges, []);
    const long = result.edges.find((item) => item.from === "n4" && item.to === "n0")!;
    const samples = densify(samplePath(long.path));

    for (const [id, node] of result.nodes) {
      if (id === "n4" || id === "n0") continue;
      const inside = samples.filter(
        (point) =>
          point.x > node.x + 2 &&
          point.x < node.x + node.width - 2 &&
          point.y > node.y + 2 &&
          point.y < node.y + node.height - 2,
      );
      expect(inside, `long edge cuts through ${id}`).toHaveLength(0);
    }
  });

  it("leaves a single-layer hop as a simple route", () => {
    // Adjacent layers need no waypoints; adding them would make the common case wander.
    const result = layoutGraph([makeModule("a"), makeModule("b")], [edge("b", "a")], []);
    const path = result.edges[0]!.path;
    // One turn each way at most: M plus a small number of segments.
    expect(samplePath(path).length).toBeLessThanOrEqual(6);
  });

  it("aligns a chain so every adjacent edge runs straight", () => {
    const result = layoutGraph(
      ["a", "b", "c"].map((id) => makeModule(id)),
      [edge("b", "a"), edge("c", "b")],
      [],
    );
    for (const routed of result.edges) {
      const points = samplePath(routed.path);
      expect(points.length).toBeLessThanOrEqual(2);
      expect(routed.path).not.toContain("A ");
    }
  });

  it("keeps a long edge straight when the corridor is clear", () => {
    const result = layoutGraph(
      ["a", "b", "c"].map((id) => makeModule(id)),
      [edge("b", "a"), edge("c", "b"), edge("c", "a")],
      [],
      {
        previous: {
          a: { x: 0, y: 0, pinned: true },
          b: { x: 340, y: 400, pinned: true },
          c: { x: 680, y: 81.2, pinned: true },
        },
      },
    );
    const long = result.edges.find((item) => item.from === "c" && item.to === "a")!;
    expect(samplePath(long.path).length).toBeLessThanOrEqual(2);
  });
});

describe("edges into one node fan out instead of stacking", () => {
  it("gives each edge its own anchor on a shared face", () => {
    // Four dependents of one module used to be four lines meeting the same pixel.
    const modules = ["a", "b", "c", "d", "e"].map((id) => makeModule(id));
    const edges = [edge("b", "a"), edge("c", "a"), edge("d", "a"), edge("e", "a")];
    const result = layoutGraph(modules, edges, []);

    const starts = result.edges.map((item) => samplePath(item.path)[0]!.y);
    expect(new Set(starts.map((y) => y.toFixed(1))).size).toBe(starts.length);
  });

  it("keeps anchors inside the node's own face", () => {
    const modules = ["a", "b", "c", "d"].map((id) => makeModule(id));
    const edges = [edge("b", "a"), edge("c", "a"), edge("d", "a")];
    const result = layoutGraph(modules, edges, []);
    const a = result.nodes.get("a")!;

    for (const item of result.edges) {
      const start = samplePath(item.path)[0]!;
      expect(start.y).toBeGreaterThanOrEqual(a.y);
      expect(start.y).toBeLessThanOrEqual(a.y + a.height);
    }
  });

  it("centers a lone edge rather than offsetting it", () => {
    const result = layoutGraph([makeModule("a"), makeModule("b")], [edge("b", "a")], []);
    const a = result.nodes.get("a")!;
    const start = samplePath(result.edges[0]!.path)[0]!;
    expect(start.y).toBeCloseTo(a.y + a.height / 2);
    expect(start.x).toBeCloseTo(a.x + a.width);
  });
});

describe("edges crossing one gap take separate lanes", () => {
  it("does not put two vertical runs on the same x", () => {
    // Two edges from the same layer to the same layer previously shared the midpoint
    // corridor, so their vertical segments were drawn on top of each other.
    const modules = ["a", "b", "c", "d"].map((id) => makeModule(id));
    const edges = [edge("c", "a"), edge("d", "b")];
    const result = layoutGraph(modules, edges, []);

    const verticalRuns = result.edges.map((item) => {
      const points = samplePath(item.path);
      // The long vertical segment: the pair of consecutive points sharing an x.
      for (let index = 1; index < points.length; index += 1) {
        const from = points[index - 1]!;
        const to = points[index]!;
        if (Math.abs(from.x - to.x) < 0.6 && Math.abs(from.y - to.y) > 8) return from.x;
      }
      return null;
    });

    const present = verticalRuns.filter((x): x is number => x !== null);
    if (present.length === 2) {
      expect(Math.abs(present[0]! - present[1]!)).toBeGreaterThan(1);
    }
  });
});

describe("routing stays deterministic", () => {
  const modules = ["a", "b", "c", "d", "e"].map((id) => makeModule(id));
  const edges = [
    edge("b", "a"),
    edge("c", "a"),
    edge("d", "b"),
    edge("e", "a"),
    edge("e", "c"),
  ];

  it("produces identical paths across runs", () => {
    const first = layoutGraph(modules, edges, []);
    const second = layoutGraph(modules, edges, []);
    expect(first.edges.map((item) => item.path)).toEqual(
      second.edges.map((item) => item.path),
    );
  });

  it("produces identical paths regardless of edge input order", () => {
    // Anchors and lanes are assigned by sorted key precisely so this holds: an anchor
    // that moved when an unrelated edge arrived would make the whole graph twitch.
    const forward = layoutGraph(modules, edges, []);
    const reversed = layoutGraph(modules, [...edges].reverse(), []);

    const sorted = (result: ReturnType<typeof layoutGraph>) =>
      result.edges.map((item) => `${item.id} ${item.path}`).sort();
    expect(sorted(forward)).toEqual(sorted(reversed));
  });

  it("emits no NaN coordinates", () => {
    // A degenerate radius or a zero-length segment used to be able to produce NaN, which
    // silently drops the whole path from the SVG rather than erroring.
    const result = layoutGraph(modules, edges, []);
    for (const item of result.edges) {
      expect(item.path).not.toContain("NaN");
      for (const point of samplePath(item.path)) {
        expect(Number.isFinite(point.x)).toBe(true);
        expect(Number.isFinite(point.y)).toBe(true);
      }
    }
  });
});

describe("routing slots are not renderable nodes", () => {
  it("never leaks a waypoint into the node map", () => {
    // Slots reserve space and carry a y, but nothing may render or persist them: a slot
    // in `nodes` would appear as a blank module and be written to ui-state.json.
    const ids = ["n0", "n1", "n2", "n3"];
    const modules = ids.map((id) => makeModule(id));
    const edges = ids.slice(1).map((id, index) => edge(id, `n${index}`));
    edges.push(edge("n3", "n0"));

    const result = layoutGraph(modules, edges, []);
    expect([...result.nodes.keys()].sort()).toEqual(ids);
    for (const id of result.nodes.keys()) {
      expect(id.startsWith("__bend__")).toBe(false);
    }
  });

  it("reserves room so nodes are pushed clear of the routing band", () => {
    // The slot has to actually occupy space, or "route around" would mean routing into
    // whatever sits at that height anyway.
    const withShortcut = layoutGraph(
      ["a", "b", "c"].map((id) => makeModule(id)),
      [edge("b", "a"), edge("c", "b"), edge("c", "a")],
      [],
    );
    // Layer 1 holds b plus one slot, so its total extent exceeds a bare node.
    const layerOne = [...withShortcut.nodes.values()].filter((node) => node.layer === 1);
    expect(layerOne).toHaveLength(1);
    expect(withShortcut.height).toBeGreaterThan(NODE_HEIGHT);
  });

  it("still reports bounds covering every real node", () => {
    const ids = ["n0", "n1", "n2", "n3"];
    const modules = ids.map((id) => makeModule(id));
    const edges = ids.slice(1).map((id, index) => edge(id, `n${index}`));
    edges.push(edge("n3", "n0"));

    const result = layoutGraph(modules, edges, []);
    for (const node of result.nodes.values()) {
      expect(result.width).toBeGreaterThanOrEqual(node.x + node.width);
      expect(result.height).toBeGreaterThanOrEqual(node.y + node.height);
    }
    expect(result.width).toBeGreaterThanOrEqual(NODE_WIDTH);
  });
});

describe("back edges travel under the graph, not through it", () => {
  // KNOWN_ISSUES #12: a back edge (arrow returning to a deeper layer) was one
  // straight line across the whole graph, over the intervening node and through
  // the hover labels of the forward edges. It now runs below the graph.
  it("keeps a back edge clear of every node it skips", () => {
    const modules = ["core", "api", "ui"].map((id) => makeModule(id));
    const edges = [
      edge("api", "core"),
      edge("ui", "api"),
      edge("core", "ui", "event"),
    ];
    const result = layoutGraph(modules, edges, []);

    const back = result.edges.find((item) => item.kind === "event")!;
    expect(back).toBeDefined();
    const samples = densify(samplePath(back.path));
    for (const id of ["core", "api", "ui"]) {
      const node = result.nodes.get(id)!;
      const inside = samples.filter(
        (point) =>
          point.x > node.x + 2 &&
          point.x < node.x + node.width - 2 &&
          point.y > node.y + 2 &&
          point.y < node.y + node.height - 2,
      );
      expect(inside, `back edge cuts through ${id}`).toHaveLength(0);
    }
  });

  it("runs the back edge in a lane below the graph, inside the bounds", () => {
    const modules = ["core", "api", "ui"].map((id) => makeModule(id));
    const edges = [
      edge("api", "core"),
      edge("ui", "api"),
      edge("core", "ui", "event"),
    ];
    const result = layoutGraph(modules, edges, []);

    const bottoms = [...result.nodes.values()].map((node) => node.y + node.height);
    const deepestNode = Math.max(...bottoms);
    const back = result.edges.find((item) => item.kind === "event")!;
    // The whole run sits below every node, which also puts its hover label
    // far from every forward edge's label.
    expect(back.midpoint.y).toBeGreaterThan(deepestNode);
    expect(result.height).toBeGreaterThanOrEqual(back.midpoint.y);
  });

  it("routes a long event edge around the nodes it skips", () => {
    // Slot reservation used to be build-order-only, so a long `event` edge drew
    // straight through every intervening node. Reservation is a drawing concern.
    const modules = ["a", "b", "c", "d", "e"].map((id) => makeModule(id));
    const edges = [
      edge("b", "a"),
      edge("c", "b"),
      edge("d", "c"),
      edge("e", "a", "event"),
    ];
    const result = layoutGraph(modules, edges, []);

    const long = result.edges.find((item) => item.from === "e" && item.to === "a")!;
    expect(long).toBeDefined();
    const samples = densify(samplePath(long.path));
    for (const id of ["b", "c", "d"]) {
      const node = result.nodes.get(id)!;
      const inside = samples.filter(
        (point) =>
          point.x > node.x + 2 &&
          point.x < node.x + node.width - 2 &&
          point.y > node.y + 2 &&
          point.y < node.y + node.height - 2,
      );
      expect(inside, `long event edge cuts through ${id}`).toHaveLength(0);
    }
  });
});

describe("same-layer edges keep their label off the node", () => {
  it("puts the midpoint out on the bow, not between the endpoints", () => {
    // A cubic at t=0.5 sits well out toward its control points, so averaging the
    // endpoints parked the hover label inside the node the bow travels around.
    const result = layoutGraph(
      ["a", "b", "c"].map((id) => makeModule(id)),
      [edge("c", "a", "event")],
      [],
    );
    const bow = result.edges[0]!;
    const a = result.nodes.get("a")!;
    expect(bow.midpoint.x).toBeGreaterThan(a.x + a.width / 2);
  });

  it("keeps the label on a straight same-layer edge", () => {
    const result = layoutGraph(
      [makeModule("a"), makeModule("b")],
      [edge("b", "a", "event")],
      [],
    );
    const straight = result.edges[0]!;
    const a = result.nodes.get("a")!;
    expect(straight.midpoint.x).toBeCloseTo(a.x + a.width / 2);
    expect(straight.path).not.toContain("C ");
  });
});
