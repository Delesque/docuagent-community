import { post } from "./shared";
import type { ProviderConfig } from "./types";

export type DiscoveryDecision = "evaluate_adoption" | "differentiate" | "independent" | "reject";
export interface DiscoveryRecord {
  id?: string;
  query?: string;
  status: "empty" | "proposed" | "searched" | "skipped";
  incomplete?: boolean;
  candidates: Array<{
    name: string; url: string; description: string; license: string;
    updated_at: string; archived: boolean; coverage: "unverified";
  }>;
  decisions: Array<{ candidate: string; decision: DiscoveryDecision; reason: string }>;
}
export const readDiscovery = (path: string) => post<DiscoveryRecord>("/api/discovery/read", { path });
export const proposeDiscovery = (path: string, provider: ProviderConfig, query?: string) =>
  post<DiscoveryRecord>("/api/discovery/propose", { path, provider, ...(query === undefined ? {} : { query }) });
export const searchDiscovery = (path: string, id: string) =>
  post<DiscoveryRecord>("/api/discovery/search", { path, id, approved: true });
export const decideDiscovery = (path: string, id: string, decision: DiscoveryDecision | "skip", candidate?: string, reason?: string) =>
  post<DiscoveryRecord>("/api/discovery/decision", { path, id, decision, candidate, reason });
