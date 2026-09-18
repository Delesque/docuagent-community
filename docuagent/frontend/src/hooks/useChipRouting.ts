import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { DialogTab } from "../components/shell/DialogBox";
import type { Chip } from "../components/shell/TypewriterOutput";
import { ARCHITECTURE_EDIT_TAB, MICRO_TASK_TAB, SUBAGENT_STATUS_TAB } from "../conversation/workbenchConfig";
import type { GraphCommand } from "../graph/store";

const ARCHITECTURE_TABS: DialogTab[] = [ARCHITECTURE_EDIT_TAB];

export interface ChipRoutingOptions {
  tabs: DialogTab[];
  openPathPicker: () => void;
  setSuggestionsOpen: (open: boolean) => void;
  setProvenanceLedgerOpen: (open: boolean) => void;
  setSettingsOpen: Dispatch<SetStateAction<boolean>>;
  setTabs: Dispatch<SetStateAction<DialogTab[]>>;
  setDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  setActiveTab: Dispatch<SetStateAction<string | null>>;
  setDialogOpen: Dispatch<SetStateAction<boolean>>;
  handleStartWorkRef: { current: () => void };
  handleConfirmArchitectureRef: { current: () => void };
  handleConfirmAllRef: { current: () => void };
  handleOnboardRef: { current: () => void };
  storeDispatch: (command: GraphCommand) => void;
}

/** Route output chips to shell actions.
 *
 *  Chips are the only non-text controls emitted by the conversation. Their meaning is
 *  intentionally small and fixed; keeping the routing in a hook makes it testable
 *  without rendering the whole App.
 */
export function useChipRouting({
  tabs,
  openPathPicker,
  setSuggestionsOpen,
  setProvenanceLedgerOpen,
  setSettingsOpen,
  setTabs,
  setDrafts,
  setActiveTab,
  setDialogOpen,
  handleStartWorkRef,
  handleConfirmArchitectureRef,
  handleConfirmAllRef,
  handleOnboardRef,
  storeDispatch,
}: ChipRoutingOptions) {
  return useCallback(
    (chip: Chip) => {
      if (chip.text === "接入分析") {
        handleOnboardRef.current();
        return;
      }
      if (chip.text === "确认推测") {
        setProvenanceLedgerOpen(true);
        return;
      }
      if (chip.text === "查看修改建议") {
        setSuggestionsOpen(true);
        return;
      }
      if (chip.text === "开始实现") {
        handleStartWorkRef.current();
        return;
      }
      if (chip.type === "action") {
        if (chip.text === "全部确认") {
          handleConfirmAllRef.current();
        } else if (chip.text === "确认架构") {
          handleConfirmArchitectureRef.current();
        }
        return;
      }
      if (chip.type === "view") {
        if (chip.text === "查看架构图") {
          // Leaving the conversation's focus reveals the graph behind it — the same
          // thing zooming out does, since the conversation is a node on that graph.
          storeDispatch({ type: "exitFocus" });
          return;
        }
        if (chip.text === "打开任务面板") {
          storeDispatch({ type: "exitFocus" });
          return;
        }
        if (chip.text === "询问子 Agent 状态") {
          setTabs([SUBAGENT_STATUS_TAB]);
          setActiveTab(SUBAGENT_STATUS_TAB.id);
          setDialogOpen(true);
          return;
        }
        // Other view chips are inline expansions handled in the output layer.
        return;
      }
      if (chip.text === "项目地址") {
        void openPathPicker();
        return;
      }
      if (chip.text === "配置我的api") {
        setSettingsOpen(true);
        return;
      }

      // Opens the edit input empty. Checked before the option-prefill branch below, which
      // would otherwise treat this label as an answer and type it into the box.
      if (chip.text === ARCHITECTURE_EDIT_TAB.label) {
        setTabs(ARCHITECTURE_TABS);
        setActiveTab(ARCHITECTURE_EDIT_TAB.id);
        setDialogOpen(true);
        return;
      }
      if (chip.text === MICRO_TASK_TAB.label) {
        setTabs([MICRO_TASK_TAB]);
        setActiveTab(MICRO_TASK_TAB.id);
        setDialogOpen(true);
        return;
      }

      // Check if this is an option chip (for questions with predefined options)
      // If there's exactly one tab and the chip text doesn't match the tab label,
      // assume it's an option and prefill it
      if (tabs.length === 1 && tabs[0] && chip.text !== tabs[0].label) {
        const tab = tabs[0];
        setDrafts((prev) => ({ ...prev, [tab.id]: chip.text }));
        setActiveTab(tab.id);
        setDialogOpen(true);
        return;
      }

      const target = tabs.find((tab) => tab.label === chip.text);
      if (target) {
        setActiveTab(target.id);
        setDialogOpen(true);
      }
    },
    [handleConfirmArchitectureRef, handleOnboardRef, handleStartWorkRef, openPathPicker, setActiveTab, setDialogOpen, setDrafts, setProvenanceLedgerOpen, setSettingsOpen, setSuggestionsOpen, setTabs, storeDispatch, tabs],
  );
}
