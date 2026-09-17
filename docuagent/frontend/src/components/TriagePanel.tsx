import type { Architecture } from "../graph/types";
import type { TriageErrorItem, TriageGroup, TriageResult } from "../api";

interface TriagePanelProps {
  result: TriageResult;
  errors: TriageErrorItem[];
  architecture?: Architecture | null;
  busy?: boolean;
  busyTaskId?: string | null;
  onRetryAll: () => void;
  onRetryTask: (taskId: string) => void;
  onResumeTask: (taskId: string) => void;
  onFocus: (moduleId: string) => void;
  onClose: () => void;
}

const SEVERITY_META: Record<
  TriageGroup["severity"],
  { label: string; color: string }
> = {
  critical: { label: "致命错误", color: "#FF5C63" },
  warning: { label: "警告", color: "#FFC857" },
  info: { label: "提示", color: "#6C8A9E" },
};

function groupErrorCount(groups: TriageGroup[]): number {
  return groups.reduce((total, group) => total + group.errors.length, 0);
}

export function TriagePanel({
  result,
  errors,
  architecture,
  busy = false,
  busyTaskId = null,
  onRetryAll,
  onRetryTask,
  onResumeTask,
  onFocus,
  onClose,
}: TriagePanelProps) {
  const moduleName = (moduleId: string): string =>
    architecture?.modules.find((module) => module.id === moduleId)?.name ?? moduleId;
  const errorById = new Map(errors.map((error) => [error.task_id, error]));
  const total = groupErrorCount(result.groups);

  return (
    <div
      className="absolute inset-0 z-[60] flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="对话流错误汇总与决策"
    >
      <section className="flex max-h-full w-full max-w-4xl flex-col overflow-hidden rounded-lg border border-vermilion/40 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-vermilion/30 px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">对话流错误汇总</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              发现 {total} 个问题需要处理
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            {result.suggested_order.length > 0 ? (
              <span className="max-w-56 truncate font-mono text-[10px] text-chalk-faint">
                推荐顺序：{result.suggested_order.join(" → ")}
              </span>
            ) : null}
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-8 items-center rounded-md px-3 font-mono text-[11px] text-chalk-faint transition-colors hover:text-chalk"
            >
              关闭
            </button>
          </div>
        </header>

        <div className="flex-1 space-y-4 overflow-y-auto px-5 py-4" data-scrollable>
          <section className="rounded-lg border border-ink-ghost bg-paper/70 p-4">
            <h3 className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink">
              AI 总结
            </h3>
            <p className="mt-1.5 whitespace-pre-wrap font-body text-[12.5px] leading-relaxed text-chalk">
              {result.summary}
            </p>
          </section>

          {result.dependency_chains.length > 0 ? (
            <section className="rounded-lg border border-ink-ghost bg-paper/70 p-4">
              <h3 className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink">
                依赖链
              </h3>
              <ul className="mt-2 space-y-1">
                {result.dependency_chains.map((chain, index) => (
                  <li key={index} className="font-mono text-[10.5px] text-chalk-dim">
                    {chain.join(" → ")}
                  </li>
                ))}
              </ul>
            </section>
          ) : null}

          {result.groups.map((group) => {
            const meta = SEVERITY_META[group.severity];
            return (
              <section
                key={group.severity}
                className="rounded-lg border bg-paper/70 p-4"
                style={{ borderColor: `${meta.color}55` }}
              >
                <div className="flex items-center gap-2">
                  <span
                    aria-hidden
                    className="h-2.5 w-2.5 rounded-full"
                    style={{ backgroundColor: meta.color }}
                  />
                  <h3
                    className="font-mono text-[11px] uppercase tracking-[0.12em]"
                    style={{ color: meta.color }}
                  >
                    {meta.label}（{group.errors.length} 个）
                  </h3>
                </div>
                {group.recommendation ? (
                  <p className="mt-2 font-body text-[12px] leading-relaxed text-chalk-dim">
                    {group.recommendation}
                  </p>
                ) : null}
                <ul className="mt-3 space-y-2">
                  {group.errors.map((error) => {
                    const source = errorById.get(error.task_id);
                    return (
                      <li
                        key={error.task_id}
                        className="rounded-md border border-ink-ghost bg-paper-raise/70 p-3"
                      >
                        <div className="flex items-start gap-3">
                          <button
                            type="button"
                            onClick={() => error.module_id && onFocus(error.module_id)}
                            className="min-w-0 text-left font-mono text-[11px] text-chalk transition-colors hover:text-[#6CFFA8]"
                            title={error.module_id ? "跳到对应节点" : undefined}
                          >
                            <span className="font-semibold">{error.task_id}</span>
                            <span className="ml-2 text-chalk-faint">
                              {moduleName(error.module_id)}
                            </span>
                          </button>
                          <span className="ml-auto shrink-0 font-mono text-[9.5px] text-chalk-faint">
                            {source?.source ?? group.severity}
                          </span>
                        </div>
                        <p className="mt-1.5 font-mono text-[10.5px] text-chalk-dim">
                          {error.title}
                        </p>
                        {error.detail ? (
                          <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap rounded border border-vermilion/20 bg-vermilion/5 p-2 font-mono text-[9.5px] leading-[1.45] text-vermilion">
                            {error.detail}
                          </pre>
                        ) : null}
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            onClick={() => onResumeTask(error.task_id)}
                            disabled={busy || busyTaskId === error.task_id || !source?.checkpoint_available}
                            title={
                              source?.checkpoint_available
                                ? "从已保存的 Agent 工具循环检查点继续"
                                : "该任务没有可用检查点，只能重新生成"
                            }
                            className="inline-flex h-7 items-center rounded border border-ink/60 bg-ink-ghost px-2.5 font-mono text-[10px] text-chalk transition-colors hover:bg-paper-float disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            从检查点恢复
                          </button>
                          <button
                            type="button"
                            onClick={() => onRetryTask(error.task_id)}
                            disabled={busy || busyTaskId === error.task_id}
                            className="inline-flex h-7 items-center rounded border border-vermilion/50 px-2.5 font-mono text-[10px] text-vermilion transition-colors hover:bg-vermilion/10 disabled:cursor-not-allowed disabled:opacity-40"
                          >
                            重新生成
                          </button>
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </section>
            );
          })}
        </div>

        <footer className="flex flex-wrap items-center gap-2 border-t border-ink-ghost px-5 py-3">
          <button
            type="button"
            onClick={onRetryAll}
            disabled={busy || result.suggested_order.length === 0}
            className="inline-flex h-8 items-center rounded-md border border-ink/70 bg-ink-ghost px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-paper-float disabled:cursor-not-allowed disabled:opacity-40"
          >
            {busyTaskId ? `正在重试 ${busyTaskId}…` : "按推荐顺序重试"}
          </button>
        </footer>
      </section>
    </div>
  );
}
