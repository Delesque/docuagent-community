/** Live progress lines for the conversation flow while a wave is generating.
 *
 *  One task = one line, newest reasoning tail for running tasks, final verdict for
 *  finished ones. Kept as a pure function so the conversation surface and the
 *  status answer share exactly one format.
 */

export interface TaskProgressEntry {
  status: "running" | "done" | "failed" | "stopped";
  latest: string;
}

export const TASK_STATUS_TEXT: Record<TaskProgressEntry["status"], string> = {
  running: "生成中",
  done: "已生成",
  failed: "失败",
  stopped: "已停止",
};

export function composeTaskProgress(
  entries: Array<{ id: string; progress: TaskProgressEntry }>,
): string {
  if (entries.length === 0) return "并行生成准备中…";
  const lines = entries.map(({ id, progress }) => {
    if (progress.status === "running") {
      const latest = progress.latest.trim().split("\n").pop() ?? "";
      return `「${id}」生成中：${latest || "等待模型输出…"}`;
    }
    return `「${id}」${TASK_STATUS_TEXT[progress.status]}`;
  });
  return `并行生成中 · ${entries.length} 个任务\n${lines.join("\n")}`;
}

export function composeTaskStatusAnswer(
  entries: Array<{ id: string; progress: TaskProgressEntry }>,
): string {
  if (entries.length === 0) return "当前没有正在生成的任务。";
  return composeTaskProgress(entries);
}
