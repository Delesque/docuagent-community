// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useBootstrapTurn } from "./useBootstrapTurn";
import type { BootstrapState } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const base = {
  status: "interviewing",
  work_state: null,
  project: { name: "Demo", slug: "demo", root: "C:/demo", mode: "new" },
  architecture: { summary: "", platform: "", language: "", runtime: "", frameworks: [], stack: [], modules: [{ id: "core", name: "Core", brief: "logic", responsibility: "", path: "src", depends_on: [], needs_ui: false, group: null }], groups: [], edges: [], data: [], integrations: [], constraints: [], verification: [], risks: [], unresolved: [] },
  answers: {},
  current_question: null,
  progress: 0,
  agent_mode: "",
  model_name: null,
  model_notice: null,
  thinking: "model reasoning",
} as BootstrapState;

function renderHook() {
  let current!: ReturnType<typeof useBootstrapTurn>;
  const setBootstrap = vi.fn();
  const setDialogOpen = vi.fn();
  const setDrafts = vi.fn();
  const setView = vi.fn();
  const setTabs = vi.fn();
  const setActiveTab = vi.fn();
  const dispatch = vi.fn();
  const say = vi.fn();
  const refreshAttachments = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useBootstrapTurn({ setBootstrap, setDialogOpen, setDrafts, setView, setTabs, setActiveTab, dispatch, say, refreshAttachments });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, setBootstrap, setDialogOpen, setDrafts, setView, setTabs, setActiveTab, dispatch, say, refreshAttachments, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useBootstrapTurn", () => {
  it("applies a question turn through the shared projection", () => {
    const h = renderHook();
    const next = { ...base, current_question: { id: "audience", title: "用户", prompt: "谁会使用？", placeholder: "目标用户", options: ["个人", "团队"] } } as BootstrapState;
    act(() => h.current(next));
    expect(h.setBootstrap).toHaveBeenCalledWith(next);
    expect(h.setDialogOpen).toHaveBeenCalledWith(false);
    expect(h.setDrafts).toHaveBeenCalledWith({});
    expect(h.dispatch).toHaveBeenCalledWith({ type: "setThinking", thinking: "model reasoning", fallback: expect.stringContaining("audience") });
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ role: "agent", text: expect.stringContaining("DocuAgent: 谁会使用？ →「用户」") }));
    // The turn's design pulse rides after the question: counts only, never the draft.
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("当前草案：1 个模块") }));
    expect(h.setTabs).toHaveBeenCalledWith([{ id: "audience", label: "用户", promptKey: "audience", placeholder: "目标用户" }]);
    expect(h.setActiveTab).toHaveBeenCalledWith("audience");
    expect(h.refreshAttachments).not.toHaveBeenCalled();
    h.unmount();
  });

  it("applies a ready/initialized turn and refreshes attachments for onboard results", () => {
    const h = renderHook();
    const next = { ...base, status: "initialized", onboard: true, suggestions: [{ module_id: "core", title: "Fix", detail: "", kind: "fix", files: [], priority: "high" }] } as BootstrapState;
    act(() => h.current(next));
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ role: "agent", text: expect.stringContaining("项目已接入") }));
    expect(h.setTabs).toHaveBeenCalledWith([expect.objectContaining({ id: "conversation" })]);
    expect(h.setActiveTab).toHaveBeenCalledWith("conversation");
    expect(h.refreshAttachments).toHaveBeenCalledTimes(1);
    h.unmount();
  });

  it("applies a review turn without refreshing attachments", () => {
    const h = renderHook();
    const next = { ...base, status: "review" } as BootstrapState;
    act(() => h.current(next));
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("共 1 个模块") }));
    expect(h.setActiveTab).toHaveBeenCalledWith("revise");
    expect(h.refreshAttachments).not.toHaveBeenCalled();
    h.unmount();
  });
});
