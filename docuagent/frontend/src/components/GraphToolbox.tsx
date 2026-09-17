export type GraphTool =
  | "note"
  | "focus"
  | "verify"
  | "archive";

const TOOLS: Array<{ id: GraphTool; label: string; hint: string; input?: boolean }> = [
  { id: "focus", label: "聚焦", hint: "进入该节点聚焦" },
  { id: "verify", label: "验证", hint: "运行该模块验证" },
  { id: "archive", label: "归档", hint: "归档该节点未完成附件" },
  { id: "note", label: "备注", hint: "给节点留备注", input: true },
];

export function graphToolNeedsInput(tool: GraphTool): boolean {
  return Boolean(TOOLS.find((item) => item.id === tool)?.input);
}

export function GraphToolbox({
  activeTool,
  onSelect,
}: {
  activeTool: GraphTool | null;
  onSelect: (tool: GraphTool | null) => void;
}) {
  return (
    <aside
      aria-label="架构图工具"
      className="absolute right-3 top-1/2 z-20 flex -translate-y-1/2 flex-col gap-1 rounded border border-ink-ghost bg-paper-raise/95 p-1"
    >
      {TOOLS.map((tool) => (
        <button
          key={tool.id}
          type="button"
          title={tool.hint}
          onClick={() => onSelect(activeTool === tool.id ? null : tool.id)}
          className={`inline-flex h-8 w-16 items-center justify-center rounded border px-2 font-mono text-[10px] transition-colors ${
            activeTool === tool.id
              ? "border-ink bg-ink/20 text-ink"
              : "border-ink-dim/45 text-chalk-dim hover:border-ink hover:text-chalk"
          }`}
        >
          {tool.label}
        </button>
      ))}
    </aside>
  );
}

export function GraphToolInput({
  tool,
  draft,
  onChange,
  onSubmit,
  onCancel,
}: {
  tool: GraphTool;
  draft: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
}) {
  const placeholder =
    "给这个节点留一条备注…";

  return (
    <div className="absolute left-1/2 top-6 z-30 w-[min(560px,90vw)] -translate-x-1/2 rounded border border-ink/60 bg-paper-raise/98 p-3 shadow-2xl">
      <div className="mb-2 flex items-center gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-ink">
          {TOOLS.find((item) => item.id === tool)?.label ?? tool}
        </span>
        <button
          type="button"
          onClick={onCancel}
          className="ml-auto font-mono text-[10px] text-chalk-faint hover:text-chalk"
        >
          取消
        </button>
      </div>
      <textarea
        value={draft}
        onChange={(event) => onChange(event.target.value)}
        rows={3}
        autoFocus
        placeholder={placeholder}
        className="w-full rounded border border-ink-ghost bg-paper px-3 py-2 font-mono text-[12px] text-chalk outline-none focus:border-ink"
      />
      <button
        type="button"
        onClick={onSubmit}
        disabled={!draft.trim()}
        className="mt-2 inline-flex h-8 items-center rounded border border-ink/70 bg-ink-ghost px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-paper-float disabled:opacity-40"
      >
        写入
      </button>
    </div>
  );
}
