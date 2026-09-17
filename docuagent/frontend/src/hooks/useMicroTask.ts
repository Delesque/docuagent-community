import { useCallback, useEffect, useRef, useState } from "react";
import {
  applyMicroTask,
  dispatchMicroTask,
  rejectTask,
  type ProviderConfig,
  type TaskItem,
  type TaskPlan,
  type WorkspaceInfo,
} from "../api";

export interface MicroTaskHookOptions {
  workspace: WorkspaceInfo | null;
  provider: ProviderConfig;
  setTasks: (tasks: TaskPlan) => void;
  setBusy: (busy: boolean) => void;
  notify: (role: "user" | "agent", message: string) => void;
}

export interface MicroTaskHook {
  microTask: TaskItem | null;
  microOpen: boolean;
  setMicroOpen: (open: boolean) => void;
  reset: () => void;
  dispatch: (request: string) => Promise<void>;
  apply: () => Promise<void>;
  reject: () => Promise<void>;
}

export function useMicroTask({
  workspace,
  provider,
  setTasks,
  setBusy,
  notify,
}: MicroTaskHookOptions): MicroTaskHook {
  const [microTask, setMicroTask] = useState<TaskItem | null>(null);
  const [microOpen, setMicroOpen] = useState(false);
  const workspacePath = workspace?.path;
  const requestVersion = useRef(0);
  useEffect(() => { requestVersion.current += 1; setMicroTask(null); setMicroOpen(false); setBusy(false); }, [workspacePath, setBusy]);

  const reset = useCallback(() => {
    setMicroTask(null);
    setMicroOpen(false);
  }, []);

  const dispatch = useCallback(
    async (request: string) => {
      if (!workspace) {
        notify("agent", "还没有选择项目地址。");
        return;
      }
      const version = requestVersion.current;
      notify("user", "微任务：" + request);
      setBusy(true);
      try {
        const result = await dispatchMicroTask(workspace.path, provider, request);
        if (version !== requestVersion.current) return;
        setTasks(result.tasks);
        setMicroTask(result.task);
        setMicroOpen(true);
        notify("agent", "已生成微任务 diff：" + result.task.summary);
      } catch (cause) {
        if (version === requestVersion.current) notify("agent", "微任务失败：" + (cause as Error).message);
      } finally {
        if (version === requestVersion.current) setBusy(false);
      }
    },
    [notify, provider, setBusy, setTasks, workspace],
  );

  const apply = useCallback(async () => {
    if (!workspace || !microTask) return;
    const version = requestVersion.current;
    setBusy(true);
    try {
      const result = await applyMicroTask(workspace.path, provider, microTask.id);
      if (version !== requestVersion.current) return;
      setTasks(result.tasks);
      reset();
      notify("agent", "微任务已应用：" + microTask.summary);
    } catch (cause) {
      if (version === requestVersion.current) notify("agent", "应用微任务失败：" + (cause as Error).message);
    } finally {
      if (version === requestVersion.current) setBusy(false);
    }
  }, [microTask, notify, provider, reset, setBusy, setTasks, workspace]);

  const reject = useCallback(async () => {
    if (!workspace || !microTask) return;
    const version = requestVersion.current;
    setBusy(true);
    try {
      const next = await rejectTask(workspace.path, microTask.id);
      if (version !== requestVersion.current) return;
      setTasks(next);
      reset();
      notify("agent", "微任务已拒绝。");
    } catch (cause) {
      if (version === requestVersion.current) notify("agent", "拒绝微任务失败：" + (cause as Error).message);
    } finally {
      if (version === requestVersion.current) setBusy(false);
    }
  }, [microTask, notify, reset, setBusy, setTasks, workspace]);


  return {
    microTask,
    microOpen,
    setMicroOpen,
    reset,
    dispatch,
    apply,
    reject,
  };
}
