import { useMemo, useState } from "react";
import type { Architecture, GraphModule, ProvenanceClaim } from "../graph/types";
import type { ArchitectureEdgeOperation, ArchitectureNodeOperation, InterviewMode, ProvenanceAction } from "../api";
import { explainGraphQualityIssue } from "../conversation/graphQuality";

export interface ArchitectureReviewPanelProps {
  architecture: Architecture;
  provenance: ProvenanceClaim[];
  graphQualityIssues: string[];
  interviewMode: InterviewMode;
  architectureVersion: number;
  onProvenanceAction: (claimId: string, action: ProvenanceAction, text?: string) => Promise<boolean>;
  onNodeAction: (operation: ArchitectureNodeOperation) => Promise<boolean>;
  onEdgeAction: (operation: ArchitectureEdgeOperation) => Promise<boolean>;
}

const SOURCE_LABELS: Record<ProvenanceClaim["source"], string> = {
  confirmed: "用户事实",
  inferred: "AI 推测",
  recommended: "AI 建议",
  unknown: "未知",
  rejected: "已拒绝",
};
const SOURCE_ORDER: ProvenanceClaim["source"][] = ["confirmed", "inferred", "recommended", "unknown", "rejected"];

export function ArchitectureReviewPanel({
  architecture,
  provenance,
  graphQualityIssues,
  interviewMode,
  architectureVersion,
  onProvenanceAction,
  onNodeAction,
  onEdgeAction,
}: ArchitectureReviewPanelProps) {
  const [busyClaimId, setBusyClaimId] = useState<string | null>(null);
  const [busyModuleId, setBusyModuleId] = useState<string | null>(null);
  const [mergeTarget, setMergeTarget] = useState<string>("");
  const [mergeSources, setMergeSources] = useState<Set<string>>(new Set());
  const [busyEdgeKey, setBusyEdgeKey] = useState<string | null>(null);

  const grouped = useMemo(() => {
    const map = new Map<ProvenanceClaim["source"], ProvenanceClaim[]>();
    for (const claim of provenance) {
      const list = map.get(claim.source) ?? [];
      list.push(claim);
      map.set(claim.source, list);
    }
    return SOURCE_ORDER.filter((source) => map.has(source)).map((source) => ({
      source,
      label: SOURCE_LABELS[source],
      claims: map.get(source) ?? [],
    }));
  }, [provenance]);

  const runClaim = async (claimId: string, action: ProvenanceAction, text?: string) => {
    if (busyClaimId) return;
    setBusyClaimId(claimId);
    try {
      await onProvenanceAction(claimId, action, text);
    } finally {
      setBusyClaimId(null);
    }
  };

  const runNode = async (moduleId: string, operation: ArchitectureNodeOperation) => {
    if (busyModuleId) return;
    setBusyModuleId(moduleId);
    try {
      await onNodeAction(operation);
    } finally {
      setBusyModuleId(null);
    }
  };

  const promptValue = (message: string, fallback = "") => {
    const value = window.prompt(message, fallback);
    return value === null ? null : value.trim();
  };

  const runEdge = async (operation: ArchitectureEdgeOperation) => {
    const key = `${operation.from}->${operation.to}`;
    if (busyEdgeKey) return;
    setBusyEdgeKey(key);
    try {
      await onEdgeAction(operation);
    } finally {
      setBusyEdgeKey(null);
    }
  };

  const moduleName = (moduleId: string) =>
    architecture.modules.find((module) => module.id === moduleId)?.name ?? moduleId;

  const splitModule = (module: GraphModule) => {
    const nameA = promptValue("拆分后的第一个模块名称", `${module.name} A`);
    if (!nameA) return;
    const nameB = promptValue("拆分后的第二个模块名称", `${module.name} B`);
    if (!nameB) return;
    const responsibilityA = promptValue("第一个模块的职责", module.responsibility) ?? nameA;
    const responsibilityB = promptValue("第二个模块的职责", module.responsibility) ?? nameB;
    void runNode(module.id, {
      action: "split",
      module_id: module.id,
      name_a: nameA,
      name_b: nameB,
      responsibility_a: responsibilityA,
      responsibility_b: responsibilityB,
    });
  };

  return (
    <section
      className="pointer-events-auto fixed left-5 top-20 z-40 max-h-[calc(100vh-8rem)] w-[min(28rem,calc(100vw-2rem))] overflow-y-auto rounded-xl border border-ink-ghost bg-paper-raise/95 p-3 shadow-xl backdrop-blur-sm"
      aria-label="架构确认"
    >
      <div className="flex items-start justify-between gap-2">
        <div>
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">架构确认</p>
          <p className="mt-1 font-body text-xs text-chalk-dim">
            确认后的每个模块会成为一个工作单元，按依赖顺序生成；未处理的 AI 推测会阻止确认。
          </p>
        </div>
        {architectureVersion > 0 ? (
          <span className="rounded-full border border-emerald/50 px-2 py-1 font-mono text-[10px] text-emerald">
            版本 {architectureVersion}
          </span>
        ) : (
          <span className="rounded-full border border-ink-dim/50 px-2 py-1 font-mono text-[10px] text-chalk-dim">
            草稿
          </span>
        )}
      </div>

      <details className="mt-3 border-t border-ink-ghost pt-2">
        <summary className="cursor-pointer list-none font-mono text-[10px] text-chalk-dim">
          信息来源分区 · {provenance.length}
        </summary>
        <div className="mt-2 grid gap-2">
          {grouped.map((group) => (
            <div key={group.source} className="rounded border border-ink-dim/40 p-2">
              <p className="font-mono text-[10px] text-chalk-faint">{group.label}</p>
              <ul className="mt-1 grid gap-1.5 font-body text-[11px] text-chalk-dim">
                {group.claims.map((claim) => (
                  <li key={claim.id} className="flex items-start justify-between gap-2">
                    <span className="min-w-0 whitespace-pre-wrap break-words">{claim.text}</span>
                    <span className="flex shrink-0 gap-1">
                      <button type="button" disabled={busyClaimId === claim.id} onClick={() => void runClaim(claim.id, "accept")} className="text-emerald disabled:opacity-40">确认</button>
                      <button type="button" disabled={busyClaimId === claim.id} onClick={() => { const text = promptValue("修改这条来源说明", claim.text); if (text) void runClaim(claim.id, "modify", text); }} className="text-chalk-dim disabled:opacity-40">修改</button>
                      <button type="button" disabled={busyClaimId === claim.id} onClick={() => void runClaim(claim.id, "unknown")} className="text-chalk-faint disabled:opacity-40">未知</button>
                      <button type="button" disabled={busyClaimId === claim.id} onClick={() => void runClaim(claim.id, "reject")} className="text-vermilion disabled:opacity-40">拒绝</button>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </details>

      <details className="mt-2 border-t border-ink-ghost pt-2">
        <summary className="cursor-pointer list-none font-mono text-[10px] text-chalk-dim">
          架构检查 · {graphQualityIssues.length}
        </summary>
        <ul className="mt-2 grid gap-1.5 font-body text-[11px] text-chalk-dim">
          {graphQualityIssues.map((issue, index) => (
            <li key={`${index}-${issue}`} className="whitespace-pre-wrap break-words">
              {explainGraphQualityIssue(issue, interviewMode)}
            </li>
          ))}
        </ul>
      </details>

      <div className="mt-3 border-t border-ink-ghost pt-2">
        <div className="flex items-center justify-between gap-2">
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">模块节点 · {architecture.modules.length}</p>
          <button
            type="button"
            className="rounded border border-vermilion/60 px-2 py-1 font-mono text-[10px] text-vermilion hover:bg-vermilion/10 disabled:opacity-40"
            disabled={mergeSources.size === 0 || !mergeTarget || mergeSources.has(mergeTarget)}
            onClick={() => {
              if (!mergeTarget) return;
              void runNode(mergeTarget, {
                action: "merge",
                module_id: mergeTarget,
                source_ids: Array.from(mergeSources),
              });
              setMergeSources(new Set());
            }}
          >
            合并选中到目标
          </button>
        </div>
        <label className="mt-2 block font-mono text-[10px] text-chalk-faint">
          合并目标
          <select
            className="mt-1 w-full rounded border border-ink/50 bg-paper px-2 py-1 font-body text-[11px] text-chalk"
            value={mergeTarget}
            onChange={(event) => setMergeTarget(event.target.value)}
          >
            <option value="">选择保留的模块</option>
            {architecture.modules.map((module) => (
              <option key={module.id} value={module.id}>{module.name}</option>
            ))}
          </select>
        </label>
        <ul className="mt-2 grid gap-2">
          {architecture.modules.map((module) => (
            <li key={module.id} className="rounded border border-ink-dim/40 p-2">
              <label className="flex items-start gap-2">
                <input
                  type="checkbox"
                  className="mt-1"
                  checked={mergeSources.has(module.id)}
                  onChange={(event) => {
                    const next = new Set(mergeSources);
                    if (event.target.checked) next.add(module.id); else next.delete(module.id);
                    setMergeSources(next);
                  }}
                  aria-label={`选择合并 ${module.name}`}
                />
                <span className="min-w-0">
                  <span className="block font-body text-xs font-medium text-chalk">{module.name}</span>
                  <span className="mt-0.5 block whitespace-pre-wrap break-words font-body text-[11px] text-chalk-dim">{module.responsibility}</span>
                  {module.uncertain ? <span className="mt-1 inline-block rounded border border-amber/60 px-1 font-mono text-[9px] text-amber">不确定：{module.uncertain_reason || "待确认"}</span> : null}
                </span>
              </label>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <button type="button" disabled={busyModuleId === module.id} onClick={() => { const name = promptValue("模块名称", module.name); if (name) void runNode(module.id, { action: "rename", module_id: module.id, name }); }} className="rounded border border-ink-dim/50 px-2 py-0.5 font-mono text-[10px] text-chalk-dim hover:bg-paper-float disabled:opacity-40">改名</button>
                <button type="button" disabled={busyModuleId === module.id} onClick={() => { const text = promptValue("模块职责", module.responsibility); if (text) void runNode(module.id, { action: "responsibility", module_id: module.id, text }); }} className="rounded border border-ink-dim/50 px-2 py-0.5 font-mono text-[10px] text-chalk-dim hover:bg-paper-float disabled:opacity-40">改职责</button>
                <button type="button" disabled={busyModuleId === module.id} onClick={() => { const reason = promptValue("为什么不确定？", "还需要用户进一步确认"); if (reason) void runNode(module.id, { action: "uncertain", module_id: module.id, reason }); }} className="rounded border border-amber/60 px-2 py-0.5 font-mono text-[10px] text-amber hover:bg-amber/10 disabled:opacity-40">不确定</button>
                <button type="button" disabled={busyModuleId === module.id} onClick={() => void splitModule(module)} className="rounded border border-ink-dim/50 px-2 py-0.5 font-mono text-[10px] text-chalk-dim hover:bg-paper-float disabled:opacity-40">拆分</button>
                <button type="button" disabled={busyModuleId === module.id} onClick={() => { if (window.confirm(`删除模块「${module.name}」？`)) void runNode(module.id, { action: "delete", module_id: module.id }); }} className="rounded border border-vermilion/60 px-2 py-0.5 font-mono text-[10px] text-vermilion hover:bg-vermilion/10 disabled:opacity-40">删除</button>
              </div>
            </li>
          ))}
        </ul>
      </div>

      <div className="mt-3 border-t border-ink-ghost pt-2">
        <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">连线关系 · {architecture.edges.length}</p>
        <ul className="mt-2 grid gap-2">
          {architecture.edges.length === 0 ? (
            <li className="font-body text-[11px] text-chalk-dim">没有显式连线；依赖由模块的 depends_on 派生。</li>
          ) : null}
          {architecture.edges.map((edge) => (
            <li key={`${edge.from}-${edge.to}`} className="rounded border border-ink-dim/40 p-2">
              <p className="font-body text-[11px] text-chalk">
                {moduleName(edge.from)} <span className="text-chalk-dim">→</span> {moduleName(edge.to)}
              </p>
              <p className="mt-1 font-mono text-[9px] text-chalk-faint">
                {edge.kind}{edge.accepted ? " · 已接受" : " · 待接受"}
              </p>
              <p className="mt-1 whitespace-pre-wrap break-words font-body text-[10px] text-chalk-dim">{edge.reason || "（未填写原因）"}</p>
              <div className="mt-2 flex flex-wrap gap-1.5">
                <button type="button" disabled={busyEdgeKey === `${edge.from}->${edge.to}`} onClick={() => void runEdge({ action: "accept", from: edge.from, to: edge.to })} className="rounded border border-emerald/60 px-2 py-0.5 font-mono text-[10px] text-emerald hover:bg-emerald/10 disabled:opacity-40">接受</button>
                <button type="button" disabled={busyEdgeKey === `${edge.from}->${edge.to}`} onClick={() => { const kind = promptValue("连线类型：uses/data/event/extends/blocks", edge.kind); if (kind) void runEdge({ action: "type", from: edge.from, to: edge.to, kind }); }} className="rounded border border-ink-dim/50 px-2 py-0.5 font-mono text-[10px] text-chalk-dim hover:bg-paper-float disabled:opacity-40">改类型</button>
                <button type="button" disabled={busyEdgeKey === `${edge.from}->${edge.to}`} onClick={() => { const reason = promptValue("连线原因", edge.reason); if (reason) void runEdge({ action: "reason", from: edge.from, to: edge.to, reason }); }} className="rounded border border-ink-dim/50 px-2 py-0.5 font-mono text-[10px] text-chalk-dim hover:bg-paper-float disabled:opacity-40">改原因</button>
                <button type="button" disabled={busyEdgeKey === `${edge.from}->${edge.to}`} onClick={() => void runEdge({ action: "delete", from: edge.from, to: edge.to })} className="rounded border border-vermilion/60 px-2 py-0.5 font-mono text-[10px] text-vermilion hover:bg-vermilion/10 disabled:opacity-40">删除</button>
              </div>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
