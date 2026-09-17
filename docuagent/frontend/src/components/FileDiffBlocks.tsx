import { useEffect, useState } from "react";
import type { DiffHunk, TaskPatchEntry } from "../api";

interface FileHunkBlockProps {
  file: TaskPatchEntry;
  applied: boolean;
  onApplyHunks?: (file: string, hunkIds: number[]) => boolean | Promise<boolean>;
}

function FileHunkBlock({ file, applied, onApplyHunks }: FileHunkBlockProps) {
  const hunks: DiffHunk[] = file.hunks ?? [];
  const [showDiff, setShowDiff] = useState(false);
  const [accepted, setAccepted] = useState<Set<number>>(
    () => new Set(hunks.map((h) => h.id)),
  );
  const [applying, setApplying] = useState(false);

  useEffect(() => {
    setAccepted(new Set(hunks.map((h) => h.id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [file.path, hunks.length]);

  const toggleHunk = (id: number) => {
    setAccepted((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const applyAccepted = async () => {
    if (!onApplyHunks || applied) return;
    const ids = hunks.filter((h) => accepted.has(h.id)).map((h) => h.id);
    setApplying(true);
    try {
      await onApplyHunks(file.path, ids);
    } finally {
      setApplying(false);
    }
  };

  const added = hunks.reduce((n, h) => n + h.after_count, 0);
  const removed = hunks.reduce((n, h) => n + h.before_count, 0);

  return (
    <div
      className={`rounded-none border font-mono ${
        applied ? "border-emerald/40 bg-emerald/5" : "border-ink/40 bg-paper/80"
      }`}
    >
      <div className="flex items-center justify-between border-b border-ink/30 px-3 py-2">
        <div className="flex items-center gap-3">
          <span className="text-[11px] text-chalk">{file.path}</span>
          <span className="text-[10px] text-chalk-faint">
            <span className="text-emerald">+{added}</span>{" "}
            <span className="text-vermilion">-{removed}</span>
          </span>
          {applied ? (
            <span className="text-[10px] uppercase tracking-wider text-emerald">✓ 已应用</span>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => setShowDiff(!showDiff)}
          className="rounded-none border border-ink/50 px-2 py-1 text-[10px] text-chalk-faint transition-colors hover:bg-ink/10 hover:text-chalk"
        >
          {showDiff ? "▲ 收起" : "▼ 改动块"}
        </button>
      </div>
      {showDiff ? (
        <div className="space-y-2 p-2">
          {hunks.length === 0 ? (
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap text-[10px] leading-[1.5] text-chalk-dim">
              {file.diff}
            </pre>
          ) : (
            hunks.map((hunk) => {
              const isAccepted = accepted.has(hunk.id);
              return (
                <div
                  key={hunk.id}
                  className={`rounded-none border ${
                    isAccepted ? "border-ink/30 bg-paper/70" : "border-vermilion/40 bg-vermilion/5"
                  }`}
                >
                  <div className="flex items-center gap-2 border-b border-ink/30 px-2 py-1">
                    <button
                      type="button"
                      onClick={() => toggleHunk(hunk.id)}
                      disabled={applied}
                      className={`rounded-none border px-2 py-0.5 text-[10px] transition-colors disabled:opacity-40 ${
                        isAccepted
                          ? "border-emerald/50 text-emerald"
                          : "border-vermilion/50 text-vermilion"
                      }`}
                    >
                      {isAccepted ? "✓ 保留" : "✗ 丢弃"}
                    </button>
                    <span className="text-[10px] text-chalk-faint">
                      {hunk.tag} · 原 {hunk.before_start} → 新 {hunk.after_start}
                    </span>
                  </div>
                  {hunk.before ? (
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap px-2 py-1 text-[10px] leading-[1.5] text-vermilion">
                      {hunk.before}
                    </pre>
                  ) : null}
                  {hunk.after ? (
                    <pre className="max-h-40 overflow-auto whitespace-pre-wrap px-2 py-1 text-[10px] leading-[1.5] text-emerald">
                      {hunk.after}
                    </pre>
                  ) : null}
                </div>
              );
            })
          )}
          {hunks.length > 0 && !applied ? (
            <div className="flex justify-end pt-1">
              <button
                type="button"
                onClick={applyAccepted}
                disabled={applying}
                className="rounded-none border border-emerald/50 px-3 py-1 text-[10px] text-emerald transition-colors hover:bg-emerald/10 disabled:opacity-40"
              >
                应用选中块 ({accepted.size}/{hunks.length})
              </button>
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

interface FileDiffBlocksProps {
  files: TaskPatchEntry[];
  appliedFiles?: string[];
  rejectedFiles?: string[];
  onApplyHunks?: (file: string, hunkIds: number[]) => boolean | Promise<boolean>;
}

export function FileDiffBlocks({
  files,
  appliedFiles = [],
  onApplyHunks,
}: FileDiffBlocksProps) {
  const applied = (path: string) => appliedFiles.includes(path);
  return (
    <div className="my-4 space-y-3 rounded-none border border-ink/40 bg-paper/50 p-3">
      <div className="flex items-center justify-between border-b border-ink/30 pb-2">
        <span className="text-[11px] text-chalk-dim">📄 生成的文件</span>
        <span className="text-[10px] text-chalk-faint">
          {appliedFiles.length} 已应用 / {files.length} 个文件
        </span>
      </div>
      <div className="space-y-2">
        {files.map((file) => (
          <FileHunkBlock
            key={file.path}
            file={file}
            applied={applied(file.path)}
            onApplyHunks={onApplyHunks}
          />
        ))}
      </div>
    </div>
  );
}
