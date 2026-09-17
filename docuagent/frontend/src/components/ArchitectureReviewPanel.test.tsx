import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ArchitectureReviewPanel } from "./ArchitectureReviewPanel";
import type { Architecture } from "../graph/types";

const architecture = {
  summary: "demo",
  platform: "web",
  language: "python",
  runtime: "python3",
  frameworks: [],
  stack: [],
  modules: [
    { id: "core", name: "Core", brief: "core", responsibility: "Core logic", path: "src/core", depends_on: [], needs_ui: false, group: null },
    { id: "api", name: "API", brief: "api", responsibility: "HTTP API", path: "src/api", depends_on: ["core"], needs_ui: false, group: null },
    { id: "worker", name: "Worker", brief: "worker", responsibility: "Background jobs", path: "src/worker", depends_on: ["core"], needs_ui: false, group: null },
  ],
  groups: [],
  edges: [
    { from: "api", to: "core", kind: "uses", label: "调用", reason: "API 调用核心逻辑。", accepted: false },
  ],
  data: [],
  integrations: [],
  constraints: [],
  verification: [],
  risks: [],
  unresolved: [],
} as Architecture;

const noop = () => Promise.resolve(true);

describe("ArchitectureReviewPanel", () => {
  it("groups provenance claims by source", () => {
    const html = renderToStaticMarkup(
      <ArchitectureReviewPanel
        architecture={architecture}
        provenance={[
          { id: "c1", text: "用户确认 Python", source: "confirmed" },
          { id: "c2", text: "AI 推测需要缓存", source: "inferred" },
        ]}
        graphQualityIssues={[]}
        interviewMode="beginner"
        architectureVersion={0}
        onProvenanceAction={noop}
        onNodeAction={noop}
        onEdgeAction={noop}
      />,
    );
    expect(html).toContain("用户事实");
    expect(html).toContain("AI 推测");
    expect(html).toContain("草稿");
  });

  it("renders node operation controls for every module", () => {
    const html = renderToStaticMarkup(
      <ArchitectureReviewPanel
        architecture={architecture}
        provenance={[]}
        graphQualityIssues={[]}
        interviewMode="professional"
        architectureVersion={2}
        onProvenanceAction={noop}
        onNodeAction={noop}
        onEdgeAction={noop}
      />,
    );
    expect(html).toContain("版本 2");
    for (const label of ["改名", "改职责", "不确定", "拆分", "删除", "合并选中到目标", "连线关系", "接受", "改类型", "改原因"]) {
      expect(html).toContain(label);
    }
    expect(html).toContain("架构确认");
  });

  it("explains quality issues according to interview mode", () => {
    const html = renderToStaticMarkup(
      <ArchitectureReviewPanel
        architecture={architecture}
        provenance={[]}
        graphQualityIssues={["图密度偏高：8 条边 / 3 个模块 = 2.7；检查是否存在可经组合根推导的重复边。"]}
        interviewMode="beginner"
        architectureVersion={0}
        onProvenanceAction={noop}
        onNodeAction={noop}
        onEdgeAction={noop}
      />,
    );
    expect(html).toContain("蜘蛛网");
  });
});
