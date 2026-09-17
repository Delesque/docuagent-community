// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react";
import { ErrorEdgeIndicators } from "./ErrorEdgeIndicators";
import type { ErrorNode } from "../api/errorNodes";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const VIEWPORT = { width: 800, height: 600 };

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
    actions: [],
    code_task_id: "core",
    changed_files: [],
    created_at: "T0",
    updated_at: "T0",
    resolved_at: "",
    ...overrides,
  };
}

const BOXES = { core: { x: 100, y: 200, width: 160, height: 80 } };

function renderIndicators(
  nodes: ErrorNode[],
  camera: { x: number; y: number; scale: number },
  onFocusOwner: (owner: string) => void,
) {
  const container = document.createElement("div");
  const root: Root = createRoot(container);
  act(() => {
    root.render(
      <ErrorEdgeIndicators
        errorNodes={nodes}
        moduleBoxes={BOXES}
        camera={camera}
        viewport={VIEWPORT}
        onFocusOwner={onFocusOwner}
      />,
    );
  });
  return {
    container,
    unmount() {
      act(() => root.unmount());
    },
  };
}

describe("ErrorEdgeIndicators", () => {
  it("shows nothing while every owner is on screen", () => {
    // World (100,200) at scale 1 + camera (0,0) lands at (100,200) — visible.
    const view = renderIndicators([makeNode()], { x: 0, y: 0, scale: 1 }, () => undefined);
    expect(view.container.querySelectorAll("button")).toHaveLength(0);
    view.unmount();
  });

  it("pins a merged reminder with icon, count and direction word when off screen", () => {
    // Same owner pushed far off the right edge.
    const view = renderIndicators(
      [makeNode(), makeNode({ id: "e2", owner_node_id: "core", severity: "warning" })],
      { x: -2000, y: 0, scale: 1 },
      () => undefined,
    );
    const button = view.container.querySelector<HTMLButtonElement>(
      "button[aria-label='视口左侧有 2 个错误，点击定位']",
    );
    expect(button).not.toBeNull();
    expect(button!.textContent).toContain("2");
    expect(button!.textContent).toContain("左侧");
    view.unmount();
  });

  it("flies the camera to the most severe owner on click", () => {
    const onFocusOwner = vi.fn();
    const view = renderIndicators(
      [
        makeNode({ owner_node_id: "warn-mod", severity: "warning", id: "w1" }),
        makeNode({ owner_node_id: "core", severity: "critical" }),
      ],
      { x: 3000, y: 3000, scale: 1 },
      onFocusOwner,
    );
    const button = view.container.querySelector<HTMLButtonElement>("button");
    act(() => {
      button!.click();
    });
    expect(onFocusOwner).toHaveBeenCalledWith("core");
    view.unmount();
  });

  it("ignores resolved errors", () => {
    const view = renderIndicators(
      [makeNode({ status: "resolved", resolved_at: "T1" })],
      { x: -2000, y: 0, scale: 1 },
      () => undefined,
    );
    expect(view.container.querySelectorAll("button")).toHaveLength(0);
    view.unmount();
  });
});
