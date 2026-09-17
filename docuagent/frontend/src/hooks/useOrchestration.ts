import { useCallback, useEffect, useRef, useState } from "react";
import { streamOrchestrateTasks, type OrchestratePlanStreamEvent, type ProviderConfig, type TaskPlan, type WorkspaceInfo } from "../api";

export interface OrchestrationHookOptions {
  workspace: WorkspaceInfo | null;
  provider: ProviderConfig;
  setTasks: (tasks: TaskPlan) => void;
  notify: (message: string) => void;
}

export function useOrchestration({ workspace, provider, setTasks, notify }: OrchestrationHookOptions) {
  const [orchestrationStreaming, setOrchestrationStreaming] = useState(false);
  const [orchestrationText, setOrchestrationText] = useState("");
  const [orchestrationModuleId, setOrchestrationModuleId] = useState<string | null>(null);
  const [orchestrationError, setOrchestrationError] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const workspacePath = workspace?.path;

  useEffect(() => {
    requestVersion.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setOrchestrationStreaming(false);
    setOrchestrationText("");
    setOrchestrationModuleId(null);
    setOrchestrationError(null);
  }, [workspacePath]);

  useEffect(() => () => abortRef.current?.abort(), []);

  const stopOrchestration = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setOrchestrationStreaming(false);
  }, []);

  const orchestrate = useCallback(async (moduleId?: string, instruction?: string) => {
    if (!workspace) { notify("还没有选择项目地址。"); return null; }
    stopOrchestration();
    const version = ++requestVersion.current;
    const controller = new AbortController();
    abortRef.current = controller;
    setOrchestrationStreaming(true);
    setOrchestrationText("");
    setOrchestrationModuleId(moduleId || null);
    setOrchestrationError(null);
    const onEvent = (event: OrchestratePlanStreamEvent) => {
      if (version !== requestVersion.current) return;
      if (event.type === "started") { setOrchestrationModuleId(event.module_id || moduleId || null); return; }
      if (event.type === "reasoning" || event.type === "content") setOrchestrationText((current) => current + (event.text || ""));
    };
    try {
      const result = await streamOrchestrateTasks({ path: workspace.path, provider, module_id: moduleId, instruction }, onEvent, controller.signal);
      if (version !== requestVersion.current) return null;
      setTasks(result);
      return result;
    } catch (cause) {
      if (version !== requestVersion.current || controller.signal.aborted) return null;
      const message = (cause as Error).message;
      setOrchestrationError(message);
      notify("编排失败：" + message);
      return null;
    } finally {
      if (version === requestVersion.current) { setOrchestrationStreaming(false); abortRef.current = null; }
    }
  }, [notify, provider, setTasks, stopOrchestration, workspace]);

  return { orchestrationStreaming, orchestrationText, orchestrationModuleId, orchestrationError, orchestrate, stopOrchestration };
}
