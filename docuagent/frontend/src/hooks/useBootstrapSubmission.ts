import { useCallback } from "react";
import type { DialogTab } from "../components/shell/DialogBox";
import type { BootstrapState, ModelCallOptions, ProviderConfig, WorkspaceInfo } from "../api";
import { bootstrapRevise, streamBootstrapAnswer, streamBootstrapStart } from "../api";
import { delay } from "../conversation/delay";
import { pendingThinkingChip, randomThinkingLine } from "../conversation/thinking";
import type { Message } from "../components/shell/TypewriterOutput";

export interface BootstrapSubmissionOptions {
  workspace: WorkspaceInfo | null;
  provider: ProviderConfig;
  bootstrap: BootstrapState | null;
  tabs: DialogTab[];
  say: (message: Message) => void;
  setBusy: (busy: boolean) => void;
  setRevealOnOpen: (reveal: boolean) => void;
  applyBootstrapTurn: (next: BootstrapState) => void;
  confirmArchitecture: () => Promise<unknown>;
  runModelCall: <T>(label: string, request: (options: ModelCallOptions) => Promise<T>) => Promise<T | null>;
  runBootstrapStream: <T>(label: string, request: (onReasoning: (text: string) => void, signal: AbortSignal) => Promise<T>) => Promise<T | null>;
}

export function useBootstrapSubmission({ workspace, provider, bootstrap, tabs, say, setBusy, setRevealOnOpen, applyBootstrapTurn, confirmArchitecture, runModelCall, runBootstrapStream }: BootstrapSubmissionOptions) {
  return useCallback(async (values: Record<string, string>): Promise<boolean> => {
    if (!workspace) { say({ role: "agent", text: "还没有选择项目地址。" }); return false; }
    setBusy(true);
    const spoken = tabs.map((tab) => tab.label + "：" + (values[tab.id] ?? "")).join("\n");
    say({ role: "user", text: spoken });
    await delay(120);
    const thinkingLine = randomThinkingLine();
    say({ role: "agent", text: "DocuAgent: " + thinkingLine, pending: true, chips: [pendingThinkingChip(thinkingLine)] });
    try {
      let next: BootstrapState | null = null;
      if (!bootstrap) {
        next = await runBootstrapStream("AI 思考", (onReasoning, signal) => {
          let reasoning = "";
          return streamBootstrapStart({ path: workspace.path, name: values.name ?? workspace.project_name, description: values.description ?? "", ...(provider.interview_mode ? { interview_mode: provider.interview_mode } : {}), provider }, (event) => {
            if (event.type === "reasoning") { reasoning += event.text ?? ""; onReasoning(reasoning); }
          }, signal);
        });
      } else if (bootstrap.status === "review") {
        const reviseText = (values.revise ?? "").trim();
        if (reviseText.length >= 2) {
          next = await runModelCall("AI 修订", (options) => bootstrapRevise({ path: workspace.path, feedback: reviseText, provider }, options));
        } else {
          await confirmArchitecture();
          return true;
        }
      } else {
        const structured = tabs.map((tab) => tab.promptKey + ": " + (values[tab.id] ?? "")).join("\n");
        next = await runBootstrapStream("AI 思考", (onReasoning, signal) => {
          let reasoning = "";
          return streamBootstrapAnswer({ path: workspace.path, answer: structured, provider }, (event) => {
            if (event.type === "reasoning") { reasoning += event.text ?? ""; onReasoning(reasoning); }
          }, signal);
        });
      }
      if (!next) return false;
      if (next.status === "ready" || next.status === "initialized") setRevealOnOpen(true);
      applyBootstrapTurn(next);
      return true;
    } catch (cause) {
      say({ role: "agent", text: "出错了：" + (cause as Error).message });
      return false;
    } finally { setBusy(false); }
  }, [applyBootstrapTurn, bootstrap, confirmArchitecture, provider, runBootstrapStream, runModelCall, say, setBusy, setRevealOnOpen, tabs, workspace]);
}
