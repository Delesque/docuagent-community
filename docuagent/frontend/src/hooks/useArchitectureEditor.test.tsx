// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useArchitectureEditor } from "./useArchitectureEditor";
import type { ArchitectureEditResult, ArchitectureUndoResult, WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ undoArchitecture: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));

const workspace = { path: "one" } as WorkspaceInfo;
const architecture = { summary: "", platform: "", language: "", runtime: "", frameworks: [], stack: [], modules: [{ id: "core", name: "Core", brief: "logic", responsibility: "", path: "src", depends_on: [], needs_ui: false, group: null }], groups: [], edges: [], data: [], integrations: [], constraints: [], verification: [], risks: [], unresolved: [] };
const editResult = { architecture, architecture_version: 3, thinking: "think", changes: ["拆分认证模块"], history_remaining: 1 } as ArchitectureEditResult;
const undoResult = { architecture, architecture_version: 2, history_remaining: 0 } as ArchitectureUndoResult;

function renderHook(overrides: Partial<Parameters<typeof useArchitectureEditor>[0]> = {}) {
  let current!: ReturnType<typeof useArchitectureEditor>;
  const say = vi.fn();
  const runArchitectureEdit = vi.fn().mockResolvedValue(editResult);
  const setBusy = vi.fn();
  const setDialogOpen = vi.fn();
  const setDrafts = vi.fn();
  const setView = vi.fn();
  const setTabs = vi.fn();
  const setActiveTab = vi.fn();
  const setEditedArchitecture = vi.fn();
  const setUndoAvailable = vi.fn();
  const dispatch = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useArchitectureEditor({ workspace, say, runArchitectureEdit, setBusy, setDialogOpen, setDrafts, setView, setTabs, setActiveTab, setEditedArchitecture, setUndoAvailable, dispatch, ...overrides });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, say, runArchitectureEdit, setBusy, setDialogOpen, setDrafts, setView, setTabs, setActiveTab, setEditedArchitecture, setUndoAvailable, dispatch, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useArchitectureEditor", () => {
  it("edits an architecture and wraps the turn with the new version", async () => {
    const h = renderHook();
    await act(async () => h.current.requestArchitectureEdit("拆分认证模块"));
    expect(h.say).toHaveBeenCalledWith({ role: "user", text: "修改架构：拆分认证模块" });
    expect(h.runArchitectureEdit).toHaveBeenCalledWith("拆分认证模块");
    expect(h.dispatch).toHaveBeenCalledWith({ type: "setThinking", thinking: "think", fallback: "· 拆分认证模块" });
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("架构已修改（版本 3）") }));
    expect(h.setTabs).toHaveBeenCalledWith([expect.objectContaining({ id: "conversation" })]);
    expect(h.setActiveTab).toHaveBeenCalledWith("conversation");
    expect(h.setDialogOpen).toHaveBeenCalledWith(false);
    h.unmount();
  });

  it("undoes to the previous architecture version", async () => {
    api.undoArchitecture.mockResolvedValue(undoResult);
    const h = renderHook();
    await act(async () => h.current.undoArchitectureEdit());
    expect(api.undoArchitecture).toHaveBeenCalledWith("one");
    expect(h.setEditedArchitecture).toHaveBeenCalledWith(architecture);
    expect(h.setUndoAvailable).toHaveBeenCalledWith(false);
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("已撤销到架构版本 2") }));
    h.unmount();
  });

  it("reports failures and keeps the edit tab available", async () => {
    const h = renderHook({ runArchitectureEdit: vi.fn().mockRejectedValue(new Error("boom")) });
    await act(async () => h.current.requestArchitectureEdit("改"));
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("修改架构失败：boom") }));
    expect(h.setTabs).toHaveBeenCalledWith([expect.objectContaining({ id: "conversation" })]);
    h.unmount();
  });
});
