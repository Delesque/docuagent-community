import { describe, expect, it, vi } from "vitest";
import { clearAgentErrors, getAgentDetail, listAgents, sendAgentMessage } from "./agents";
import { get, post } from "./shared";

vi.mock("./shared", () => ({ get: vi.fn(), post: vi.fn() }));
const getMock = vi.mocked(get);
const postMock = vi.mocked(post);

describe("agents API", () => {
  it("encodes both agent query parameters", async () => {
    getMock.mockResolvedValueOnce({ module_id: "a" });
    await getAgentDetail("C:/项目", "api/core");
    expect(getMock).toHaveBeenCalledWith("/api/agents/detail?path=C%3A%2F%E9%A1%B9%E7%9B%AE&module_id=api%2Fcore");
  });

  it("unwraps cleared error memory", async () => {
    const errors = [{ id: "e1", error: "bad" }];
    postMock.mockResolvedValueOnce({ error_memory: errors });
    await expect(clearAgentErrors("C:/p", "core")).resolves.toBe(errors);
    expect(postMock).toHaveBeenCalledWith("/api/agents/clear-errors", { path: "C:/p", module_id: "core" });
  });

  it("passes agent messages with the expected field names", async () => {
    postMock.mockResolvedValueOnce({ content: "ok" });
    await sendAgentMessage("C:/p", "core", "continue");
    expect(postMock).toHaveBeenCalledWith("/api/agents/message", { path: "C:/p", module_id: "core", message: "continue" });
  });

  it("lists agents through a path query", async () => {
    getMock.mockResolvedValueOnce([]);
    await listAgents("C:/p");
    expect(getMock).toHaveBeenCalledWith("/api/agents?path=C%3A%2Fp");
  });
});
