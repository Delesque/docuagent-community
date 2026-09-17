import { useState } from "react";
import type { TaskItem } from "../api";

export interface HandoffEntry {
  task: TaskItem;
  moduleName: string;
  modulePath: string;
}

export function HandoffPanel({
  entries,
  busyTaskId = null,
  onRetry,
  onReject,
  onSendMessage,
  onClose,
}: {
  entries: HandoffEntry[];
  busyTaskId?: string | null;
  onRetry: (taskId: string) => void;
  onReject: (taskId: string) => void;
  onSendMessage: (moduleId: string, text: string) => Promise<void>;
  onClose: () => void;
}) {
  const [messageDrafts, setMessageDrafts] = useState<Record<string, string>>({});
  const [sendingId, setSendingId] = useState<string | null>(null);

  return (
    <div className="absolute inset-0 z-[55] flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm">
      <section className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-vermilion/40 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-vermilion/30 px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">子 Agent 请示</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {entries.length} 个子 Agent 遇到超出模块边界的工作
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

        <p className="border-b border-vermilion/25 bg-vermilion/5 px-5 py-2 font-body text-[11.5px] leading-[1.55] text-chalk-dim">
          子 Agent 不能擅自扩大自己的模块范围，于是把这件事交回给你：要么授权它继续做，要么重新生成，要么不再处理。
        </p>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4" data-scrollable>
          {entries.map(({ task, moduleName, modulePath }) => {
            const reason = task.last_error.replace(/^已转接对话流：?/, "");
            const draft = (messageDrafts[task.id] ?? "").trim();
            const isBusy = busyTaskId === task.id || sendingId === task.id;
            return (
              <article
                key={task.id}
                className="rounded-lg border border-vermilion/30 bg-paper/70 p-4"
              >
                <div className="flex items-center gap-3">
                  <span
                    aria-hidden
                    className="h-2.5 w-2.5 shrink-0 animate-pulse rounded-full bg-vermilion"
                  />
                  <strong className="font-mono text-[12px] text-chalk">
                    {task.id}
                  </strong>
                  <span className="rounded bg-paper-float px-1.5 py-0.5 font-mono text-[9.5px] text-chalk-dim">
                    {moduleName}
                  </span>
                  <span className="truncate font-mono text-[10px] text-chalk-faint">
                    {modulePath || task.module_id}
                  </span>
                </div>

                <p className="mt-3 whitespace-pre-wrap rounded border border-vermilion/25 bg-vermilion/5 p-3 font-mono text-[10.5px] leading-[1.5] text-chalk-dim">
                  <span className="text-vermilion">它说：</span>
                  {reason || task.last_error}
                </p>

                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[9.5px] text-chalk-faint">
                  <span>文件：{task.target_files.join("、")}</span>
                  {task.depends_on.length > 0 ? (
                    <span>依赖：{task.depends_on.join("、")}</span>
                  ) : null}
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    onClick={() => onRetry(task.id)}
                    disabled={isBusy}
                    className="inline-flex h-7 items-center rounded border border-ink/70 bg-ink-ghost px-2.5 font-mono text-[10.5px] text-chalk transition-colors hover:bg-paper-float disabled:opacity-40"
                  >
                    {busyTaskId === task.id ? "生成中" : "重新生成"}
                  </button>
                  <button
                    type="button"
                    onClick={() => onReject(task.id)}
                    disabled={isBusy}
                    className="inline-flex h-7 items-center rounded border border-vermilion/50 px-2.5 font-mono text-[10.5px] text-vermilion transition-colors hover:bg-vermilion/10 disabled:opacity-40"
                  >
                    不再处理
                  </button>
                </div>

                <div className="mt-3 flex gap-2">
                  <input
                    value={messageDrafts[task.id] ?? ""}
                    onChange={(event) =>
                      setMessageDrafts((previous) => ({
                        ...previous,
                        [task.id]: event.target.value,
                      }))
                    }
                    placeholder="授权它继续，例如：允许你修改 src/main.js 完成接线"
                    className="h-8 min-w-0 flex-1 rounded-md border border-ink-dim/45 bg-paper px-3 font-mono text-[10.5px] text-chalk outline-none focus:border-ink"
                  />
                  <button
                    type="button"
                    onClick={async () => {
                      if (!draft || isBusy) return;
                      setSendingId(task.id);
                      try {
                        await onSendMessage(task.module_id, draft);
                        setMessageDrafts((previous) => ({
                          ...previous,
                          [task.id]: "",
                        }));
                        onRetry(task.id);
                      } finally {
                        setSendingId(null);
                      }
                    }}
                    disabled={!draft || isBusy}
                    title={
                      draft
                        ? "把指令转给子 Agent，并立即带着指令重新生成该模块"
                        : "先输入补充指令；没有指令请直接点「重新生成」"
                    }
                    className="inline-flex h-8 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[10.5px] text-chalk transition-colors hover:bg-paper-float disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    {sendingId === task.id ? "转发中" : "转发并重试"}
                  </button>
                </div>
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
