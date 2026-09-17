import type { ConversationMessage, ModelCallOptions, ProviderConfig, McpServerConfig, McpServersResult, SkillInfo, PluginInfo, MemoryCandidate, SnapshotSummary, SnapshotRestoreResult, NodeAttachmentType, NodeAttachments } from "./types";
import { get, post, postWithRetry } from "./shared";

export async function fetchMcpServers(path: string): Promise<McpServersResult> {
  return get(`/api/mcp/servers?path=${encodeURIComponent(path)}`);
}

export async function saveMcpServers(
  path: string,
  servers: McpServerConfig[],
): Promise<McpServersResult> {
  return post("/api/mcp/servers", { path, servers });
}

export async function approveMcpServer(
  path: string,
  server: string,
): Promise<McpServersResult> {
  return post("/api/mcp/approve", { path, server });
}

export async function testMcpServer(
  command: string,
  args: string[],
): Promise<{ tools: unknown[] }> {
  return post("/api/mcp/test", { command, args });
}

export async function fetchSkills(path: string): Promise<{ skills: SkillInfo[] }> {
  return get(`/api/skills?path=${encodeURIComponent(path)}`);
}

export async function importSkill(
  path: string,
  source: string,
): Promise<{ skill: SkillInfo }> {
  return post("/api/skills/import", { path, source });
}

export async function fetchMarketplace(
  marketplace: string,
): Promise<{ entries: SkillInfo[] }> {
  return post("/api/skills/marketplace", { marketplace });
}

export async function installSkill(
  path: string,
  marketplace: string,
  name: string,
): Promise<{ skill: SkillInfo }> {
  return post("/api/skills/install", { path, marketplace, name });
}

export async function fetchPlugins(path: string): Promise<{ plugins: PluginInfo[] }> {
  return get(`/api/plugins?path=${encodeURIComponent(path)}`);
}

export async function installPlugin(
  path: string,
  source: string,
): Promise<{ plugin: PluginInfo }> {
  return post("/api/plugins/install", { path, source });
}

export async function uninstallPlugin(
  path: string,
  name: string,
): Promise<{ name: string; uninstalled: boolean }> {
  return post("/api/plugins/uninstall", { path, name });
}

export async function togglePlugin(
  path: string,
  name: string,
  enabled: boolean,
): Promise<{ plugin: PluginInfo }> {
  return post("/api/plugins/toggle", { path, name, enabled });
}

export async function fetchPluginMarketplace(
  marketplace: string,
): Promise<{ entries: PluginInfo[] }> {
  return post("/api/plugins/marketplace", { marketplace });
}

export async function installMarketPlugin(
  path: string,
  marketplace: string,
  name: string,
): Promise<{ plugin: PluginInfo }> {
  return post("/api/plugins/install-market", { path, marketplace, name });
}

export async function listMemoryCandidates(
  path: string,
): Promise<MemoryCandidate[]> {
  const result = await get<{ candidates: MemoryCandidate[] }>(
    `/api/memory/candidates?path=${encodeURIComponent(path)}`,
  );
  return result.candidates;
}

export async function suggestMemoryCandidates(
  path: string,
  provider: ProviderConfig,
  options: ModelCallOptions = {},
): Promise<MemoryCandidate[]> {
  const result = await postWithRetry<{ candidates: MemoryCandidate[] }>(
    "/api/memory/suggest",
    { path, provider },
    options,
  );
  return result.candidates;
}

export async function applyMemoryCandidate(
  path: string,
  candidateId: string,
): Promise<MemoryCandidate> {
  return post("/api/memory/apply", { path, candidate_id: candidateId });
}

export async function rejectMemoryCandidate(
  path: string,
  candidateId: string,
): Promise<MemoryCandidate> {
  return post("/api/memory/reject", { path, candidate_id: candidateId });
}

export async function revertMemoryCandidate(
  path: string,
  candidateId: string,
): Promise<MemoryCandidate> {
  return post("/api/memory/revert", { path, candidate_id: candidateId });
}

export async function listSnapshots(path: string): Promise<SnapshotSummary[]> {
  const result = await get<{ snapshots: SnapshotSummary[] }>(
    `/api/snapshots?path=${encodeURIComponent(path)}`,
  );
  return result.snapshots;
}

export async function restoreSnapshot(
  path: string,
  snapshotId: string,
): Promise<SnapshotRestoreResult> {
  return post("/api/snapshots/restore", { path, snapshot_id: snapshotId });
}

export async function clearStale(
  path: string,
  moduleIds?: string[],
): Promise<{ stale_modules: string[] }> {
  return post("/api/architecture/clear-stale", {
    path,
    module_ids: moduleIds,
  });
}

export async function listAttachments(path: string): Promise<NodeAttachments> {
  const result = await get<{ attachments: NodeAttachments }>(
    `/api/attachments?path=${encodeURIComponent(path)}`,
  );
  return result.attachments;
}

export async function addAttachment(
  path: string,
  moduleId: string,
  type: NodeAttachmentType,
  text: string,
): Promise<NodeAttachments> {
  const result = await post<{ attachments: NodeAttachments }>(
    "/api/attachments/add",
    { path, module_id: moduleId, type, text },
  );
  return result.attachments;
}

export async function resolveAttachment(
  path: string,
  moduleId: string,
  attachmentId: string,
): Promise<NodeAttachments> {
  const result = await post<{ attachments: NodeAttachments }>(
    "/api/attachments/resolve",
    { path, module_id: moduleId, attachment_id: attachmentId },
  );
  return result.attachments;
}

export async function archiveAttachment(
  path: string,
  moduleId: string,
  attachmentId: string,
): Promise<NodeAttachments> {
  const result = await post<{ attachments: NodeAttachments }>(
    "/api/attachments/archive",
    { path, module_id: moduleId, attachment_id: attachmentId },
  );
  return result.attachments;
}

export async function saveConversation(
  path: string,
  messages: ConversationMessage[],
): Promise<{ saved: number }> {
  return post("/api/conversation", { path, messages });
}