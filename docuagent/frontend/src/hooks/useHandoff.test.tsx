// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useHandoff } from "./useHandoff";
import type { TaskPlan } from "../api";
import type { Architecture } from "../graph/types";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const architecture = { summary: "", platform: "", language: "", runtime: "", frameworks: [], stack: [], modules: [{ id: "core", name: "Core", brief: "logic", responsibility: "", path: "src", depends_on: [], needs_ui: false, group: null }], groups: [], edges: [], data: [], integrations: [], constraints: [], verification: [], risks: [], unresolved: [] } as unknown as Architecture;

function taskPlan(tasks: TaskPlan["tasks"]) {
  return { tasks } as TaskPlan;
}

function renderHook(tasks: TaskPlan | null, arch: Architecture | null = architecture) {
  let current!: ReturnType<typeof useHandoff>;
  const root: Root = createRoot(document.createElement("div"));
  function Probe() {
    current = useHandoff({ tasks, architecture: arch });
    return null;
  }
  act(() => root.render(<Probe />));
  return { get current() { return current; }, unmount: () => act(() => root.unmount()) };
}

afterEach(() => vi.clearAllMocks());

describe("useHandoff", () => {
  it("derives blocked handoff tasks and auto-opens once per task id", () => {
    const h = renderHook(taskPlan([
      { id: "h1", module_id: "core", status: "blocked", last_error: "已转接对话流：需要人工确认" },
    ] as TaskPlan["tasks"]));
    expect(h.current.handoffEntries).toHaveLength(1);
    expect(h.current.handoffEntries[0]?.moduleName).toBe("Core");
    expect(h.current.handoffTasks).toHaveLength(1);
    expect(h.current.handoffOpen).toBe(true);
    h.unmount();
  });

  it("stays closed when there are no handoff tasks", () => {
    const h = renderHook(taskPlan([
      { id: "t1", module_id: "core", status: "verified", last_error: "" },
    ] as TaskPlan["tasks"]));
    expect(h.current.handoffEntries).toHaveLength(0);
    expect(h.current.handoffOpen).toBe(false);
    h.unmount();
  });
});
