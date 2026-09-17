// Unified error nodes (P1): typed failure records that can be attached to
// architecture graph nodes. Mirrors docuagent/error_nodes.py.

import { get } from "./shared";

export type ErrorNodeSource =
  | "generation"
  | "verification"
  | "documentation"
  | "handoff"
  | "system";

export type ErrorNodeSeverity = "critical" | "warning" | "info";

export interface ErrorNodeAction {
  id: string;
  label: string;
}

export interface ErrorNode {
  id: string;
  type: "error";
  owner_node_id: string;
  source: ErrorNodeSource;
  kind: string;
  severity: ErrorNodeSeverity;
  title: string;
  detail: string;
  retry_count: number;
  max_retries: number;
  status: "active" | "resolved";
  actions: ErrorNodeAction[];
  code_task_id: string;
  changed_files: string[];
  created_at: string;
  updated_at: string;
  resolved_at: string;
}

export async function fetchErrorNodes(path: string): Promise<ErrorNode[]> {
  const payload = await get<{ nodes: ErrorNode[] }>(
    `/api/error-nodes?path=${encodeURIComponent(path)}`,
  );
  return Array.isArray(payload?.nodes) ? payload.nodes : [];
}
