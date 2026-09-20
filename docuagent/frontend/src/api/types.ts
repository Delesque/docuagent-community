import type { Architecture, UiState } from "../graph/types";

export type { Architecture, UiState } from "../graph/types";

export interface WorkspaceInfo {
  capabilities?: { project_reconstruction: boolean };
  path: string;
  exists: boolean;
  mode: "new" | "imported";
  entries: Array<{ name: string; kind: "directory" | "file" }>;
  bootstrap: BootstrapState | null;
  /** Resolved from bootstrap.json when an interview is live, otherwise from
   *  architecture.json. Null when the project has no architecture yet. */
  architecture: Architecture | null;
  project_name: string;
  ui_state: UiState | null;
  conversation: ConversationMessage[];
  architecture_can_undo: boolean;
  architecture_version?: number;
  stale_modules: string[];
  work_state?: TaskPlan | null;
}

/** A chip as it is persisted with a conversation message. Structurally identical to the
 *  render-time `Chip`; declared here so the API layer stays independent of components. */
export interface ConversationChip {
  text: string;
  type: "input" | "view" | "action";
  detail?: string;
  localSummary?: boolean;
}

export interface ConversationMessage {
  role: "user" | "assistant";
  content: string;
  timestamp?: string;
  /** Persisted so a restored conversation keeps its buttons. Chips are routed by their
   *  text, so a stale one simply does nothing when it no longer applies. */
  chips?: ConversationChip[];
}

export type InterviewMode = "beginner" | "guided" | "professional";

export interface UserProfile {
  identity?: string;
  coding_experience?: string;
  desired_help?: string;
  explanation_preference?: string;
  constraints?: string;
}

export interface ProvenanceClaim {
  id: string;
  text: string;
  source: "confirmed" | "inferred" | "recommended" | "unknown" | "rejected";
  /** The module this claim describes, when the model anchored it. A claim
   *  without one is architecture-wide. Whitelisted server-side: an anchor
   *  naming a non-existent module is dropped before it reaches the client. */
  module_id?: string;
}

/** One structured graph-quality entry: stable rule code, exact subject, measured
 *  evidence, plus the same human message `graph_quality_issues` carries. */
export interface GraphDiagnostic {
  rule: string;
  severity: "warning" | "error";
  subject: { surface: string; type?: string; id?: string };
  evidence: Record<string, unknown>;
  message: string;
}

export type ArchitectureNodeAction = "rename" | "responsibility" | "uncertain" | "delete" | "merge" | "split";

export type ProvenanceAction = "accept" | "modify" | "unknown" | "reject";

export interface ProvenanceUpdateResult {
  state?: BootstrapState;
  architecture?: Architecture;
}

export interface ArchitectureNodeResult {
  architecture: Architecture;
  architecture_version: number;
  stale_modules: string[];
  history_remaining: number;
  work_state?: TaskPlan | null;
}
export type ArchitectureEdgeAction = "accept" | "type" | "reason" | "delete";

export interface ArchitectureEdgeOperation {
  action: ArchitectureEdgeAction;
  from: string;
  to: string;
  kind?: string;
  reason?: string;
}

export interface ArchitectureNodeOperation {
  action: ArchitectureNodeAction;
  module_id?: string;
  target_id?: string;
  source_ids?: string[];
  name?: string;
  text?: string;
  reason?: string;
  name_a?: string;
  name_b?: string;
  responsibility_a?: string;
  responsibility_b?: string;
  path_a?: string;
  path_b?: string;
}

export interface ModeOffer {
  target_mode: InterviewMode;
  label: string;
  confirm_label: string;
}

export interface BootstrapQuestion {
  id: string;
  title: string;
  prompt: string;
  why?: string;
  placeholder?: string;
  options?: string[] | null;
  /** Senior-engineer proposals on topology-affecting questions: one honest
   *  pros/cons pair per option. Present only when the model judged the question
   *  to change module boundaries or data ownership. */
  tradeoffs?: BootstrapQuestionTradeoff[] | null;
}

export interface BootstrapQuestionTradeoff {
  option: string;
  pros: string;
  cons: string;
}

export interface InterviewAspect {
  id: string;
  title: string;
  /** Questions already spent on this aspect (answers whose question mapped here). */
  asked: number;
  /** Per-aspect budget cap. */
  max: number;
}

export interface BootstrapState {
  status: "interviewing" | "review" | "ready" | "initialized";
  work_state?: TaskPlan | null;
  project: { name: string; slug: string; root: string; mode: string };
  architecture: Architecture;
  answers: Record<string, string>;
  current_question: BootstrapQuestion | null;
  progress: number;
  agent_mode: string;
  model_name: string | null;
  model_notice: string | null;
  /** The model's own reasoning for the last turn: the provider's
   *  `reasoning_content` channel when there is one, otherwise the `thinking`
   *  field the prompt asks for. Empty string when the model exposed neither —
   *  callers must not substitute a local summary without labelling it. */
  thinking: string;
  user_profile?: UserProfile;
  interview_mode?: InterviewMode;
  profile_progress?: number;
  mode_offer?: ModeOffer | null;
  provenance?: ProvenanceClaim[];
  /** Soft engineering diagnostics for the draft architecture. */
  graph_quality_issues?: string[];
  /** Structured counterparts of graph_quality_issues (stable rule codes + evidence). */
  graph_diagnostics?: GraphDiagnostic[];
  /** Titles of fixed interview topics not yet covered; present only while
   *  interviewing. Counts feed the per-turn draft status line. */
  topics_remaining?: string[];
  topics_total?: number;
  /** The model's own coverage plan, driving the progress display while
   *  interviewing (scheme B). Each aspect carries its spent question budget. */
  interview_plan?: InterviewAspect[];
  /** Computed diff from the last review-stage revision; refreshed on every
   *  revise turn while the project waits for confirmation. */
  architecture_delta?: ArchitectureDelta | null;
  /** 0 while the draft is unconfirmed; 1 once the confirmation gate has passed. */
  architecture_version?: number;
  /** Existing-project onboarding extras; only present for onboard states. */
  onboard?: boolean;
  onboard_summary?: string;
  doc_tree?: OnboardDocTreeEntry[];
  suggestions?: OnboardSuggestion[];
}

export interface OnboardDocTreeEntry {
  dir: string;
  purpose: string;
}

export type OnboardSuggestionKind =
  | "fix"
  | "improve"
  | "risk"
  | "architecture"
  | "cleanup"
  | "security";

export interface OnboardSuggestion {
  module_id: string;
  title: string;
  detail: string;
  kind: OnboardSuggestionKind;
  files: string[];
  priority: "high" | "medium" | "low";
}

export interface OnboardScanSummary {
  schema_version: number;
  scanned_at: string;
  root: string;
  language: string;
  totals: {
    files: number;
    code: number;
    manifest: number;
    data: number;
    doc: number;
    other: number;
    lines: number;
  };
  by_ext: Record<string, number>;
  directories_count: number;
  entry_points: string[];
  manifests: Array<{ path: string; facts: string[] }>;
}

export interface ModelCallOptions {
  signal?: AbortSignal;
  /** Per-attempt timeout in milliseconds. */
  timeoutMs?: number;
  /** Number of attempts including the first. Defaults to 10. */
  retries?: number;
  /** Called with the failed attempt number before the next attempt starts. */
  onRetry?: (failedAttempt: number, total: number) => void;
}

export interface ProviderConfig {
  enabled: boolean;
  base_url: string;
  model: string;
  api_key: string;
  format: "openai" | "anthropic" | "gemini";
  has_api_key?: boolean;
  roles?: Record<string, { base_url?: string; model?: string; api_key?: string }>;
  /** 全局访谈模式；未选择时为 null，首次使用会先让用户选择。 */
  interview_mode?: InterviewMode | null;
}

export interface ProviderTestResult {
  connected: boolean;
  model: string;
  base_url: string;
  capability: string;
}

export interface ArchitectureDeltaChange {
  kind: "added" | "removed" | "changed" | "moved";
  subject: { type: string; id?: string; from?: string; to?: string; kind?: string };
  /** Semantic fields that differ; absent for added/removed. */
  fields?: string[];
  /** Ready-to-display Chinese line for this change. */
  message: string;
}

/** Computed truth about what an edit did. `moved` counts are presentation-only
 *  (path/group/target_files) and must not be read as semantic warnings. */
export interface ArchitectureDelta {
  schema_version: number;
  counts: { added: number; removed: number; changed: number; moved: number };
  changes: ArchitectureDeltaChange[];
}

export interface ArchitectureEditResult {
  architecture: Architecture;
  architecture_version: number;
  thinking: string;
  /** Short list of what the model changed, for the user to scan. */
  changes: string[];
  /** Computed diff between the previous and the new architecture. Preferred
   *  over `changes` for display: it is derived, not model prose. */
  delta?: ArchitectureDelta;
  delta_lines?: string[];
  delta_summary?: string | null;
  history_remaining: number;
  work_state?: TaskPlan | null;
}

export interface ArchitectureUndoResult {
  architecture: Architecture;
  architecture_version: number;
  history_remaining: number;
}

export interface ArchitectureRequirementResult {
  architecture: Architecture;
  architecture_version: number;
  stale_modules: string[];
  history_remaining: number;
}

export type TaskStatus =
  | "pending"
  | "running"
  | "applying"
  | "verifying"
  | "review"
  | "applied"
  | "verified"
  | "rejected"
  | "failed"
  | "blocked"
  | "partially_applied";

/** What happens to a finished sandbox: become a patch for review (default) or
 *  apply itself once verification passes. Backed by the project-level
 *  apply-mode.json. */
export type ApplyMode = "review" | "auto";

/** The project's documentation-ignore configuration, backed by the project-level
 *  doc-ignore.json. `defaults` are the built-in dependency/cache directories;
 *  `ignored_dirs` is the merged view actually in force, so entries that are not
 *  in `defaults` came from the project itself. */
export interface DocIgnoreState {
  defaults: string[];
  ignored_dirs: string[];
}

export interface DiffHunk {
  id: number;
  tag: "replace" | "delete" | "insert";
  before_start: number;
  before_count: number;
  after_start: number;
  after_count: number;
  before: string;
  after: string;
}

export interface TaskPatchEntry {
  path: string;
  before: string;
  after: string;
  diff: string;
  hunks?: DiffHunk[];
}

export interface TaskItem {
  id: string;
  module_id: string;
  summary: string;
  target_files: string[];
  depends_on: string[];
  verification: string[];
  priority?: "high" | "medium" | "low";
  status: TaskStatus;
  patch: TaskPatchEntry[];
  thinking: string;
  last_error: string;
  verification_output?: {
    stdout: string;
    stderr: string;
    returncode: number;
    command: string;
    timestamp: string;
  };
  applied_files?: string[];
  rejected_files?: string[];
  pending_files?: string[];
  repair_attempts?: number;
  checkpoint_available?: boolean;
  contract_lint_violations?: string[];
  contract_delta?: {
    module_id: string;
    symbol: string;
    kind: string;
    file: string;
    reason: string;
  }[];
  contract_lint_skipped?: string[];
}

export interface OrchestrationMeta {
  summary: string;
  conflicts: string[];
  priorities: Record<string, string>;
  scope_module_ids: string[];
}

export interface TaskPlan {
  schema_version: number;
  task_version: number;
  status: string;
  tasks: TaskItem[];
  last_error: string;
  updated?: string[];
  orchestration?: OrchestrationMeta;
  documentation_task?: DocumentationTask;
  docs_in_sync?: boolean;
  docs_sync_required?: boolean;
  delivery_status?: "ready" | "blocked";
  overall_status?: "ready" | "blocked";
}

export interface DocumentationTask {
  status:
    | "not_required"
    | "pending"
    | "running"
    | "synced"
    | "retrying"
    | "blocked";
  code_task_id: string;
  code_task_ids: string[];
  changed_files: string[];
  affected_modules: string[];
  document_version: string;
  document_hash: string;
  last_error: string;
  auto_retry_count: number;
  max_auto_retries: number;
  updated_at: string;
}

export interface MicroTaskResult {
  task: TaskItem;
  tasks: TaskPlan;
  docs?: unknown;
}

export interface LayoutRect {
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LayoutNode {
  id: string;
  tag: string;
  selector: string;
  role: string;
  text: string;
  value: string;
  aria_label: string;
  source_hint: string;
  rect: LayoutRect;
  layout: Record<string, unknown>;
  frame: {
    state: "same-origin" | "opaque";
    src: string;
    accessible: boolean;
  } | null;
  children: LayoutNode[];
}

export interface LayoutSnapshot {
  schema_version: number;
  captured_at: string;
  url: string;
  module_id: string;
  title: string;
  viewport: { width: number; height: number };
  root: LayoutNode;
  meta: {
    node_count: number;
    pruned: number;
    truncated: boolean;
  };
}

export interface LayoutCaptureResult {
  module_id: string;
  path: string;
  node_count: number;
  truncated: boolean;
  size: number;
}

export interface LayoutDelta {
  schema_version: number;
  module_id: string;
  element_id?: string;
  selector?: string;
  source_hint?: string;
  reason: string;
  container: Record<string, unknown>;
  element: Record<string, unknown>;
  grid: Record<string, unknown>;
  changed?: string[];
}

export interface ScreenshotDiff {
  width: number;
  height: number;
  changed_pixels: number;
  total_pixels: number;
  ratio: number;
  grid: number[][];
  size_mismatch: boolean;
  before?: string;
  after?: string;
}

export interface ContextCacheBlock {
  key: string;
  hits: number;
  misses: number;
  hit_rate: number | null;
  chars: number;
}

export interface ContextCacheSummary {
  blocks: ContextCacheBlock[];
  total_hits: number;
  total_misses: number;
  overall_hit_rate: number | null;
  modules: Array<{
    module_id: string;
    name: string;
    total_hits: number;
    total_misses: number;
    overall_hit_rate: number | null;
  }>;
}

export interface TokenUsageBreakdown {
  module_id?: string;
  feature?: string;
  name?: string;
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  server_hit_rate: number | null;
}

export interface TokenUsageSummary {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  server_hit_rate: number | null;
  scope: "global" | "project";
  modules: TokenUsageBreakdown[];
  features: TokenUsageBreakdown[];
}

export interface TelemetryMetrics {
  app_starts: number;
  jobs_started: number;
  jobs_completed: number;
  jobs_failed: number;
  jobs_cancelled: number;
  jobs_orphaned: number;
  job_duration_count: number;
  job_duration_total_ms: number;
  context_cache_hits: number;
  context_cache_misses: number;
  provider_calls: number;
  provider_prompt_tokens: number;
  provider_completion_tokens: number;
  provider_cache_hits: number;
  provider_cache_misses: number;
  verification_failures: number;
  documentation_failures: number;
  documentation_retries: number;
  jobs_by_kind: Record<
    string,
    {
      started: number;
      completed: number;
      failed: number;
      cancelled: number;
      orphaned: number;
    }
  >;
}

export interface TelemetrySummary {
  schema_version: number;
  policy_version: number;
  local_recording_enabled: boolean;
  consent: boolean;
  consent_at: string;
  upload_available: boolean;
  auto_upload_interval_hours: number;
  pending: TelemetryMetrics;
  lifetime: TelemetryMetrics;
  pending_events: number;
  failure_rate: number | null;
  cancellation_rate: number | null;
  orphan_rate: number | null;
  cache_hit_rate: number | null;
  average_job_duration_ms: number | null;
  last_upload_at: string;
  last_upload_error: string;
}

export interface TaskStreamEvent {
  type:
    | "task_started"
    | "task_sandbox_start"
    | "task_sandbox_verify"
    | "task_direct_fallback"
    | "task_reasoning"
    | "task_done"
    | "done";
  task_id?: string;
  status?: string;
  error?: string;
  text?: string;
  path?: string;
  reason?: string;
  tasks?: TaskPlan;
}

export interface TaskPlanStreamEvent {
  type: "reasoning" | "content" | "done";
  text?: string;
  tasks?: TaskPlan;
}

export interface OrchestratePlanStreamEvent {
  type: "started" | "reasoning" | "content" | "done";
  module_id?: string;
  scope_module_ids?: string[];
  text?: string;
  tasks?: TaskPlan;
}

export interface BootstrapStreamEvent {
  type: "reasoning" | "content" | "done";
  text?: string;
  state?: BootstrapState;
}

export interface ArchitectureEditStreamEvent {
  type: "reasoning" | "content" | "done";
  text?: string;
  result?: ArchitectureEditResult;
}

export interface OnboardStreamEvent {
  type: "reasoning" | "content" | "done";
  text?: string;
  state?: BootstrapState;
}

export interface TerminalExecResult {
  command: string;
  stdout: string;
  stderr: string;
  returncode: number;
}

export interface TriageErrorItem {
  task_id: string;
  module_id: string;
  title: string;
  detail: string;
  source?: string;
  files?: string[];
  checkpoint_available?: boolean;
}

export interface TriageGroup {
  severity: "critical" | "warning" | "info";
  recommendation: string;
  errors: TriageErrorItem[];
}

export interface TriageResult {
  summary: string;
  dependency_chains: string[][];
  groups: TriageGroup[];
  suggested_order: string[];
}

export interface GitChange {
  path: string;
  status: string;
  staged: boolean;
  untracked: boolean;
}

export interface GitStatus {
  repo: boolean;
  branch: string;
  head: string;
  changes: GitChange[];
  dirty: boolean;
}

export interface GitDiff {
  working: string;
  staged: string;
}

export interface GitCommitResult {
  committed: boolean;
  head: string;
  status: GitStatus;
}

export interface McpServerConfig {
  name: string;
  command: string;
  args: string[];
  approved?: boolean;
}

export interface McpServersResult {
  servers: McpServerConfig[];
}

export interface SkillInfo {
  name: string;
  description: string;
  tags: string[];
  content?: string;
  source?: string;
  warning?: string;
}

export interface PluginInfo {
  name: string;
  version: string;
  description: string;
  entry?: string;
  tools?: Array<{ name: string; description?: string; args?: Record<string, unknown> }>;
  prompts?: Array<{ target?: string; content?: string }>;
  enabled: boolean;
  source?: string;
}

export interface VerifyStreamEvent {
  type: "output" | "command_start" | "command_done" | "done";
  task_id?: string;
  line?: string;
  stream?: string;
  command?: string;
  returncode?: number;
  state?: TaskPlan;
}

export interface MemoryCandidate {
  id: string;
  target: "user-profile" | "standards" | "recipe";
  action: "append";
  title: string;
  content: string;
  source: string;
  reason: string;
  status: "pending" | "applied" | "rejected" | "reverted";
  created_at: string;
  applied_at?: string;
  rejected_at?: string;
  reverted_at?: string;
  before?: string;
}

export interface SnapshotSummary {
  id: string;
  created_at: string;
  reason: string;
  file_count: number;
  total_files?: number;
  truncated?: boolean;
  skipped_count?: number;
}

export interface SnapshotRestoreResult {
  id: string;
  created_at: string;
  reason: string;
  restored_files: number;
  truncated?: boolean;
  skipped_count?: number;
  extra_files?: string[];
  extra_file_count?: number;
}

export interface AgentInfo {
  module_id: string;
  task_id?: string;
  work_area: string[];
  status: string;
  work_log?: string;
  error_memory?: string;
  last_error?: string;
  created_at?: string;
  updated_at?: string;
}

export interface AgentErrorEntry {
  id: string;
  created_at: string;
  source: "generation" | "verification" | "review" | string;
  task_id?: string;
  error: string;
  related_files?: string[];
}

export interface AgentDetail {
  module_id: string;
  work_log: string;
  error_memory: AgentErrorEntry[];
  session?: string;
  work_log_exists: boolean;
  error_memory_exists: boolean;
  session_path?: string;
  work_log_path: string;
  error_memory_path: string;
}

export type NodeAttachmentType =
  | "note"
  | "question"
  | "decision"
  | "option"
  | "error_memory"
  | "suggestion";

export interface NodeAttachment {
  id: string;
  type: NodeAttachmentType;
  text: string;
  created_at: string;
  resolved: boolean;
  archived?: boolean;
  /** Suggestion-only fields. */
  title?: string;
  kind?: OnboardSuggestionKind;
  files?: string[];
  priority?: "high" | "medium" | "low";
  accepted?: boolean;
}

export type NodeAttachments = Record<string, NodeAttachment[]>;
