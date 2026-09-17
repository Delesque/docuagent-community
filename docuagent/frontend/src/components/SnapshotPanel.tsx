/** Rollback surface: every interview/edit/task moment is a project snapshot, and
 *  restoring one reloads the workspace from that moment. */

import type { SnapshotSummary } from "../api";

interface SnapshotPanelProps {
  snapshots: SnapshotSummary[];
  busy: boolean;
  onClose: () => void;
  onRestore: (snapshotId: string) => void;
}

export function SnapshotPanel({
  snapshots,
  busy,
  onClose,
  onRestore,
}: SnapshotPanelProps) {
  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm">
      <section className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-ink-dim/50 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink-ghost px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">项目快照</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {snapshots.length} 个可回滚时刻
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
          {snapshots.length === 0 ? (
            <p className="font-body text-[12.5px] text-chalk-dim">
              还没有快照。每次访谈、修改架构或任务操作后会自动记录一个时刻。
            </p>
          ) : (
            snapshots.map((snapshot) => (
              <article
                key={snapshot.id}
                className="rounded-lg border border-ink-ghost bg-paper/70 p-4"
              >
                <div className="flex items-center gap-3">
                  <span className="font-mono text-[11px] text-chalk">
                    {new Date(snapshot.created_at).toLocaleString()}
                  </span>
                  <span className="truncate font-body text-[12px] text-chalk-dim">
                    {snapshot.reason}
                  </span>
                  <span className="ml-auto shrink-0 font-mono text-[10px] text-chalk-faint">
                    {snapshot.file_count} 个文件
                      {snapshot.truncated ? ` · 跳过 ${snapshot.skipped_count ?? 0}` : ""}
                  </span>
                  <button
                    type="button"
                    onClick={() => onRestore(snapshot.id)}
                    disabled={busy}
                    className="inline-flex h-7 items-center rounded border border-ink-dim/45 px-2.5 font-mono text-[10.5px] text-chalk transition-colors hover:border-ink disabled:opacity-40"
                  >
                    恢复到此
                  </button>
                </div>
                <p className="mt-1.5 font-mono text-[9.5px] text-chalk-faint">
                  恢复后页面会重新加载为这个时刻的项目状态；不会自动删除该时刻之后新增的文件。
                </p>
                {snapshot.truncated ? (
                  <p className="mt-1 font-mono text-[9.5px] text-amber">
                    这个快照不完整：部分大文件、二进制文件或超出数量上限的文件未纳入。
                  </p>
                ) : null}
              </article>
            ))
          )}
        </div>
      </section>
    </div>
  );
}
