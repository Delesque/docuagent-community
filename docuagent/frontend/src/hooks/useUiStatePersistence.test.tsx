// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStatePersistence } from "./useUiStatePersistence";
import { useGraphStore, type GraphStore } from "../graph/store";
import type { WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ saveUiState: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));

const workspace = { path: "one" } as WorkspaceInfo;

function renderHook() {
  let store!: GraphStore;
  let ui!: ReturnType<typeof useUiStatePersistence>;
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    store = useGraphStore(null, null);
    ui = useUiStatePersistence({ workspace, store });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get store() { return store; }, get ui() { return ui; }, unmount: () => act(() => root.unmount()) };
}

beforeEach(() => { vi.useFakeTimers(); api.saveUiState.mockResolvedValue(undefined); });
afterEach(() => { vi.useRealTimers(); vi.clearAllMocks(); });

describe("useUiStatePersistence", () => {
  it("saves pinned positions on explicit layout save", () => {
    const h = renderHook();
    act(() => h.ui.handlePositions({ core: { x: 1, y: 2, pinned: true } }));
    act(() => h.ui.saveLayoutNow());
    expect(api.saveUiState).toHaveBeenCalledWith("one", expect.objectContaining({ nodes: { core: { x: 1, y: 2, pinned: true } } }));
    h.unmount();
  });

  it("moves the camera immediately and debounces persistence", () => {
    const h = renderHook();
    act(() => h.ui.setCamera({ x: 3, y: 4, scale: 1 }));
    expect(h.store.camera).toEqual({ x: 3, y: 4, scale: 1 });
    expect(api.saveUiState).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(600));
    expect(api.saveUiState).toHaveBeenCalledWith("one", expect.objectContaining({ camera: { x: 3, y: 4, scale: 1 } }));
    h.unmount();
  });

  it("restores default layout by releasing pins and saving an empty node map", () => {
    const h = renderHook();
    act(() => { h.store.dispatch({ type: "moveNode", nodeId: "core", x: 5, y: 6 }); });
    expect(h.store.pinned.core).toBe(true);
    act(() => h.ui.restoreDefaultLayout());
    expect(h.store.pinned.core).toBe(undefined);
    expect(api.saveUiState).toHaveBeenCalledWith("one", expect.objectContaining({ nodes: {} }));
    h.unmount();
  });
});
