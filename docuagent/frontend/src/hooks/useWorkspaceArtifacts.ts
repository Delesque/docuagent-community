import { useCallback, useEffect, useRef, useState } from "react";
import { addAttachment, archiveAttachment, listAgents, listAttachments, listSnapshots, restoreSnapshot, sendAgentMessage, type AgentInfo, type NodeAttachments, type SnapshotSummary, type WorkspaceInfo } from "../api";
import type { NodeAttachmentType } from "../api";
import { projectReloadUrl } from "../conversation/projectRecovery";

export interface WorkspaceArtifactsOptions { workspace: WorkspaceInfo | null; notify: (message: string) => void; setBusy: (busy: boolean) => void; }
export interface WorkspaceArtifacts {
  agents: AgentInfo[]; snapshots: SnapshotSummary[]; attachments: NodeAttachments;
  refreshAgents: () => Promise<void>; refreshSnapshots: () => Promise<void>; refreshAttachments: () => Promise<void>;
  replaceAttachments: (next: NodeAttachments) => void;
  restoreSnapshot: (snapshotId: string) => Promise<void>; sendAgentMessage: (moduleId: string, text: string) => Promise<void>;
  addNodeAttachment: (moduleId: string, type: NodeAttachmentType, text: string) => Promise<void>; archiveModule: (moduleId: string) => Promise<void>;
}

export function useWorkspaceArtifacts({ workspace, notify, setBusy }: WorkspaceArtifactsOptions): WorkspaceArtifacts {
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [snapshots, setSnapshots] = useState<SnapshotSummary[]>([]);
  const [attachments, setAttachments] = useState<NodeAttachments>({});
  const workspacePath = workspace?.path;
  const requestVersion = useRef(0);
  useEffect(() => { requestVersion.current += 1; setAgents([]); setSnapshots([]); setAttachments({}); }, [workspacePath]);
  const isCurrent = (version: number) => version === requestVersion.current;
  const refreshSnapshots = useCallback(async () => {
    const version = requestVersion.current; if (!workspacePath) { setSnapshots([]); return; }
    try { const next = await listSnapshots(workspacePath); if (version === requestVersion.current) setSnapshots(next); } catch { if (version === requestVersion.current) setSnapshots([]); }
  }, [workspacePath]);
  const refreshAgents = useCallback(async () => {
    const version = requestVersion.current; if (!workspacePath) { setAgents([]); return; }
    try { const next = await listAgents(workspacePath); if (version === requestVersion.current) setAgents(next); } catch { if (version === requestVersion.current) setAgents([]); }
  }, [workspacePath]);
  const refreshAttachments = useCallback(async () => {
    const version = requestVersion.current; if (!workspacePath) { setAttachments({}); return; }
    try { const next = await listAttachments(workspacePath); if (version === requestVersion.current) setAttachments(next); } catch { if (version === requestVersion.current) setAttachments({}); }
  }, [workspacePath]);
  useEffect(() => { void refreshSnapshots(); void refreshAttachments(); }, [refreshAttachments, refreshSnapshots]);
  const replaceAttachments = useCallback((next: NodeAttachments) => setAttachments(next), []);
  const handleRestoreSnapshot = useCallback(async (snapshotId: string) => {
    if (!workspacePath || !workspace) return; setBusy(true);
    try { await restoreSnapshot(workspacePath, snapshotId); window.location.href = projectReloadUrl(window.location.href, workspacePath); }
    catch (cause) { notify("恢复失败：" + (cause as Error).message); setBusy(false); }
  }, [notify, setBusy, workspace, workspacePath]);
  const handleSendAgentMessage = useCallback(async (moduleId: string, text: string) => {
    const version = requestVersion.current;
    if (!workspacePath) return;
    try { await sendAgentMessage(workspacePath, moduleId, text); if (isCurrent(version)) notify("已把补充指令转发给「" + moduleId + "」。"); }
    catch (cause) { if (isCurrent(version)) notify("转发子 Agent 失败：" + (cause as Error).message); }
  }, [notify, workspacePath]);
  const handleAddNodeAttachment = useCallback(async (moduleId: string, type: NodeAttachmentType, text: string) => {
    const version = requestVersion.current;
    if (!workspacePath) return;
    try { const next = await addAttachment(workspacePath, moduleId, type, text); if (isCurrent(version)) setAttachments(next); }
    catch (cause) { if (isCurrent(version)) notify("写入节点失败：" + (cause as Error).message); }
  }, [notify, workspacePath]);
  const handleArchiveModule = useCallback(async (moduleId: string) => {
    const version = requestVersion.current;
    if (!workspacePath) return; const pending = (attachments[moduleId] ?? []).filter((a) => !a.resolved && !a.archived);
    if (pending.length === 0) { notify("「" + moduleId + "」没有可归档的未完成附件。"); return; }
    let latest = attachments; try { for (const attachment of pending) { latest = await archiveAttachment(workspacePath, moduleId, attachment.id); if (isCurrent(version)) setAttachments(latest); }
      if (isCurrent(version)) notify("已归档「" + moduleId + "」的 " + pending.length + " 条未完成附件。"); }
    catch (cause) { if (isCurrent(version)) notify("归档节点附件失败：" + (cause as Error).message); }
  }, [attachments, notify, workspacePath]);
  return { agents, snapshots, attachments, refreshAgents, refreshSnapshots, refreshAttachments, replaceAttachments, restoreSnapshot: handleRestoreSnapshot, sendAgentMessage: handleSendAgentMessage, addNodeAttachment: handleAddNodeAttachment, archiveModule: handleArchiveModule };
}