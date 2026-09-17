import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import type { TriageResult } from "../api";
import { TriagePanel } from "./TriagePanel";
import { FocusLayer } from "./FocusLayer";
import type { TaskItem } from "../api";

it("offers checkpoint recovery on a stopped module and hides it after verification", () => {
  const module = { id: "core", name: "Core", path: "core.py", depends_on: [], brief: "Core", responsibility: "Core", needs_ui: false, group: null };
  const task = { id: "core", status: "pending", checkpoint_available: true, target_files: [] } as unknown as TaskItem;
  const render = (value: TaskItem) => renderToStaticMarkup(
    <FocusLayer module={module} task={value} reducedMotion onExit={() => {}} onResumeTask={() => {}} />,
  );
  expect(render(task)).toContain("从检查点恢复");
  expect(render({ ...task, status: "verified" })).not.toContain("从检查点恢复");
});

const result: TriageResult = {
  summary: "Core breaks auth.",
  dependency_chains: [["core", "auth"]],
  groups: [
    {
      severity: "critical",
      recommendation: "Fix core first.",
      errors: [
        {
          task_id: "core",
          module_id: "core",
          title: "Core syntax error",
          detail: "invalid syntax",
        },
      ],
    },
  ],
  suggested_order: ["core"],
};

const noop = (): void => undefined;

describe("TriagePanel", () => {
  it("renders the summary, severity groups, and decision actions", () => {
    const errors = result.groups[0]?.errors ?? [];
    const html = renderToStaticMarkup(
      <TriagePanel
        result={result}
        errors={errors}
        architecture={null}
        onRetryAll={noop}
        onRetryTask={noop}
        onResumeTask={noop}
        onFocus={noop}
        onClose={noop}
      />,
    );
    expect(html).toContain("对话流错误汇总");
    expect(html).toContain("Core breaks auth.");
    expect(html).toContain("致命错误");
    expect(html).toContain("按推荐顺序重试");
    expect(html).toContain("从检查点恢复");
    expect(html).toContain("重新生成");
    expect(html).not.toContain("批量跳过警告");
    expect(html).not.toContain("逐个处理");
  });
});
