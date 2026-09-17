import { describe, expect, it } from "vitest";
import { focusHashFor, parseFocusHash } from "./focusLink";

describe("focus deep link", () => {
  it("parses #focus= from a full URL or a bare hash", () => {
    expect(parseFocusHash("#focus=auth")).toBe("auth");
    expect(parseFocusHash("http://x/#focus=auth")).toBe("auth");
    expect(parseFocusHash("#view=1&focus=auth-2")).toBe("auth-2");
  });

  it("returns null for absent or malformed hashes", () => {
    expect(parseFocusHash("")).toBeNull();
    expect(parseFocusHash("#focus=")).toBeNull();
    expect(parseFocusHash("#other=1")).toBeNull();
  });

  it("round-trips through focusHashFor", () => {
    expect(parseFocusHash(focusHashFor("core") ?? "")).toBe("core");
    expect(focusHashFor(null)).toBeNull();
  });
});
