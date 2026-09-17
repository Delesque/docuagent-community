// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useComposer } from "./useComposer";
import type { BootstrapState, WorkspaceInfo } from "../api";
import { ARCHITECTURE_EDIT_TAB, CONVERSATION_TAB, MICRO_TASK_TAB } from "../conversation/workbenchConfig";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const workspace = { path: "one" } as WorkspaceInfo;
const tabs = [CONVERSATION_TAB, ARCHITECTURE_EDIT_TAB, MICRO_TASK_TAB];

function renderHook(overrides: Partial<Parameters<typeof useComposer>[0]> = {}) {
  let current!: ReturnType<typeof useComposer>;
  const say = vi.fn();
  const dispatch = vi.fn();
  const setDialogOpen = vi.fn();
  const setDrafts = vi.fn();
  const submitBootstrap = vi.fn().mockResolvedValue(true);
  const requestArchitectureEdit = vi.fn().mockResolvedValue(undefined);
  const dispatchMicroTask = vi.fn().mockResolvedValue(undefined);
  const taskProgressRef = { current: new Map([["t1", { status: "running" as const, latest: "working" }]]) };
  const progressTextRef = { current: "progress..." };
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useComposer({
      workspace,
      bootstrap: null,
      tasks: null,
      tabs,
      activeTab: CONVERSATION_TAB.id,
      taskStreaming: false,
      taskProgressRef,
      progressTextRef,
      say,
      dispatch,
      setDialogOpen,
      setDrafts,
      submitBootstrap,
      requestArchitectureEdit,
      dispatchMicroTask,
      ...overrides,
    });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, say, dispatch, setDialogOpen, setDrafts, submitBootstrap, requestArchitectureEdit, dispatchMicroTask, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useComposer", () => {
  it("answers task-progress questions while a wave is streaming", async () => {
    const h = renderHook({ taskStreaming: true });
    await act(async () => h.current({ [CONVERSATION_TAB.id]: "进度如何？" }));
    expect(h.dispatch).toHaveBeenCalledWith({ type: "updateMessage", message: expect.objectContaining({ text: expect.stringContaining("进度如何？") }) });
    expect(h.setDialogOpen).toHaveBeenCalledWith(false);
    h.unmount();
  });

  it("dispatches micro task submissions", async () => {
    const h = renderHook({ activeTab: MICRO_TASK_TAB.id });
    await act(async () => h.current({ [MICRO_TASK_TAB.id]: "把按钮改成绿色" }));
    expect(h.dispatchMicroTask).toHaveBeenCalledWith("把按钮改成绿色");
    expect(h.submitBootstrap).not.toHaveBeenCalled();
    h.unmount();
  });

  it("answers status conversations with a user and agent turn", async () => {
    const h = renderHook({ activeTab: CONVERSATION_TAB.id, bootstrap: { status: "ready", current_question: null } as BootstrapState });
    await act(async () => h.current({ [CONVERSATION_TAB.id]: "现在什么状态？" }));
    expect(h.say).toHaveBeenCalledWith({ role: "user", text: "现在什么状态？" });
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ role: "agent", text: expect.stringContaining("当前项目状态：ready") }));
    h.unmount();
  });

  it("delegates architecture edits", async () => {
    const h = renderHook({ activeTab: ARCHITECTURE_EDIT_TAB.id });
    await act(async () => h.current({ [ARCHITECTURE_EDIT_TAB.id]: "拆出认证模块" }));
    expect(h.requestArchitectureEdit).toHaveBeenCalledWith("拆出认证模块");
    h.unmount();
  });

  it("falls through to bootstrap submission for interview tabs", async () => {
    const h = renderHook({ activeTab: "name", tabs: [{ id: "name", label: "项目名称", promptKey: "project_name", placeholder: "name" }] });
    await act(async () => h.current({ name: "Demo" }));
    expect(h.submitBootstrap).toHaveBeenCalledWith({ name: "Demo" });
    h.unmount();
  });
});
