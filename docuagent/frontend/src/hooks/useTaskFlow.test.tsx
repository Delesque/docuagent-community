// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTaskFlow } from "./useTaskFlow";
import type {
  Architecture,
  ProviderConfig,
  TaskPlan,
  TaskStreamEvent,
  WorkspaceInfo,
} from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  streamWorkStart: vi.fn(),
  streamVerifyTask: vi.fn(),
  applyTask: vi.fn(),
  rejectTask: vi.fn(),
  retryTask: vi.fn(),
  editTaskPatch: vi.fn(),
  applyTaskHunks: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

const provider = {} as ProviderConfig;
const architecture = { modules: [{ id: "core", name: "Core" }] } as Architecture;
const task = (overrides: Partial<Record<string, unknown>> = {}) => ({
  id: "t1",
  module_id: "core",
  summary: "implement core",
  target_files: ["src/core.ts"],
  depends_on: [],
  verification: ["pytest"],
  status: "pending",
  patch: [],
  thinking: "",
  last_error: "",
  ...overrides,
});
const plan = (tasks = [task()]): TaskPlan => ({
  schema_version: 1,
  task_version: 1,
  status: "planned",
  tasks: tasks as TaskPlan["tasks"],
  last_error: "",
});
const workspace = (path: string) => ({ path, project_name: path } as WorkspaceInfo);

type Options = Parameters<typeof useTaskFlow>[0];
function renderHook(initialWorkspace: WorkspaceInfo | null, initialTasks: TaskPlan | null = plan()) {
  let current: ReturnType<typeof useTaskFlow>;
  let currentWorkspace = initialWorkspace;
  let currentTasks = initialTasks;
  const notify = vi.fn();
  const setTasks = vi.fn((next: TaskPlan | null | ((prev: TaskPlan | null) => TaskPlan | null)) => {
    currentTasks = typeof next === "function" ? next(currentTasks) : next;
  });
  const setBusy = vi.fn();
  const refreshSnapshots = vi.fn();
  const refreshAgents = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    const options: Options = {
      workspace: currentWorkspace,
      provider,
      architecture,
      tasks: currentTasks,
      setTasks,
      setBusy,
      say: (message) => notify(message.text),
      refreshSnapshots,
      refreshAgents,
      streamWorkStart: api.streamWorkStart,
      streamVerifyTask: api.streamVerifyTask,
      applyTask: api.applyTask,
      rejectTask: api.rejectTask,
      retryTask: api.retryTask,
      editTaskPatch: api.editTaskPatch,
      applyTaskHunks: api.applyTaskHunks,
    };
    current = useTaskFlow(options);
    return null;
  }
  const render = () => act(() => root.render(<Probe />));
  render();
  return {
    get current() { return current!; },
    notify,
    setTasks,
    setBusy,
    refreshSnapshots,
    refreshAgents,
    switchWorkspace(next: WorkspaceInfo | null) {
      currentWorkspace = next;
      currentTasks = null;
      render();
    },
    setPlan(next: TaskPlan | null) {
      currentTasks = next;
      render();
    },
    unmount() { act(() => root.unmount()); },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

afterEach(() => vi.clearAllMocks());

describe("useTaskFlow", () => {
  it("starts disabled without an architecture/tasks and respects dependencies and streaming", async () => {
    const empty = renderHook(workspace("one"), null);
    expect(empty.current.canStartWork).toBe(false);
    empty.setPlan(plan([task({ id: "dep", status: "pending" }), task({ id: "blocked", depends_on: ["dep"] })]));
    expect(empty.current.canStartWork).toBe(true);
    empty.unmount();

    api.streamWorkStart.mockReturnValue(new Promise(() => undefined));
    const h = renderHook(workspace("one"));
    act(() => { void h.current.startWork(); });
    expect(h.current.taskStreaming).toBe(true);
    expect(h.current.canStartWork).toBe(false);
    h.unmount();
  });

  it("applies task_started, reasoning, sandbox, and done events", async () => {
    const pending = deferred<TaskPlan>();
    let onEvent!: (event: TaskStreamEvent) => void;
    api.streamWorkStart.mockImplementation((_path, _provider, callback) => {
      onEvent = callback;
      return pending.promise;
    });
    const h = renderHook(workspace("one"));
    act(() => { void h.current.startWork(); });
    act(() => onEvent({ type: "task_started", task_id: "t1" }));
    act(() => onEvent({ type: "task_reasoning", task_id: "t1", text: "think" }));
    act(() => onEvent({ type: "task_sandbox_start", task_id: "t1", text: "write" }));
    act(() => onEvent({ type: "task_done", task_id: "t1", status: "review" }));
    expect((h.current.taskTails.byTask.t1 ?? []).join("")).toContain("think");
    expect((h.current.taskTails.byTask.t1 ?? []).join("")).toContain("write");
    expect(h.setTasks).toHaveBeenCalled();
    pending.resolve(plan([task({ status: "review" })]));
    await act(async () => { await pending.promise; });
    expect(h.current.taskStreaming).toBe(false);
    h.unmount();
  });

  it("replaces the final plan from the done event and reports verification output", async () => {
    const finalPlan = plan([task({ status: "review", summary: "final" })]);
    api.streamWorkStart.mockImplementation(async (_path, _provider, onEvent) => {
      onEvent({ type: "done", tasks: finalPlan });
      return finalPlan;
    });
    api.streamVerifyTask.mockImplementation(async (_path, _id, onEvent) => {
      onEvent({ type: "output", line: "pytest ok" });
      return plan([task({ status: "verified", verification_output: { stdout: "ok", stderr: "", returncode: 0, command: "pytest", timestamp: "now" } })]);
    });
    const h = renderHook(workspace("one"));
    await act(async () => { await h.current.startWork(); });
    expect(h.setTasks).toHaveBeenCalledWith(finalPlan);
    await act(async () => { await h.current.verifyTask("t1"); });
    expect(api.streamVerifyTask).toHaveBeenCalledWith("one", "t1", expect.any(Function));
    expect((h.current.taskTails.byTask.t1 ?? []).join("")).toContain("pytest ok");
    h.unmount();
  });

  it("delegates apply, reject, retry, savePatch, and applyHunks with workspace path", async () => {
    const result = plan([task({ status: "applied" })]);
    api.applyTask.mockResolvedValue(result);
    api.rejectTask.mockResolvedValue(result);
    api.retryTask.mockResolvedValue(result);
    api.editTaskPatch.mockResolvedValue(result);
    api.applyTaskHunks.mockResolvedValue(result);
    const h = renderHook(workspace("one"));
    await act(async () => {
      await h.current.applyTask("t1");
      await h.current.rejectTask("t1");
      await h.current.retryTask("t1");
      expect(await h.current.savePatch("t1", "a.ts", "new", "old")).toBe(true);
      expect(await h.current.applyHunks("t1", "a.ts", [1, 3])).toBe(true);
    });
    expect(api.applyTask).toHaveBeenCalledWith("one", "t1");
    expect(api.rejectTask).toHaveBeenCalledWith("one", "t1");
    expect(api.retryTask).toHaveBeenCalledWith("one", provider, "t1");
    expect(api.editTaskPatch).toHaveBeenCalledWith("one", "t1", "a.ts", "new", "old");
    expect(api.applyTaskHunks).toHaveBeenCalledWith("one", "t1", "a.ts", [1, 3]);
    expect(h.current.busyTaskId).toBeNull();
    h.unmount();
  });

  it("notifies failures and clears busy state for wave and task actions", async () => {
    api.streamWorkStart.mockRejectedValue(new Error("wave bad"));
    api.streamVerifyTask.mockRejectedValue(new Error("verify bad"));
    api.retryTask.mockRejectedValue(new Error("retry bad"));
    const h = renderHook(workspace("one"));
    await act(async () => { await h.current.startWork(); });
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("失败：wave bad"));
    await act(async () => { await h.current.verifyTask("t1"); });
    expect(h.notify).toHaveBeenCalledWith("验证失败：verify bad");
    await act(async () => { await h.current.retryTask("t1"); });
    expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("失败：retry bad"));
    expect(h.current.busyTaskId).toBeNull();
    expect(h.current.taskStreaming).toBe(false);
    h.unmount();
  });

  it("stops an active wave by aborting its signal and cleans refs", async () => {
    const pending = deferred<TaskPlan>();
    api.streamWorkStart.mockImplementation((_path, _provider, _onEvent, signal: AbortSignal) => {
      signal.addEventListener("abort", () => pending.reject(new DOMException("aborted", "AbortError")));
      return pending.promise;
    });
    const h = renderHook(workspace("one"));
    act(() => { void h.current.startWork(); });
    act(() => h.current.stopWork());
    await act(async () => { await pending.promise.catch(() => undefined); });
    expect(h.current.taskStreaming).toBe(false);
    expect(h.current.taskTails).toEqual({ byTask: {}, byModule: {} });
    h.unmount();
  });

  it("ignores a late stream result after switching workspace", async () => {
    const pending = deferred<TaskPlan>();
    api.streamWorkStart.mockReturnValue(pending.promise);
    const h = renderHook(workspace("one"));
    act(() => { void h.current.startWork(); });
    h.switchWorkspace(workspace("two"));
    pending.resolve(plan([task({ summary: "old result" })]));
    await act(async () => { await pending.promise; });
    expect(h.current.taskStreaming).toBe(false);
    expect(h.current.taskTails).toEqual({ byTask: {}, byModule: {} });
    expect(h.setTasks).not.toHaveBeenCalledWith(expect.objectContaining({ tasks: expect.arrayContaining([expect.objectContaining({ summary: "old result" })]) }));
    h.unmount();
  });

  it("keeps progress refs and per-task tails available while streaming", async () => {
    const pending = deferred<TaskPlan>();
    let onEvent!: (event: TaskStreamEvent) => void;
    api.streamWorkStart.mockImplementation((_path, _provider, callback) => { onEvent = callback; return pending.promise; });
    const h = renderHook(workspace("one"), plan([task(), task({ id: "t2", module_id: "core" })]));
    act(() => { void h.current.startWork(); });
    act(() => onEvent({ type: "task_started", task_id: "t1" }));
    act(() => onEvent({ type: "task_reasoning", task_id: "t1", text: "one\n" }));
    act(() => onEvent({ type: "task_reasoning", task_id: "t1", text: "two\n" }));
    act(() => onEvent({ type: "task_started", task_id: "t2" }));
    act(() => onEvent({ type: "task_sandbox_start", task_id: "t2", text: "sandbox\n" }));
    expect((h.current.taskTails.byTask.t1 ?? []).join("")).toContain("one");
    expect((h.current.taskTails.byTask.t1 ?? []).join("")).toContain("two");
    expect((h.current.taskTails.byTask.t2 ?? []).join("")).toContain("sandbox");
    expect(h.current.taskTails.byModule.core).toEqual(h.current.taskTails.byTask.t2);
    pending.resolve(plan());
    await act(async () => { await pending.promise; });
    expect(h.current.taskStreaming).toBe(false);
    h.unmount();
  });
});
