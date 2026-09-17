// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useArchitectureWorkflow } from "./useArchitectureWorkflow";
import type { Architecture, BootstrapState, ProviderConfig, TaskPlan, WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ bootstrapConfirm: vi.fn(), bootstrapFinalize: vi.fn(), streamOnboardStart: vi.fn(), streamEditArchitecture: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));

const provider = {} as ProviderConfig;
const workspace = { path: "one", project_name: "One" } as WorkspaceInfo;
const architecture = { modules: [] } as unknown as Architecture;
const state = (status: BootstrapState["status"], work_state: TaskPlan | null = null) => ({ status, work_state, architecture, project: { name: "One", slug: "one", root: "one", mode: "new" }, answers: {}, current_question: null, progress: 1, agent_mode: "guided", model_name: null, model_notice: null, thinking: "" } as BootstrapState);

function renderHook(initialWorkspace: WorkspaceInfo | null = workspace, configured = true) {
  let current: ReturnType<typeof useArchitectureWorkflow>;
  const say = vi.fn(); const refreshSnapshots = vi.fn(); const refreshAttachments = vi.fn(); const applyBootstrapTurn = vi.fn(); const setTasks = vi.fn(); const setEditedArchitecture = vi.fn(); const setUndoAvailable = vi.fn(); const setRevealOnOpen = vi.fn(); const setBusy = vi.fn(); const setModelStreaming = vi.fn(); const setSettingsOpen = vi.fn(); const setThinking = vi.fn(); const setRetryStatus = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() { current = useArchitectureWorkflow({ workspace: initialWorkspace, provider, configured, say, refreshSnapshots, refreshAttachments, applyBootstrapTurn, setTasks, setEditedArchitecture, setUndoAvailable, setRevealOnOpen, setBusy, setModelStreaming, setSettingsOpen, setThinking, setRetryStatus }); return null; }
  act(() => root.render(<Probe />));
  return { get current() { return current!; }, say, refreshSnapshots, refreshAttachments, applyBootstrapTurn, setTasks, setEditedArchitecture, setUndoAvailable, setRevealOnOpen, setBusy, setModelStreaming, setSettingsOpen, setThinking, unmount() { act(() => root.unmount()); } };
}

afterEach(() => vi.clearAllMocks());
describe("useArchitectureWorkflow", () => {
  it("confirms then finalizes through one state-machine action", async () => {
    const planned = {} as TaskPlan;
    api.bootstrapConfirm.mockResolvedValue(state("ready"));
    api.bootstrapFinalize.mockResolvedValue(state("initialized", planned));
    const h = renderHook();
    await act(async () => h.current.confirm());
    expect(api.bootstrapConfirm).toHaveBeenCalledWith({ path: "one", provider }, expect.objectContaining({ retries: 10 }));
    expect(api.bootstrapFinalize).toHaveBeenCalledWith({ path: "one", provider }, expect.objectContaining({ retries: 10 }));
    expect(h.setTasks).toHaveBeenCalledWith(planned);
    expect(h.applyBootstrapTurn).toHaveBeenCalledWith(expect.objectContaining({ status: "initialized" }));
    expect(h.setRevealOnOpen).toHaveBeenCalledWith(true);
    h.unmount();
  });

  it("blocks onboarding until a provider is configured", async () => {
    const h = renderHook(workspace, false);
    await act(async () => h.current.onboard());
    expect(api.streamOnboardStart).not.toHaveBeenCalled();
    expect(h.setSettingsOpen).toHaveBeenCalledWith(true);
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("配置 AI 模型") }));
    h.unmount();
  });

  it("streams onboarding reasoning then projects the reviewed state", async () => {
    api.streamOnboardStart.mockImplementation(async (_request: unknown, onEvent: (event: { type: string; text?: string }) => void) => { onEvent({ type: "reasoning", text: "分析中" }); return state("review"); });
    const h = renderHook();
    await act(async () => h.current.onboard());
    expect(h.setThinking).toHaveBeenCalledWith("分析中", "模型正在思考…");
    expect(h.applyBootstrapTurn).toHaveBeenCalledWith(expect.objectContaining({ status: "review" }));
    expect(h.refreshAttachments).toHaveBeenCalled();
    h.unmount();
  });

  it("streams an edit and applies only the returned architecture state", async () => {
    const result = { architecture, architecture_version: 2, thinking: "done", changes: [], history_remaining: 1, work_state: null };
    api.streamEditArchitecture.mockImplementation(async (_request: unknown, onEvent: (event: { type: string; text?: string }) => void) => { onEvent({ type: "reasoning", text: "编辑中" }); return result; });
    const h = renderHook();
    await act(async () => h.current.edit("增加模块"));
    expect(h.setEditedArchitecture).toHaveBeenCalledWith(architecture);
    expect(h.setTasks).toHaveBeenCalledWith(null);
    expect(h.setUndoAvailable).toHaveBeenCalledWith(true);
    expect(h.setThinking).toHaveBeenCalledWith("编辑中", "模型正在思考…");
    h.unmount();
  });
});
