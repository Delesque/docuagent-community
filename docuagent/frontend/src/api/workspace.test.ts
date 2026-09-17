import { describe, expect, it, vi } from "vitest";
import { chooseFolder, getProviderConfig, inspectWorkspace, saveProviderConfig } from "./workspace";
import { get, post } from "./shared";

vi.mock("./shared", () => ({ get: vi.fn(), post: vi.fn() }));
const mockedGet = vi.mocked(get);
const mockedPost = vi.mocked(post);

describe("workspace API", () => {
  it("encodes workspace paths for inspection", async () => {
    mockedGet.mockResolvedValueOnce({ path: "C:/项目" });
    await expect(inspectWorkspace("C:/项目 a")).resolves.toEqual({ path: "C:/项目" });
    expect(mockedGet).toHaveBeenCalledWith("/api/workspace?path=C%3A%2F%E9%A1%B9%E7%9B%AE%20a");
  });

  it("uses the desktop picker and inspects its selected path", async () => {
    const inspect = { path: "C:/selected", exists: true };
    mockedGet.mockResolvedValueOnce(inspect);
    vi.stubGlobal("window", { docuagentDesktop: { chooseFolder: vi.fn().mockResolvedValue({ cancelled: false, path: "C:/selected" }) } });
    await expect(chooseFolder("C:/initial")).resolves.toEqual({ cancelled: false, workspace: inspect });
    expect((window as unknown as { docuagentDesktop: { chooseFolder: ReturnType<typeof vi.fn> } }).docuagentDesktop.chooseFolder).toHaveBeenCalledWith("C:/initial");
    expect(mockedGet).toHaveBeenCalledWith("/api/workspace?path=C%3A%2Fselected");
  });

  it("normalizes a saved API key to a has_api_key flag", async () => {
    mockedGet.mockResolvedValueOnce({ base_url: "http://local", api_key: "secret", model: "m" });
    await expect(getProviderConfig()).resolves.toMatchObject({ api_key: "", has_api_key: true });
  });

  it("sends provider configuration without changing its envelope", async () => {
    mockedPost.mockResolvedValueOnce(undefined);
    const config = { enabled: false, base_url: "http://local", api_key: "secret", model: "m", format: "openai" as const };
    await saveProviderConfig(config);
    expect(mockedPost).toHaveBeenCalledWith("/api/config", config);
  });
});
