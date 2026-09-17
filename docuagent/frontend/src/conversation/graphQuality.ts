import type { InterviewMode } from "../api";

/** Turn a technical graph-quality diagnostic into language the selected mode can read.
 *
 *  `architecture_graph_quality_issues` writes engineer-facing strings. Beginner mode
 *  keeps those strings available elsewhere, but the review surface should explain the
 *  same finding in plain terms rather than asking a non-programmer to parse fan-out.
 */
export function explainGraphQualityIssue(issue: string, mode: InterviewMode): string {
  if (mode !== "beginner") return issue;

  if (issue.includes("模块或边格式不正确")) {
    return "这次生成的架构检查信息格式不对，正在显示提醒而不是直接采用。";
  }
  if (issue.includes("入口模块") && issue.includes("出边")) {
    return "入口页面或入口程序连接了太多东西。入口通常只负责启动，并把界面交给第一个真正干活的模块；多余的连接可以删掉。";
  }
  if (issue.includes("出边") && issue.includes("加载/装配")) {
    return "这个模块同时依赖了太多其他模块。通常是 AI 把“按顺序加载文件”也画成了依赖线；真正稳定的关系应该是少数几条“调用”线，加载顺序可以写进项目规则里。";
  }
  if (issue.includes("职责很短的叶子节点")) {
    return "这个模块只做一件很小的事，建议把它并进使用它的模块。这样图里的方块更少，每个方块都能用一句话说清楚。";
  }
  if (issue.includes("图密度偏高")) {
    return "模块之间的连线比模块数量还多很多，图看起来会像蜘蛛网。可以检查有没有重复线：如果 A 连到 B、B 连到 C，通常就不用再画 A 直接连到 C。";
  }
  return `有一条架构检查提醒，用技术语言说就是：${issue}`;
}
