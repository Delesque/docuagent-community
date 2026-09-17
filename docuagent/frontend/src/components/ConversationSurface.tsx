import {
  ModeBadge,
  OverviewTimeline,
} from "./v2/OverviewTimeline";
import { TypewriterOutput, type Chip, type Message } from "./v2/TypewriterOutput";
import { DialogBox, type DialogTab } from "./v2/DialogBox";
import type { ViewState } from "../conversation/viewMode";
import type { InterviewMode } from "../api";

export interface ConversationSurfaceProps {
  view: ViewState;
  booted: boolean;
  dialogVisible: boolean;
  tabs: DialogTab[];
  activeTab: string | null;
  drafts: Record<string, string>;
  busy: boolean;
  taskStreaming: boolean;
  retryStatus: {
    label: string;
    attempt: number;
    total: number;
  } | null;
  modelStreaming: boolean;
  displayedMessage: Message | null;
  messages: Message[];
  reviewIndex: number;
  enterFrom: "top" | "bottom" | null;
  onPick: (index: number) => void;
  onExit: () => void;
  onChipClick: (chip: Chip) => void;
  onTabChange: (tabId: string) => void;
  onDraftChange: (tabId: string, value: string) => void;
  onCloseDialog: () => void;
  onSubmit: (values: Record<string, string>) => void;
  onOpenChat: () => void;
  onStopRetry: () => void;
  onStopModelStream: () => void;
  onStopTask?: () => void;
  interviewMode?: InterviewMode;
  /** 首次进入项目时展示的一次性访谈模式选择。 */
  showInterviewModePicker?: boolean;
  onInterviewModeChange?: (mode: InterviewMode) => void;
}

export function ConversationSurface({
  view,
  booted,
  dialogVisible,
  tabs,
  activeTab,
  drafts,
  busy,
  taskStreaming,
  retryStatus,
  modelStreaming,
  displayedMessage,
  messages,
  reviewIndex,
  enterFrom,
  onPick,
  onExit,
  onChipClick,
  onTabChange,
  onDraftChange,
  onCloseDialog,
  onSubmit,
  onOpenChat,
  onStopRetry,
  onStopModelStream,
  onStopTask,
  interviewMode = "guided",
  showInterviewModePicker = false,
  onInterviewModeChange,
}: ConversationSurfaceProps) {
  return (
    <>
      <ModeBadge mode={view.mode} />
      {booted && showInterviewModePicker && onInterviewModeChange ? (
        <div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/75 px-6 backdrop-blur-sm"
          aria-modal="true"
          role="dialog"
          aria-label="选择访谈模式"
        >
          <div className="w-full max-w-2xl rounded-2xl border border-vermilion/40 bg-paper-raise p-7 shadow-2xl">
            <p className="font-display text-[22px] text-chalk">选择访谈模式</p>
            <p className="mt-2 font-body text-[12.5px] text-chalk-dim">
              选择 AI 的解释和提问深度。选完即永久保存到 DocuAgent，之后可在设置中调整。
            </p>
            <div className="mt-6 grid grid-cols-3 gap-3" role="group" aria-label="访谈模式">
              {([["beginner", "陪伴学习", "白话 + 例子"], ["guided", "引导实践", "取舍 + 风险"], ["professional", "专业定制", "契约 + 波次"]] as const).map(([mode, label, detail]) => (
                <button
                  key={mode}
                  type="button"
                  aria-pressed={interviewMode === mode}
                  onClick={() => onInterviewModeChange(mode)}
                  className={`flex min-h-[10rem] flex-col justify-between rounded-xl border p-4 text-left transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-vermilion/70 ${
                    interviewMode === mode
                      ? "border-vermilion bg-vermilion/10 text-chalk"
                      : "border-ink-ghost text-chalk-dim hover:border-ink-dim hover:text-chalk"
                  }`}
                >
                  <span className="block font-body text-[15px] font-medium">{label}</span>
                  <span className="mt-3 block font-mono text-[10px] leading-relaxed opacity-70">{detail}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      ) : null}

      {view.mode === "overview" ? (
        <OverviewTimeline
          messages={messages}
          anchor={view.anchor}
          onPick={onPick}
          onExit={onExit}
        />
      ) : (
        /* Both `live` and `review` render the same full-screen typewriter: review is
           not a second layout, it is this layout showing a different turn, which is
           what keeps type size a single dial across the whole transcript. */
        <TypewriterOutput
          key={view.mode === "review" ? `review-${reviewIndex}` : "live"}
          visible={booted}
          message={displayedMessage}
          dialogOpen={dialogVisible}
          onChipClick={onChipClick}
          scale={view.scale}
          typing={view.mode === "live"}
          enterFrom={enterFrom}
        />
      )}

      <DialogBox
        isOpen={dialogVisible}
        tabs={tabs}
        activeTab={activeTab}
        drafts={drafts}
        busy={busy}
        onTabChange={onTabChange}
        onDraftChange={onDraftChange}
        onClose={onCloseDialog}
        onSubmit={onSubmit}
      />

      {/* Floating chat ball — visible in live mode whenever there are tabs to fill.
          Disappears in review/overview (the transcript is read-only there) and while the
          dialog is already open. Gives users a guaranteed click target regardless of
          whether any inline chip happened to appear in the message text. */}
      {view.mode === "live" && (tabs.length > 0 || taskStreaming) && !dialogVisible && booted ? (
        <button
          type="button"
          aria-label="打开输入"
          onClick={onOpenChat}
          className="absolute bottom-8 left-1/2 z-50 flex h-12 w-12 -translate-x-1/2 items-center justify-center rounded-full border border-ink-dim/60 bg-paper-raise shadow-lg transition-all duration-200 hover:border-ink hover:bg-paper-float hover:scale-110 active:scale-95"
          title={tabs.map((t) => t.label).join(" · ")}
        >
          <svg className="h-5 w-5 text-chalk-dim" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.5}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M12 20h9M16.5 3.5a2.121 2.121 0 013 3L7 19l-4 1 1-4L16.5 3.5z" />
          </svg>
          <span className="absolute inset-0 animate-ping rounded-full border border-ink/20" style={{ animationDuration: "2.4s", animationIterationCount: 1 }} />
        </button>
      ) : null}

      {retryStatus ? (
        <div className="absolute bottom-24 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-3 rounded-lg border border-ink-dim/50 bg-paper-raise/95 px-4 py-2 font-mono text-[11px] text-chalk shadow-2xl">
          <span>
            {retryStatus.label} · 第 {retryStatus.attempt}/{retryStatus.total} 次
          </span>
          <button
            type="button"
            onClick={onStopRetry}
            className="rounded border border-vermilion/50 px-2 py-0.5 text-vermilion transition-colors hover:bg-vermilion/10"
          >
            停止
          </button>
        </div>
      ) : null}

      {taskStreaming ? (
        <div className="absolute bottom-24 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-3 rounded-lg border border-ink/50 bg-paper-raise/95 px-4 py-2 font-mono text-[11px] text-chalk shadow-2xl">
          <span className="animate-pulse">工具循环运行中</span>
          <button type="button" onClick={onStopTask} className="rounded border border-vermilion/50 px-2 py-0.5 text-vermilion transition-colors hover:bg-vermilion/10">停止</button>
        </div>
      ) : null}

      {modelStreaming ? (
        <div className="absolute bottom-24 left-1/2 z-[60] flex -translate-x-1/2 items-center gap-3 rounded-lg border border-ink/50 bg-paper-raise/95 px-4 py-2 font-mono text-[11px] text-chalk shadow-2xl">
          <span className="animate-pulse">正在接收 AI 思考流…</span>
          <button
            type="button"
            onClick={onStopModelStream}
            className="rounded border border-vermilion/50 px-2 py-0.5 text-vermilion transition-colors hover:bg-vermilion/10"
          >
            停止
          </button>
        </div>
      ) : null}
    </>
  );
}
