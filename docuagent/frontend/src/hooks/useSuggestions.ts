import { useCallback, useEffect, useMemo, useState } from "react";
import { acceptSuggestion, rejectSuggestion, type NodeAttachments, type WorkspaceInfo } from "../api";
import type { Message } from "../components/shell/TypewriterOutput";
import type { SuggestionEntry } from "../components/SuggestionsPanel";

export interface SuggestionsOptions {
  workspace: WorkspaceInfo | null;
  attachments: NodeAttachments;
  say: (message: Message) => void;
  replaceAttachments: (next: NodeAttachments) => void;
}

export interface Suggestions {
  suggestionEntries: SuggestionEntry[];
  suggestionsOpen: boolean;
  setSuggestionsOpen: (open: boolean) => void;
  suggestionBusy: string | null;
  acceptSuggestion: (moduleId: string, attachmentId: string) => Promise<void>;
  rejectSuggestion: (moduleId: string, attachmentId: string) => Promise<void>;
}

/** Suggestion attachments are the one node-attachment kind that can be accepted or
 *  rejected directly from the shell. Keeping their panel state here stops the App from
 *  re-implementing the same accept/reject orchestration for the toolbar and the
 *  "查看修改建议" chip. */
export function useSuggestions({ workspace, attachments, say, replaceAttachments }: SuggestionsOptions): Suggestions {
  const [suggestionsOpen, setSuggestionsOpen] = useState(false);
  const [suggestionBusy, setSuggestionBusy] = useState<string | null>(null);

  const workspacePath = workspace?.path;
  useEffect(() => {
    setSuggestionsOpen(false);
    setSuggestionBusy(null);
  }, [workspacePath]);

  const handleAcceptSuggestion = useCallback(
    async (moduleId: string, attachmentId: string) => {
      if (!workspace) return;
      setSuggestionBusy(attachmentId);
      try {
        const result = await acceptSuggestion(workspace.path, moduleId, attachmentId);
        replaceAttachments(result.attachments);
        say({
          role: "agent",
          text: `已采纳建议「${result.task.summary}」，已挂载到对应架构节点，可以在图上开始实现和审阅。`,
        });
      } catch (cause) {
        say({ role: "agent", text: `采纳建议失败：${(cause as Error).message}` });
      } finally {
        setSuggestionBusy(null);
      }
    },
    [replaceAttachments, say, workspace],
  );

  const handleRejectSuggestion = useCallback(
    async (moduleId: string, attachmentId: string) => {
      if (!workspace) return;
      setSuggestionBusy(attachmentId);
      try {
        const result = await rejectSuggestion(workspace.path, moduleId, attachmentId);
        replaceAttachments(result.attachments);
      } catch (cause) {
        say({ role: "agent", text: `拒绝建议失败：${(cause as Error).message}` });
      } finally {
        setSuggestionBusy(null);
      }
    },
    [replaceAttachments, say, workspace],
  );

  const suggestionEntries = useMemo<SuggestionEntry[]>(() => {
    const entries: SuggestionEntry[] = [];
    for (const [moduleId, items] of Object.entries(attachments)) {
      for (const attachment of items) {
        if (attachment.type === "suggestion" && !attachment.resolved) {
          entries.push({ moduleId, attachment });
        }
      }
    }
    entries.sort((a, b) => {
      const order = { high: 0, medium: 1, low: 2 } as Record<string, number>;
      return (order[a.attachment.priority ?? "medium"] ?? 1) - (order[b.attachment.priority ?? "medium"] ?? 1);
    });
    return entries;
  }, [attachments]);

  return {
    suggestionEntries,
    suggestionsOpen,
    setSuggestionsOpen,
    suggestionBusy,
    acceptSuggestion: handleAcceptSuggestion,
    rejectSuggestion: handleRejectSuggestion,
  };
}
