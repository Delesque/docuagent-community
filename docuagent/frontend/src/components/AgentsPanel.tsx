import { useState } from "react";
import {
  clearAgentErrors,
  getAgentDetail,
  sendAgentMessage,
  type AgentDetail,
  type AgentInfo,
} from "../api";

const STATUS_LABELS: Record<string, string> = {
  idle: "空闲",
  running: "生成中",
  review: "待审阅",
  blocked: "阻塞",
  archived: "已归档",
};

export function AgentsPanel({
  agents,
  path,
  onClose,
}: {
  agents: AgentInfo[];
  path: string;
  onClose: () => void;
}) {
  const [details, setDetails] = useState<Record<string, AgentDetail>>({});
  const [loadingModule, setLoadingModule] = useState<string | null>(null);
  const [messageDrafts, setMessageDrafts] = useState<Record<string, string>>({});

  async function loadDetail(moduleId: string): Promise<void> {
    if (!path) return;
    setLoadingModule(moduleId);
    try {
      const detail = await getAgentDetail(path, moduleId);
      setDetails((previous) => ({ ...previous, [moduleId]: detail }));
    } catch (cause) {
      setDetails((previous) => ({
        ...previous,
        [moduleId]: {
          module_id: moduleId,
          work_log: `读取失败：${(cause as Error).message}`,
          error_memory: [],
          work_log_exists: false,
          error_memory_exists: false,
          work_log_path: "",
          error_memory_path: "",
        },
      }));
    } finally {
      setLoadingModule(null);
    }
  }

  async function handleClearErrors(moduleId: string): Promise<void> {
    if (!path) return;
    try {
      const errorMemory = await clearAgentErrors(path, moduleId);
      setDetails((previous) => {
        const detail = previous[moduleId];
        if (!detail) return previous;
        return { ...previous, [moduleId]: { ...detail, error_memory: errorMemory } };
      });
    } catch (cause) {
      setDetails((previous) => ({
        ...previous,
        [moduleId]: {
          ...(previous[moduleId] ?? {
            module_id: moduleId,
            work_log: "",
            error_memory: [],
            work_log_exists: false,
            error_memory_exists: false,
            work_log_path: "",
            error_memory_path: "",
          }),
          work_log: `清空失败：${(cause as Error).message}`,
        },
      }));
    }
  }

  async function handleSendMessage(moduleId: string): Promise<void> {
    const message = (messageDrafts[moduleId] ?? "").trim();
    if (!path || !message) return;
    try {
      const result = await sendAgentMessage(path, moduleId, message);
      setMessageDrafts((previous) => ({ ...previous, [moduleId]: "" }));
      setDetails((previous) => {
        const detail = previous[moduleId];
        if (!detail) return previous;
        return {
          ...previous,
          [moduleId]: { ...detail, session: result.content },
        };
      });
    } catch (cause) {
      setDetails((previous) => ({
        ...previous,
        [moduleId]: {
          ...(previous[moduleId] ?? {
            module_id: moduleId,
            work_log: "",
            error_memory: [],
            work_log_exists: false,
            error_memory_exists: false,
            work_log_path: "",
            error_memory_path: "",
          }),
          work_log: `转发失败：${(cause as Error).message}`,
        },
      }));
    }
  }

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm">
      <section className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-ink-dim/50 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink-ghost px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">子 Agent 注册表</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {agents.length} 个 · 地址 = module_id
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

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4" data-scrollable>
          {agents.length === 0 ? (
            <p className="font-body text-[12.5px] text-chalk-faint">
              还没有注册任何子 Agent。并行生成任务时会自动注册。
            </p>
          ) : (
            agents.map((agent) => {
              const detail = details[agent.module_id];
              return (
              <article
                key={agent.module_id}
                className="rounded-lg border border-ink-ghost bg-paper/70 p-4"
              >
                <div className="flex items-center gap-3">
                  <span
                    aria-hidden
                    className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                      agent.status === "running" ? "animate-pulse" : ""
                    }`}
                    style={{
                      backgroundColor:
                        agent.status === "running"
                          ? "#6CFFA8"
                          : agent.status === "review"
                            ? "#FFC857"
                            : agent.status === "blocked"
                              ? "#FF5C63"
                              : "#5FB58F",
                    }}
                  />
                  <strong className="font-mono text-[12px] text-chalk">
                    {agent.module_id}
                  </strong>
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-chalk-dim">
                    {STATUS_LABELS[agent.status] ?? agent.status}
                  </span>
                  {agent.task_id ? (
                    <span className="rounded bg-paper-float px-1.5 py-0.5 font-mono text-[9.5px] text-chalk-dim">
                      task: {agent.task_id}
                    </span>
                  ) : null}
                </div>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 font-mono text-[9.5px] text-chalk-faint">
                  {agent.work_area && agent.work_area.length > 0 ? (
                    <span>工作区：{agent.work_area.join("、")}</span>
                  ) : null}
                  {agent.work_log ? <span>日志：{agent.work_log}</span> : null}
                  {agent.error_memory ? (
                    <span>错误记忆：{agent.error_memory}</span>
                  ) : null}
                </div>
                {agent.last_error ? (
                  <p className="mt-2 whitespace-pre-wrap rounded bg-vermilion/5 p-2 font-mono text-[10px] text-vermilion">
                    {agent.last_error}
                  </p>
                ) : null}
                <div className="mt-3 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void loadDetail(agent.module_id)}
                    disabled={loadingModule === agent.module_id}
                    className="inline-flex h-7 items-center rounded border border-ink-dim/45 px-2.5 font-mono text-[10.5px] text-chalk transition-colors hover:border-ink disabled:opacity-40"
                  >
                    {loadingModule === agent.module_id
                      ? "读取中"
                      : detail
                        ? "刷新记忆"
                        : "查看记忆"}
                  </button>
                  {detail && (detail.work_log_exists || detail.error_memory_exists) ? (
                    <span className="font-mono text-[9.5px] text-chalk-faint">
                      记忆文件已持久化
                    </span>
                  ) : null}
                </div>
                {detail ? (
                  <div className="mt-3 grid gap-3">
                    <section>
                      <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-chalk-dim">
                        工作日志
                      </p>
                      <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded border border-ink-ghost bg-paper/80 p-3 font-mono text-[10px] leading-[1.5] text-chalk-dim">
                        {detail.work_log || "暂无日志"}
                      </pre>
                    </section>
                    <section>
                      <div className="mb-1 flex items-center gap-2">
                        <p className="font-mono text-[10px] uppercase tracking-[0.12em] text-chalk-dim">
                          错误记忆
                        </p>
                        {detail.error_memory.length > 0 ? (
                          <button
                            type="button"
                            onClick={() => void handleClearErrors(agent.module_id)}
                            className="ml-auto rounded border border-vermilion/40 px-2 py-0.5 font-mono text-[9.5px] text-vermilion transition-colors hover:bg-vermilion/10"
                          >
                            清空
                          </button>
                        ) : null}
                      </div>
                      {detail.error_memory.length === 0 ? (
                        <p className="rounded border border-ink-ghost bg-paper/70 p-3 font-mono text-[10px] text-chalk-faint">
                          暂无错误记忆
                        </p>
                      ) : (
                        <div className="space-y-2">
                          {detail.error_memory.map((entry) => (
                            <article
                              key={entry.id}
                              className="rounded border border-vermilion/20 bg-vermilion/5 p-2.5"
                            >
                              <p className="font-mono text-[9.5px] text-chalk-faint">
                                {entry.created_at} · {entry.source}
                                {entry.task_id ? ` · ${entry.task_id}` : ""}
                              </p>
                              <p className="mt-1 whitespace-pre-wrap font-mono text-[10px] text-vermilion">
                                {entry.error}
                              </p>
                            </article>
                          ))}
                        </div>
                      )}
                    </section>
                    <section>
                      <p className="mb-1 font-mono text-[10px] uppercase tracking-[0.12em] text-chalk-dim">
                        用户消息转发
                      </p>
                      <div className="flex gap-2">
                        <input
                          value={messageDrafts[agent.module_id] ?? ""}
                          onChange={(event) =>
                            setMessageDrafts((previous) => ({
                              ...previous,
                              [agent.module_id]: event.target.value,
                            }))
                          }
                          placeholder="给子 Agent 留言…"
                          className="h-8 min-w-0 flex-1 rounded-md border border-ink-dim/45 bg-paper px-3 font-mono text-[10.5px] text-chalk outline-none focus:border-ink"
                        />
                        <button
                          type="button"
                          onClick={() => void handleSendMessage(agent.module_id)}
                          className="inline-flex h-8 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[10.5px] text-chalk transition-colors hover:bg-paper-float"
                        >
                          转发
                        </button>
                      </div>
                      {detail.session ? (
                        <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap rounded border border-ink-ghost bg-paper/80 p-2.5 font-mono text-[9.5px] leading-[1.45] text-chalk-dim">
                          {detail.session}
                        </pre>
                      ) : null}
                    </section>
                  </div>
                ) : null}
              </article>
              );
            })
          )}
        </div>
      </section>
    </div>
  );
}
