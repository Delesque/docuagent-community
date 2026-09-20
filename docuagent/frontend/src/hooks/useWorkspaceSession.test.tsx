// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useWorkspaceSession } from "./useWorkspaceSession";
import type { WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ chooseFolder: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));

const chosen: WorkspaceInfo = {
  path: "one",
  exists: true,
  mode: "imported",
  entries: [],
  bootstrap: null,
  architecture: { summary: "", platform: "", language: "", runtime: "", frameworks: [], stack: [], modules: [{ id: "core", name: "Core", brief: "logic", responsibility: "", path: "src", depends_on: [], needs_ui: false, group: null }], groups: [], edges: [], data: [], integrations: [], constraints: [], verification: [], risks: [], unresolved: [] },
  project_name: "One",
  ui_state: null,
  conversation: [{ role: "assistant", content: "hello" }],
  architecture_can_undo: false,
  stale_modules: [],
  work_state: null,
};

function renderHook() {
  let current!: ReturnType<typeof useWorkspaceSession>;
  const say = vi.fn();
  const setBooted = vi.fn();
  const setWorkspace = vi.fn();
  const setBootstrap = vi.fn();
  const setTasks = vi.fn();
  const setEditedArchitecture = vi.fn();
  const setRevealOnOpen = vi.fn();
  const setTabs = vi.fn();
  const setDrafts = vi.fn();
  const setActiveTab = vi.fn();
  const dispatch = vi.fn();
  const resetMicroTask = vi.fn();
  const suppressGraphIntro = { current: false };
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useWorkspaceSession({ workspace: null, say, setBooted, setWorkspace, setBootstrap, setTasks, setEditedArchitecture, setRevealOnOpen, setTabs, setDrafts, setActiveTab, dispatch, resetMicroTask, suppressGraphIntro });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, say, setBooted, setWorkspace, setBootstrap, setTasks, setEditedArchitecture, setRevealOnOpen, setTabs, setDrafts, setActiveTab, dispatch, resetMicroTask, suppressGraphIntro, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useWorkspaceSession", () => {
  it("reopens an architecture-only project without restarting the new-project interview", async () => {
    const h = renderHook();
    await act(async () => h.current.loadWorkspace({ ...chosen, mode: "new", conversation: [] }));
    expect(h.setTabs).toHaveBeenCalledWith([expect.objectContaining({ id: "conversation" })]);
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("已有架构") }));
    h.unmount();
  });

  it("does not offer unavailable onboarding for unmanaged existing code", async () => {
    const h = renderHook();
    await act(async () => h.current.loadWorkspace({
      ...chosen, architecture: null, conversation: [],
      capabilities: { project_reconstruction: false },
    }));
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({
      chips: [{ text: "项目地址", type: "input" }],
    }));
    expect(h.setTabs).toHaveBeenCalledWith([]);
    expect(h.setActiveTab).toHaveBeenCalledWith(null);
    h.unmount();
  });

  it("loads a workspace and resets per-project state while restoring the transcript", async () => {
    const h = renderHook();
    await act(async () => h.current.loadWorkspace(chosen));
    expect(h.setBooted).toHaveBeenCalledWith(true);
    expect(h.setWorkspace).toHaveBeenCalledWith(chosen);
    expect(h.setBootstrap).toHaveBeenCalledWith(null);
    expect(h.setTasks).toHaveBeenCalledWith(null);
    expect(h.setEditedArchitecture).toHaveBeenCalledWith(null);
    expect(h.resetMicroTask).toHaveBeenCalledTimes(1);
    expect(h.dispatch).toHaveBeenCalledWith({ type: "reset" });
    expect(h.dispatch).toHaveBeenCalledWith({ type: "message", message: expect.objectContaining({ role: "agent", text: "hello" }) });
    // Resuming says nothing: the transcript already ends where the user left off.
    expect(h.say).not.toHaveBeenCalled();
    expect(h.setTabs).toHaveBeenCalledWith([expect.objectContaining({ id: "conversation" })]);
    expect(h.setActiveTab).toHaveBeenCalledWith("conversation");
    h.unmount();
  });

  it("rebuilds the input for a question that was still open when the project was left", async () => {
    const h = renderHook();
    const resumed = {
      ...chosen,
      bootstrap: {
        status: "interviewing",
        current_question: { id: "audience", title: "用户", prompt: "谁会使用？", placeholder: "目标用户" },
      },
    } as unknown as WorkspaceInfo;
    await act(async () => h.current.loadWorkspace(resumed));
    // Nothing is appended — the open question is the last thing in the transcript, and the
    // point of the fix is that its own input comes back instead of a generic one.
    expect(h.say).not.toHaveBeenCalled();
    expect(h.setTabs).toHaveBeenCalledWith([
      expect.objectContaining({ id: "audience", label: "用户" }),
    ]);
    expect(h.setActiveTab).toHaveBeenCalledWith("audience");
    h.unmount();
  });

  it("restores the chips that belong to a message", async () => {
    const h = renderHook();
    const withChips = {
      ...chosen,
      conversation: [
        {
          role: "assistant",
          content: "架构草案已完整。",
          chips: [
            { text: "确认架构", type: "action" },
            { text: "查看架构图", type: "view", detail: "打开图形视图" },
          ],
        },
      ],
    } as unknown as WorkspaceInfo;
    await act(async () => h.current.loadWorkspace(withChips));
    expect(h.dispatch).toHaveBeenCalledWith({
      type: "message",
      message: expect.objectContaining({
        text: "架构草案已完整。",
        chips: [
          { text: "确认架构", type: "action" },
          { text: "查看架构图", type: "view", detail: "打开图形视图" },
        ],
      }),
    });
    h.unmount();
  });

  it("keeps the screen untouched when the folder picker is cancelled", async () => {
    api.chooseFolder.mockResolvedValue({ cancelled: true, workspace: null });
    const h = renderHook();
    await act(async () => h.current.openPathPicker());
    expect(api.chooseFolder).toHaveBeenCalledWith("");
    expect(h.setWorkspace).not.toHaveBeenCalled();
    expect(h.say).not.toHaveBeenCalled();
    h.unmount();
  });

  it("reports folder picker failures as an agent message", async () => {
    api.chooseFolder.mockRejectedValue(new Error("denied"));
    const h = renderHook();
    await act(async () => h.current.openPathPicker());
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("没能打开目录选择器：denied") }));
    h.unmount();
  });
});
