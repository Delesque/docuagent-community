import { describe, expect, it } from "vitest";
import type { BootstrapState } from "../api";
import { draftStatus, formatDraftDetail, formatDraftSummary } from "./draftStatus";

const base = {
  status: "interviewing",
  architecture: { modules: [], edges: [] },
  provenance: [],
  graph_quality_issues: [],
  topics_remaining: [],
} as unknown as BootstrapState;

describe("draft status", () => {
  it("returns null outside the interviewing status", () => {
    expect(draftStatus({ ...base, status: "review" } as BootstrapState)).toBeNull();
    expect(draftStatus({ ...base, status: "ready" } as BootstrapState)).toBeNull();
  });

  it("returns null for an empty first turn instead of a zero line", () => {
    expect(draftStatus(base)).toBeNull();
  });

  it("counts modules, edges, inferred claims, and quality issues", () => {
    const status = draftStatus({
      ...base,
      architecture: { modules: [{ id: "a" }, { id: "b" }], edges: [{}, {}] },
      provenance: [
        { id: "c1", source: "confirmed" },
        { id: "c2", source: "inferred" },
        { id: "c3", source: "inferred" },
      ],
      graph_quality_issues: ["图密度偏高"],
      topics_remaining: ["完成标准"],
    } as unknown as BootstrapState);
    expect(status).toEqual({
      modules: 2,
      edges: 2,
      inferred: 2,
      qualityIssues: 1,
      topicsRemaining: ["完成标准"],
      aspects: [],
    });
    expect(formatDraftSummary(status!)).toBe(
      "2 个模块 · 2 条依赖 · 2 处推测待确认 · 1 条图质量提醒 · 1 个主题未覆盖",
    );
  });

  it("drops zero-value parts instead of printing 0 lines", () => {
    const status = draftStatus({
      ...base,
      architecture: { modules: [{ id: "a" }], edges: [] },
    } as unknown as BootstrapState);
    expect(formatDraftSummary(status!)).toBe("1 个模块");
  });

  it("never exposes module names — counts only", () => {
    const status = draftStatus({
      ...base,
      architecture: { modules: [{ id: "secret-module" }], edges: [] },
      provenance: [{ id: "c", source: "inferred" }],
    } as unknown as BootstrapState);
    expect(formatDraftSummary(status!)).not.toContain("secret-module");
    expect(formatDraftDetail(status!).join("\n")).not.toContain("secret-module");
  });

  it("keeps the aspect list out of the summary and in the detail", () => {
    const status = draftStatus({
      ...base,
      interview_plan: [
        { id: "goal", title: "项目目标", asked: 2, max: 3 },
        { id: "stack", title: "技术栈", asked: 0, max: 3 },
      ],
    } as unknown as BootstrapState);
    // The summary carries the count only: the full list is what used to be re-printed
    // under every question.
    expect(formatDraftSummary(status!)).toBe("访谈方面 1/2");
    expect(formatDraftDetail(status!)).toEqual(["项目目标（2/3）", "技术栈（0/3）"]);
  });

  it("falls back to a topic count in the summary and names them in the detail", () => {
    const status = draftStatus({
      ...base,
      topics_remaining: ["完成标准", "部署方式"],
    } as unknown as BootstrapState);
    expect(formatDraftSummary(status!)).toBe("2 个主题未覆盖");
    expect(formatDraftDetail(status!)).toEqual(["未覆盖主题：完成标准、部署方式"]);
  });
});
