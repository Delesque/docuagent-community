// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useWorkspaceActions } from "./useWorkspaceActions";
import type { BootstrapState, WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ clearStale: vi.fn(), inspectWorkspace: vi.fn(), terminalExec: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));
const terminal = vi.hoisted(() => ({ writelnToTerminal: vi.fn() }));
vi.mock("../components/terminalBus", () => terminal);

const workspace = { path: "one" } as WorkspaceInfo;

function renderHook(overrides: Partial<Parameters<typeof useWorkspaceActions>[0]> = {}) {
  let current!: ReturnType<typeof useWorkspaceActions>;
  const say = vi.fn();
  const setWorkspace = vi.fn();
  const setBootstrap = vi.fn();
  const setUndoAvailable = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useWorkspaceActions({ workspace, say, setWorkspace, setBootstrap, setUndoAvailable, ...overrides });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, say, setWorkspace, setBootstrap, setUndoAvailable, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useWorkspaceActions", () => {
  it("prints terminal output and the exit code", async () => {
    api.terminalExec.mockResolvedValue({ stdout: "ok\n", stderr: "warn\n", returncode: 0 });
    const h = renderHook();
    await act(async () => h.current.handleTerminalExec("git status"));
    expect(api.terminalExec).toHaveBeenCalledWith("one", "git status");
    expect(terminal.writelnToTerminal).toHaveBeenCalledWith("ok");
    expect(terminal.writelnToTerminal).toHaveBeenCalledWith(expect.stringContaining("warn"));
    expect(terminal.writelnToTerminal).toHaveBeenCalledWith(expect.stringContaining("退出码 0"));
    h.unmount();
  });

  it("refreshes workspace state after clearing stale modules", async () => {
    const refreshed = { path: "one", architecture_can_undo: true, bootstrap: { status: "ready" } } as WorkspaceInfo;
    api.clearStale.mockResolvedValue(undefined);
    api.inspectWorkspace.mockResolvedValue(refreshed);
    const h = renderHook();
    await act(async () => h.current.handleClearStale("core"));
    expect(api.clearStale).toHaveBeenCalledWith("one", ["core"]);
    expect(h.setWorkspace).toHaveBeenCalledWith(refreshed);
    expect(h.setBootstrap).toHaveBeenCalledWith(refreshed.bootstrap as BootstrapState);
    expect(h.setUndoAvailable).toHaveBeenCalledWith(true);
    h.unmount();
  });

  it("reports stale-clear failures as an agent message", async () => {
    api.clearStale.mockRejectedValue(new Error("locked"));
    const h = renderHook();
    await act(async () => h.current.handleClearStale("core"));
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("清除过期标记失败：locked") }));
    h.unmount();
  });
});
