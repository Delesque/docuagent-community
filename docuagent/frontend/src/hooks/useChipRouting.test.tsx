// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useChipRouting } from "./useChipRouting";
import { ARCHITECTURE_EDIT_TAB, MICRO_TASK_TAB, SUBAGENT_STATUS_TAB } from "../conversation/workbenchConfig";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function renderHook(tabs = [{ id: "name", label: "项目名称", promptKey: "project_name", placeholder: "name" }]) {
  let current!: ReturnType<typeof useChipRouting>;
  const openPathPicker = vi.fn();
  const setSuggestionsOpen = vi.fn();
  const setProvenanceLedgerOpen = vi.fn();
  const setSettingsOpen = vi.fn();
  const setTabs = vi.fn();
  const setDrafts = vi.fn();
  const setActiveTab = vi.fn();
  const setDialogOpen = vi.fn();
  const handleStartWorkRef = { current: vi.fn() };
  const handleConfirmArchitectureRef = { current: vi.fn() };
  const handleConfirmAllRef = { current: vi.fn() };
  const handleOnboardRef = { current: vi.fn() };
  const storeDispatch = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useChipRouting({ tabs, openPathPicker, setSuggestionsOpen, setProvenanceLedgerOpen, setSettingsOpen, setTabs, setDrafts, setActiveTab, setDialogOpen, handleStartWorkRef, handleConfirmArchitectureRef, handleConfirmAllRef, handleOnboardRef, storeDispatch });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, openPathPicker, setSuggestionsOpen, setProvenanceLedgerOpen, setSettingsOpen, setTabs, setDrafts, setActiveTab, setDialogOpen, handleStartWorkRef, handleConfirmArchitectureRef, handleConfirmAllRef, handleOnboardRef, storeDispatch, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useChipRouting", () => {
  it("routes action chips to the corresponding refs", () => {
    const h = renderHook();
    act(() => h.current({ text: "接入分析", type: "action" }));
    expect(h.handleOnboardRef.current).toHaveBeenCalled();
    act(() => h.current({ text: "开始实现", type: "action" }));
    expect(h.handleStartWorkRef.current).toHaveBeenCalled();
    act(() => h.current({ text: "确认架构", type: "action" }));
    expect(h.handleConfirmArchitectureRef.current).toHaveBeenCalled();
    act(() => h.current({ text: "全部确认", type: "action" }));
    expect(h.handleConfirmAllRef.current).toHaveBeenCalled();
    h.unmount();
  });

  it("routes view chips to graph exit focus or sub-agent status tab", () => {
    const h = renderHook();
    act(() => h.current({ text: "查看架构图", type: "view" }));
    expect(h.storeDispatch).toHaveBeenCalledWith({ type: "exitFocus" });
    act(() => h.current({ text: "询问子 Agent 状态", type: "view" }));
    expect(h.setTabs).toHaveBeenCalledWith([SUBAGENT_STATUS_TAB]);
    expect(h.setActiveTab).toHaveBeenCalledWith(SUBAGENT_STATUS_TAB.id);
    expect(h.setDialogOpen).toHaveBeenCalledWith(true);
    h.unmount();
  });

  it("opens picker and settings for shell chips", () => {
    const h = renderHook();
    act(() => h.current({ text: "项目地址", type: "input" }));
    expect(h.openPathPicker).toHaveBeenCalled();
    act(() => h.current({ text: "配置我的api", type: "input" }));
    expect(h.setSettingsOpen).toHaveBeenCalledWith(true);
    h.unmount();
  });

  it("opens empty edit tabs for known tab labels before option prefill", () => {
    const h = renderHook();
    act(() => h.current({ text: ARCHITECTURE_EDIT_TAB.label, type: "input" }));
    expect(h.setTabs).toHaveBeenCalledWith([ARCHITECTURE_EDIT_TAB]);
    expect(h.setActiveTab).toHaveBeenCalledWith(ARCHITECTURE_EDIT_TAB.id);
    act(() => h.current({ text: MICRO_TASK_TAB.label, type: "input" }));
    expect(h.setTabs).toHaveBeenCalledWith([MICRO_TASK_TAB]);
    expect(h.setActiveTab).toHaveBeenCalledWith(MICRO_TASK_TAB.id);
    h.unmount();
  });

  it("prefills an option chip into the single open tab", () => {
    const h = renderHook();
    act(() => h.current({ text: "个人", type: "input" }));
    expect(h.setDrafts).toHaveBeenCalledWith(expect.any(Function));
    expect(h.setActiveTab).toHaveBeenCalledWith("name");
    expect(h.setDialogOpen).toHaveBeenCalledWith(true);
    h.unmount();
  });

  it("activates a matching tab label chip", () => {
    const h = renderHook([{ id: "name", label: "项目名称", promptKey: "project_name", placeholder: "name" }]);
    act(() => h.current({ text: "项目名称", type: "input" }));
    expect(h.setActiveTab).toHaveBeenCalledWith("name");
    expect(h.setDialogOpen).toHaveBeenCalledWith(true);
    h.unmount();
  });
});
