import type { DialogTab } from "../components/shell/DialogBox";
import type { ProviderConfig } from "../api";

/** The input for revising an existing architecture.
 *
 *  A module-level constant because three separate paths offer it — after generation,
 *  after an edit, and after a failed edit — and they must all show the same tab id, or
 *  the draft the user typed would be keyed to a tab that no longer exists.
 */
/** The normal project conversation. It is deliberately unlabelled in the composer;
 * chips switch to explicit specialised modes rather than guessing user intent. */
export const CONVERSATION_TAB: DialogTab = {
  id: "conversation",
  label: "",
  promptKey: "conversation",
  placeholder: "问问项目现在的情况，或者说下一步要做什么…",
};

export const ARCHITECTURE_EDIT_TAB: DialogTab = {
  id: "architecture-edit",
  label: "继续修改架构",
  promptKey: "architecture_edit",
  placeholder: "想改什么？例如：把认证拆成独立模块、去掉缓存层、加一个导出功能…",
};

export const MICRO_TASK_TAB: DialogTab = {
  id: "micro-task",
  label: "微任务",
  promptKey: "micro_task",
  placeholder: "例如：给 core 模块的打印改成固定文案，只要一个文件。",
};

export const SUBAGENT_STATUS_TAB: DialogTab = {
  id: "subagent-status",
  label: "子 Agent 状态",
  promptKey: "subagent_status",
  placeholder: "问一下现在每个子 Agent 的进度…",
};

export const EMPTY_PROVIDER: ProviderConfig = {
  enabled: false,
  base_url: "https://api.openai.com/v1",
  model: "",
  api_key: "",
  format: "openai",
  interview_mode: null,
};

export const SAVED_PATH_KEY = "docuagent.path";
