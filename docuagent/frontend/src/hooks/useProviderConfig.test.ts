import { describe, expect, it } from "vitest";
import { isProviderConfigured } from "./useProviderConfig";

const base = { enabled: true, base_url: "https://api.example/v1", model: "model", api_key: "", format: "openai" as const };

describe("provider configuration", () => {
  it("requires enabled, endpoint, model, and a key or saved key", () => {
    expect(isProviderConfigured(base)).toBe(false);
    expect(isProviderConfigured({ ...base, api_key: "secret" })).toBe(true);
    expect(isProviderConfigured({ ...base, has_api_key: true })).toBe(true);
    expect(isProviderConfigured({ ...base, enabled: false, api_key: "secret" })).toBe(false);
    expect(isProviderConfigured({ ...base, model: "", api_key: "secret" })).toBe(false);
  });
});
