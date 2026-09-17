// Typed client for the code-intelligence backend routes. Kept inside
// src/codeintel so it never leaks into the rest of the frontend; it only
// depends on the low-level transport (same as every other API module).

import { requestJson } from "../transport";

export interface CodeIntelCapabilities {
  available: boolean;
  languages: string[];
  features: string[];
  provider: string;
}

export interface CodeIntelLocation {
  uri: string;
  line: number;
  character: number;
  end_line: number;
  end_character: number;
}

export interface CodeIntelSymbol {
  name: string;
  kind: string;
  line: number;
  character: number;
  detail: string;
}

export interface CodeIntelDiagnostic {
  line: number;
  character: number;
  end_line: number;
  end_character: number;
  severity: number;
  message: string;
}

// A record from the derived symbol_index (the code-intel single source of truth).
export interface CodeIntelSymbolRecord {
  name: string;
  kind: string;
  file: string;
  line: number;
  character: number;
  signature: string;
  is_public: boolean;
  source_hash: string;
  last_seen: number;
  status: string;
}

export interface CodeIntelReconcileReport {
  stale: Array<{ name: string; file?: string }>;
  orphan: Array<{ name: string; file?: string }>;
  unregistered: Array<{ name: string }>;
}

export function codeIntelCapabilities(path: string): Promise<CodeIntelCapabilities> {
  return requestJson<CodeIntelCapabilities>(
    `/api/codeintel/capabilities?path=${encodeURIComponent(path)}`,
  );
}

export function codeIntelGoto(
  path: string,
  file: string,
  line: number,
  character: number,
): Promise<CodeIntelLocation[]> {
  return requestJson<CodeIntelLocation[]>("/api/codeintel/goto", {
    method: "POST",
    body: { path, file, line, character },
  });
}

export function codeIntelReferences(
  path: string,
  file: string,
  line: number,
  character: number,
): Promise<CodeIntelLocation[]> {
  return requestJson<CodeIntelLocation[]>("/api/codeintel/references", {
    method: "POST",
    body: { path, file, line, character },
  });
}

export function codeIntelHover(
  path: string,
  file: string,
  line: number,
  character: number,
): Promise<{ text: string | null }> {
  return requestJson<{ text: string | null }>("/api/codeintel/hover", {
    method: "POST",
    body: { path, file, line, character },
  });
}

export function codeIntelSymbols(
  path: string,
  file: string,
): Promise<CodeIntelSymbol[]> {
  return requestJson<CodeIntelSymbol[]>("/api/codeintel/symbols", {
    method: "POST",
    body: { path, file },
  });
}

export function codeIntelDiagnostics(
  path: string,
  file: string,
): Promise<CodeIntelDiagnostic[]> {
  return requestJson<CodeIntelDiagnostic[]>("/api/codeintel/diagnostics", {
    method: "POST",
    body: { path, file },
  });
}

// --- symbol_index (derived single source of truth) -------------------------
export function codeIntelSearch(
  path: string,
  query: string,
  limit = 50,
): Promise<CodeIntelSymbolRecord[]> {
  return requestJson<CodeIntelSymbolRecord[]>("/api/codeintel/search", {
    method: "POST",
    body: { path, query, limit },
  });
}

export function codeIntelModuleSymbols(
  path: string,
  module: string,
): Promise<CodeIntelSymbolRecord[]> {
  return requestJson<CodeIntelSymbolRecord[]>(
    `/api/codeintel/module_symbols?path=${encodeURIComponent(path)}&module=${encodeURIComponent(module)}`,
  );
}

export function codeIntelStats(path: string): Promise<{
  files: number;
  symbols: number;
  public_symbols: number;
  by_kind: Record<string, number>;
}> {
  return requestJson(
    `/api/codeintel/stats?path=${encodeURIComponent(path)}`,
  );
}

export function codeIntelReconcile(
  path: string,
  contracts: Array<{ name: string; file?: string; kind?: string }>,
): Promise<CodeIntelReconcileReport> {
  return requestJson<CodeIntelReconcileReport>("/api/codeintel/reconcile", {
    method: "POST",
    body: { path, contracts },
  });
}

// --- architecture-diagram projection (P3) ---------------------------------
export interface ModuleProjection {
  path: string;
  status: "active" | "stale";
  total: number;
  public_api: CodeIntelSymbolRecord[];
}

export interface ArchitectureProjection {
  modules: Record<string, ModuleProjection>;
  orphan: { count: number; files: string[] };
}

export function codeIntelArchitectureProjection(
  path: string,
  modules: Array<{ id: string; path: string }>,
): Promise<ArchitectureProjection> {
  return requestJson<ArchitectureProjection>(
    "/api/codeintel/architecture_projection",
    {
      method: "POST",
      body: { path, modules },
    },
  );
}

// --- contract-registry projection (§3 step3) --------------------------------
export type RegistryType =
  | "public_api"
  | "data_schema"
  | "commands_events"
  | "config_policy"
  | "shared_kernel"
  | "vocabulary";

export interface RegistryNode {
  id: string;
  type: RegistryType;
  /** Owning architecture module id (empty for orphan/registry-level entries). */
  owner: string;
  name: string;
  status:
    | "planned"
    | "proposed"
    | "active"
    | "stale"
    | "deprecated"
    | "orphan"
    | "unregistered";
  /** Source file (relative path) for a file:// deep link. */
  file: string;
  line: number | null;
  public: boolean;
}

export interface RegistryEdge {
  from: string;
  to: string;
  kind: "owns" | "depends_on" | "uses";
  label: string;
  reason: string;
}

/** Computed on every fetch: every module downstream of a stale contract, at its
 *  dependency distance. `roots` are the owners of the stale entries themselves. */
export interface RegistryImpact {
  roots: string[];
  affected: Array<{ module_id: string; depth: number }>;
  count: number;
}

export interface RegistryProjection {
  nodes: RegistryNode[];
  edges: RegistryEdge[];
  impact?: RegistryImpact;
}

export function codeIntelRegistryProjection(
  path: string,
): Promise<RegistryProjection> {
  return requestJson<RegistryProjection>("/api/codeintel/registry_projection", {
    method: "POST",
    body: { path },
  });
}

