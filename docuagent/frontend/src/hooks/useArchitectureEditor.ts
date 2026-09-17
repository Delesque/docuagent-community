import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import { undoArchitecture, type ArchitectureEditResult, type WorkspaceInfo } from "../api";
import type { DialogTab } from "../components/v2/DialogBox";
import type { Message } from "../components/v2/TypewriterOutput";
import { delay } from "../conversation/delay";
import { pendingThinkingChip, randomThinkingLine } from "../conversation/thinking";
import type { TranscriptAction } from "../conversation/transcript";
import { reduceView, type ViewState } from "../conversation/viewMode";
import { CONVERSATION_TAB } from "../conversation/workbenchConfig";
import type { Architecture } from "../graph/types";

const INITIALIZED_TABS: DialogTab[] = [CONVERSATION_TAB];

export interface ArchitectureEditorOptions {
  workspace: WorkspaceInfo | null;
  say: (message: Message) => void;
  runArchitectureEdit: (request: string) => Promise<ArchitectureEditResult | null>;
  setBusy: Dispatch<SetStateAction<boolean>>;
  setDialogOpen: Dispatch<SetStateAction<boolean>>;
  setDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  setView: Dispatch<SetStateAction<ViewState>>;
  setTabs: Dispatch<SetStateAction<DialogTab[]>>;
  setActiveTab: Dispatch<SetStateAction<string | null>>;
  setEditedArchitecture: Dispatch<SetStateAction<Architecture | null>>;
  setUndoAvailable: Dispatch<SetStateAction<boolean>>;
  dispatch: Dispatch<TranscriptAction>;
}

/** UI orchestration for editing and undoing an existing architecture.
 *
 *  Editing is distinct from the interview: no question is asked, no status advances,
 *  and the graph updates in place. Undo restores the previous accepted architecture
 *  version and mirrors the same tab/transcript wrap-up.
 */
export function useArchitectureEditor({
  workspace,
  say,
  runArchitectureEdit,
  setBusy,
  setDialogOpen,
  setDrafts,
  setView,
  setTabs,
  setActiveTab,
  setEditedArchitecture,
  setUndoAvailable,
  dispatch,
}: ArchitectureEditorOptions) {
  const requestArchitectureEdit = useCallback(
    async (request: string) => {
      if (!workspace) {
        say({ role: "agent", text: "还没有选择项目地址。" });
        return;
      }
      setBusy(true);
      say({ role: "user", text: `修改架构：${request}` });
      await delay(120);
      const thinkingLine = randomThinkingLine();
      say({
        role: "agent",
        text: `DocuAgent: ${thinkingLine}`,
        pending: true,
        chips: [pendingThinkingChip(thinkingLine)],
      });

      try {
        const result = await runArchitectureEdit(request);
        if (!result) return;
        setDialogOpen(false);
        setDrafts({});
        setView((prev) => reduceView(prev, { type: "turn" }));

        // The computed delta is the display source of truth; the model's own
        // `changes` prose is only the fallback when a delta is not available.
        const lines =
          result.delta_lines && result.delta_lines.length > 0
            ? result.delta_lines.map((line) => `· ${line}`)
            : result.changes.length > 0
              ? result.changes.map((change) => `· ${change}`)
              : [];
        const summary =
          lines.length > 0
            ? lines.join("\n")
            : "架构已更新。";
        dispatch({
          type: "setThinking",
          thinking: result.thinking,
          fallback: summary,
        });
        const countLine = result.delta_summary ? `\n${result.delta_summary}` : "";
        say({
          role: "agent",
          text: `架构已修改（版本 ${result.architecture_version}），现在共 ${result.architecture.modules.length} 个模块。${countLine}\n${summary}`,
          chips: [
            { text: "查看架构图", type: "view", detail: "打开图形视图查看修改后的模块关系" },
            { text: "开始实现", type: "action" },
            { text: "继续修改架构", type: "input" },
          ],
        });
        // The tab stays available: revising a design is usually iterative, and clearing it
        // would make the second change harder to reach than the first.
        setTabs(INITIALIZED_TABS);
        setActiveTab(CONVERSATION_TAB.id);
      } catch (cause) {
        say({
          role: "agent",
          text: `修改架构失败：${(cause as Error).message}`,
          chips: [{ text: "继续修改架构", type: "input" }],
        });
        setTabs(INITIALIZED_TABS);
        setActiveTab(CONVERSATION_TAB.id);
      } finally {
        setBusy(false);
      }
    },
    [dispatch, runArchitectureEdit, say, setActiveTab, setBusy, setDialogOpen, setDrafts, setTabs, setView, workspace],
  );

  const undoArchitectureEdit = useCallback(async () => {
    if (!workspace) {
      say({ role: "agent", text: "还没有选择项目地址。" });
      return;
    }
    setBusy(true);
    try {
      const result = await undoArchitecture(workspace.path);
      setEditedArchitecture(result.architecture);
      setUndoAvailable(result.history_remaining > 0);
      setView((prev) => reduceView(prev, { type: "turn" }));
      say({
        role: "agent",
        text: `已撤销到架构版本 ${result.architecture_version}，现在共 ${result.architecture.modules.length} 个模块。`,
        chips: [
          { text: "查看架构图", type: "view", detail: "打开图形视图查看撤销后的模块关系" },
          { text: "开始实现", type: "action" },
          { text: "继续修改架构", type: "input" },
        ],
      });
      setTabs(INITIALIZED_TABS);
      setActiveTab(CONVERSATION_TAB.id);
    } catch (cause) {
      say({
        role: "agent",
        text: `撤销失败：${(cause as Error).message}`,
        chips: [{ text: "继续修改架构", type: "input" }],
      });
      setTabs(INITIALIZED_TABS);
      setActiveTab(CONVERSATION_TAB.id);
    } finally {
      setBusy(false);
    }
  }, [say, setActiveTab, setBusy, setEditedArchitecture, setTabs, setUndoAvailable, setView, workspace]);

  return { requestArchitectureEdit, undoArchitectureEdit };
}
