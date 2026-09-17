import { afterEach, describe, expect, it, vi } from "vitest";

function jsonResponse(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "content-type": "application/json" },
  });
}

async function loadTransport() {
  vi.resetModules();
  return import("./transport");
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("API transport", () => {
  it("shares one session initialization across JSON requests", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, data: { value: 1 } }))
      .mockResolvedValueOnce(jsonResponse({ ok: true, data: { value: 2 } }));
    vi.stubGlobal("fetch", fetchMock);
    const { requestJson } = await loadTransport();

    await expect(requestJson<{ value: number }>("/api/one")).resolves.toEqual({ value: 1 });
    await expect(requestJson<{ value: number }>("/api/two")).resolves.toEqual({ value: 2 });

    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual(["/api/session", "/api/one", "/api/two"]);
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({ cache: "no-store", credentials: "same-origin" });
  });

  it("allows session initialization to retry after failure", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    const { ensureLocalSession } = await loadTransport();

    await expect(ensureLocalSession()).rejects.toThrow("会话初始化失败：503");
    await expect(ensureLocalSession()).resolves.toBeUndefined();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("propagates JSON HTTP and envelope errors with status", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ ok: false, error: "工作区不可用" }, 409));
    vi.stubGlobal("fetch", fetchMock);
    const { requestJson } = await loadTransport();

    const error = await requestJson("/api/fail").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(Error);
    expect((error as Error).message).toBe("工作区不可用");
    expect((error as { status?: number }).status).toBe(409);
  });

  it("propagates NDJSON HTTP errors", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(jsonResponse({ ok: false, error: "已有进行中的计划" }, 409));
    vi.stubGlobal("fetch", fetchMock);
    const { requestNdjson } = await loadTransport();

    await expect(requestNdjson("/api/stream", {}, vi.fn())).rejects.toThrow("已有进行中的计划");
  });

  it("emits NDJSON events and propagates late stream errors", async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        const encoder = new TextEncoder();
        controller.enqueue(encoder.encode('{"type":"content","text":"ok"}\n'));
        controller.enqueue(encoder.encode('{"type":"error","error":"流内失败"}'));
        controller.close();
      },
    });
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(new Response(null, { status: 204 }))
      .mockResolvedValueOnce(new Response(stream, { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const { requestNdjson } = await loadTransport();
    const onEvent = vi.fn();

    await expect(requestNdjson("/api/stream", { path: "x" }, onEvent)).rejects.toThrow("流内失败");
    expect(onEvent).toHaveBeenCalledWith({ type: "content", text: "ok" });
    expect(fetchMock.mock.calls[1]?.[1]).toMatchObject({
      method: "POST",
      cache: "no-store",
      credentials: "same-origin",
    });
  });
});
