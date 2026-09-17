import { describe, expect, it, vi } from "vitest";
import { bootstrapStart, editArchitecture, reopenArchitectureReview, setInterviewMode, updateArchitectureEdges, updateArchitectureNodes, updateModuleRequirement, updateProvenance } from "./bootstrap";
import { postWithRetry, post } from "./shared";

vi.mock("./shared", () => ({ postWithRetry: vi.fn(), post: vi.fn(), readNdjsonEvents: vi.fn() }));
const retry = vi.mocked(postWithRetry);
const postMock = vi.mocked(post);
const provider = { enabled: false, base_url: "http://local", api_key: "", model: "m", format: "openai" as const };

describe("bootstrap API", () => {
  it("keeps the provider nested for model calls", async () => {
    const state = { status: "interviewing" };
    retry.mockResolvedValueOnce(state);
    const request = { path: "C:/p", name: "Demo", description: "desc", provider };
    await expect(bootstrapStart(request, { retries: 2 })).resolves.toBe(state);
    expect(retry).toHaveBeenCalledWith("/api/bootstrap/start", request, { retries: 2 });
  });

  it("maps architecture requirement arguments to backend names", async () => {
    postMock.mockResolvedValueOnce({ ok: true });
    await updateModuleRequirement("C:/p", "core", "Keep boundaries");
    expect(postMock).toHaveBeenCalledWith("/api/architecture/update-module-requirement", { path: "C:/p", module_id: "core", requirement: "Keep boundaries" });
  });

  it("uses the mode endpoint without provider data", async () => {
    postMock.mockResolvedValueOnce({ status: "review" });
    await setInterviewMode("C:/p", "beginner");
    expect(postMock).toHaveBeenCalledWith("/api/bootstrap/mode", { path: "C:/p", interview_mode: "beginner" });
  });

  it("maps provenance decisions to the action endpoint", async () => {
    postMock.mockResolvedValueOnce({ state: { status: "review" } });
    await updateProvenance("C:/p", "claim-1", "modify", "confirmed text");
    expect(postMock).toHaveBeenCalledWith("/api/provenance/update", { path: "C:/p", claim_id: "claim-1", action: "modify", text: "confirmed text" });
  });

  it("maps node operations to the architecture nodes endpoint", async () => {
    postMock.mockResolvedValueOnce({ architecture: {}, architecture_version: 2, stale_modules: [], history_remaining: 1 });
    await updateArchitectureNodes("C:/p", { action: "rename", module_id: "core", name: "Domain Core" });
    expect(postMock).toHaveBeenCalledWith("/api/architecture/nodes", { path: "C:/p", action: "rename", module_id: "core", name: "Domain Core" });
  });

  it("maps edge decisions and reopen review to their endpoints", async () => {
    postMock.mockResolvedValueOnce({ architecture: {}, architecture_version: 2, stale_modules: [], history_remaining: 1 });
    await updateArchitectureEdges("C:/p", { action: "type", from: "api", to: "core", kind: "data" });
    expect(postMock).toHaveBeenCalledWith("/api/architecture/edges", { path: "C:/p", action: "type", from: "api", to: "core", kind: "data" });

    postMock.mockResolvedValueOnce({ status: "review" });
    await reopenArchitectureReview("C:/p");
    expect(postMock).toHaveBeenCalledWith("/api/architecture/reopen-review", { path: "C:/p" });
  });

  it("routes architecture edits through the retry transport", async () => {
    retry.mockResolvedValueOnce({ architecture: {} });
    const request = { path: "C:/p", request: "split core", provider };
    await editArchitecture(request);
    expect(retry).toHaveBeenCalledWith("/api/architecture/edit", request, {});
  });
});
