// @vitest-environment jsdom
(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useOrchestration } from "./useOrchestration";
import type { ProviderConfig, TaskPlan, WorkspaceInfo } from "../api";

const api = vi.hoisted(() => ({ streamOrchestrateTasks: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));

const provider = {} as ProviderConfig;
const plan = { schema_version: 1, task_version: 1, status: "planned", tasks: [], last_error: "" } as TaskPlan;
const workspace = (path: string) => ({ path, project_name: path } as WorkspaceInfo);

function deferred<T>() { let resolve!: (value: T) => void; let reject!: (reason?: unknown) => void; const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; }); return { promise, resolve, reject }; }

function renderHook(initialWorkspace: WorkspaceInfo | null) {
  let current: ReturnType<typeof useOrchestration>;
  let currentWorkspace = initialWorkspace;
  const setTasks = vi.fn(); const notify = vi.fn(); const root: Root = createRoot(document.createElement("div"));
  function Probe() { current = useOrchestration({ workspace: currentWorkspace, provider, setTasks, notify }); return null; }
  const render = () => act(() => root.render(<Probe />)); render();
  return { get current() { return current!; }, setTasks, notify, switchWorkspace(next: WorkspaceInfo | null) { currentWorkspace = next; render(); }, unmount() { act(() => root.unmount()); } };
}

afterEach(() => vi.clearAllMocks());

describe("useOrchestration", () => {
  it("maps lifecycle events and commits only the final plan", async () => {
    api.streamOrchestrateTasks.mockImplementation(async (_request, onEvent) => {
      onEvent({ type: "started", module_id: "core", scope_module_ids: ["core"] });
      onEvent({ type: "reasoning", text: "reading" });
      onEvent({ type: "content", text: " notes" });
      return plan;
    });
    const h = renderHook(workspace("one"));
    await act(async () => { await h.current.orchestrate("core", "focus"); });
    expect(api.streamOrchestrateTasks).toHaveBeenCalledWith({ path: "one", provider, module_id: "core", instruction: "focus" }, expect.any(Function), expect.any(AbortSignal));
    expect(h.current.orchestrationText).toBe("reading notes");
    expect(h.current.orchestrationModuleId).toBe("core");
    expect(h.current.orchestrationStreaming).toBe(false);
    expect(h.setTasks).toHaveBeenCalledWith(plan);
    h.unmount();
  });

  it("aborts and ignores late results after a workspace switch", async () => {
    const pending = deferred<TaskPlan>();
    let signal!: AbortSignal;
    api.streamOrchestrateTasks.mockImplementation((_request, _onEvent, nextSignal) => { signal = nextSignal; return pending.promise; });
    const h = renderHook(workspace("one"));
    act(() => { void h.current.orchestrate(); });
    h.switchWorkspace(workspace("two"));
    expect(signal.aborted).toBe(true);
    pending.resolve(plan);
    await act(async () => { await pending.promise; });
    expect(h.setTasks).not.toHaveBeenCalled();
    expect(h.current.orchestrationStreaming).toBe(false);
    h.unmount();
  });

  it("reports non-abort failures and requires a workspace", async () => {
    const empty = renderHook(null);
    await act(async () => { await empty.current.orchestrate(); });
    expect(empty.notify).toHaveBeenCalledWith("还没有选择项目地址。");
    empty.unmount();
    api.streamOrchestrateTasks.mockRejectedValue(new Error("model unavailable"));
    const h = renderHook(workspace("one"));
    await act(async () => { await h.current.orchestrate(); });
    expect(h.current.orchestrationError).toBe("model unavailable");
    expect(h.notify).toHaveBeenCalledWith("编排失败：model unavailable");
    h.unmount();
  });
});
