/** Per-turn draft status shown while interviewing.
 *
 *  The architecture stays a private draft until ready=true (the graph is only drawn
 *  once the design is complete) — but between turns the user gets no feedback about
 *  what their answer changed. This status reports counts only — never module names,
 *  never topology — so the user can see the draft grow and what still blocks
 *  completion without the draft being revealed early.
 *
 *  It is split in two on purpose. The summary carries only what moves between turns; the
 *  full aspect list is long and barely changes, so it belongs in the detail that the UI
 *  reveals on demand. Zero-value parts are dropped from both: "0 条提醒" is noise, not
 *  reassurance. A turn with nothing to report returns null.
 */

import type { BootstrapState } from "../api";

export interface DraftStatus {
  modules: number;
  edges: number;
  /** Provenance claims still marked `inferred` — the ready gate blocks on these. */
  inferred: number;
  /** Soft graph-quality warnings on the current draft. */
  qualityIssues: number;
  /** Interview topics not yet covered, shown by title (legacy fixed topics). */
  topicsRemaining: string[];
  /** The model's own coverage plan with per-aspect spend (scheme B). */
  aspects: { title: string; asked: number; max: number }[];
}

export function draftStatus(state: BootstrapState): DraftStatus | null {
  if (state.status !== "interviewing") return null;
  const modules = state.architecture?.modules?.length ?? 0;
  const edges = state.architecture?.edges?.length ?? 0;
  const inferred =
    state.provenance?.filter((claim) => claim.source === "inferred").length ?? 0;
  const qualityIssues = state.graph_quality_issues?.length ?? 0;
  const topicsRemaining = state.topics_remaining ?? [];
  const aspects = (state.interview_plan ?? []).map((aspect) => ({
    title: aspect.title,
    asked: aspect.asked,
    max: aspect.max,
  }));
  const hasAnything =
    modules > 0 || edges > 0 || inferred > 0 || qualityIssues > 0 ||
    topicsRemaining.length > 0 || aspects.length > 0;
  if (!hasAnything) return null;
  return { modules, edges, inferred, qualityIssues, topicsRemaining, aspects };
}

/** The always-visible line: only what changes between turns.
 *
 *  The blocking counts stay here — `inferred` in particular is what the ready gate waits
 *  on, so the user must see it without opening anything. */
export function formatDraftSummary(status: DraftStatus): string {
  const parts: string[] = [];
  if (status.modules > 0) parts.push(`${status.modules} 个模块`);
  if (status.edges > 0) parts.push(`${status.edges} 条依赖`);
  if (status.inferred > 0) parts.push(`${status.inferred} 处推测待确认`);
  if (status.qualityIssues > 0) parts.push(`${status.qualityIssues} 条图质量提醒`);
  // Scheme B: with a model-authored plan the progress speaks the plan's own vocabulary;
  // without one the fixed-topic fallback reports how many topics are still open.
  if (status.aspects.length > 0) {
    const started = status.aspects.filter((aspect) => aspect.asked > 0).length;
    parts.push(`访谈方面 ${started}/${status.aspects.length}`);
  } else if (status.topicsRemaining.length > 0) {
    parts.push(`${status.topicsRemaining.length} 个主题未覆盖`);
  }
  return parts.join(" · ");
}

/** The expanded detail: the per-aspect spend the summary deliberately leaves out. */
export function formatDraftDetail(status: DraftStatus): string[] {
  const lines = status.aspects.map(
    (aspect) => `${aspect.title}（${aspect.asked}/${aspect.max}）`,
  );
  if (status.topicsRemaining.length > 0) {
    lines.push(`未覆盖主题：${status.topicsRemaining.join("、")}`);
  }
  return lines;
}
