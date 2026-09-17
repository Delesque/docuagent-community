// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useWorkspaceArtifacts } from "./useWorkspaceArtifacts";
import type { NodeAttachments, WorkspaceInfo } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  addAttachment: vi.fn(),
  archiveAttachment: vi.fn(),
  listAgents: vi.fn().mockResolvedValue([]),
  listAttachments: vi.fn().mockResolvedValue({}),
  listSnapshots: vi.fn().mockResolvedValue([]),
  restoreSnapshot: vi.fn(),
  sendAgentMessage: vi.fn(),
}));
vi.mock("../api", () => api);

function renderHook(initialWorkspace: WorkspaceInfo | null) {
  let workspace = initialWorkspace;
  let current: ReturnType<typeof useWorkspaceArtifacts>;
  const notify = vi.fn();
  const setBusy = vi.fn();
  const host = document.createElement("div");
  const root: Root = createRoot(host);

  function Probe() {
    current = useWorkspaceArtifacts({ workspace, notify, setBusy });
    return null;
  }

  act(() => root.render(<Probe />));
  return {
    get current() {
      return current!;
    },
    notify,
    setBusy,
    rerender(next: WorkspaceInfo | null) {
      workspace = next;
      act(() => root.render(<Probe />));
    },
    unmount() {
      act(() => root.unmount());
    },
  };
}

const ws = (path: string) => ({ path }) as WorkspaceInfo;
const attachments: NodeAttachments = {
  m: [{ id: "a", type: "note", text: "x", created_at: "", resolved: false }],
};
const archivedAttachments: NodeAttachments = {
  m: [{ ...attachments.m![0]!, archived: true }],
};

afterEach(() => vi.clearAllMocks());

describe("useWorkspaceArtifacts workspace versioning", () => {
  it("applies successful attachment writes", async () => {
    api.addAttachment.mockResolvedValue(attachments);
    const h = renderHook(ws("one"));

    await act(async () => h.current.addNodeAttachment("m", "note", "x"));

    expect(h.current.attachments).toEqual(attachments);
    h.unmount();
  });

  it("reports attachment write failures", async () => {
    api.addAttachment.mockRejectedValue(new Error("no"));
    const h = renderHook(ws("one"));

    await act(async () => h.current.addNodeAttachment("m", "note", "x"));

    expect(h.notify).toHaveBeenCalledWith("写入节点失败：no");
    h.unmount();
  });

  it("ignores attachment responses after switching workspace", async () => {
    let resolve!: (value: NodeAttachments) => void;
    api.addAttachment.mockReturnValue(new Promise<NodeAttachments>((r) => { resolve = r; }));
    const h = renderHook(ws("one"));
    let pending!: Promise<void>;
    act(() => { pending = h.current.addNodeAttachment("m", "note", "x"); });

    h.rerender(ws("two"));
    await act(async () => {
      resolve(attachments);
      await pending;
    });

    expect(h.current.attachments).toEqual({});
    expect(h.notify).not.toHaveBeenCalledWith("写入节点失败：no");
    h.unmount();
  });

  it("archives pending attachments successfully", async () => {
    api.archiveAttachment.mockResolvedValue(archivedAttachments);
    const h = renderHook(ws("one"));
    act(() => h.current.replaceAttachments(attachments));

    await act(async () => h.current.archiveModule("m"));

    expect(api.archiveAttachment).toHaveBeenCalledWith("one", "m", "a");
    expect(h.current.attachments).toEqual(archivedAttachments);
    expect(h.notify).toHaveBeenCalledWith("已归档「m」的 1 条未完成附件。");
    h.unmount();
  });

  it("reports archive failures", async () => {
    api.archiveAttachment.mockRejectedValue(new Error("archive"));
    const h = renderHook(ws("one"));
    act(() => h.current.replaceAttachments(attachments));

    await act(async () => h.current.archiveModule("m"));

    expect(h.notify).toHaveBeenCalledWith("归档节点附件失败：archive");
    h.unmount();
  });

  it("ignores stale archive responses after switching workspace", async () => {
    let resolve!: (value: NodeAttachments) => void;
    api.archiveAttachment.mockReturnValue(new Promise<NodeAttachments>((r) => { resolve = r; }));
    const h = renderHook(ws("one"));
    act(() => h.current.replaceAttachments(attachments));
    let pending!: Promise<void>;
    act(() => { pending = h.current.archiveModule("m"); });

    h.rerender(ws("two"));
    await act(async () => {
      resolve(archivedAttachments);
      await pending;
    });

    expect(h.current.attachments).toEqual({});
    expect(h.notify).not.toHaveBeenCalledWith("已归档「m」的 1 条未完成附件。");
    h.unmount();
  });
});
