/** Modification-suggestion surface for existing-project onboarding (M4).
 *
 *  Suggestions are `suggestion` node attachments produced by the Onboarding Agent and
 *  mounted on their module. Accepting one enqueues a pending task through the same
 *  DAG the task panel drives; rejecting just resolves it. */

import type { NodeAttachment } from "../api";

export interface SuggestionEntry {
  moduleId: string;
  attachment: NodeAttachment;
}

interface SuggestionsPanelProps {
  entries: SuggestionEntry[];
  busyId: string | null;
  onAccept: (moduleId: string, attachmentId: string) => void;
  onReject: (moduleId: string, attachmentId: string) => void;
  onClose: () => void;
}

const KIND_LABEL: Record<string, string> = {
  fix: "修复",
  improve: "改进",
  risk: "风险",
  architecture: "架构",
  cleanup: "清理",
  security: "安全",
};

export function SuggestionsPanel({
  entries,
  busyId,
  onAccept,
  onReject,
  onClose,
}: SuggestionsPanelProps) {
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm">
      <section className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-ink-dim/50 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink-ghost px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">修改建议</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {entries.length} 条待处理 · 采纳后进入任务面板
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto inline-flex h-8 items-center rounded-md px-3 font-mono text-[11px] text-chalk-faint transition-colors hover:text-chalk"
          >
            关闭
          </button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {entries.length === 0 ? (
            <p className="font-body text-[12.5px] text-chalk-dim">
              没有待处理的修改建议。
            </p>
          ) : (
            entries.map(({ moduleId, attachment }) => {
              const busy = busyId === attachment.id;
              return (
                <article
                  key={attachment.id}
                  className="rounded-lg border border-ink-ghost bg-paper/70 p-4"
                >
                  <div className="flex items-center gap-2">
                    <span className="rounded border border-ink-dim/45 px-1.5 py-0.5 font-mono text-[9.5px] text-chalk-dim">
                      {KIND_LABEL[attachment.kind ?? "improve"] ?? "改进"}
                    </span>
                    <span className="font-mono text-[9.5px] text-chalk-faint">
                      {attachment.priority ?? "medium"}
                    </span>
                    <span className="ml-auto font-mono text-[9.5px] text-chalk-faint">
                      {moduleId}
                    </span>
                  </div>
                  <p className="mt-1.5 font-body text-[12.5px] text-chalk">
                    {attachment.title ?? attachment.text}
                  </p>
                  {attachment.text && attachment.title && attachment.text !== attachment.title ? (
                    <p className="mt-1 font-body text-[11.5px] leading-relaxed text-chalk-dim">
                      {attachment.text}
                    </p>
                  ) : null}
                  {attachment.files && attachment.files.length > 0 ? (
                    <p className="mt-1.5 font-mono text-[9.5px] text-chalk-faint">
                      {attachment.files.join(" · ")}
                    </p>
                  ) : null}
                  <div className="mt-2.5 flex gap-2">
                    <button
                      type="button"
                      onClick={() => onAccept(moduleId, attachment.id)}
                      disabled={busy}
                      className="inline-flex h-7 items-center rounded border border-ink-dim/45 px-2.5 font-mono text-[10.5px] text-chalk transition-colors hover:border-ink disabled:opacity-40"
                    >
                      采纳
                    </button>
                    <button
                      type="button"
                      onClick={() => onReject(moduleId, attachment.id)}
                      disabled={busy}
                      className="inline-flex h-7 items-center rounded border border-ink-dim/45 px-2.5 font-mono text-[10.5px] text-chalk-dim transition-colors hover:border-ink hover:text-chalk disabled:opacity-40"
                    >
                      拒绝
                    </button>
                  </div>
                </article>
              );
            })
          )}
        </div>
      </section>
    </div>
  );
}
