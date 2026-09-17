import type { TaskItem } from "../api";
import type { GraphModule } from "../graph/types";

interface TaskInfoCardProps {
  module: GraphModule;
  task?: TaskItem | null;
}

const TASK_STATUS_LABELS: Record<TaskItem["status"], string> = {
  pending: "待生成",
  running: "生成中",
  applying: "应用中",
  verifying: "验证中",
  review: "待审阅",
  applied: "已应用",
  verified: "已验证",
  rejected: "已跳过",
  failed: "失败",
  blocked: "需人工处理",
  partially_applied: "部分应用",
};

export function TaskInfoCard({ module, task }: TaskInfoCardProps) {
  return (
    <div className="mb-3 rounded-none border border-ink/40 bg-paper-raise/70 p-3 font-mono">
      <div className="flex items-center gap-2 text-[11px]">
        <span className="text-ink">📋</span>
        <span className="text-chalk">
          {module.responsibility || module.brief || "尚未定义职责"}
        </span>
      </div>
      {task ? (
        <div className="mt-2 flex items-center gap-2 text-[10px] text-chalk-dim">
          <span className="text-ink">状态:</span>
          <span>{TASK_STATUS_LABELS[task.status]}</span>
          {["failed", "pending"].includes(task.status) && task.checkpoint_available ? (
            <span className="text-amber">可从检查点恢复</span>
          ) : null}
          {task.status === "blocked" ? (
            <span className="text-amber">等待人工处理</span>
          ) : null}
        </div>
      ) : null}
      {task?.last_error ? (
        <div className="mt-1.5 whitespace-pre-wrap text-[10px] text-vermilion">
          {task.last_error}
        </div>
      ) : null}
      {task?.target_files && task.target_files.length > 0 ? (
        <div className="mt-1.5 flex items-start gap-2 text-[10px] text-chalk-faint">
          <span className="text-ink">📂</span>
          <span className="flex-1">{task.target_files.join(", ")}</span>
        </div>
      ) : null}
      {module.depends_on.length > 0 ? (
        <div className="mt-1.5 flex items-center gap-2 text-[10px] text-chalk-faint">
          <span className="text-ink">🔗</span>
          <span>依赖: {module.depends_on.join(", ")}</span>
        </div>
      ) : null}
    </div>
  );
}
