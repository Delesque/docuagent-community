import { afterEach, describe, expect, it, vi } from "vitest";
import { cancelTasks, editTaskPatch, fetchGitDiff, streamOrchestrateTasks, streamTaskWave } from "./tasks";
import { get, post, readNdjsonEvents, requestNdjson, withSavedProvider } from "./shared";

vi.mock("./shared", () => ({
  get: vi.fn(), post: vi.fn(), postWithRetry: vi.fn(), readNdjsonEvents: vi.fn(),
  requestNdjson: vi.fn(), withSavedProvider: vi.fn((body) => ({ wrapped: body })),
}));
const getMock = vi.mocked(get);
const postMock = vi.mocked(post);
const ndjson = vi.mocked(requestNdjson);
const readNdjson = vi.mocked(readNdjsonEvents);
const provider = { enabled: true, base_url: "https://model.example", api_key: "", model: "m", has_api_key: true, format: "openai" as const };

afterEach(() => vi.resetAllMocks());

describe("tasks API", () => {
  it("omits empty optional task and git fields", async () => {
    postMock.mockResolvedValueOnce({ cancelled: 0 });
    await cancelTasks("C:/p", []);
    expect(postMock).toHaveBeenCalledWith("/api/tasks/cancel", { path: "C:/p" });
    getMock.mockResolvedValueOnce({ diff: "" });
    await fetchGitDiff("C:/a b");
    expect(getMock).toHaveBeenCalledWith("/api/git/diff?path=C%3A%2Fa+b");
  });

  it("maps patch edit version fields to snake_case", async () => {
    postMock.mockResolvedValueOnce({ tasks: [] });
    await editTaskPatch("C:/p", "t1", "src/a.ts", "next", "base");
    expect(postMock).toHaveBeenCalledWith("/api/tasks/patch-edit", { path: "C:/p", task_id: "t1", file: "src/a.ts", content: "next", base_after: "base" });
  });

  it("extracts the completed plan from a task stream", async () => {
    const plan = { tasks: [{ id: "t1" }] };
    ndjson.mockImplementationOnce(async (_url, _body, onEvent) => { onEvent({ type: "done", tasks: plan } as never); });
    const onEvent = vi.fn();
    await expect(streamTaskWave("C:/p", provider, onEvent)).resolves.toBe(plan);
    expect(withSavedProvider).toHaveBeenCalledWith({ path: "C:/p", provider });
    expect(ndjson).toHaveBeenCalledWith("/api/tasks/generate-wave-stream", { wrapped: { path: "C:/p", provider } }, expect.any(Function), undefined);
    expect(onEvent).toHaveBeenCalledWith({ type: "done", tasks: plan });
  });

  it("streams orchestration events with the scoped request and final plan", async () => {
    const plan = { tasks: [{ id: "t1" }] };
    readNdjson.mockImplementationOnce(async (_url, _body, onEvent) => {
      onEvent({ type: "started", module_id: "core", scope_module_ids: ["core"] });
      onEvent({ type: "reasoning", text: "thinking" });
      onEvent({ type: "done", tasks: plan } as never);
    });
    const onEvent = vi.fn();
    await expect(streamOrchestrateTasks({ path: "C:/p", provider, module_id: "core", instruction: "focus" }, onEvent)).resolves.toBe(plan);
    expect(readNdjson).toHaveBeenCalledWith("/api/orchestrate/plan-stream", { path: "C:/p", provider, module_id: "core", instruction: "focus" }, expect.any(Function), undefined);
    expect(onEvent).toHaveBeenCalledWith({ type: "started", module_id: "core", scope_module_ids: ["core"] });
  });

  it("rejects orchestration streams without a done plan", async () => {
    readNdjson.mockResolvedValueOnce(undefined);
    await expect(streamOrchestrateTasks({ path: "C:/p", provider }, vi.fn())).rejects.toThrow("流式编排未返回完成事件。");
  });

  it("rejects streams that finish without a done plan", async () => {
    ndjson.mockResolvedValueOnce(undefined);
    await expect(streamTaskWave("C:/p", provider, vi.fn())).rejects.toThrow("流式生成未返回完成事件。");
  });
});
