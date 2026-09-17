import { describe, expect, it, vi } from "vitest";
import { addAttachment, approveMcpServer, clearStale, listMemoryCandidates, listSnapshots, saveMcpServers } from "./extensions";
import { get, post } from "./shared";

vi.mock("./shared", () => ({ get: vi.fn(), post: vi.fn(), postWithRetry: vi.fn() }));
const getMock = vi.mocked(get);
const postMock = vi.mocked(post);

describe("extensions API", () => {
  it("unwraps memory candidates and snapshots", async () => {
    getMock.mockResolvedValueOnce({ candidates: [{ id: "m1" }] });
    await expect(listMemoryCandidates("C:/项目")).resolves.toEqual([{ id: "m1" }]);
    expect(getMock).toHaveBeenCalledWith("/api/memory/candidates?path=C%3A%2F%E9%A1%B9%E7%9B%AE");
    getMock.mockResolvedValueOnce({ snapshots: [{ id: "s1" }] });
    await expect(listSnapshots("C:/p")).resolves.toEqual([{ id: "s1" }]);
  });

  it("uses snake_case for attachments and stale modules", async () => {
    postMock.mockResolvedValueOnce({ attachments: { core: [] } });
    await addAttachment("C:/p", "core", "note", "remember");
    expect(postMock).toHaveBeenCalledWith("/api/attachments/add", { path: "C:/p", module_id: "core", type: "note", text: "remember" });
    postMock.mockResolvedValueOnce({ stale_modules: ["core"] });
    await clearStale("C:/p", ["core"]);
    expect(postMock).toHaveBeenCalledWith("/api/architecture/clear-stale", { path: "C:/p", module_ids: ["core"] });
  });

  it("approves an MCP server command", async () => {
    postMock.mockResolvedValueOnce({ servers: [{ name: "local", command: "node", args: [], approved: true }] });
    await approveMcpServer("C:/p", "local");
    expect(postMock).toHaveBeenCalledWith("/api/mcp/approve", { path: "C:/p", server: "local" });
  });

  it("preserves MCP server arrays in the request", async () => {
    const servers = [{ name: "local", command: "node", args: [] }];
    postMock.mockResolvedValueOnce({ servers });
    await saveMcpServers("C:/p", servers);
    expect(postMock).toHaveBeenCalledWith("/api/mcp/servers", { path: "C:/p", servers });
  });
});
