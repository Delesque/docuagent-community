import { describe, expect, it } from "vitest";
import type { RegistryEdge, RegistryNode } from "../codeintel/api";
import { highlightModulesForContract, impactForRoots } from "./impact";

function nodes(...specs: Array<[string, string, RegistryNode["status"]]>): RegistryNode[] {
  return specs.map(([id, owner, status]) => ({
    id,
    type: "public_api",
    owner,
    name: id,
    status,
    file: "",
    line: null,
    public: true,
  }));
}

function edge(from: string, to: string, kind: RegistryEdge["kind"] = "depends_on"): RegistryEdge {
  return { from, to, kind, label: "", reason: "" };
}

describe("impactForRoots", () => {
  it("propagates transitively through depends_on edges", () => {
    const ns = nodes(["core-api", "core", "active"], ["api-api", "api", "active"], ["ui-api", "ui", "active"]);
    const es = [edge("api-api", "core"), edge("ui-api", "api")];

    const impact = impactForRoots(ns, es, ["core"]);

    expect(impact.affected.map((item) => item.module_id)).toEqual(["api", "ui"]);
    expect(impact.affected.map((item) => item.depth)).toEqual([1, 2]);
  });

  it("treats uses edges as consumer-depends-on-kernel-owner", () => {
    const ns = nodes(["kern", "kernel", "active"], ["consumer-api", "consumer", "active"]);
    const es = [edge("kern", "consumer", "uses")];

    const impact = impactForRoots(ns, es, ["kernel"]);

    expect(impact.affected.map((item) => item.module_id)).toEqual(["consumer"]);
  });

  it("returns no affected modules without roots", () => {
    const ns = nodes(["a-api", "a", "active"], ["b-api", "b", "active"]);
    expect(impactForRoots(ns, [edge("a-api", "b")], []).affected).toEqual([]);
  });
});

describe("highlightModulesForContract", () => {
  const ns = nodes(
    ["core-api", "core", "stale"],
    ["api-api", "api", "active"],
    ["ui-api", "ui", "active"],
    ["unrelated-api", "unrelated", "active"],
  );
  const es = [edge("api-api", "core"), edge("ui-api", "api")];

  it("keeps the owner and its downstream visible", () => {
    const modules = highlightModulesForContract(ns, es, "core-api");
    expect(modules).not.toBeNull();
    expect(modules!.has("core")).toBe(true);
    expect(modules!.has("api")).toBe(true);
    expect(modules!.has("ui")).toBe(true);
    expect(modules!.has("unrelated")).toBe(false);
  });

  it("returns null without a hovered contract or an owner", () => {
    expect(highlightModulesForContract(ns, es, null)).toBeNull();
    expect(highlightModulesForContract(ns, es, "missing")).toBeNull();
  });
});

import { reachHighlightSet, reachModules } from "./impact";

describe("reachModules", () => {
  const edges = [
    { from: "entry", to: "main" },
    { from: "main", to: "renderer" },
    { from: "state", to: "renderer" },
  ];

  it("walks both directions from the selected module", () => {
    const { upstream, downstream } = reachModules(edges, "renderer");
    expect(upstream.has("entry")).toBe(true);
    expect(upstream.has("state")).toBe(true);
    expect(downstream.size).toBe(1); // renderer is a leaf
  });

  it("includes self in both sets", () => {
    const { upstream, downstream } = reachModules(edges, "main");
    expect(upstream.has("main")).toBe(true);
    expect(downstream.has("main")).toBe(true);
    expect(downstream.has("renderer")).toBe(true);
  });
});

describe("reachHighlightSet", () => {
  it("returns null without a selection", () => {
    expect(reachHighlightSet([], null)).toBeNull();
  });

  it("unions upstream and downstream into one set", () => {
    const set = reachHighlightSet([{ from: "a", to: "b" }, { from: "b", to: "c" }], "b");
    expect(set!.has("a")).toBe(true);
    expect(set!.has("b")).toBe(true);
    expect(set!.has("c")).toBe(true);
  });
});
