/** Live interview progress, kept out of the message stream.
 *
 *  This used to ride inside each question's text, which re-printed a long and barely
 *  changing aspect list under every turn of the conversation. It is status, not dialogue:
 *  it sits above the conversation, collapsed to what actually moves between turns, and
 *  expands on click for the per-aspect detail.
 *
 *  Counts only — never module names, never topology — so the draft stays private until the
 *  user confirms it.
 */

import { useState } from "react";
import type { BootstrapState } from "../api";
import { draftStatus, formatDraftDetail, formatDraftSummary } from "../conversation/draftStatus";

interface DraftStatusBarProps {
  state: BootstrapState | null;
}

export function DraftStatusBar({ state }: DraftStatusBarProps) {
  const [expanded, setExpanded] = useState(false);
  const status = state ? draftStatus(state) : null;
  if (!status) return null;
  const summary = formatDraftSummary(status);
  const detail = formatDraftDetail(status);
  return (
    <div className="fixed left-4 top-4 z-40 max-w-[min(30rem,calc(100vw-2rem))]">
      <div className="overflow-hidden rounded-md border border-ink-ghost bg-paper-raise/90">
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          aria-expanded={expanded}
          title={expanded ? "收起访谈进度" : "展开访谈进度"}
          className="flex w-full items-center gap-2 px-3 py-2 font-mono text-[11px] text-chalk-dim transition-colors hover:text-chalk"
        >
          <span className="shrink-0 opacity-70">当前草案</span>
          <span className="truncate">{summary}</span>
          <svg
            className={`ml-auto h-3 w-3 shrink-0 transition-transform duration-200 ${expanded ? "rotate-180" : ""}`}
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            aria-hidden="true"
          >
            <path d="M6 9l6 6 6-6" />
          </svg>
        </button>
        {expanded && detail.length > 0 ? (
          <div className="space-y-1 border-t border-ink-ghost px-3 py-2 font-mono text-[11px] text-chalk-dim">
            {detail.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
        ) : null}
      </div>
    </div>
  );
}
