/** Quick-switch slots for nodes the user is actively juggling.
 *
 *  Reachable three ways — long press on the canvas (Stage 3), Ctrl+Shift+D, and `D` in
 *  the Outline — because a gesture-only entry point is invisible to keyboard users and
 *  undiscoverable to everyone else.
 */

import type { GraphCommand } from "../graph/store";
import { WINDOW_BAR_SLOTS } from "../graph/constants";
import type { Architecture } from "../graph/types";

interface WindowBarProps {
  slots: string[];
  architecture: Architecture;
  selectedId: string | null;
  dispatch: (command: GraphCommand) => void;
}

export function WindowBar({ slots, architecture, selectedId, dispatch }: WindowBarProps) {
  if (slots.length === 0) return null;
  const nameOf = (id: string): string =>
    architecture.modules.find((module) => module.id === id)?.name ?? id;

  return (
    <aside
      aria-label="窗口管理器"
      className="absolute right-0 top-0 flex h-full w-44 flex-col gap-1 border-l border-ink-ghost bg-paper-raise/94 p-2"
    >
      <div className="flex items-baseline justify-between px-1 pb-1">
        <span className="font-mono text-[9.5px] uppercase tracking-[0.16em] text-chalk-dim">
          窗口栏
        </span>
        <span className="font-mono text-[9px] text-chalk-faint">
          {slots.length}/{WINDOW_BAR_SLOTS}
        </span>
      </div>
      <ul className="flex flex-col gap-1">
        {slots.map((id, index) => (
          <li key={id}>
            <button
              type="button"
              onClick={() => {
                dispatch({ type: "select", nodeId: id });
                dispatch({ type: "centerNode", nodeId: id });
              }}
              className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left transition-colors duration-150 ease-ui focus-visible:ring-1 focus-visible:ring-ink ${
                id === selectedId ? "bg-ink-ghost text-chalk" : "text-chalk-dim hover:bg-paper-float"
              }`}
            >
              <kbd className="shrink-0 font-mono text-[9px] text-chalk-faint">
                ^{index + 1}
              </kbd>
              <span className="truncate font-body text-[11px]">{nameOf(id)}</span>
              <span
                role="button"
                tabIndex={0}
                aria-label={`从窗口栏移除 ${nameOf(id)}`}
                onClick={(event) => {
                  event.stopPropagation();
                  dispatch({ type: "toggleWindowBar", nodeId: id });
                }}
                onKeyDown={(event) => {
                  if (event.key !== "Enter" && event.key !== " ") return;
                  event.stopPropagation();
                  event.preventDefault();
                  dispatch({ type: "toggleWindowBar", nodeId: id });
                }}
                className="ml-auto shrink-0 font-mono text-[10px] text-chalk-faint hover:text-vermilion"
              >
                ×
              </span>
            </button>
          </li>
        ))}
      </ul>
    </aside>
  );
}
