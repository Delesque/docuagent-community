import { useCallback, useEffect, useState } from "react";
import {
  commitGitChanges,
  fetchGitDiff,
  fetchGitStatus,
  type GitDiff,
  type GitStatus,
} from "../api";

interface GitPanelProps {
  path: string;
  onClose: () => void;
}

export function GitPanel({ path, onClose }: GitPanelProps) {
  const [gitState, setGitState] = useState<GitStatus | null>(null);
  const [diff, setDiff] = useState<GitDiff>({ working: "", staged: "" });
  const [selected, setSelected] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const next = await fetchGitStatus(path);
      setGitState(next);
      if (next.repo && next.changes.length > 0) {
        const first = next.changes[0]?.path ?? "";
        setSelected(first);
        setDiff(await fetchGitDiff(path, first));
      } else {
        setSelected("");
        setDiff({ working: "", staged: "" });
      }
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [path]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleSelect = useCallback(
    async (file: string) => {
      setSelected(file);
      setError("");
      try {
        setDiff(await fetchGitDiff(path, file));
      } catch (cause) {
        setError((cause as Error).message);
      }
    },
    [path],
  );

  const handleCommit = useCallback(async () => {
    if (!message.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await commitGitChanges(path, message.trim());
      setGitState(result.status);
      setSelected("");
      setDiff({ working: "", staged: "" });
      setMessage("");
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [message, path]);

  const diffText = [diff.working, diff.staged].filter(Boolean).join("\n");

  return (
    <div
      className="absolute inset-0 z-[60] flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm"
      role="dialog"
      aria-modal="true"
      aria-label="Git 面板"
    >
      <section className="flex max-h-full w-full max-w-5xl flex-col overflow-hidden rounded-lg border border-ink/60 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink/40 px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">Git</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {gitState?.repo
                ? `${gitState.branch || "(detached)"} @ ${gitState.head || "no commits"}`
                : "当前目录不是 Git 仓库"}
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={busy}
              className="inline-flex h-8 items-center rounded-md border border-ink/60 px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-ink-ghost disabled:opacity-40"
            >
              刷新
            </button>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-8 items-center rounded-md px-3 font-mono text-[11px] text-chalk-faint transition-colors hover:text-chalk"
            >
              关闭
            </button>
          </div>
        </header>

        {error ? (
          <p className="border-b border-vermilion/20 bg-vermilion/5 px-5 py-2 font-mono text-[10.5px] text-vermilion">
            {error}
          </p>
        ) : null}

        {!gitState?.repo ? (
          <div className="p-8">
            <p className="font-body text-[12.5px] text-chalk-dim">
              当前项目还没有 Git 仓库，无法显示变更。
            </p>
          </div>
        ) : (
          <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 overflow-y-auto p-5 md:grid-cols-[280px_1fr]">
            <div className="min-h-0 overflow-y-auto rounded-lg border border-ink/40 bg-paper/70">
              <div className="border-b border-ink/30 px-3 py-2">
                <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
                  变更文件（{gitState.changes.length}）
                </p>
              </div>
              <div className="space-y-0.5 p-2">
                {gitState.changes.map((change) => (
                  <button
                    key={change.path}
                    type="button"
                    onClick={() => void handleSelect(change.path)}
                    className={`w-full truncate rounded-none px-2 py-1.5 text-left font-mono text-[10.5px] transition-colors ${
                      selected === change.path
                        ? "bg-ink-ghost text-chalk"
                        : "text-chalk-dim hover:bg-ink-ghost/60 hover:text-chalk"
                    }`}
                    title={change.path}
                  >
                    <span
                      className={
                        change.staged
                          ? "text-emerald"
                          : change.untracked
                            ? "text-chalk-faint"
                            : "text-vermilion"
                      }
                    >
                      {change.status}
                    </span>{" "}
                    {change.path}
                  </button>
                ))}
                {gitState.changes.length === 0 ? (
                  <p className="px-2 py-4 text-center font-mono text-[10.5px] text-chalk-faint">
                    工作区干净
                  </p>
                ) : null}
              </div>
            </div>

            <div className="flex min-h-0 flex-col gap-3">
              <div className="min-h-0 flex-1 overflow-hidden rounded-lg border border-ink/40 bg-paper/70">
                <div className="flex items-center justify-between border-b border-ink/30 px-3 py-2">
                  <span className="truncate font-mono text-[11px] text-chalk">
                    {selected || "未选择文件"}
                  </span>
                  <span className="font-mono text-[9.5px] text-chalk-faint">
                    unified diff
                  </span>
                </div>
                <pre className="max-h-[46vh] overflow-auto whitespace-pre-wrap p-3 font-mono text-[10.5px] leading-[1.5] text-chalk-dim">
                  {diffText || "没有可显示的 diff。"}
                </pre>
              </div>

              <div className="rounded-lg border border-ink/40 bg-paper/70 p-3">
                <label className="mb-1.5 block font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
                  提交信息
                </label>
                <textarea
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                  placeholder="git commit -m ..."
                  rows={3}
                  className="w-full resize-none rounded-md border border-ink/50 bg-paper px-3 py-2 font-mono text-[11px] text-chalk outline-none transition-colors focus:border-ink"
                />
                <div className="mt-2 flex justify-end">
                  <button
                    type="button"
                    onClick={() => void handleCommit()}
                    disabled={busy || !message.trim()}
                    className="inline-flex h-8 items-center rounded-md border border-emerald/50 px-3 font-mono text-[11px] text-emerald transition-colors hover:bg-emerald/10 disabled:cursor-not-allowed disabled:opacity-40"
                  >
                    提交全部变更
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
