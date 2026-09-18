import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { BootstrapState } from "../api";
import type { DialogTab } from "../components/shell/DialogBox";
import type { Message } from "../components/shell/TypewriterOutput";
import { projectBootstrapTurn } from "../conversation/bootstrapProjection";
import type { TranscriptAction } from "../conversation/transcript";
import { reduceView, type ViewState } from "../conversation/viewMode";

export interface BootstrapTurnOptions {
  setBootstrap: Dispatch<SetStateAction<BootstrapState | null>>;
  setDialogOpen: Dispatch<SetStateAction<boolean>>;
  setDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  setView: Dispatch<SetStateAction<ViewState>>;
  setTabs: Dispatch<SetStateAction<DialogTab[]>>;
  setActiveTab: Dispatch<SetStateAction<string | null>>;
  dispatch: Dispatch<TranscriptAction>;
  say: (message: Message) => void;
  refreshAttachments: () => Promise<unknown> | void;
}

/** Apply one bootstrap turn to the shell.

 *
 *  Shared by typed submissions and the "确认架构" action chip, so the two paths cannot
 *  drift. Rendering is delegated to `projectBootstrapTurn` so the projection tests are
 *  the single source of truth for messages, tabs and attachment refresh intent.
 */
export function useBootstrapTurn({
  setBootstrap,
  setDialogOpen,
  setDrafts,
  setView,
  setTabs,
  setActiveTab,
  dispatch,
  say,
  refreshAttachments,
}: BootstrapTurnOptions) {
  return useCallback(
    (next: BootstrapState) => {
      setBootstrap(next);
      setDialogOpen(false);
      setDrafts({});
      setView((prev) => reduceView(prev, { type: "turn" }));

      const projection = projectBootstrapTurn(next);
      dispatch({
        type: "setThinking",
        thinking: projection.thinking,
        fallback: projection.thinkingFallback,
      });
      say(projection.message);
      setTabs(projection.tabs);
      setActiveTab(projection.activeTab);
      if (projection.refreshAttachments) void refreshAttachments();
    },
    [dispatch, refreshAttachments, say, setActiveTab, setBootstrap, setDialogOpen, setDrafts, setTabs, setView],
  );
}
