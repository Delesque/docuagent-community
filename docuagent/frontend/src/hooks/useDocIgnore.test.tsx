// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { projectIgnoredDirs, useDocIgnore } from "./useDocIgnore";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  fetchDocIgnore: vi.fn(),
  setDocIgnore: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

const MERGED = { defaults: ["node_modules", ".venv"], ignored_dirs: ["node_modules", ".venv"] };

function renderHook(initialPath: string | null) {
  let current: ReturnType<typeof useDocIgnore>;
  let currentPath = initialPath;
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useDocIgnore(currentPath);
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

describe("projectIgnoredDirs", () => {
  it("keeps only entries the built-in list does not cover", () => {
    expect(
      projectIgnoredDirs({ defaults: ["node_modules"], ignored_dirs: ["node_modules", "vendor"] }),
    ).toEqual(["vendor"]);
  });
});

describe("useDocIgnore", () => {
  it("loads the merged view for the workspace", async () => {
    api.fetchDocIgnore.mockResolvedValue(MERGED);
    const hook = renderHook("C:/p");
    await act(async () => {});
    expect(api.fetchDocIgnore).toHaveBeenCalledWith("C:/p");
    expect(hook.current.state).toEqual(MERGED);
    expect(hook.current.projectDirs).toEqual([]);
  });

  it("reports the read failure instead of inventing a list", async () => {
    api.fetchDocIgnore.mockRejectedValue(new Error("offline"));
    const hook = renderHook("C:/p");
    await act(async () => {});
    expect(hook.current.state).toBeNull();
    expect(hook.current.error).toBe("offline");
  });

  it("does not request anything without a workspace", () => {
    renderHook(null);
    expect(api.fetchDocIgnore).not.toHaveBeenCalled();
  });

  it("adds a project directory and adopts the merged reply", async () => {
    api.fetchDocIgnore.mockResolvedValue(MERGED);
    api.setDocIgnore.mockResolvedValue({
      defaults: MERGED.defaults,
      ignored_dirs: [...MERGED.ignored_dirs, "vendor"],
    });
    const hook = renderHook("C:/p");
    await act(async () => {});
    await act(async () => {
      hook.current.add(" vendor ");
    });
    expect(api.setDocIgnore).toHaveBeenCalledWith("C:/p", ["vendor"]);
    expect(hook.current.projectDirs).toEqual(["vendor"]);
    expect(hook.current.busy).toBe(false);
  });

  it("refuses duplicates and empty names without a request", async () => {
    api.fetchDocIgnore.mockResolvedValue({
      defaults: [],
      ignored_dirs: ["vendor"],
    });
    const hook = renderHook("C:/p");
    await act(async () => {});
    expect(hook.current.add("vendor")).toBe(false);
    expect(hook.current.add("   ")).toBe(false);
    expect(api.setDocIgnore).not.toHaveBeenCalled();
  });

  it("removes a project directory", async () => {
    api.fetchDocIgnore.mockResolvedValue({
      defaults: ["node_modules"],
      ignored_dirs: ["node_modules", "vendor"],
    });
    api.setDocIgnore.mockResolvedValue({ defaults: ["node_modules"], ignored_dirs: ["node_modules"] });
    const hook = renderHook("C:/p");
    await act(async () => {});
    await act(async () => {
      hook.current.remove("vendor");
    });
    expect(api.setDocIgnore).toHaveBeenCalledWith("C:/p", []);
    expect(hook.current.projectDirs).toEqual([]);
  });

  it("surfaces a write failure and keeps the previous list", async () => {
    api.fetchDocIgnore.mockResolvedValue(MERGED);
    api.setDocIgnore.mockRejectedValue(new Error("不能包含路径分隔符"));
    const hook = renderHook("C:/p");
    await act(async () => {});
    await act(async () => {
      hook.current.add("a/b");
    });
    expect(hook.current.error).toBe("不能包含路径分隔符");
    expect(hook.current.projectDirs).toEqual([]);
  });

  it("reloads when the workspace changes", async () => {
    api.fetchDocIgnore.mockResolvedValue(MERGED);
    const hook = renderHook("C:/one");
    await act(async () => {});
    api.fetchDocIgnore.mockResolvedValue({ defaults: [], ignored_dirs: ["gen"] });
    hook.switchPath("C:/two");
    await act(async () => {});
    expect(api.fetchDocIgnore).toHaveBeenNthCalledWith(2, "C:/two");
    expect(hook.current.projectDirs).toEqual(["gen"]);
  });
});
