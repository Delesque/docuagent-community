// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useBootstrapSubmission } from "./useBootstrapSubmission";
import type { BootstrapState, ProviderConfig, WorkspaceInfo } from "../api";
import type { DialogTab } from "../components/shell/DialogBox";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ bootstrapRevise: vi.fn(), streamBootstrapAnswer: vi.fn(), streamBootstrapStart: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));

const provider = {} as ProviderConfig;
const workspace = { path: "one", project_name: "One" } as WorkspaceInfo;
const tabs: DialogTab[] = [
  { id: "name", label: "项目名称", promptKey: "project_name", placeholder: "name" },
  { id: "description", label: "项目需求", promptKey: "project_description", placeholder: "description" },
];
const state = (status: BootstrapState["status"]) => ({ status, architecture: {}, project: {}, answers: {}, current_question: null, progress: 1, agent_mode: "guided", model_name: null, model_notice: null, thinking: "" } as BootstrapState);

function renderHook(bootstrap: BootstrapState | null, confirmArchitecture = vi.fn()) {
  let current!: ReturnType<typeof useBootstrapSubmission>;
  const say = vi.fn(); const setBusy = vi.fn(); const setRevealOnOpen = vi.fn(); const applyBootstrapTurn = vi.fn();
  const runModelCall = vi.fn() as unknown as Parameters<typeof useBootstrapSubmission>[0]["runModelCall"];
  const runBootstrapStream = vi.fn() as unknown as Parameters<typeof useBootstrapSubmission>[0]["runBootstrapStream"];
  (runModelCall as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (_label: string, request: (options: object) => Promise<unknown>) => request({}));
  (runBootstrapStream as unknown as ReturnType<typeof vi.fn>).mockImplementation(async (_label: string, request: (onReasoning: (text: string) => void, signal: AbortSignal) => Promise<unknown>) => request(vi.fn(), new AbortController().signal));
  const root: Root = createRoot(document.createElement("div"));
  function Probe() { current = useBootstrapSubmission({ workspace, provider, bootstrap, tabs, say, setBusy, setRevealOnOpen, applyBootstrapTurn, confirmArchitecture, runModelCall, runBootstrapStream }); return null; }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, say, setBusy, setRevealOnOpen, applyBootstrapTurn, runModelCall, runBootstrapStream, confirmArchitecture, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());
describe("useBootstrapSubmission", () => {
  it("starts a new project with labelled name and description", async () => {
    api.streamBootstrapStart.mockImplementation(async (_request: unknown, onEvent: (event: { type: string; text?: string }) => void) => { onEvent({ type: "reasoning", text: "思考" }); return state("interviewing"); });
    const h = renderHook(null);
    await act(async () => h.current({ name: "One", description: "需求" }));
    expect(api.streamBootstrapStart).toHaveBeenCalledWith({ path: "one", name: "One", description: "需求", provider }, expect.any(Function), expect.any(AbortSignal));
    expect(h.applyBootstrapTurn).toHaveBeenCalledWith(expect.objectContaining({ status: "interviewing" }));
    expect(h.setBusy).toHaveBeenLastCalledWith(false);
    h.unmount();
  });

  it("revises review feedback through the retry-aware model call", async () => {
    api.bootstrapRevise.mockResolvedValue(state("review"));
    const h = renderHook(state("review"));
    await act(async () => h.current({ revise: "拆分认证模块" }));
    expect(api.bootstrapRevise).toHaveBeenCalledWith({ path: "one", feedback: "拆分认证模块", provider }, {});
    expect(h.applyBootstrapTurn).toHaveBeenCalledWith(expect.objectContaining({ status: "review" }));
    h.unmount();
  });

  it("delegates empty review feedback to the shared confirmation action", async () => {
    const confirm = vi.fn().mockResolvedValue(null);
    const h = renderHook(state("review"), confirm);
    await act(async () => h.current({ revise: " " }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(api.bootstrapRevise).not.toHaveBeenCalled();
    h.unmount();
  });

  it("answers an interview with prompt keys and applies the next state", async () => {
    api.streamBootstrapAnswer.mockResolvedValue(state("review"));
    const h = renderHook(state("interviewing"));
    await act(async () => h.current({ name: "One", description: "回答" }));
    expect(api.streamBootstrapAnswer).toHaveBeenCalledWith({ path: "one", answer: "project_name: One\nproject_description: 回答", provider }, expect.any(Function), expect.any(AbortSignal));
    expect(h.applyBootstrapTurn).toHaveBeenCalledWith(expect.objectContaining({ status: "review" }));
    h.unmount();
  });
});
