import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { HandoffPanel, type HandoffEntry } from "./HandoffPanel";

const entries: HandoffEntry[] = [
  {
    task: {
      id: "auth",
      module_id: "auth",
      summary: "Auth",
      target_files: ["src/auth.py"],
      depends_on: ["core"],
      verification: [],
      status: "blocked",
      patch: [],
      thinking: "",
      last_error: "已转接对话流：这个改动会改变模块边界。",
    },
    moduleName: "认证",
    modulePath: "src/auth",
  },
];

const noop = (): void => undefined;
const noopAsync = async (): Promise<void> => undefined;

describe("HandoffPanel", () => {
  it("renders the handoff reason and arbitration actions", () => {
    const html = renderToStaticMarkup(
      <HandoffPanel
        entries={entries}
        onRetry={noop}
        onReject={noop}
        onSendMessage={noopAsync}
        onClose={noop}
      />,
    );
    expect(html).toContain("子 Agent 请示");
    expect(html).toContain("这个改动会改变模块边界。");
    expect(html).toContain("它说：");
    expect(html).toContain("重新生成");
    expect(html).toContain("不再处理");
    expect(html).toContain("转发并重试");
  });
});
