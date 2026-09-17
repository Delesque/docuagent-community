import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { INITIAL_VIEW_STATE } from "../conversation/viewMode";
import { ConversationSurface } from "./ConversationSurface";

const baseProps = {
  view: INITIAL_VIEW_STATE,
  booted: true,
  dialogVisible: false,
  tabs: [{ id: "goal", label: "项目目标", promptKey: "goal" }],
  activeTab: null,
  drafts: {},
  busy: false,
  taskStreaming: false,
  retryStatus: null,
  modelStreaming: false,
  displayedMessage: { role: "agent" as const, text: "开始访谈" },
  messages: [{ role: "agent" as const, text: "开始访谈" }],
  reviewIndex: 0,
  enterFrom: null as "top" | "bottom" | null,
  onPick: () => undefined,
  onExit: () => undefined,
  onChipClick: () => undefined,
  onTabChange: () => undefined,
  onDraftChange: () => undefined,
  onCloseDialog: () => undefined,
  onSubmit: () => undefined,
  onOpenChat: () => undefined,
  onStopRetry: () => undefined,
  onStopModelStream: () => undefined,
};

describe("ConversationSurface", () => {
  it("renders the live typewriter and the floating input ball", () => {
    const html = renderToStaticMarkup(<ConversationSurface {...baseProps} />);
    expect(html).toContain('aria-label="打开输入"');
  });

  it("renders the overview instead of the typewriter in overview mode", () => {
    const html = renderToStaticMarkup(
      <ConversationSurface
        {...baseProps}
        view={{ ...INITIAL_VIEW_STATE, mode: "overview" }}
      />,
    );
    expect(html).toContain("总览");
    expect(html).not.toContain("打开输入");
  });

  it("shows the full-screen interview mode picker before the first interview", () => {
    const html = renderToStaticMarkup(
      <ConversationSurface
        {...baseProps}
        showInterviewModePicker
        interviewMode="guided"
        onInterviewModeChange={() => undefined}
      />,
    );
    expect(html).toContain("选择访谈模式");
    expect(html).toContain("陪伴学习");
    expect(html).toContain("引导实践");
    expect(html).toContain("专业定制");
  });

  it("renders retry progress and the model stream stop control", () => {
    const html = renderToStaticMarkup(
      <ConversationSurface
        {...baseProps}
        retryStatus={{ label: "AI 修订", attempt: 2, total: 3 }}
        modelStreaming
      />,
    );
    expect(html).toContain("第 2/3 次");
    expect(html).toContain("正在接收 AI 思考流");
  });
});
