import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Architecture } from "../graph/types";
import { emptyTaskStream, pushTaskStream, taskStreamText, type TaskStream } from "../graph/taskStream";
import type { ProviderConfig, TaskPlan, TaskStatus, TaskStreamEvent, WorkspaceInfo } from "../api";
import type { Message } from "../components/v2/TypewriterOutput";

export interface TaskFlowOptions {
  workspace: WorkspaceInfo | null;
  provider: ProviderConfig;
  architecture: Architecture | null;
  tasks: TaskPlan | null;
  setTasks: (next: TaskPlan | null | ((prev: TaskPlan | null) => TaskPlan | null)) => void;
  setBusy: (busy: boolean) => void;
  say: (message: Message) => void;
  refreshSnapshots: () => Promise<unknown> | void;
  refreshAgents: () => Promise<unknown> | void;
  streamWorkStart: (path: string, provider: ProviderConfig, onEvent: (event: TaskStreamEvent) => void, signal?: AbortSignal) => Promise<TaskPlan>;
  streamVerifyTask: (path: string, taskId: string, onEvent: (event: { type: string; line?: string }) => void) => Promise<TaskPlan>;
  applyTask: (path: string, taskId: string) => Promise<TaskPlan>;
  rejectTask: (path: string, taskId: string) => Promise<TaskPlan>;
  retryTask: (path: string, provider: ProviderConfig, taskId: string) => Promise<TaskPlan>;
  editTaskPatch: (path: string, taskId: string, file: string, content: string, baseAfter: string) => Promise<TaskPlan>;
  applyTaskHunks: (path: string, taskId: string, file: string, hunkIds: number[]) => Promise<TaskPlan>;
}

export function useTaskFlow(options: TaskFlowOptions) {
  const { tasks, setTasks, workspace, provider, architecture, setBusy, say, refreshSnapshots, refreshAgents, streamWorkStart, streamVerifyTask, applyTask, rejectTask, retryTask, editTaskPatch, applyTaskHunks } = options;
  const [taskStreams, setTaskStreams] = useState<Record<string, TaskStream>>({});
  const [busyTaskId, setBusyTaskId] = useState<string | null>(null);
  const [taskStreaming, setTaskStreaming] = useState(false);
  const taskStreamAbort = useRef<AbortController | null>(null);
  const taskProgressRef = useRef<Map<string, { status: "running" | "done" | "failed" | "stopped"; latest: string }>>(new Map());
  const progressTextRef = useRef("");
  const workspacePathRef = useRef<string | null>(workspace?.path ?? null);
  workspacePathRef.current = workspace?.path ?? null;
  const isCurrentWorkspace = useCallback((path: string) => workspacePathRef.current === path, []);

  useEffect(() => {
    taskStreamAbort.current?.abort();
    taskStreamAbort.current = null;
    setTaskStreaming(false);
    setTaskStreams({});
    setBusyTaskId(null);
    taskProgressRef.current = new Map();
    progressTextRef.current = "";
  }, [workspace?.path]);
  useEffect(() => () => { taskStreamAbort.current?.abort(); }, []);

  const runTaskRequest = useCallback(async (label: string, request: () => Promise<TaskPlan>): Promise<TaskPlan | null> => {
    setBusy(true);
    try { const result = await request(); void refreshSnapshots(); return result; }
    catch (cause) { say({ role: "agent", text: label + "失败：" + (cause as Error).message }); return null; }
    finally { setBusy(false); }
  }, [refreshSnapshots, say, setBusy]);

  const taskTails = useMemo(() => {
    const byTask: Record<string, string[]> = {}; const byModule: Record<string, string[]> = {};
    for (const task of tasks?.tasks ?? []) { const lines = taskStreamText(taskStreams[task.id]); if (lines.length) { byTask[task.id] = lines; byModule[task.module_id] = lines; } }
    return { byTask, byModule };
  }, [tasks, taskStreams]);
  const updateTaskReasoning = useCallback((taskId: string, text: string) => {
    setTaskStreams((prev) => ({ ...prev, [taskId]: pushTaskStream(prev[taskId], text) }));
    const current = taskProgressRef.current.get(taskId); if (current) taskProgressRef.current.set(taskId, { ...current, latest: current.latest + text });
  }, []);
  const updateTaskStatus = useCallback((taskId: string, status: TaskStatus, error = "") => {
    setTasks((prev) => prev ? { ...prev, tasks: prev.tasks.map((task) => task.id === taskId ? { ...task, status, last_error: error } : task) } : prev);
    const progressStatus = status === "failed" ? "failed" : status === "pending" && error === "已停止" ? "stopped" : status === "running" ? "running" : "done";
    const current = taskProgressRef.current.get(taskId); taskProgressRef.current.set(taskId, { status: progressStatus, latest: current?.latest ?? error });
  }, []);

  const runTaskWaveStream = useCallback(async (label: string, markReady: (prev: TaskPlan) => TaskPlan) => {
    const path = workspace?.path; if (!path) return;
    const controller = new AbortController(); taskStreamAbort.current?.abort(); taskStreamAbort.current = controller;
    setTaskStreaming(true); setTasks((prev) => prev ? markReady(prev) : prev);
    say({ role: "agent", text: "我先看一下哪些模块可以并行开始。", pending: true, instant: true, chips: [] });
    try {
      const plan = await streamWorkStart(path, provider, (event) => {
        if (!isCurrentWorkspace(path)) return;
        if (event.type === "task_started" && event.task_id) { updateTaskStatus(event.task_id, "running"); setTaskStreams((prev) => ({ ...prev, [event.task_id as string]: emptyTaskStream() })); }
        if ((event.type === "task_reasoning" || event.type === "task_sandbox_start") && event.task_id && event.text) updateTaskReasoning(event.task_id, event.text);
        if (event.type === "task_done" && event.task_id) updateTaskStatus(event.task_id, (event.status as TaskStatus) ?? "failed", event.error ?? "");
      }, controller.signal);
      if (!isCurrentWorkspace(path)) return;
      setTasks(plan);
      const moduleLabel = (id: string) => architecture?.modules.find((module) => module.id === id)?.name ?? id;
      const review = plan.tasks.filter((task) => task.status === "review"); const failed = plan.tasks.filter((task) => task.status === "failed"); const stopped = plan.tasks.filter((task) => task.status === "pending" && task.last_error === "已停止"); const verified = plan.tasks.filter((task) => task.status === "verified");
      const lines: string[] = [];
      if (review.length) lines.push("已经并行完成 " + review.length + " 个模块的实现：" + review.map((task) => moduleLabel(task.module_id)).join("、") + "。改动已生成，你可以在架构图上逐个审阅。");
      if (verified.length) lines.push("其中 " + verified.length + " 个模块已经通过验证：" + verified.map((task) => moduleLabel(task.module_id)).join("、") + "。");
      if (failed.length) lines.push(failed.length + " 个模块需要处理：" + failed.slice(0, 3).map((task) => "「" + moduleLabel(task.module_id) + "」" + (task.last_error || "没有留下详细错误。" )).join("；"));
      if (stopped.length) lines.push("已停止：" + stopped.map((task) => moduleLabel(task.module_id)).join("、") + "。");
      if (!lines.length) lines.push("没有可以开始工作的模块：它们可能已经完成，或还在等待依赖模块完成。");
      say({ role: "agent", text: lines.join("\n"), instant: true, chips: [{ text: "查看架构图", type: "view" }] }); void refreshSnapshots();
    } catch (cause) { if (controller.signal.aborted) say({ role: "agent", text: "已停止" + label + "。" }); else if (isCurrentWorkspace(path)) say({ role: "agent", text: label + "失败：" + (cause as Error).message }); }
    finally {
      if (taskStreamAbort.current === controller) taskStreamAbort.current = null;
      if (isCurrentWorkspace(path)) { setTaskStreaming(false); taskProgressRef.current = new Map(); progressTextRef.current = ""; void refreshAgents(); }
    }
  }, [architecture, isCurrentWorkspace, provider, refreshAgents, refreshSnapshots, say, streamWorkStart, updateTaskReasoning, updateTaskStatus, workspace?.path]);

  const markWaveReady = useCallback((prev: TaskPlan): TaskPlan => { const applied = new Set(prev.tasks.filter((task) => task.status === "applied" || task.status === "verified").map((task) => task.id)); return { ...prev, tasks: prev.tasks.map((task) => task.status === "pending" && task.depends_on.every((dep) => applied.has(dep)) ? { ...task, status: "running" } : task) }; }, []);
  const startWork = useCallback(() => { void runTaskWaveStream("并行实现", markWaveReady); }, [markWaveReady, runTaskWaveStream]);
  const stopTask = useCallback(() => { taskStreamAbort.current?.abort(); }, []);
  const handleApplyTask = useCallback((taskId: string) => { if (workspace) void runTaskRequest("应用补丁", () => applyTask(workspace.path, taskId)).then((next) => next && setTasks(next)); }, [applyTask, runTaskRequest, workspace]);
  const handleRejectTask = useCallback((taskId: string) => { if (workspace) void runTaskRequest("拒绝任务", () => rejectTask(workspace.path, taskId)).then((next) => next && setTasks(next)); }, [rejectTask, runTaskRequest, workspace]);
  const handleRetryTask = useCallback((taskId: string) => { if (!workspace) return; const path = workspace.path; setBusyTaskId(taskId); void runTaskRequest("重新生成", () => retryTask(path, provider, taskId)).then((next) => { if (next && isCurrentWorkspace(path)) { setTasks(next); } }).finally(() => { if (isCurrentWorkspace(path)) setBusyTaskId(null); }); }, [isCurrentWorkspace, provider, retryTask, runTaskRequest, workspace]);
  const handleVerifyTask = useCallback((taskId: string) => { if (!workspace) return; const path = workspace.path; setBusyTaskId(taskId); setTaskStreams((prev) => ({ ...prev, [taskId]: emptyTaskStream() })); void (async () => { try { const next = await streamVerifyTask(path, taskId, (event) => { if (event.type === "output" && event.line && isCurrentWorkspace(path)) updateTaskReasoning(taskId, event.line + "\n"); }); if (isCurrentWorkspace(path)) setTasks(next); } catch (cause) { if (isCurrentWorkspace(path)) say({ role: "agent", text: "验证失败：" + (cause as Error).message }); } finally { if (isCurrentWorkspace(path)) setBusyTaskId(null); } })(); }, [isCurrentWorkspace, say, streamVerifyTask, updateTaskReasoning, workspace]);
  const handleSavePatch = useCallback(async (taskId: string, file: string, content: string, baseAfter: string) => { if (!workspace) return false; const path = workspace.path; const next = await runTaskRequest("编辑补丁", () => editTaskPatch(path, taskId, file, content, baseAfter)); if (!next || !isCurrentWorkspace(path)) return false; setTasks(next); return true; }, [editTaskPatch, isCurrentWorkspace, runTaskRequest, workspace]);
  const handleApplyHunks = useCallback(async (taskId: string, file: string, hunkIds: number[]) => { if (!workspace) return false; const path = workspace.path; const next = await runTaskRequest("应用改动块", () => applyTaskHunks(path, taskId, file, hunkIds)); if (!next || !isCurrentWorkspace(path)) return false; setTasks(next); return true; }, [applyTaskHunks, isCurrentWorkspace, runTaskRequest, workspace]);
  const failedTasks = useMemo(() => (tasks?.tasks ?? []).filter((task) => task.status === "failed"), [tasks]);
  const canStartWork = useMemo(() => { if (!architecture || !tasks || taskStreaming) return false; const applied = new Set(tasks.tasks.filter((task) => task.status === "applied" || task.status === "verified").map((task) => task.id)); return tasks.tasks.some((task) => task.status === "pending" && task.depends_on.every((dependency) => applied.has(dependency))); }, [architecture, taskStreaming, tasks]);
  return { tasks, setTasks, taskStreams, taskTails, busyTaskId, taskStreaming, taskProgressRef, progressTextRef, failedTasks, canStartWork, runTaskRequest, updateTaskReasoning, updateTaskStatus, startWork, stopWork: stopTask, stopTask, applyTask: handleApplyTask, rejectTask: handleRejectTask, retryTask: handleRetryTask, verifyTask: handleVerifyTask, savePatch: handleSavePatch, applyHunks: handleApplyHunks, handleApplyTask, handleRejectTask, handleRetryTask, handleVerifyTask, handleSavePatch, handleApplyHunks };
}
