/** Bottom dialog. Occupies the lower third; the typewriter output above stays fully
 *  legible — no backdrop, no blur, no dim. The output and the input are one surface.
 *
 *  Multi-tab turns collect every field before sending once. The primary button reads
 *  「下一项」 while any tab is still empty and 「发送」 only when the whole turn is
 *  complete, so a partial turn can never reach the model.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface DialogTab {
  id: string;
  label: string;
  /** Sent to the model as the field's semantic name, so the payload is structured
   *  rather than a bare concatenation of whatever the user typed. */
  promptKey: string;
  placeholder?: string;
}

interface DialogBoxProps {
  isOpen: boolean;
  tabs: DialogTab[];
  activeTab: string | null;
  drafts: Record<string, string>;
  busy: boolean;
  onTabChange: (tabId: string) => void;
  onDraftChange: (tabId: string, value: string) => void;
  onClose: () => void;
  /** Receives every tab's content at once, keyed by tab id. */
  onSubmit: (values: Record<string, string>) => void;
}

type Stage = "hidden" | "ball" | "open";

export function DialogBox({
  isOpen,
  tabs,
  activeTab,
  drafts,
  busy,
  onTabChange,
  onDraftChange,
  onClose,
  onSubmit,
}: DialogBoxProps) {
  const [stage, setStage] = useState<Stage>("hidden");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Ball -> full box. The ball frame must paint before the transition starts,
  // otherwise the browser interpolates from nothing and the spring is invisible.
  useEffect(() => {
    if (isOpen) {
      setStage("ball");
      const raf = requestAnimationFrame(() => {
        requestAnimationFrame(() => setStage("open"));
      });
      return () => cancelAnimationFrame(raf);
    }
    setStage((current) => (current === "hidden" ? "hidden" : "ball"));
    const timer = window.setTimeout(() => setStage("hidden"), 320);
    return () => window.clearTimeout(timer);
  }, [isOpen]);

  useEffect(() => {
    if (stage === "open") textareaRef.current?.focus();
  }, [stage, activeTab]);

  const missing = useMemo(
    () => tabs.filter((tab) => !(drafts[tab.id] ?? "").trim()),
    [tabs, drafts],
  );
  const complete = tabs.length > 0 && missing.length === 0;

  const advance = useCallback(() => {
    // Prefer the next unfilled tab after the current one, wrapping around.
    const order = tabs.map((tab) => tab.id);
    const start = activeTab ? order.indexOf(activeTab) : -1;
    for (let step = 1; step <= order.length; step += 1) {
      const candidate = order[(start + step + order.length) % order.length];
      if (candidate && !(drafts[candidate] ?? "").trim()) {
        onTabChange(candidate);
        return;
      }
    }
  }, [tabs, activeTab, drafts, onTabChange]);

  const handlePrimary = useCallback(() => {
    if (busy) return;
    if (!complete) {
      advance();
      return;
    }
    const values: Record<string, string> = {};
    for (const tab of tabs) values[tab.id] = (drafts[tab.id] ?? "").trim();
    onSubmit(values);
  }, [busy, complete, advance, tabs, drafts, onSubmit]);

  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent) => {
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
        event.preventDefault();
        handlePrimary();
      }
      if (event.key === "Escape") onClose();
    },
    [handlePrimary, onClose],
  );

  if (stage === "hidden") return null;

  const current = tabs.find((tab) => tab.id === activeTab) ?? tabs[0];
  const value = current ? (drafts[current.id] ?? "") : "";

  return (
    <div
      className="pointer-events-none fixed inset-x-0 bottom-0 z-50 flex justify-center"
      style={{ height: "34vh" }}
    >
      <div
        className="pointer-events-auto w-full max-w-4xl origin-bottom"
        style={{
          height: "100%",
          transform: stage === "open" ? "scale(1)" : "scale(0.08)",
          opacity: stage === "open" ? 1 : 0,
          borderRadius: stage === "open" ? "28px 28px 0 0" : "999px",
          // Spring with a slight overshoot on the way in.
          transition:
            "transform 420ms cubic-bezier(0.22, 1.4, 0.36, 1), opacity 200ms ease, border-radius 300ms ease",
        }}
      >
        <div className="flex h-full flex-col overflow-hidden rounded-t-[28px] border border-b-0 border-ink-ghost bg-paper-raise">
          {tabs.length > 1 || Boolean(tabs[0]?.label) ? (
          <div className="flex items-center gap-1 border-b border-ink-ghost/70 px-3">
            {tabs.map((tab) => {
              const filled = Boolean((drafts[tab.id] ?? "").trim());
              const active = current?.id === tab.id;
              return (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => onTabChange(tab.id)}
                  className={`relative px-4 py-3 font-body text-[12.5px] transition-colors duration-150 ${
                    active ? "text-chalk" : "text-chalk-dim hover:text-chalk"
                  }`}
                >
                  <span
                    className={`mr-2 inline-block h-1.5 w-1.5 rounded-full transition-colors ${
                      filled ? "bg-ink" : "bg-chalk-faint/50"
                    }`}
                  />
                  {tab.label}
                  {active ? (
                    <span className="absolute inset-x-3 bottom-0 h-[1.5px] bg-vermilion" />
                  ) : null}
                </button>
              );
            })}
            <button
              type="button"
              onClick={onClose}
              aria-label="收起输入"
              className="ml-auto p-2 text-chalk-faint transition-colors hover:text-chalk"
            >
              <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
                <path d="M6 6l12 12M18 6 6 18" />
              </svg>
            </button>
          </div>
          ) : null}

          <textarea
            ref={textareaRef}
            value={value}
            disabled={busy}
            onChange={(event) => current && onDraftChange(current.id, event.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={current?.placeholder ?? `说说${current?.label ?? ""}…`}
            className="min-h-0 flex-1 resize-none bg-transparent px-6 py-4 font-body text-[14.5px] leading-relaxed text-chalk placeholder-chalk-faint focus:outline-none disabled:opacity-50"
          />

          <div className="flex items-center justify-between px-6 pb-5 pt-1">
            <span className="font-body text-[10.5px] text-chalk-faint">
              {busy
                ? "正在发送…"
                : complete
                  ? "Ctrl + Enter 发送"
                  : current?.label ? `还需填写：${missing.map((tab) => tab.label).join("、")}` : "可以直接发送"}
            </span>
            <button
              type="button"
              onClick={handlePrimary}
              disabled={busy}
              className={`rounded-full px-5 py-2 font-body text-[12.5px] transition-colors duration-150 disabled:opacity-40 ${
                complete
                  ? "border border-vermilion/60 bg-vermilion/15 text-chalk hover:bg-vermilion/25"
                  : "border border-ink-ghost text-chalk-dim hover:border-ink-dim hover:text-chalk"
              }`}
            >
              {complete ? "发送" : "下一项"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
