import { useState } from "react";
import { useDocIgnore } from "../hooks/useDocIgnore";

/** Project-level documentation-ignore configuration. Lives in a small panel
 *  because the list is short and rarely touched: the built-in dependency/cache
 *  directories cover most projects, and the project adds its own when the
 *  navigation-document tree reaches somewhere it should not. */
export function DocIgnorePanel({ path, onClose }: { path: string; onClose: () => void }) {
  const { state, projectDirs, busy, error, add, remove } = useDocIgnore(path);
  const [draft, setDraft] = useState("");

  const submit = () => {
    if (add(draft)) setDraft("");
  };

  return (
    <div
      role="dialog"
      aria-label="忽略目录"
      className="fixed right-4 top-16 z-50 w-80 rounded-lg border border-ink-ghost bg-paper-raise/95 p-4 font-mono text-[12px] text-chalk shadow-2xl backdrop-blur"
    >
      <div className="flex items-center justify-between">
        <h2 className="text-[13px] text-chalk">忽略目录</h2>
        <button
          type="button"
          aria-label="关闭"
          onClick={onClose}
          className="rounded px-2 py-1 text-chalk-dim hover:text-chalk"
        >
          ✕
        </button>
      </div>
      <p className="mt-1 text-[11px] leading-relaxed text-chalk-dim">
        这些目录不生成导航文档。目录名是单级名字，不含路径分隔符。
      </p>

      {state ? (
        <>
          {state.defaults.length > 0 ? (
            <section className="mt-3">
              <h3 className="text-[11px] text-chalk-dim">系统内置</h3>
              <div className="mt-1 flex flex-wrap gap-1">
                {state.defaults.map((name) => (
                  <span
                    key={name}
                    className="rounded border border-ink-ghost px-2 py-0.5 text-chalk-dim"
                  >
                    {name}
                  </span>
                ))}
              </div>
            </section>
          ) : null}

          <section className="mt-3">
            <h3 className="text-[11px] text-chalk-dim">本项目添加</h3>
            {projectDirs.length === 0 ? (
              <p className="mt-1 text-[11px] text-chalk-dim">还没有添加过。</p>
            ) : (
              <div className="mt-1 flex flex-wrap gap-1">
                {projectDirs.map((name) => (
                  <span
                    key={name}
                    className="flex items-center gap-1 rounded border border-ink-ghost bg-paper-float/60 px-2 py-0.5"
                  >
                    {name}
                    <button
                      type="button"
                      aria-label={`移除 ${name}`}
                      disabled={busy}
                      onClick={() => remove(name)}
                      className="text-chalk-dim hover:text-chalk disabled:opacity-40"
                    >
                      ✕
                    </button>
                  </span>
                ))}
              </div>
            )}
          </section>

          <div className="mt-3 flex gap-2">
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter") submit();
              }}
              placeholder="目录名，例如 vendor"
              disabled={busy}
              aria-label="新目录名"
              className="h-8 min-w-0 flex-1 rounded border border-ink-ghost bg-paper/60 px-2 text-chalk placeholder:text-chalk-dim/60 focus:border-ink-ghost focus:outline-none"
            />
            <button
              type="button"
              onClick={submit}
              disabled={busy || !draft.trim()}
              className="h-8 rounded border border-ink-ghost px-3 text-chalk hover:bg-paper-float/60 disabled:opacity-40"
            >
              添加
            </button>
          </div>
        </>
      ) : (
        <p className="mt-3 text-[11px] text-chalk-dim">
          {error ? `读取失败：${error}` : "读取中…"}
        </p>
      )}

      {error && state ? (
        <p role="alert" className="mt-2 text-[11px] text-amber">
          {error}
        </p>
      ) : null}
    </div>
  );
}
