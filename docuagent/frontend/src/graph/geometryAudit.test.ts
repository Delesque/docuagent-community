import { describe, expect, it } from "vitest";
import type { GraphEdge, GraphModule } from "./types";
import { layoutGraph } from "./layout";
import type { RoutedEdge } from "./layout";
import {
  auditFalseBranches,
  auditGeometry,
  auditLabelClearance,
  labelBoxes,
  segmentsOf,
} from "./geometryAudit";

function edge(id: string, from: string, to: string, path: string, midpoint: { x: number; y: number }): RoutedEdge {
  const base: GraphEdge = {
    from,
    to,
    kind: "uses",
    label: id,
    reason: "",
  };
  return { ...base, id, path, midpoint };
}

describe("segmentsOf / labelBoxes", () => {
  it("parses an orthogonal path into axis-tagged segments", () => {
    const segments = segmentsOf([edge("e1", "a", "b", "M 0 0 L 100 0 L 100 60", { x: 50, y: 30 })]);
    expect(segments.map((s) => s.axis)).toEqual(["horizontal", "vertical"]);
  });

  it("centres the label box on the midpoint at EdgeLayer's size", () => {
    const boxes = labelBoxes([edge("e1", "a", "b", "M 0 0 L 10 0", { x: 100, y: 50 })]);
    const box = boxes[0]!;
    expect(box.left).toBe(70);
    expect(box.right).toBe(130);
    expect(box.top).toBe(41);
    expect(box.bottom).toBe(59);
  });
});

describe("auditLabelClearance", () => {
  it("flags a label sitting on another route as an error", () => {
    const edges = [
      edge("e1", "a", "b", "M 0 0 L 100 0", { x: 50, y: 0 }),
      // Runs straight through e1's label box (70..130 x, -9..9 y).
      edge("e2", "c", "d", "M 100 -50 L 100 50", { x: 100, y: 0 }),
    ];

    const violations = auditLabelClearance(edges);

    expect(violations.length).toBeGreaterThan(0);
    expect(violations[0]!.severity).toBe("error");
    expect(violations[0]!.rule).toBe("label-clearance");
  });

  it("records a near miss instead of staying silent", () => {
    const edges = [
      edge("e1", "a", "b", "M 0 0 L 100 0", { x: 50, y: 0 }),
      // 1px below the label box bottom (9): inside the warning band.
      edge("e2", "c", "d", "M 0 10 L 100 10", { x: 50, y: 10 }),
    ];

    const violations = auditLabelClearance(edges);

    expect(violations.some((v) => v.severity === "warning")).toBe(true);
  });

  it("stays quiet when routes keep clear of the label", () => {
    const edges = [
      edge("e1", "a", "b", "M 0 0 L 100 0", { x: 50, y: 0 }),
      edge("e2", "c", "d", "M 0 200 L 100 200", { x: 50, y: 200 }),
    ];

    expect(auditLabelClearance(edges)).toEqual([]);
  });
});

describe("auditFalseBranches", () => {
  it("flags unrelated edges sharing a long lane", () => {
    const edges = [
      edge("e1", "a", "b", "M 0 0 L 100 0 L 100 40", { x: 50, y: 20 }),
      edge("e2", "c", "d", "M 0 0 L 100 0 L 100 80", { x: 50, y: 40 }),
    ];

    const violations = auditFalseBranches(edges);

    expect(violations.length).toBeGreaterThan(0);
    expect(violations[0]!.rule).toBe("false-branch");
    expect(violations[0]!.evidence.shared as number).toBeGreaterThanOrEqual(8);
  });

  it("does not treat a real junction as a false branch", () => {
    const edges = [
      edge("e1", "a", "b", "M 0 0 L 100 0 L 100 40", { x: 50, y: 20 }),
      // Shares the lane but meets e1 at `a` — a genuine fork at the node.
      edge("e2", "a", "c", "M 0 0 L 100 0 L 100 80", { x: 50, y: 40 }),
    ];

    expect(auditFalseBranches(edges)).toEqual([]);
  });

  it("ignores brief crossings below the threshold", () => {
    const edges = [
      edge("e1", "a", "b", "M 0 0 L 100 0 L 100 40", { x: 50, y: 20 }),
      edge("e2", "c", "d", "M 96 -20 L 96 20", { x: 96, y: 0 }),
    ];

    // e2 crosses e1's horizontal lane at a point: shared length is a point, not a lane.
    expect(auditFalseBranches(edges)).toEqual([]);
  });
});

describe("the built-in layout clears the gate", () => {
  const modules: GraphModule[] = [
    {
      id: "conversation", name: "对话", brief: "", responsibility: "", path: "",
      depends_on: [], needs_ui: false, group: null,
    },
    {
      id: "core", name: "Core", brief: "内核", responsibility: "Owns the domain.", path: "src/core",
      depends_on: [], needs_ui: false, group: null,
    },
    {
      id: "api", name: "API", brief: "接口", responsibility: "Serves requests.", path: "src/api",
      depends_on: ["core"], needs_ui: false, group: null,
    },
    {
      id: "ui", name: "UI", brief: "界面", responsibility: "Renders the graph.", path: "src/ui",
      depends_on: ["api"], needs_ui: true, group: null,
    },
  ];
  const edges: GraphEdge[] = [
    { from: "api", to: "core", kind: "uses", label: "读取状态", reason: "Reads project state." },
    { from: "ui", to: "api", kind: "uses", label: "请求数据", reason: "Fetches data." },
  ];

  it("reports no errors for a three-module graph without a back edge", () => {
    const layout = layoutGraph(modules, edges, []);

    expect(auditGeometry(layout.edges)).toEqual([]);
  });

  it("routes a back edge under the graph so forward labels stay clear", () => {
    // KNOWN_ISSUES #12 used to fail here: a back edge was drawn as one straight
    // line across the whole graph, straight through the hover labels of the
    // forward edges. It now travels below the graph along its own lane, and the
    // gate that documented the weakness clears.
    const withBackEdge: GraphEdge[] = [
      ...edges,
      { from: "core", to: "ui", kind: "event", label: "推送更新", reason: "Pushes updates." },
    ];
    const layout = layoutGraph(modules, withBackEdge, []);

    expect(auditGeometry(layout.edges)).toEqual([]);
  });
});
