import { useCallback } from "react";
import type { Dispatch } from "react";
import type { BootstrapState, TaskPlan, WorkspaceInfo } from "../api";
import type { DialogTab } from "../components/v2/DialogBox";
import type { Message } from "../components/v2/TypewriterOutput";
import { composeTaskStatusAnswer } from "../conversation/taskProgress";
import type { TranscriptAction } from "../conversation/transcript";
import { ARCHITECTURE_EDIT_TAB, CONVERSATION_TAB, MICRO_TASK_TAB } from "../conversation/workbenchConfig";

export interface ComposerOptions {
  workspace: WorkspaceInfo | null;
  bootstrap: BootstrapState | null;
  tasks: TaskPlan | null;
  tabs: DialogTab[];
  activeTab: string | null;
  taskStreaming: boolean;
  taskProgressRef: { current: Map<string, { status: "running" | "done" | "failed" | "stopped"; latest: string }> };
  progressTextRef: { current: string };
  say: (message: Message) => void;
  dispatch: Dispatch<TranscriptAction>;
  setDialogOpen: (open: boolean) => void;
  setDrafts: (drafts: Record<string, string>) => void;
  submitBootstrap: (values: Record<string, string>) => Promise<boolean>;
  requestArchitectureEdit: (request: string) => Promise<void>;
  dispatchMicroTask: (request: string) => Promise<void>;
}

/** Route the composer's single submission to the active feature.

 *
 *  The dialog has one submit button but many tab kinds; this is the one place that
 *  decides whether a submission is a task-progress question, a micro task, a status
 *  conversation, an architecture edit, or a bootstrap turn.
 */
export function useComposer({
  workspace,
  bootstrap,
  tasks,
  tabs,
  activeTab,
  taskStreaming,
  taskProgressRef,
  progressTextRef,
  say,
  dispatch,
  setDialogOpen,
  setDrafts,
  submitBootstrap,
  requestArchitectureEdit,
  dispatchMicroTask,
}: ComposerOptions) {
  return useCallback(
    async (values: Record<string, string>) => {
      if (!workspace) {
        say({ role: "agent", text: "还没有选择项目地址。" });
        return;
      }
      if (taskStreaming) {
        const question =
          tabs.map((tab) => values[tab.id] ?? "").join(" ").trim() || "现在进度如何？";
        const entries = Array.from(taskProgressRef.current, ([id, progress]) => ({ id, progress }));
        const answer = composeTaskStatusAnswer(entries);
        dispatch({
          type: "updateMessage",
          message: {
            role: "agent",
            text:
              progressTextRef.current +
              "\n\n你：" +
              question +
              "\nDocuAgent：" +
              answer,
            pending: true,
            instant: true,
            chips: [{ text: "询问子 Agent 状态", type: "input" }],
          },
        });
        setDialogOpen(false);
        setDrafts({});
        return;
      }
      const microRequest = (values[MICRO_TASK_TAB.id] ?? "").trim();
      if (activeTab === MICRO_TASK_TAB.id && microRequest) {
        await dispatchMicroTask(microRequest);
        return;
      }
      const conversationRequest = (values[CONVERSATION_TAB.id] ?? "").trim();
      if (activeTab === CONVERSATION_TAB.id && conversationRequest) {
        const statusText = tasks
          ? composeTaskStatusAnswer(
              tasks.tasks.map((task) => ({
                id: task.id,
                progress: {
                  status:
                    task.status === "running"
                      ? ("running" as const)
                      : task.status === "failed"
                        ? ("failed" as const)
                        : task.last_error === "已停止"
                          ? ("stopped" as const)
                          : ("done" as const),
                  latest: task.last_error || task.summary,
                },
              })),
            )
          : bootstrap
            ? "当前项目状态：" +
              bootstrap.status +
              "。" +
              (bootstrap.current_question
                ? "正在等待回答：" + bootstrap.current_question.title
                : "")
            : "当前还没有可报告的任务状态。";
        say({ role: "user", text: conversationRequest });
        say({
          role: "agent",
          text: statusText,
          chips: [
            { text: "继续修改架构", type: "input" },
            { text: "微任务", type: "input" },
          ],
        });
        setDialogOpen(false);
        setDrafts({});
        return;
      }
      const editRequest = (values[ARCHITECTURE_EDIT_TAB.id] ?? "").trim();
      if (editRequest) {
        await requestArchitectureEdit(editRequest);
        return;
      }
      await submitBootstrap(values);
    },
    [
      activeTab,
      bootstrap,
      dispatch,
      dispatchMicroTask,
      progressTextRef,
      requestArchitectureEdit,
      say,
      setDialogOpen,
      setDrafts,
      submitBootstrap,
      tabs,
      taskProgressRef,
      taskStreaming,
      tasks,
      workspace,
    ],
  );
}
