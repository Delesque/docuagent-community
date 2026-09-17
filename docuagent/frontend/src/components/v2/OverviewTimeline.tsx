/** Overview: the whole interview as one scannable column.
 *
 *  Reached by an explicit toggle (Ctrl+O or the mode control) — not by zooming out,
 *  because Ctrl+wheel belongs to the graph camera. Every turn compresses to a single
 *  row — role band, index, one-line summary — so the shape of a long interview is
 *  visible without reading it. Picking a row drops back into review anchored on that
 *  turn, which is the only way to jump backward without scrolling through everything
 *  in between.
 *
 *  The rows stay real buttons: this is a navigation surface, so it has to be reachable
 *  by Tab and announce its position, not just respond to a pointer.
 */

import { useEffect, useRef } from "react";
import { overviewDensity, summarize, type ViewMode } from "../../conversation/viewMode";
import type { Message } from "./TypewriterOutput";

interface OverviewTimelineProps {
  messages: Message[];
  /** Highlighted row: where review would resume. */
  anchor: number | null;
  onPick: (index: number) => void;
  onExit: () => void;
}

export function OverviewTimeline({
  messages,
  anchor,
  onPick,
  onExit,
}: OverviewTimelineProps) {
  const density = overviewDensity();
  const rowHeight = 26 + density * 18;
  const gap = 2 + density * 6;
  const fontSize = 10.5 + density * 2.5;

  const activeIndex = anchor ?? messages.length - 1;
  const activeRef = useRef<HTMLButtonElement>(null);

  // Keep the anchored row in view when arriving from a deep scroll position.
  useEffect(() => {
    activeRef.current?.scrollIntoView({ block: "center" });
  }, []);

  return (
    <div
      className="absolute inset-0 overflow-y-auto px-[8vw] py-14"
      role="listbox"
      aria-label="对话总览"
      aria-activedescendant={`overview-row-${activeIndex}`}
      tabIndex={-1}
      onKeyDown={(event) => {
        if (event.key === "Escape") onExit();
      }}
    >
      <div className="mx-auto max-w-[1100px]">
        <p className="mb-6 font-body text-[10.5px] uppercase tracking-[0.14em] text-chalk-faint">
          总览 · {messages.length} 轮 · 点选一行回到该处，Ctrl+O 或 Esc 返回
        </p>

        <div className="flex flex-col" style={{ gap: `${gap}px` }}>
          {messages.map((entry, index) => {
            const agent = entry.role === "agent";
            const active = index === activeIndex;
            return (
              <button
                key={index}
                id={`overview-row-${index}`}
                ref={active ? activeRef : undefined}
                type="button"
                role="option"
                aria-selected={active}
                onClick={() => onPick(index)}
                className={`group flex items-center gap-3 rounded-[6px] px-3 text-left transition-colors duration-150 ${
                  active ? "bg-paper-high" : "hover:bg-paper-raise"
                }`}
                style={{ height: `${rowHeight}px` }}
              >
                {/* Role is carried by band position and the label below, not by hue
                    alone — the spec forbids color as the only channel. */}
                <span
                  className={`h-full w-[2.5px] shrink-0 rounded-full ${
                    agent ? "bg-ink" : "bg-vermilion"
                  }`}
                />
                <span className="w-9 shrink-0 font-mono text-[9.5px] text-chalk-faint">
                  {agent ? "AI" : "我"}
                  <span className="ml-1 opacity-60">{index + 1}</span>
                </span>
                <span
                  className={`truncate font-body ${active ? "text-chalk" : "text-chalk-dim"} group-hover:text-chalk`}
                  style={{ fontSize: `${fontSize}px` }}
                >
                  {summarize(entry.text)}
                </span>
                {entry.thinking ? (
                  <span className="ml-auto shrink-0 font-mono text-[9px] text-chalk-faint">
                    思考
                  </span>
                ) : null}
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/** Mode indicator, top-left. Present in review and overview only: in `live` the
 *  screen already is the message, so a label naming the mode is noise.
 *
 *  No longer reports a zoom percentage — reading size is a setting, and showing it
 *  here implied the badge was tracking a gesture. */
export function ModeBadge({ mode }: { mode: ViewMode }) {
  if (mode === "live") return null;
  return (
    <div className="pointer-events-none fixed left-7 top-7 z-40 font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
      {mode === "overview" ? "总览" : "回看"}
      <span className="ml-2 opacity-60">Esc 返回</span>
    </div>
  );
}
