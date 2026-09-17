import type { AgentInfo, AgentErrorEntry, AgentDetail } from "./types";
import { get, post } from "./shared";

export async function listAgents(path: string): Promise<AgentInfo[]> {
  return get(`/api/agents?path=${encodeURIComponent(path)}`);
}

export async function getAgentDetail(
  path: string,
  moduleId: string,
): Promise<AgentDetail> {
  return get(
    `/api/agents/detail?path=${encodeURIComponent(path)}&module_id=${encodeURIComponent(moduleId)}`,
  );
}

export async function clearAgentErrors(
  path: string,
  moduleId: string,
): Promise<AgentErrorEntry[]> {
  const result = await post<{ error_memory: AgentErrorEntry[] }>(
    "/api/agents/clear-errors",
    { path, module_id: moduleId },
  );
  return result.error_memory;
}

export async function sendAgentMessage(
  path: string,
  moduleId: string,
  message: string,
): Promise<{ module_id: string; session_path: string; content: string }> {
  return post("/api/agents/message", {
    path,
    module_id: moduleId,
    message,
  });
}