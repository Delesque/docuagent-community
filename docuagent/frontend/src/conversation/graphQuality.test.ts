import { describe, expect, it } from "vitest";
import { explainGraphQualityIssue } from "./graphQuality";

describe("explainGraphQualityIssue", () => {
  it("returns the original diagnostic for guided and professional modes", () => {
    const issue = "模块 `core` 出边 9 条，疑似把加载/装配边混入了领域依赖。";
    expect(explainGraphQualityIssue(issue, "guided")).toBe(issue);
    expect(explainGraphQualityIssue(issue, "professional")).toBe(issue);
  });

  it("explains fan-out in plain language for beginner mode", () => {
    const issue = "模块 `core` 出边 9 条，疑似把加载/装配边混入了领域依赖。";
    expect(explainGraphQualityIssue(issue, "beginner")).toContain("按顺序加载文件");
  });

  it("explains entry fan-out before the generic fan-out pattern", () => {
    const issue = "入口模块 `index` 出边 3 条；入口通常只应连接组合根和直接绑定的 DOM 模块。";
    expect(explainGraphQualityIssue(issue, "beginner")).toContain("入口页面或入口程序连接了太多东西");
  });

  it("explains tiny leaves and graph density", () => {
    expect(explainGraphQualityIssue("模块 `helper` 是职责很短的叶子节点，疑似可合并进消费方。", "beginner")).toContain("只做一件很小的事");
    expect(explainGraphQualityIssue("图密度偏高：8 条边 / 3 个模块 = 2.7；检查是否存在可经组合根推导的重复边。", "beginner")).toContain("蜘蛛网");
  });

  it("keeps unknown diagnostics visible with a plain prefix", () => {
    expect(explainGraphQualityIssue("future diagnostic", "beginner")).toContain("架构检查提醒");
  });
});
