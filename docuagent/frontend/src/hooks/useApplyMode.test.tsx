// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useApplyMode } from "./useApplyMode";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  fetchApplyMode: vi.fn(),
  setApplyMode: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

function renderHook(initialPath: string | null) {
  let current: ReturnType<typeof useApplyMode>;
  let currentPath = initialPath;
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useApplyMode(currentPath);
    return null;
  }
  const render = () => act(() => root.render(<Probe />));
  render();
  return {
    get current() { return current!; },
    switchPath(next: string | null) {
      currentPath = next;
      render();
    },
    unmount() { act(() => root.unmount()); },
  };
}

afterEach(() => vi.clearAllMocks());

describe("useApplyMode", () => {
  it("loads the stored mode for the workspace", async () => {
    api.fetchApplyMode.mockResolvedValue("auto");
    const hook = renderHook("C:/p");
    await act(async () => {});
    expect(api.fetchApplyMode).toHaveBeenCalledWith("C:/p");
    expect(hook.current.mode).toBe("auto");
  });

  it("keeps the safe default when the read fails", async () => {
    api.fetchApplyMode.mockRejectedValue(new Error("offline"));
    const hook = renderHook("C:/p");
    await act(async () => {});
    expect(hook.current.mode).toBe("review");
  });

  it("does not request anything without a workspace", () => {
    renderHook(null);
    expect(api.fetchApplyMode).not.toHaveBeenCalled();
  });

  it("writes through and adopts the reported mode", async () => {
    api.fetchApplyMode.mockResolvedValue("review");
    api.setApplyMode.mockResolvedValue("auto");
    const hook = renderHook("C:/p");
    await act(async () => {});
    await act(async () => {
      await hook.current.setMode("auto");
    });
    expect(api.setApplyMode).toHaveBeenCalledWith("C:/p", "auto");
    expect(hook.current.mode).toBe("auto");
    expect(hook.current.busy).toBe(false);
  });

  it("reloads when the workspace changes", async () => {
    api.fetchApplyMode.mockResolvedValue("review");
    const hook = renderHook("C:/one");
    await act(async () => {});
    api.fetchApplyMode.mockResolvedValue("auto");
    hook.switchPath("C:/two");
    await act(async () => {});
    expect(api.fetchApplyMode).toHaveBeenNthCalledWith(2, "C:/two");
    expect(hook.current.mode).toBe("auto");
  });
});
