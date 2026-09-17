// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useTriage } from "./useTriage";
import type { ProviderConfig, TaskItem, TaskPlan, WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ latestTriage: vi.fn(), triageErrors: vi.fn(), retryTask: vi.fn(), resumeTask: vi.fn() }));
vi.mock("../api", async () => { const actual = await vi.importActual<typeof import("../api")>("../api"); return { ...actual, ...api }; });
const provider = {} as ProviderConfig;
const plan = {} as TaskPlan;
const failedTask = { id: "t1", module_id: "core", summary: "broken", target_files: ["a.ts"], depends_on: [], verification: [], status: "failed", patch: [], thinking: "", last_error: "bad" } as TaskItem;
const workspace = (path: string) => ({ path }) as WorkspaceInfo;
function renderHook(initialWorkspace: WorkspaceInfo | null) { let current: ReturnType<typeof useTriage>; let currentWorkspace = initialWorkspace; const notify = vi.fn(); const setTasks = vi.fn(); const runTaskRequest = vi.fn(async (_label: string, request: () => Promise<TaskPlan>) => request()); const focusModule = vi.fn(); const root: Root = createRoot(document.createElement("div")); function Probe() { current = useTriage({ workspace: currentWorkspace, provider, failedTasks: [failedTask], taskStreaming: false, runTaskRequest, setTasks, notify, focusModule }); return null; } act(() => root.render(<Probe />)); return { get current() { return current!; }, notify, setTasks, runTaskRequest, focusModule, switchWorkspace(next: WorkspaceInfo | null) { currentWorkspace = next; act(() => root.render(<Probe />)); }, unmount() { act(() => root.unmount()); } }; }
afterEach(() => vi.clearAllMocks());
describe("useTriage", () => {
  it("maps failed tasks and uses saved triage", async () => { const result = { summary: "saved", dependency_chains: [], groups: [], suggested_order: [] }; api.latestTriage.mockResolvedValue(result); const h = renderHook(workspace("one")); expect(h.current.failedTriageErrors[0]).toMatchObject({ task_id: "t1", detail: "bad", source: "generation" }); await act(async () => h.current.requestTriage()); expect(api.latestTriage).toHaveBeenCalledWith("one", expect.any(Array)); expect(api.triageErrors).not.toHaveBeenCalled(); expect(h.current.triageResult).toEqual(result); h.unmount(); });
  it("falls back and reports triage failures", async () => { api.latestTriage.mockResolvedValue(null); api.triageErrors.mockResolvedValue({ summary: "new", dependency_chains: [], groups: [], suggested_order: [] }); const h = renderHook(workspace("one")); await act(async () => h.current.requestTriage()); expect(api.triageErrors).toHaveBeenCalled(); api.latestTriage.mockRejectedValue(new Error("bad")); await act(async () => h.current.requestTriage()); expect(h.notify).toHaveBeenCalledWith("错误汇总失败：bad"); h.unmount(); });
  it("ignores triage response after workspace switch", async () => { let resolve!: (value: unknown) => void; api.latestTriage.mockReturnValue(new Promise((r) => { resolve = r; })); const h = renderHook(workspace("one")); let pending!: Promise<void>; act(() => { pending = h.current.requestTriage(); }); h.switchWorkspace(workspace("two")); await act(async () => { resolve({ summary: "old", dependency_chains: [], groups: [], suggested_order: [] }); await pending; }); expect(h.current.triageResult).toBeNull(); h.unmount(); });
  it("retries, resumes, and focuses", async () => { api.retryTask.mockResolvedValue(plan); api.resumeTask.mockResolvedValue(plan); const h = renderHook(workspace("one")); await act(async () => h.current.handleTriageRetryTask("t1")); expect(h.runTaskRequest).toHaveBeenCalled(); await act(async () => h.current.handleTriageResumeTask("t1")); expect(api.resumeTask).toHaveBeenCalledWith("one", provider, "t1"); act(() => h.current.handleTriageFocus("core")); expect(h.focusModule).toHaveBeenCalledWith("core"); h.unmount(); });
});
