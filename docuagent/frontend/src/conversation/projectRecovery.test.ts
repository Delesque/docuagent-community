import { describe, expect, it } from "vitest";
import {
  projectReloadUrl,
  resolveInitialProjectPath,
} from "./projectRecovery";

describe("projectRecovery", () => {
  it("prefers a deep-link path over the saved path", () => {
    expect(
      resolveInitialProjectPath("?path=E%3A%5Cproject-a", "E:\\project-b"),
    ).toBe("E:\\project-a");
  });

  it("falls back to the saved path on a plain reload", () => {
    expect(resolveInitialProjectPath("", "E:\\project-a")).toBe("E:\\project-a");
  });

  it("returns null when neither path exists", () => {
    expect(resolveInitialProjectPath("", null)).toBeNull();
  });

  it("keeps the project path in the reload URL", () => {
    const url = projectReloadUrl("http://127.0.0.1:8765/", "E:\\project-a");
    expect(new URL(url).searchParams.get("path")).toBe("E:\\project-a");
  });
});
