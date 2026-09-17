// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useMicroTask } from "./useMicroTask";
import type { ProviderConfig, TaskPlan, WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  applyMicroTask: vi.fn(),
  dispatchMicroTask: vi.fn(),
  rejectTask: vi.fn(),
}));
vi.mock("../api", () => api);

const provider = {} as ProviderConfig;
const tasks = {} as TaskPlan;
const result = { tasks, task: { id: "t", summary: "summary" } };

function renderHook(initialWorkspace: WorkspaceInfo | null) {
  let workspace = initialWorkspace;
  let current: ReturnType<typeof useMicroTask>;
  const notify = vi.fn();
  const setBusy = vi.fn();
  const setTasks = vi.fn();
  const host = document.createElement("div");
  const root: Root = createRoot(host);

  function Probe() {
    current = useMicroTask({ workspace, provider, setTasks, setBusy, notify });
    return null;
  }

  act(() => root.render(<Probe />));
  return {
    get current() {
      return current!;
    },
    notify,
    setBusy,
    setTasks,
    rerender(next: WorkspaceInfo | null) {
      workspace = next;
      act(() => root.render(<Probe />));
    },
    unmount() {
      act(() => root.unmount());
    },
  };
}

const ws = (path: string) => ({ path }) as WorkspaceInfo;

afterEach(() => vi.clearAllMocks());

describe("useMicroTask workspace versioning", () => {
  it("dispatches successfully and updates the task panel", async () => {
    api.dispatchMicroTask.mockResolvedValue(result);
    const h = renderHook(ws("one"));

    await act(async () => h.current.dispatch("do it"));

    expect(h.setTasks).toHaveBeenCalledWith(tasks);
    expect(h.notify).toHaveBeenCalledWith("agent", "已生成微任务 diff：summary");
    h.unmount();
  });

  it("reports dispatch failures", async () => {
    api.dispatchMicroTask.mockRejectedValue(new Error("bad"));
    const h = renderHook(ws("one"));

    await act(async () => h.current.dispatch("do it"));

    expect(h.notify).toHaveBeenCalledWith("agent", "微任务失败：bad");
    h.unmount();
  });

  it("ignores stale dispatch responses after switching workspace", async () => {
    let resolve!: (value: typeof result) => void;
    api.dispatchMicroTask.mockReturnValue(new Promise<typeof result>((r) => { resolve = r; }));
    const h = renderHook(ws("one"));
    let pending!: Promise<void>;
    act(() => { pending = h.current.dispatch("do it"); });

    h.rerender(ws("two"));
    await act(async () => {
      resolve(result);
      await pending;
    });

    expect(h.setTasks).not.toHaveBeenCalledWith(tasks);
    expect(h.notify).not.toHaveBeenCalledWith("agent", "已生成微任务 diff：summary");
    h.unmount();
  });


  it("applies and rejects the current task", async () => {
    api.dispatchMicroTask.mockResolvedValue(result);
    api.applyMicroTask.mockResolvedValue(result);
    api.rejectTask.mockResolvedValue(tasks);
    const h = renderHook(ws("one"));

    await act(async () => h.current.dispatch("do it"));
    await act(async () => h.current.apply());
    expect(api.applyMicroTask).toHaveBeenCalledWith("one", provider, "t");

    await act(async () => h.current.dispatch("do it"));
    await act(async () => h.current.reject());
    expect(api.rejectTask).toHaveBeenCalledWith("one", "t");
    h.unmount();
  });

  it("reports apply and reject failures", async () => {
    api.dispatchMicroTask.mockResolvedValue(result);
    api.applyMicroTask.mockRejectedValue(new Error("apply"));
    api.rejectTask.mockRejectedValue(new Error("reject"));
    const h = renderHook(ws("one"));

    await act(async () => h.current.dispatch("do it"));
    await act(async () => h.current.apply());
    expect(h.notify).toHaveBeenCalledWith("agent", "应用微任务失败：apply");

    await act(async () => h.current.reject());
    expect(h.notify).toHaveBeenCalledWith("agent", "拒绝微任务失败：reject");
    h.unmount();
  });


  it("ignores stale apply responses", async () => {
    api.dispatchMicroTask.mockResolvedValue(result);
    let resolveApply!: (value: typeof result) => void;
    api.applyMicroTask.mockReturnValue(new Promise<typeof result>((r) => { resolveApply = r; }));
    const h = renderHook(ws("one"));
    await act(async () => h.current.dispatch("do it"));
    h.setTasks.mockClear();
    h.notify.mockClear();

    let applying!: Promise<void>;
    act(() => { applying = h.current.apply(); });
    h.rerender(ws("two"));
    await act(async () => {
      resolveApply(result);
      await applying;
    });

    expect(h.setTasks).not.toHaveBeenCalledWith(tasks);
    expect(h.notify).not.toHaveBeenCalledWith("agent", "微任务已应用：summary");
    h.unmount();
  });

  it("ignores stale reject responses", async () => {
    api.dispatchMicroTask.mockResolvedValue(result);
    let resolveReject!: (value: TaskPlan) => void;
    api.rejectTask.mockReturnValue(new Promise<TaskPlan>((r) => { resolveReject = r; }));
    const h = renderHook(ws("one"));
    await act(async () => h.current.dispatch("do it"));
    h.setTasks.mockClear();
    h.notify.mockClear();

    let rejecting!: Promise<void>;
    act(() => { rejecting = h.current.reject(); });
    h.rerender(ws("two"));
    await act(async () => {
      resolveReject(tasks);
      await rejecting;
    });

    expect(h.setTasks).not.toHaveBeenCalledWith(tasks);
    expect(h.notify).not.toHaveBeenCalledWith("agent", "微任务已拒绝。");
    h.unmount();
  });
});
