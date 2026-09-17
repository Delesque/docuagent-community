import { useCallback, useEffect, useRef } from "react";
import {
  bootstrapConfirm,
  bootstrapFinalize,
  streamEditArchitecture,
  streamOnboardStart,
  type ArchitectureEditResult,
  type BootstrapState,
  type ModelCallOptions,
  type ProviderConfig,
  type WorkspaceInfo,
} from "../api";
import type { Message } from "../components/v2/TypewriterOutput";

export interface ArchitectureWorkflowOptions {
  workspace: WorkspaceInfo | null;
  provider: ProviderConfig;
  configured: boolean;
  say: (message: Message) => void;
  refreshSnapshots: () => Promise<unknown> | void;
  refreshAttachments: () => Promise<unknown> | void;
  applyBootstrapTurn: (next: BootstrapState) => void;
  setTasks: (tasks: NonNullable<BootstrapState["work_state"]> | null) => void;
  setEditedArchitecture: (architecture: Awaited<ReturnType<typeof streamEditArchitecture>>["architecture"]) => void;
  setUndoAvailable: (available: boolean) => void;
  setRevealOnOpen: (reveal: boolean) => void;
  setBusy: (busy: boolean) => void;
  setModelStreaming: (streaming: boolean) => void;
  setSettingsOpen: (open: boolean) => void;
  setThinking: (thinking: string, fallback: string) => void;
  setRetryStatus: (status: { label: string; attempt: number; total: number } | null) => void;
}

export function useArchitectureWorkflow({ workspace, provider, configured, say, refreshSnapshots, refreshAttachments, applyBootstrapTurn, setTasks, setEditedArchitecture, setUndoAvailable, setRevealOnOpen, setBusy, setModelStreaming, setSettingsOpen, setThinking, setRetryStatus }: ArchitectureWorkflowOptions) {
  const retryAbort = useRef<AbortController | null>(null);
  const streamAbort = useRef<AbortController | null>(null);

  useEffect(() => () => { retryAbort.current?.abort(); streamAbort.current?.abort(); }, []);
  useEffect(() => { streamAbort.current?.abort(); streamAbort.current = null; }, [workspace?.path]);

  const runModelCall = useCallback(async <T,>(label: string, request: (options: ModelCallOptions) => Promise<T>): Promise<T | null> => {
    const controller = new AbortController();
    retryAbort.current = controller;
    try {
      const result = await request({
        signal: controller.signal, retries: 10, timeoutMs: 180_000,
        onRetry: (failed, total) => setRetryStatus({ label, attempt: failed + 1, total }),
      });
      void refreshSnapshots();
      return result;
    } catch (cause) {
      say({ role: "agent", text: controller.signal.aborted ? "已停止" + label + "。" : label + "失败：" + (cause as Error).message });
      return null;
    } finally {
      if (retryAbort.current === controller) retryAbort.current = null;
      setRetryStatus(null);
    }
  }, [refreshSnapshots, say, setRetryStatus]);

  const runBootstrapStream = useCallback(async <T,>(label: string, request: (onReasoning: (text: string) => void, signal: AbortSignal) => Promise<T>): Promise<T | null> => {
    if (!workspace) return null;
    streamAbort.current?.abort();
    const controller = new AbortController();
    streamAbort.current = controller;
    setModelStreaming(true);
    try {
      return await request((text) => setThinking(text, "模型正在思考…"), controller.signal);
    } catch (cause) {
      say({ role: "agent", text: controller.signal.aborted ? "已停止" + label + "。" : label + "失败：" + (cause as Error).message });
      return null;
    } finally {
      if (streamAbort.current === controller) streamAbort.current = null;
      setModelStreaming(false);
    }
  }, [say, setModelStreaming, setThinking, workspace]);

  const confirm = useCallback(async (): Promise<BootstrapState | null> => {
    if (!workspace) { say({ role: "agent", text: "还没有可确认的架构。" }); return null; }
    setBusy(true);
    try {
      const confirmed = await runModelCall("确认架构", (options) => bootstrapConfirm({ path: workspace.path, provider }, options));
      if (!confirmed) return null;
      let next = confirmed;
      if (confirmed.status === "ready") {
        const finalized = await runModelCall("生成框架", (options) => bootstrapFinalize({ path: workspace.path, provider }, options));
        if (!finalized) return null;
        next = finalized;
        setTasks(finalized.work_state ?? null);
      }
      setRevealOnOpen(true);
      applyBootstrapTurn(next);
      return next;
    } finally { setBusy(false); }
  }, [applyBootstrapTurn, provider, runModelCall, say, setBusy, setRevealOnOpen, setTasks, workspace]);

  const onboard = useCallback(async (): Promise<BootstrapState | null> => {
    if (!workspace) return null;
    if (workspace.capabilities?.project_reconstruction === false) {
      say({ role: "agent", text: "当前安装未包含旧项目接入扩展。" });
      return null;
    }
    if (!configured) {
      setSettingsOpen(true);
      say({ role: "agent", text: "接入分析需要先配置 AI 模型。请先在设置里填写 API。" });
      return null;
    }
    say({ role: "user", text: "接入分析：读取项目文件，反推架构、逐文件夹文档树与修改建议。" });
    const next = await runBootstrapStream("接入分析", (onReasoning, signal) => {
      let reasoning = "";
      return streamOnboardStart({ path: workspace.path, name: workspace.project_name, description: "分析这个已有项目", provider }, (event) => {
        if (event.type === "reasoning") { reasoning += event.text ?? ""; onReasoning(reasoning); }
      }, signal);
    });
    if (next) { applyBootstrapTurn(next); void refreshAttachments(); }
    return next;
  }, [applyBootstrapTurn, configured, provider, refreshAttachments, runBootstrapStream, say, setSettingsOpen, workspace]);

  const edit = useCallback(async (request: string): Promise<ArchitectureEditResult | null> => {
    if (!workspace) { say({ role: "agent", text: "还没有选择项目地址。" }); return null; }
    setBusy(true);
    try {
      const result = await runBootstrapStream("修改架构", (onReasoning, signal) => {
        let reasoning = "";
        return streamEditArchitecture({ path: workspace.path, request, provider }, (event) => {
          if (event.type === "reasoning") { reasoning += event.text ?? ""; onReasoning(reasoning); }
        }, signal);
      });
      if (!result) return null;
      setEditedArchitecture(result.architecture);
      setTasks(result.work_state ?? null);
      setUndoAvailable(result.history_remaining > 0);
      return result;
    } finally { setBusy(false); }
  }, [provider, runBootstrapStream, say, setBusy, setEditedArchitecture, setTasks, setUndoAvailable, workspace]);

  return { runModelCall, runBootstrapStream, confirm, onboard, edit, stopRetry: () => retryAbort.current?.abort(), stopStream: () => streamAbort.current?.abort() };
}
