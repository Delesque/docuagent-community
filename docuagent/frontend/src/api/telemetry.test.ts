import { describe, expect, it, vi } from "vitest";
import {
  clearTelemetry,
  fetchTelemetry,
  setTelemetryConsent,
  uploadTelemetry,
} from "./telemetry";
import { get, post } from "./shared";

vi.mock("./shared", () => ({ get: vi.fn(), post: vi.fn() }));
const getMock = vi.mocked(get);
const postMock = vi.mocked(post);

describe("telemetry API", () => {
  it("reads the local summary", async () => {
    getMock.mockResolvedValueOnce({ consent: false });
    await expect(fetchTelemetry()).resolves.toEqual({ consent: false });
    expect(getMock).toHaveBeenCalledWith("/api/telemetry");
  });

  it("requires an explicit boolean consent update", async () => {
    postMock.mockResolvedValueOnce({ consent: true });
    await setTelemetryConsent(true);
    expect(postMock).toHaveBeenCalledWith("/api/telemetry/config", {
      consent: true,
    });
  });

  it("uses dedicated upload and clear endpoints", async () => {
    postMock.mockResolvedValueOnce({ uploaded: true });
    await uploadTelemetry();
    expect(postMock).toHaveBeenCalledWith("/api/telemetry/upload", {});
    postMock.mockResolvedValueOnce({ consent: false });
    await clearTelemetry();
    expect(postMock).toHaveBeenCalledWith("/api/telemetry/clear", {});
  });
});
