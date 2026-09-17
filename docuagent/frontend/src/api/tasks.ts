import type { ApplyMode, BootstrapState, DocIgnoreState, OnboardScanSummary, ModelCallOptions, ProviderConfig, TaskPlan, MicroTaskResult, LayoutSnapshot, LayoutCaptureResult, ContextCacheSummary, TokenUsageSummary, TaskStreamEvent, OrchestratePlanStreamEvent, OnboardStreamEvent, TerminalExecResult, TriageErrorItem, TriageResult, GitStatus, GitDiff, GitCommitResult, VerifyStreamEvent,  } from "./types";
import { get, post, postWithRetry, readNdjsonEvents, requestNdjson, withSavedProvider } from "./shared";

export async function dispatchMicroTask(
  path: string,
  provider: ProviderConfig,
  request: string,
): Promise<MicroTaskResult> {
  return post("/api/micro-task/dispatch", { path, provider, request });
}

export async function applyMicroTask(
  path: string,
  provider: ProviderConfig,
  taskId: string,
): Promise<MicroTaskResult> {
  return post("/api/micro-task/apply", {
    path,
    provider,
    task_id: taskId,
  });
}

export async function fetchContextCache(path: string): Promise<ContextCacheSummary> {
  return post("/api/context-cache", { path });
}

export async function fetchTokenUsage(path?: string): Promise<TokenUsageSummary> {
  return post("/api/token-usage", path ? { path } : {});
}

export async function captureLayoutSnapshot(
  path: string,
  url: string,
  moduleId: string,
  maxNodes = 800,
  maxText = 120,
): Promise<LayoutCaptureResult> {
  return post("/api/ui-layout/capture", {
    path,
    url,
    module_id: moduleId,
    max_nodes: maxNodes,
    max_text: maxText,
  });
}

export async function fetchLayoutSnapshot(
  path: string,
  moduleId: string,
): Promise<LayoutSnapshot> {
  const params = new URLSearchParams({ path, module_id: moduleId });
  return get(`/api/ui-layout/snapshot?${params.toString()}`);
}

export async function confirmTasks(path: string): Promise<TaskPlan> {
  return post("/api/tasks/confirm", { path });
}

export async function setTaskPriority(
  path: string,
  taskId: string,
  priority: "high" | "medium" | "low",
): Promise<TaskPlan> {
  return post("/api/tasks/priority", { path, task_id: taskId, priority });
}

export async function cancelTasks(
  path: string,
  taskIds?: string[],
): Promise<{ cancelled: number }> {
  return post("/api/tasks/cancel", {
    path,
    ...(taskIds && taskIds.length > 0 ? { task_ids: taskIds } : {}),
  });
}

export async function streamTaskWave(
  path: string,
  provider: ProviderConfig,
  onEvent: (event: TaskStreamEvent) => void,
  signal?: AbortSignal,
  endpoint = "/api/tasks/generate-wave-stream",
): Promise<TaskPlan> {
  let plan: TaskPlan | null = null;
  await requestNdjson<TaskStreamEvent>(
    endpoint,
    withSavedProvider({ path, provider }),
    (event) => {
      onEvent(event);
      if (event.type === "done" && event.tasks) plan = event.tasks;
    },
    signal,
  );
  if (!plan) throw new Error("流式生成未返回完成事件。");
  return plan;
}

export async function streamOrchestrateTasks(
  request: {
    path: string;
    provider: ProviderConfig;
    module_id?: string;
    instruction?: string;
  },
  onEvent: (event: OrchestratePlanStreamEvent) => void,
  signal?: AbortSignal,
): Promise<TaskPlan> {
  let result: TaskPlan | null = null;
  await readNdjsonEvents<OrchestratePlanStreamEvent>(
    "/api/orchestrate/plan-stream",
    request,
    (event) => {
      onEvent(event);
      if (event.type === "done" && event.tasks) result = event.tasks;
    },
    signal,
  );
  if (!result) throw new Error("流式编排未返回完成事件。");
  return result;
}

export async function scanImport(path: string): Promise<OnboardScanSummary> {
  return post("/api/onboard/scan", { path });
}

export async function streamOnboardStart(
  request: {
    path: string;
    name: string;
    description: string;
    provider: ProviderConfig;
  },
  onEvent: (event: OnboardStreamEvent) => void,
  signal?: AbortSignal,
): Promise<BootstrapState> {
  let result: BootstrapState | null = null;
  await readNdjsonEvents<OnboardStreamEvent>(
    "/api/onboard/stream",
    request,
    (event) => {
      onEvent(event);
      if (event.type === "done" && event.state) result = event.state;
    },
    signal,
  );
  if (!result) throw new Error("接入分析未返回完成事件。");
  return result;
}

/** Accept a suggestion: enqueue a pending task and mark the attachment resolved. */

export async function applyTask(path: string, taskId: string): Promise<TaskPlan> {
  return post("/api/tasks/apply", { path, task_id: taskId });
}

/** Read the project's sandbox-apply switch. Omitting `mode` only reads it. */

export async function fetchApplyMode(path: string): Promise<ApplyMode> {
  const result = await post<{ mode: ApplyMode }>("/api/tasks/apply-mode", { path });
  return result.mode;
}

export async function setApplyMode(path: string, mode: ApplyMode): Promise<ApplyMode> {
  const result = await post<{ mode: ApplyMode }>("/api/tasks/apply-mode", { path, mode });
  return result.mode;
}

/** Read the project's documentation-ignore list. Omitting `ignored_dirs` only reads. */
export async function fetchDocIgnore(path: string): Promise<DocIgnoreState> {
  return post<DocIgnoreState>("/api/doc-ignore", { path });
}

/** Replace the project-level ignore list; the reply carries the merged view. */
export async function setDocIgnore(path: string, ignoredDirs: string[]): Promise<DocIgnoreState> {
  return post<DocIgnoreState>("/api/doc-ignore", { path, ignored_dirs: ignoredDirs });
}

export async function applyTaskPartial(
  path: string,
  taskId: string,
  fileDecisions: Record<string, "accept" | "reject" | "pending">,
): Promise<TaskPlan> {
  return post("/api/tasks/apply-partial", {
    path,
    task_id: taskId,
    file_decisions: fileDecisions,
  });
}

/** Persist a user edit to one proposed file in a task patch before it is applied. */

export async function editTaskPatch(
  path: string,
  taskId: string,
  file: string,
  content: string,
  baseAfter: string,
): Promise<TaskPlan> {
  return post("/api/tasks/patch-edit", {
    path,
    task_id: taskId,
    file,
    content,
    base_after: baseAfter,
  });
}

/** Apply a selected subset of diff hunks for one patch file. */

export async function applyTaskHunks(
  path: string,
  taskId: string,
  file: string,
  hunkIds: number[],
): Promise<TaskPlan> {
  return post("/api/tasks/apply-hunks", {
    path,
    task_id: taskId,
    file,
    hunk_ids: hunkIds,
  });
}

export async function terminalExec(
  path: string,
  command: string,
  cwd = "",
): Promise<TerminalExecResult> {
  return post("/api/terminal/exec", { path, command, cwd });
}

export async function latestTriage(
  path: string,
  errors: TriageErrorItem[],
): Promise<TriageResult | null> {
  const response = await post("/api/orchestrate/triage-latest", { path, errors });
  return (response as { result?: TriageResult | null }).result ?? null;
}

export async function fetchGitStatus(path: string): Promise<GitStatus> {
  return get(`/api/git/status?path=${encodeURIComponent(path)}`);
}

export async function fetchGitDiff(
  path: string,
  file = "",
): Promise<GitDiff> {
  const params = new URLSearchParams({ path });
  if (file) params.set("file", file);
  return get(`/api/git/diff?${params.toString()}`);
}

export async function commitGitChanges(
  path: string,
  message: string,
): Promise<GitCommitResult> {
  return post("/api/git/commit", { path, message });
}

export async function rejectTask(path: string, taskId: string): Promise<TaskPlan> {
  return post("/api/tasks/reject", { path, task_id: taskId });
}

export async function retryTask(
  path: string,
  provider: ProviderConfig,
  taskId: string,
): Promise<TaskPlan> {
  return post("/api/tasks/retry", {
    path,
    provider,
    task_id: taskId,
  });
}

export async function resumeTask(
  path: string,
  provider: ProviderConfig,
  taskId: string,
): Promise<TaskPlan> {
  return post("/api/tasks/resume", {
    path,
    provider,
    task_id: taskId,
  });
}

export async function repairTask(
  path: string,
  provider: ProviderConfig,
  taskId: string,
): Promise<TaskPlan> {
  return post("/api/tasks/repair", {
    path,
    provider,
    task_id: taskId,
  });
}

export async function verifyTask(path: string, taskId: string): Promise<TaskPlan> {
  return post("/api/tasks/verify", { path, task_id: taskId });
}

export async function streamVerifyTask(
  path: string,
  taskId: string,
  onEvent: (event: VerifyStreamEvent) => void,
  signal?: AbortSignal,
): Promise<TaskPlan> {
  let result: TaskPlan | null = null;
  await readNdjsonEvents<VerifyStreamEvent>(
    "/api/tasks/verify-stream",
    { path, task_id: taskId },
    (event) => {
      onEvent(event);
      if (event.type === "done" && event.state) result = event.state;
    },
    signal,
  );
  if (!result) throw new Error("流式验证未返回完成事件。");
  return result;
}

export async function syncTaskDocs(
  path: string,
  provider: ProviderConfig,
  options: ModelCallOptions = {},
): Promise<TaskPlan> {
  return postWithRetry("/api/tasks/sync-docs", { path, provider }, options);
}
export async function retryTaskDocs(
  path: string,
  provider: ProviderConfig,
  options: ModelCallOptions = {},
): Promise<TaskPlan> {
  return postWithRetry("/api/tasks/retry-documentation", { path, provider }, options);
}
export async function streamWorkStart(path:string,provider:ProviderConfig,onEvent:(event:TaskStreamEvent)=>void,signal?:AbortSignal):Promise<TaskPlan>{return streamTaskWave(path,provider,onEvent,signal,"/api/work/start-stream");}
export async function triageErrors(path:string,provider:ProviderConfig,errors:TriageErrorItem[]):Promise<TriageResult>{return post("/api/orchestrate/triage",{path,provider,errors});}
