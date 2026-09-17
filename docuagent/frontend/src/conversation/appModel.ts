import type { Architecture, NodeStatus } from "../graph/types";
import type { TaskItem, TaskPlan, WorkspaceInfo } from "../api";

export function mapTaskStatus(task: TaskItem): NodeStatus | undefined {
  if (task.status === "review") return "review";
  if (task.status === "applied") return "applied";
  if (task.status === "verified") return "verified";
  if (task.status === "rejected") return "skipped";
  if (task.status === "failed") return "failed";
  if (task.status === "running") return "running";
  if (task.status === "pending" && task.last_error === "已停止") return "stopped";
  if (task.status === "pending") return "pending";
  if (task.status === "blocked") return "blocked";
  return undefined;
}

export function deriveNodeStatuses(
  baseStatuses: Record<string, NodeStatus>,
  tasks: TaskPlan | null,
  workspace: WorkspaceInfo | null,
): Record<string, NodeStatus> {
  const base = { ...baseStatuses };
  for (const task of tasks?.tasks ?? []) {
    if (!task.module_id) continue;
    const mapped = mapTaskStatus(task);
    if (mapped) base[task.module_id] = mapped;
  }
  for (const moduleId of workspace?.stale_modules ?? []) {
    if (!base[moduleId] || base[moduleId] === "pending") base[moduleId] = "stale";
  }
  return base;
}

export interface HandoffEntry { task: TaskItem; moduleName: string; modulePath: string }

export function deriveHandoffEntries(tasks: TaskPlan | null, architecture: Architecture | null): HandoffEntry[] {
  return (tasks?.tasks ?? [])
    .filter((task) => task.status === "blocked" && task.last_error.startsWith("已转接对话流"))
    .map((task) => {
      const module = architecture?.modules.find((item) => item.id === task.module_id);
      return { task, moduleName: module?.name ?? task.module_id, modulePath: module?.path ?? "" };
    });
}
