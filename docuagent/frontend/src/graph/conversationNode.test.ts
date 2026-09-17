import { describe, expect, it } from "vitest";
import {
  CONVERSATION_NODE_ID,
  conversationModule,
  isConversationNode,
  withConversationNode,
  withoutConversationNode,
} from "./conversationNode";
import type { Architecture, GraphEdge, GraphModule } from "./types";

function makeModule(id: string, depends_on: string[] = []): GraphModule {
  return {
    id,
    name: id.toUpperCase(),
    brief: `${id} does one thing.`,
    responsibility: `${id} responsibility`,
    path: `src/${id}`,
    depends_on,
    needs_ui: false,
    group: null,
  };
}

function edge(from: string, to: string, kind: GraphEdge["kind"] = "uses"): GraphEdge {
  return { from, to, kind, label: "", reason: "" };
}

function makeArchitecture(
  modules: GraphModule[],
  edges: GraphEdge[] = [],
): Architecture {
  return {
    summary: "s",
    platform: "Windows",
    language: "Python",
    runtime: "3.12",
    frameworks: [],
    stack: ["Python"],
    modules,
    groups: [],
    edges,
    data: [],
    integrations: [],
    constraints: [],
    verification: [],
    risks: [],
    unresolved: [],
  };
}

describe("withConversationNode", () => {
  it("prepends the conversation so a linear read starts there", () => {
    const projected = withConversationNode(
      makeArchitecture([makeModule("a"), makeModule("b")], [edge("b", "a")]),
    )!;
    expect(projected.modules[0]!.id).toBe(CONVERSATION_NODE_ID);
    expect(projected.modules).toHaveLength(3);
  });

  it("does not connect the conversation to any module", () => {
    const projected = withConversationNode(
      makeArchitecture([makeModule("a"), makeModule("b")], [edge("b", "a")]),
    )!;
    const added = projected.edges.filter((e) => isConversationNode(e.to));
    expect(added).toHaveLength(0);
  });

  it("keeps the conversation free of synthetic edges", () => {
    const projected = withConversationNode(makeArchitecture([makeModule("a")]))!;
    expect(projected.edges).toHaveLength(0);
  });

  it("preserves the original edges", () => {
    const original = [edge("b", "a")];
    const projected = withConversationNode(
      makeArchitecture([makeModule("a"), makeModule("b")], original),
    )!;
    expect(projected.edges).toContainEqual(original[0]);
  });

  it("returns null for a null architecture", () => {
    expect(withConversationNode(null)).toBeNull();
  });

  it("returns null for an empty architecture so the conversation stays full-screen", () => {
    const empty = makeArchitecture([]);
    expect(withConversationNode(empty)).toBeNull();
  });

  it("is idempotent", () => {
    const once = withConversationNode(makeArchitecture([makeModule("a")]))!;
    const twice = withConversationNode(once)!;
    expect(twice.modules).toHaveLength(2);
    expect(twice.edges).toEqual(once.edges);
  });

  it("leaves the rest of the architecture untouched", () => {
    const base = makeArchitecture([makeModule("a")]);
    const projected = withConversationNode(base)!;
    expect(projected.summary).toBe(base.summary);
    expect(projected.verification).toBe(base.verification);
    expect(base.modules).toHaveLength(1); // input not mutated
  });
});

describe("withoutConversationNode", () => {
  it("round-trips back to the original architecture", () => {
    const base = makeArchitecture([makeModule("a"), makeModule("b")], [edge("b", "a")]);
    const stripped = withoutConversationNode(withConversationNode(base)!);
    expect(stripped.modules.map((m) => m.id)).toEqual(["a", "b"]);
    expect(stripped.edges).toEqual(base.edges);
  });

  it("is a no-op on an architecture that was never projected", () => {
    const base = makeArchitecture([makeModule("a")]);
    expect(withoutConversationNode(base).modules).toHaveLength(1);
  });
});

describe("the conversation module itself", () => {
  it("has no path, because it has no file behind it", () => {
    // An empty path is the signal that suppresses code-tail rendering.
    expect(conversationModule().path).toBe("");
  });

  it("uses an id no model-supplied slug can collide with", () => {
    // `slugify` strips leading and trailing separators, so it can never emit this.
    expect(CONVERSATION_NODE_ID).toBe("__conversation__");
    expect(isConversationNode("conversation")).toBe(false);
    expect(isConversationNode(null)).toBe(false);
    expect(isConversationNode(undefined)).toBe(false);
  });
});
