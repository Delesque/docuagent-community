import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { latestTriage, resumeTask, retryTask, triageErrors, type ProviderConfig, type TaskItem, type TaskPlan, type TriageErrorItem, type TriageResult, type WorkspaceInfo } from "../api";

export interface TriageHookOptions {
  workspace: WorkspaceInfo | null;
  provider: ProviderConfig;
  failedTasks: TaskItem[];
  taskStreaming: boolean;
  runTaskRequest: (label: string, request: () => Promise<TaskPlan>) => Promise<TaskPlan | null>;
  setTasks: (tasks: TaskPlan) => void;
  notify: (message: string) => void;
  focusModule: (moduleId: string) => void;
}

export function useTriage({ workspace, provider, failedTasks, taskStreaming, runTaskRequest, setTasks, notify, focusModule }: TriageHookOptions) {
  const [triageOpen, setTriageOpen] = useState(false);
  const [triageResult, setTriageResult] = useState<TriageResult | null>(null);
  const [triageBusy, setTriageBusy] = useState(false);
  const [triageBusyTaskId, setTriageBusyTaskId] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const triageAutoSeen = useRef<Set<string>>(new Set());
  const wasTaskStreamingRef = useRef(false);
  const workspacePath = workspace?.path;

  useEffect(() => {
    requestVersion.current += 1;
    triageAutoSeen.current.clear();
    setTriageOpen(false);
    setTriageResult(null);
    setTriageBusy(false);
    setTriageBusyTaskId(null);
  }, [workspacePath]);

  const failedTriageErrors = useMemo((): TriageErrorItem[] => failedTasks.map((task) => ({
    task_id: task.id,
    module_id: task.module_id,
    title: task.summary || task.id,
    detail: task.last_error || task.verification_output?.stderr || task.verification_output?.stdout || "任务失败但没有留下详细错误。",
    source: task.verification_output ? "verification" : "generation",
    files: task.target_files,
    checkpoint_available: task.checkpoint_available,
  })), [failedTasks]);

  const requestTriage = useCallback(async () => {
    if (!workspace) { notify("还没有选择项目地址。"); return; }
    if (failedTriageErrors.length === 0) return;
    const version = requestVersion.current;
    setTriageBusy(true);
    try {
      const saved = await latestTriage(workspace.path, failedTriageErrors);
      const result = saved ?? await triageErrors(workspace.path, provider, failedTriageErrors);
      if (version !== requestVersion.current) return;
      setTriageResult(result);
      setTriageOpen(true);
    } catch (cause) {
      if (version === requestVersion.current) notify("错误汇总失败：" + (cause as Error).message);
    } finally {
      if (version === requestVersion.current) setTriageBusy(false);
    }
  }, [failedTriageErrors, notify, provider, workspace]);

  const handleTriageFocus = useCallback((moduleId: string) => {
    setTriageOpen(false);
    focusModule(moduleId);
  }, [focusModule]);

  const closeAfterTaskRequest = useCallback((version: number, next: TaskPlan | null) => {
    if (version !== requestVersion.current) return;
    if (next) setTasks(next);
    setTriageResult(null);
    setTriageOpen(false);
    setTriageBusyTaskId(null);
  }, [setTasks]);

  const handleTriageRetryTask = useCallback(async (taskId: string) => {
    if (!workspace) return;
    const version = requestVersion.current;
    setTriageBusyTaskId(taskId);
    const next = await runTaskRequest("重新生成任务", () => retryTask(workspace.path, provider, taskId));
    closeAfterTaskRequest(version, next);
  }, [closeAfterTaskRequest, provider, runTaskRequest, workspace]);

  const handleTriageResumeTask = useCallback(async (taskId: string) => {
    if (!workspace) return;
    const version = requestVersion.current;
    setTriageBusyTaskId(taskId);
    const next = await runTaskRequest("恢复任务", () => resumeTask(workspace.path, provider, taskId));
    closeAfterTaskRequest(version, next);
  }, [closeAfterTaskRequest, provider, runTaskRequest, workspace]);

  const handleTriageRetryAll = useCallback(async () => {
    if (!workspace || !triageResult) return;
    const version = requestVersion.current;
    const failedIds = new Set(failedTasks.map((task) => task.id));
    const order = triageResult.suggested_order.filter((taskId) => failedIds.has(taskId));
    if (order.length === 0) { notify("推荐顺序里的任务已经不在失败列表里了。"); return; }
    for (const taskId of order) {
      if (version !== requestVersion.current) return;
      setTriageBusyTaskId(taskId);
      const next = await runTaskRequest("重试任务", () => retryTask(workspace.path, provider, taskId));
      if (version !== requestVersion.current) return;
      if (next) setTasks(next);
      setTriageBusyTaskId(null);
    }
    if (version !== requestVersion.current) return;
    setTriageResult(null);
    setTriageOpen(false);
  }, [failedTasks, notify, provider, runTaskRequest, setTasks, triageResult, workspace]);

  useEffect(() => {
    if (taskStreaming) { wasTaskStreamingRef.current = true; return; }
    if (!wasTaskStreamingRef.current) return;
    wasTaskStreamingRef.current = false;
    if (failedTriageErrors.length === 0) return;
    const ids = failedTriageErrors.map((error) => error.task_id);
    if (ids.every((id) => triageAutoSeen.current.has(id))) return;
    for (const id of ids) triageAutoSeen.current.add(id);
    const timer = window.setTimeout(() => void requestTriage(), 1200);
    return () => window.clearTimeout(timer);
  }, [failedTriageErrors, requestTriage, taskStreaming]);

  return { failedTriageErrors, triageOpen, triageResult, triageBusy, triageBusyTaskId, requestTriage, handleTriageFocus, handleTriageRetryTask, handleTriageResumeTask, handleTriageRetryAll, setTriageOpen };
}
