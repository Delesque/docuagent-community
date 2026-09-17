import { useEffect } from "react";
import type { Dispatch, SetStateAction } from "react";
import { reduceView, type ViewState } from "../conversation/viewMode";

export interface ConversationNavigationOptions {
  total: number;
  nodeSelectedOnCanvas: boolean;
  setView: Dispatch<SetStateAction<ViewState>>;
  setProjection: Dispatch<SetStateAction<"canvas" | "outline">>;
}

/** Window-level transcript paging and projection shortcuts.

 *
 *  Wheel and arrow keys page through turns; Ctrl+O toggles the timeline, Alt+O swaps
 *  canvas/outline. Arrow paging yields while a node is selected because the canvas uses
 *  the same keys to nudge the selection. Living in a hook keeps the App free of
 *  document-level listener plumbing.
 */
export function useConversationNavigation({ total, nodeSelectedOnCanvas, setView, setProjection }: ConversationNavigationOptions) {
  useEffect(() => {
    const onWheel = (event: WheelEvent): void => {
      // A ctrlKey wheel is the graph camera's gesture. Ignoring it here is what lets
      // Ctrl+wheel keep one meaning across the whole surface; the conversation's own
      // reading size lives in settings instead.
      if (event.ctrlKey) return;
      setView((prev) =>
        reduceView(prev, {
          type: "wheel",
          deltaY: event.deltaY,
          deltaMode: event.deltaMode,
          now: Date.now(),
          total,
        }),
      );
    };
    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        setView((prev) => reduceView(prev, { type: "escape" }));
        return;
      }
      // Ctrl+O replaces zoom-out-past-a-threshold as the way into the timeline.
      if (event.key === "o" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        setView((prev) => reduceView(prev, { type: "toggleOverview", total }));
        return;
      }
      // Alt+O swaps the graph's two projections. Same binding the Stage 1 shell used,
      // so anyone who learned it there keeps it.
      if (event.key === "o" && event.altKey) {
        event.preventDefault();
        setProjection((current) => (current === "canvas" ? "outline" : "canvas"));
        return;
      }
      // ArrowUp / ArrowDown page through turns, same as wheeling in review/live — but
      // only when the arrows are not being used to nudge a selected node on the canvas.
      // Both handlers are on `window`, so without this one keypress both pages the
      // transcript and moves a node.
      if (nodeSelectedOnCanvas) return;
      if (event.key === "ArrowUp") {
        setView((prev) => reduceView(prev, { type: "page", delta: -1, total }));
      }
      if (event.key === "ArrowDown") {
        setView((prev) => reduceView(prev, { type: "page", delta: 1, total }));
      }
    };
    window.addEventListener("wheel", onWheel, { passive: true });
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("wheel", onWheel);
      window.removeEventListener("keydown", onKeyDown);
    };
  }, [nodeSelectedOnCanvas, setProjection, setView, total]);
}
