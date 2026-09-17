import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import { chooseFolder, type BootstrapState, type TaskPlan, type WorkspaceInfo } from "../api";
import type { DialogTab } from "../components/v2/DialogBox";
import type { Message } from "../components/v2/TypewriterOutput";
import { delay } from "../conversation/delay";
import { projectReloadUrl } from "../conversation/projectRecovery";
import { randomThinkingLine } from "../conversation/thinking";
import type { TranscriptAction } from "../conversation/transcript";
import { CONVERSATION_TAB, SAVED_PATH_KEY } from "../conversation/workbenchConfig";
import type { Architecture } from "../graph/types";

const INITIALIZED_TABS: DialogTab[] = [CONVERSATION_TAB];

export interface WorkspaceSessionOptions {
  workspace: WorkspaceInfo | null;
  say: (message: Message) => void;
  setBooted: Dispatch<SetStateAction<boolean>>;
  setWorkspace: Dispatch<SetStateAction<WorkspaceInfo | null>>;
  setBootstrap: Dispatch<SetStateAction<BootstrapState | null>>;
  setTasks: Dispatch<SetStateAction<TaskPlan | null>>;
  setEditedArchitecture: Dispatch<SetStateAction<Architecture | null>>;
  setRevealOnOpen: Dispatch<SetStateAction<boolean>>;
  setTabs: Dispatch<SetStateAction<DialogTab[]>>;
  setDrafts: Dispatch<SetStateAction<Record<string, string>>>;
  setActiveTab: Dispatch<SetStateAction<string | null>>;
  dispatch: Dispatch<TranscriptAction>;
  resetMicroTask: () => void;
  suppressGraphIntro: { current: boolean };
}

/** Shared workspace switching entry points.
 *
 *  `loadWorkspace` is the only path through which the picker, a `?path=` deep link, or a
 *  rollback reload can land in a project. Keeping it in one hook means every entry point
 *  resets per-project state and restores the same tabs instead of silently dropping the
 *  conversation or leaking bootstrap/tasks from the previous project.
 */
export function useWorkspaceSession({
  workspace,
  say,
  setBooted,
  setWorkspace,
  setBootstrap,
  setTasks,
  setEditedArchitecture,
  setRevealOnOpen,
  setTabs,
  setDrafts,
  setActiveTab,
  dispatch,
  resetMicroTask,
  suppressGraphIntro,
}: WorkspaceSessionOptions) {
  const loadWorkspace = useCallback(
    async (chosen: WorkspaceInfo) => {
      setBooted(true);
      const hasArchitecture = (chosen.architecture?.modules?.length ?? 0) > 0;
      if (hasArchitecture) suppressGraphIntro.current = true;
      setRevealOnOpen(false);

      // A different project must not inherit this session's transcript. Reset first so
      // the auto-save effect cannot write the previous project's messages here.
      dispatch({ type: "reset" });
      setWorkspace(chosen);
      // Per-project state must not leak across a switch: bootstrap/tasks/edited
      // architecture belong to the previous project and would otherwise shadow the
      // newly chosen one (`activeBootstrap` prefers `bootstrap` over `workspace.bootstrap`).
      setBootstrap(null);
      setTasks(chosen.work_state ?? null);
      setEditedArchitecture(null);
      resetMicroTask();
      window.localStorage.setItem(SAVED_PATH_KEY, chosen.path);
      window.history.replaceState(
        null,
        "",
        projectReloadUrl(window.location.href, chosen.path),
      );

      const restored: Message[] = (chosen.conversation ?? []).map((msg) => ({
        role: msg.role === "user" ? "user" : "agent",
        text: msg.content,
        chips: [],
      }));
      restored.forEach((msg) => dispatch({ type: "message", message: msg }));

      if (chosen.mode === "imported" && !hasArchitecture && !chosen.bootstrap
          && chosen.capabilities?.project_reconstruction === false) {
        say({
          role: "agent",
          text: "当前安装未包含旧项目接入扩展。请选择新项目目录。",
          chips: [{ text: "项目地址", type: "input" }],
        });
        setTabs([]);
        setDrafts({});
        setActiveTab(null);
        return;
      }

      if (restored.length > 0) {
        if (hasArchitecture) {
          say({
            role: "agent",
            text: `已恢复 ${restored.length} 条对话记录。这个项目已有架构，可以直接说要改什么。`,
            chips: [
              { text: "查看架构图", type: "view", detail: "打开图形视图查看模块关系" },
              { text: "开始实现", type: "action" },
              { text: "继续修改架构", type: "input" },
            ],
          });
          setTabs(INITIALIZED_TABS);
          setDrafts({});
          setActiveTab(CONVERSATION_TAB.id);
        } else if (chosen.mode === "new") {
          say({
            role: "agent",
            text: `已恢复 ${restored.length} 条对话记录。这看起来是个新项目，下面需要输入项目名称和项目需求。`,
            chips: [
              { text: "项目名称", type: "input" },
              { text: "项目需求", type: "input" },
            ],
          });
          setTabs([
            { id: "name", label: "项目名称", promptKey: "project_name", placeholder: "给它起个名字…" },
            {
              id: "description",
              label: "项目需求",
              promptKey: "project_description",
              placeholder: "它要解决什么问题？谁会用？",
            },
          ]);
          setDrafts({});
          setActiveTab("name");
        } else {
          say({
            role: "agent",
            text: `已恢复 ${restored.length} 条对话记录。这是一个已有项目：${chosen.project_name}。我可以扫描文件反推架构，也可以直接对齐需求。`,
            chips: [
              { text: "接入分析", type: "action" },
              { text: "项目需求", type: "input" },
            ],
          });
          setTabs([
            {
              id: "description",
              label: "项目需求",
              promptKey: "project_description",
              placeholder: "这次要做什么？",
            },
          ]);
          setDrafts({});
          setActiveTab("description");
        }
        return;
      }

      say({
        role: "agent",
        text: `DocuAgent: ${randomThinkingLine()}`,
        chips: [
          {
            text: "看看",
            type: "view",
            detail: `目录：${chosen.path}\n条目：${chosen.entries.length} 个\n判定：${
              chosen.mode === "new" ? "新项目（没有可识别的项目文件）" : "已有项目"
            }`,
            localSummary: true,
          },
        ],
        pending: true,
      });

      await delay(1100);

      if (chosen.mode === "new" && !hasArchitecture) {
        say({
          role: "agent",
          text: "这看起来是个新项目，下面需要输入我们的项目名称和项目需求。不需要一次说清楚，针对不明确的地方我会持续追问。",
          chips: [
            { text: "项目名称", type: "input" },
            { text: "项目需求", type: "input" },
          ],
        });
        setTabs([
          { id: "name", label: "项目名称", promptKey: "project_name", placeholder: "给它起个名字…" },
          {
            id: "description",
            label: "项目需求",
            promptKey: "project_description",
            placeholder: "它要解决什么问题？谁会用？",
          },
        ]);
        setDrafts({});
        setActiveTab("name");
      } else if (hasArchitecture) {
        say({
          role: "agent",
          text: `这是一个已有架构的项目：${chosen.project_name}，共 ${chosen.architecture!.modules.length} 个模块。可以直接说要改什么，我来修改架构图。`,
          chips: [
            { text: "查看架构图", type: "view", detail: "打开图形视图查看模块关系" },
            { text: "开始实现", type: "action" },
            { text: "继续修改架构", type: "input" },
          ],
        });
        setTabs(INITIALIZED_TABS);
        setDrafts({});
        setActiveTab(CONVERSATION_TAB.id);
      } else {
        const names = chosen.entries.slice(0, 6).map((entry) => entry.name).join("、");
        say({
          role: "agent",
          text: `这是一个已有项目：${chosen.project_name}。目录里有 ${chosen.entries.length} 个条目，包括 ${names}。我可以扫描这些文件，反推架构、生成文档树和修改建议；也可以直接让我对齐需求。`,
          chips: [
            { text: "接入分析", type: "action" },
            { text: "项目需求", type: "input" },
          ],
        });
        setTabs([
          {
            id: "description",
            label: "项目需求",
            promptKey: "project_description",
            placeholder: "这次要做什么？",
          },
        ]);
        setDrafts({});
        setActiveTab("description");
      }
    },
    [dispatch, resetMicroTask, say, setActiveTab, setBooted, setBootstrap, setDrafts, setEditedArchitecture, setRevealOnOpen, setTabs, setTasks, setWorkspace],
  );

  const openPathPicker = useCallback(async () => {
    let result: Awaited<ReturnType<typeof chooseFolder>>;
    try {
      result = await chooseFolder(workspace?.path ?? "");
    } catch (cause) {
      say({ role: "agent", text: `没能打开目录选择器：${(cause as Error).message}` });
      return;
    }

    // Cancelling the native picker must leave the screen untouched — no "让我看看".
    if (result.cancelled || !result.workspace) return;

    await loadWorkspace(result.workspace);
  }, [loadWorkspace, say, workspace?.path]);

  return { loadWorkspace, openPathPicker };
}
