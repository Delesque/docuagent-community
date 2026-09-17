import { useCallback, useEffect, useState } from "react";
import {
  applyMemoryCandidate,
  listMemoryCandidates,
  rejectMemoryCandidate,
  revertMemoryCandidate,
  suggestMemoryCandidates,
  type MemoryCandidate,
  type ProviderConfig,
} from "../api";

const TARGET_LABELS: Record<MemoryCandidate["target"], string> = {
  "user-profile": "用户画像",
  standards: "项目规范",
  recipe: "项目 recipe",
};

const STATUS_LABELS: Record<MemoryCandidate["status"], string> = {
  pending: "待审阅",
  applied: "已应用",
  rejected: "已拒绝",
  reverted: "已回滚",
};

export function MemoryPanel({
  path,
  provider,
  onClose,
  embedded = false,
}: {
  path: string;
  provider: ProviderConfig;
  onClose: () => void;
  embedded?: boolean;
}) {
  const [candidates, setCandidates] = useState<MemoryCandidate[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setCandidates(await listMemoryCandidates(path));
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [path]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function run(action: () => Promise<unknown>): Promise<void> {
    setBusy(true);
    setError("");
    try {
      await action();
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={embedded ? "h-full overflow-hidden" : "absolute inset-0 z-50 flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm"}>
      <section className={`flex h-full w-full flex-col overflow-hidden border border-ink-dim/50 bg-paper-raise shadow-2xl ${embedded ? "" : "max-h-full max-w-3xl rounded-lg"}`}>
        <header className="flex items-center gap-3 border-b border-ink-ghost px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">记忆沉淀</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {candidates.filter((item) => item.status === "pending").length} 个待审阅
            </p>
          </div>
          <button
            type="button"
            onClick={() => void run(() => suggestMemoryCandidates(path, provider))}
            disabled={busy}
            className="inline-flex h-8 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-paper-float disabled:opacity-40"
          >
            {busy ? "生成中" : "生成候选"}
          </button>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto inline-flex h-8 items-center rounded-md px-3 font-mono text-[11px] text-chalk-faint transition-colors hover:text-chalk"
          >
            关闭
          </button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4" data-scrollable>
          {error ? (
            <p className="rounded bg-vermilion/5 p-2 font-mono text-[10px] text-vermilion">
              {error}
            </p>
          ) : null}
          {candidates.length === 0 ? (
            <p className="font-body text-[12.5px] text-chalk-faint">
              还没有记忆候选。完成任务后可以生成候选，并在审阅后应用或拒绝。
            </p>
          ) : (
            candidates.map((candidate) => (
              <article
                key={candidate.id}
                className="rounded-lg border border-ink-ghost bg-paper/70 p-4"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-chalk-dim">
                    {TARGET_LABELS[candidate.target]}
                  </span>
                  <span className="font-mono text-[10px] text-chalk-faint">
                    {STATUS_LABELS[candidate.status]}
                  </span>
                  <span className="truncate font-mono text-[9.5px] text-chalk-faint">
                    {candidate.created_at}
                  </span>
                  {candidate.status === "pending" ? (
                    <div className="ml-auto flex gap-1.5">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void run(() => applyMemoryCandidate(path, candidate.id))
                        }
                        className="inline-flex h-7 items-center rounded border border-ink-dim/45 px-2.5 font-mono text-[10.5px] text-chalk transition-colors hover:border-ink disabled:opacity-40"
                      >
                        应用
                      </button>
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() =>
                          void run(() => rejectMemoryCandidate(path, candidate.id))
                        }
                        className="inline-flex h-7 items-center rounded border border-ink-dim/45 px-2.5 font-mono text-[10.5px] text-chalk-dim transition-colors hover:border-vermilion hover:text-vermilion disabled:opacity-40"
                      >
                        拒绝
                      </button>
                    </div>
                  ) : candidate.status === "applied" ? (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() =>
                        void run(() => revertMemoryCandidate(path, candidate.id))
                      }
                      className="ml-auto inline-flex h-7 items-center rounded border border-ink-dim/45 px-2.5 font-mono text-[10.5px] text-chalk-dim transition-colors hover:border-ink hover:text-chalk disabled:opacity-40"
                    >
                      回滚
                    </button>
                  ) : null}
                </div>
                <h3 className="mt-2 font-body text-[13px] font-semibold text-chalk">
                  {candidate.title}
                </h3>
                <p className="mt-1 whitespace-pre-wrap font-mono text-[10.5px] leading-[1.5] text-chalk-dim">
                  {candidate.content}
                </p>
                <p className="mt-2 font-mono text-[9.5px] text-chalk-faint">
                  来源：{candidate.source} · 理由：{candidate.reason}
                </p>
              </article>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
