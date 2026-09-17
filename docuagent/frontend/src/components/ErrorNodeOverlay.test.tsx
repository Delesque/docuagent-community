// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react";
import {
  activeErrorNodes,
  ErrorNodeOverlay,
  errorBadgeOrigin,
  groupActiveErrorsByOwner,
  type ModuleBox,
} from "./ErrorNodeOverlay";
import type { ErrorNode } from "../api/errorNodes";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BOXES: Record<string, ModuleBox> = {
  core: { x: 100, y: 200, width: 160, height: 80 },
};

function makeNode(overrides: Partial<ErrorNode> = {}): ErrorNode {
  return {
    id: "error:documentation:doc-sync:core",
    type: "error",
    owner_node_id: "core",
    source: "documentation",
    kind: "doc-sync",
    severity: "critical",
    title: "文档同步失败",
    detail: "boom",
    retry_count: 2,
    max_retries: 2,
    status: "active",
    actions: [{ id: "retry_documentation", label: "重试文档" }],
    code_task_id: "core",
    changed_files: ["src/core.py"],
    created_at: "T0",
    updated_at: "T0",
    resolved_at: "",
    ...overrides,
  };
}

describe("activeErrorNodes", () => {
  it("keeps only active errors; resolved ones are history", () => {
    const nodes = [
      makeNode(),
      makeNode({ id: "r1", status: "resolved", resolved_at: "T1" }),
    ];
    expect(activeErrorNodes(nodes)).toHaveLength(1);
    expect(activeErrorNodes(nodes).map((node) => node.status)).toEqual(["active"]);
  });
});

describe("groupActiveErrorsByOwner", () => {
  it("groups by owner preserving first-seen order", () => {
    const nodes = [
      makeNode({ owner_node_id: "b" }),
      makeNode({ owner_node_id: "a" }),
      makeNode({ owner_node_id: "b", id: "e2" }),
    ];
    const groups = groupActiveErrorsByOwner(nodes);
    expect(Array.from(groups.keys())).toEqual(["b", "a"]);
    expect(groups.get("b")).toHaveLength(2);
  });
});

describe("errorBadgeOrigin", () => {
  it("anchors to the owner's module box when it exists", () => {
    const origin = errorBadgeOrigin("core", BOXES, 0);
    expect(origin).toEqual({ x: 100, y: 200, anchored: true });
  });

  it("stacks unknown owners down the left edge so failures stay visible", () => {
    const first = errorBadgeOrigin("project", BOXES, 0);
    const second = errorBadgeOrigin("project2", BOXES, 1);
    expect(first.anchored).toBe(false);
    expect(second.anchored).toBe(false);
    expect(second.y).toBeGreaterThan(first.y);
  });
});

describe("ErrorNodeOverlay", () => {
  function renderOverlay(
    nodes: ErrorNode[],
    onAction?: (node: ErrorNode, actionId: string) => void,
  ) {
    const container = document.createElement("div");
    const root: Root = createRoot(container);
    act(() => {
      root.render(
        <ErrorNodeOverlay nodes={nodes} moduleBoxes={BOXES} onAction={onAction} />,
      );
    });
    return {
      container,
      unmount() {
        act(() => root.unmount());
      },
    };
  }

  it("renders one badge per owner with the error count and nothing when healthy", () => {
    const empty = renderOverlay([]);
    expect(empty.container.querySelectorAll("button")).toHaveLength(0);
    empty.unmount();

    const view = renderOverlay([
      makeNode(),
      makeNode({ id: "e2", severity: "warning" }),
    ]);
    const badge = view.container.querySelector<HTMLButtonElement>(
      "button[aria-label='错误节点：core（2 条）']",
    );
    expect(badge).not.toBeNull();
    expect(badge!.textContent).toContain("2");
    view.unmount();
  });

  it("expands the detail card with the allowed actions and reports clicks", () => {
    const onAction = vi.fn();
    const view = renderOverlay([makeNode()], onAction);
    const badge = view.container.querySelector<HTMLButtonElement>("button[aria-expanded]");
    act(() => {
      badge!.click();
    });
    const region = view.container.querySelector("div[role='region']");
    expect(region).not.toBeNull();
    expect(region!.textContent).toContain("重试 2/2");
    const action = Array.from(
      region!.querySelectorAll<HTMLButtonElement>("button"),
    ).find((button) => button.textContent === "重试文档");
    act(() => {
      action!.click();
    });
    expect(onAction).toHaveBeenCalledWith(expect.any(Object), "retry_documentation");
    view.unmount();
  });
});
