// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useSuggestions } from "./useSuggestions";
import type { NodeAttachments, WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ acceptSuggestion: vi.fn(), rejectSuggestion: vi.fn() }));
vi.mock("../api", async () => ({ ...(await vi.importActual<typeof import("../api")>("../api")), ...api }));

const workspace = { path: "one" } as WorkspaceInfo;
const attachments = {
  core: [
    { id: "a1", type: "suggestion", title: "Fix", detail: "", priority: "medium", resolved: false, archived: false },
    { id: "a2", type: "note", title: "Note", detail: "", resolved: false, archived: false },
    { id: "a3", type: "suggestion", title: "High", detail: "", priority: "high", resolved: false, archived: false },
  ],
} as unknown as NodeAttachments;

function renderHook(overrides: Partial<Parameters<typeof useSuggestions>[0]> = {}) {
  let current!: ReturnType<typeof useSuggestions>;
  const say = vi.fn();
  const replaceAttachments = vi.fn();
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useSuggestions({ workspace, attachments, say, replaceAttachments, ...overrides });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, say, replaceAttachments, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useSuggestions", () => {
  it("derives only unresolved suggestion attachments and sorts by priority", () => {
    const h = renderHook();
    expect(h.current.suggestionEntries.map((entry) => entry.attachment.id)).toEqual(["a3", "a1"]);
    expect(h.current.suggestionsOpen).toBe(false);
    h.unmount();
  });

  it("accepts a suggestion and writes back the returned attachments", async () => {
    const returned = { core: [] } as NodeAttachments;
    api.acceptSuggestion.mockResolvedValue({ attachments: returned, task: { id: "t1", summary: "Fix core" } });
    const h = renderHook();
    await act(async () => h.current.acceptSuggestion("core", "a1"));
    expect(api.acceptSuggestion).toHaveBeenCalledWith("one", "core", "a1");
    expect(h.replaceAttachments).toHaveBeenCalledWith(returned);
    expect(h.say).toHaveBeenCalledWith(expect.objectContaining({ text: expect.stringContaining("已采纳建议「Fix core」") }));
    h.unmount();
  });

  it("rejects a suggestion and writes back the returned attachments", async () => {
    const returned = { core: [] } as NodeAttachments;
    api.rejectSuggestion.mockResolvedValue({ attachments: returned });
    const h = renderHook();
    await act(async () => h.current.rejectSuggestion("core", "a1"));
    expect(api.rejectSuggestion).toHaveBeenCalledWith("one", "core", "a1");
    expect(h.replaceAttachments).toHaveBeenCalledWith(returned);
    h.unmount();
  });
});
