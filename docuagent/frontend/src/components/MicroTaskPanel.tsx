import type { TaskItem } from "../api";

interface MicroTaskPanelProps {
  task: TaskItem;
  busy?: boolean;
  onApply: () => void;
  onReject: () => void;
  onClose: () => void;
}

export function MicroTaskPanel({
  task,
  busy = false,
  onApply,
  onReject,
  onClose,
}: MicroTaskPanelProps) {
  const diff = (task.patch ?? [])
    .map((entry) => `# ${entry.path}\n${entry.diff}`)
    .join("\n\n");

  return (
    <div
      className="absolute inset-0 z-[70] flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="微任务"
    >
      <section className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-ink/60 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink/40 px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">微任务</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {task.module_id} · {task.status}
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

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <p className="mb-3 font-body text-[13px] leading-relaxed text-chalk">
            {task.summary}
          </p>
          <pre className="max-h-[50vh] overflow-auto whitespace-pre-wrap rounded-lg border border-ink/40 bg-paper/80 p-3 font-mono text-[10.5px] leading-[1.55] text-chalk-dim">
            {diff || "这个微任务没有生成文件变更。"}
          </pre>
        </div>

        <footer className="flex items-center gap-2 border-t border-ink/40 px-5 py-3">
          <button
            type="button"
            onClick={onApply}
            disabled={busy || task.status !== "review"}
            className="inline-flex h-8 items-center rounded-md border border-emerald/50 px-3 font-mono text-[11px] text-emerald transition-colors hover:bg-emerald/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            接受并应用
          </button>
          <button
            type="button"
            onClick={onReject}
            disabled={busy}
            className="inline-flex h-8 items-center rounded-md border border-vermilion/50 px-3 font-mono text-[11px] text-vermilion transition-colors hover:bg-vermilion/10 disabled:cursor-not-allowed disabled:opacity-40"
          >
            拒绝
          </button>
        </footer>
      </section>
    </div>
  );
}
