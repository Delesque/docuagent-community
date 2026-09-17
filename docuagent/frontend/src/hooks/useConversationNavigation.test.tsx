// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useConversationNavigation } from "./useConversationNavigation";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function renderHook(total: number, nodeSelectedOnCanvas: boolean) {
  const setView = vi.fn();
  const setProjection = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    useConversationNavigation({ total, nodeSelectedOnCanvas, setView, setProjection });
    return null;
  }
  act(() => root.render(<Probe />));
  return { setView, setProjection, unmount: () => act(() => root.unmount()) };
}

function fireWheel(deltaY: number, ctrlKey = false) {
  window.dispatchEvent(new WheelEvent("wheel", { deltaY, deltaMode: 0, ctrlKey }));
}

function fireKey(key: string, options: KeyboardEventInit = {}) {
  window.dispatchEvent(new KeyboardEvent("keydown", { key, ...options }));
}

afterEach(() => vi.clearAllMocks());

describe("useConversationNavigation", () => {
  it("pages through turns on unmodified wheel", () => {
    const h = renderHook(3, false);
    fireWheel(120);
    expect(h.setView).toHaveBeenCalledWith(expect.any(Function));
    h.unmount();
  });

  it("ignores ctrl+wheel so the graph camera keeps the gesture", () => {
    const h = renderHook(3, false);
    fireWheel(120, true);
    expect(h.setView).not.toHaveBeenCalled();
    h.unmount();
  });

  it("swaps projection on Alt+O", () => {
    const h = renderHook(3, false);
    fireKey("o", { altKey: true });
    expect(h.setProjection).toHaveBeenCalledWith(expect.any(Function));
    h.unmount();
  });

  it("lets canvas arrows win while a node is selected", () => {
    const selected = renderHook(3, true);
    fireKey("ArrowDown");
    expect(selected.setView).not.toHaveBeenCalled();
    selected.unmount();

    const unselected = renderHook(3, false);
    fireKey("ArrowDown");
    expect(unselected.setView).toHaveBeenCalledWith(expect.any(Function));
    unselected.unmount();
  });
});
