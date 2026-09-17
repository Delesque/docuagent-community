import type { ApplyMode } from "../api";

const OPTIONS: Array<{ id: ApplyMode; label: string; hint: string }> = [
  {
    id: "review",
    label: "人工审阅",
    hint: "子 Agent 的改动生成补丁，等你逐个审阅应用",
  },
  {
    id: "auto",
    label: "自动应用",
    hint: "验证通过后改动直接落到项目，不经过人工审阅",
  },
];

/** Segmented control for the project's sandbox-apply switch. Sits next to the
 *  开始工作 button: the decision matters exactly when work is about to start. */
export function ApplyModeSwitch({
  mode,
  busy,
  onChange,
}: {
  mode: ApplyMode;
  busy?: boolean;
  onChange: (next: ApplyMode) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="改动落盘方式"
      className="flex overflow-hidden rounded-full border border-ink-ghost bg-paper-raise/90 font-mono text-[10px] shadow-lg"
    >
      {OPTIONS.map((option) => (
        <button
          key={option.id}
          type="button"
          role="tab"
          aria-selected={mode === option.id}
          disabled={busy}
          title={option.hint}
          onClick={() => onChange(option.id)}
          className={`px-3 py-2 transition-colors duration-150 ${
            mode === option.id
              ? option.id === "auto"
                ? "bg-amber/15 text-amber"
                : "bg-ink-ghost text-chalk"
              : "text-chalk-dim hover:text-chalk"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
