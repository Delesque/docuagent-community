import { describe, expect, it } from "vitest";
import { deriveHandoffEntries, deriveNodeStatuses, mapTaskStatus } from "./appModel";

describe("App domain projections", () => {
  it("maps task lifecycle states to node states", () => {
    expect(mapTaskStatus({ status: "pending", last_error: "已停止" } as never)).toBe("stopped");
    expect(mapTaskStatus({ status: "rejected" } as never)).toBe("skipped");
    expect(mapTaskStatus({ status: "blocked" } as never)).toBe("blocked");
  });

  it("lets task status override base status and stale fill pending nodes", () => {
    const result = deriveNodeStatuses({ core: "pending", ui: "running" }, { tasks: [
      { module_id: "core", status: "verified" },
    ] } as never, { stale_modules: ["core", "ui", "docs"] } as never);
    expect(result).toEqual({ core: "verified", ui: "running", docs: "stale" });
  });

  it("derives only handoffs and resolves module labels", () => {
    const task = { id: "t1", module_id: "core", status: "blocked", last_error: "已转接对话流：需要确认" };
    expect(deriveHandoffEntries({ tasks: [task] } as never, { modules: [{ id: "core", name: "Core", path: "src/core" }] } as never)).toEqual([{ task, moduleName: "Core", modulePath: "src/core" }]);
  });
});
