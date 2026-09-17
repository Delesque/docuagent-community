import { useCallback, useEffect, useState } from "react";
import {
  fetchContextCache,
  fetchTokenUsage,
  type ContextCacheSummary,
  type TokenUsageSummary,
} from "../api";

const BLOCK_LABELS: Record<string, { label: string; layer: string }> = {
  user_profile: { label: "用户画像", layer: "三层记忆" },
  standards: { label: "项目规范", layer: "三层记忆" },
  recipes: { label: "项目 recipe", layer: "三层记忆" },
  project_overview: { label: "项目概况", layer: "文档树" },
  module_contract: { label: "模块契约", layer: "架构图" },
  unresolved_attachments: { label: "节点备注", layer: "架构图" },
  error_memory: { label: "错误记忆", layer: "工作态" },
  work_log_tail: { label: "工作日志", layer: "工作态" },
};

const FEATURE_LABELS: Record<string, string> = {
  architecture: "架构访谈",
  planning: "任务规划",
  agent: "代码 Agent",
  documentation: "文档维护",
  microtask: "微任务",
  orchestration: "编排",
  triage: "故障分诊",
  memory: "记忆整理",
  discovery: "开源检索",
  other: "其他",
};

function layerColor(layer: string): string {
  if (layer === "三层记忆") return "bg-emerald";
  if (layer === "文档树") return "bg-sky-400";
  if (layer === "架构图") return "bg-amber";
  return "bg-chalk-dim";
}

interface ContextCachePanelProps {
  path: string;
  onClose?: () => void;
}

export function ContextCachePanel({ path, onClose }: ContextCachePanelProps) {
  const [summary, setSummary] = useState<ContextCacheSummary | null>(null);
  const [tokenUsage, setTokenUsage] = useState<TokenUsageSummary | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      setSummary(await fetchContextCache(path));
      setError("");
    } catch (cause) {
      setError((cause as Error).message);
    }
    try {
      setTokenUsage(await fetchTokenUsage(path));
    } catch {
      setTokenUsage(null);
    }
  }, [path]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const overall = summary ? summary.overall_hit_rate : null;
  const tokenModules = new Map(
    (tokenUsage?.modules ?? []).map((module) => [module.module_id ?? "", module]),
  );
  const moduleIds = new Set<string>([
    ...(summary?.modules ?? []).map((module) => module.module_id),
    ...(tokenUsage?.modules ?? []).map((module) => module.module_id ?? ""),
  ]);
  const moduleRows = [...moduleIds]
    .filter(Boolean)
    .map((moduleId) => {
      const semantic = summary?.modules.find((item) => item.module_id === moduleId);
      const tokens = tokenModules.get(moduleId);
      return {
        moduleId,
        name: semantic?.name ?? tokens?.name ?? moduleId,
        semanticRate: semantic?.overall_hit_rate ?? null,
        modelRate: tokens?.server_hit_rate ?? null,
        totalTokens: tokens?.total_tokens ?? 0,
        calls: tokens?.calls ?? 0,
      };
    })
    .sort((a, b) => b.totalTokens - a.totalTokens || a.name.localeCompare(b.name));

  return (
    <div className="rounded-none border border-ink/40 bg-paper/50 p-3 font-mono">
      <div className="flex items-center justify-between border-b border-ink/30 pb-2">
        <span className="text-[11px] text-chalk-dim">🧠 上下文缓存命中</span>
        {onClose ? (
          <button
            type="button"
            onClick={onClose}
            className="rounded-none border border-ink/50 px-2 py-0.5 text-[10px] text-chalk-faint hover:bg-ink/10 hover:text-chalk"
          >
            ✕
          </button>
        ) : null}
      </div>

      {error ? (
        <p className="mt-2 text-[10px] text-vermilion">{error}</p>
      ) : null}

      {summary ? (
        <div className="mt-2 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div className="border border-ink/30 p-2">
              <p className="text-[9px] uppercase tracking-[0.1em] text-chalk-faint">
                服务端缓存命中（真实）
              </p>
              <p className="text-[20px] text-emerald">
                {tokenUsage === null || tokenUsage.server_hit_rate === null
                  ? "—"
                  : Math.round(tokenUsage.server_hit_rate * 100) + "%"}
              </p>
              <p className="text-[9px] text-chalk-faint">
                {tokenUsage
                  ? tokenUsage.cache_hit_tokens + " hit / " + tokenUsage.cache_miss_tokens + " miss tokens"
                  : "暂无"}
              </p>
            </div>
            <div className="border border-ink/30 p-2">
              <p className="text-[9px] uppercase tracking-[0.1em] text-chalk-faint">
                语义块命中（代理）
              </p>
              <p className="text-[20px] text-chalk">
                {overall === null ? "—" : Math.round(overall * 100) + "%"}
              </p>
              <p className="text-[9px] text-chalk-faint">
                {summary.total_hits} 命中 / {summary.total_misses} 未命中
              </p>
            </div>
          </div>

          {tokenUsage ? (
            <p className="text-[9px] text-chalk-faint">
              当前项目累计 {tokenUsage.calls} 次调用 · {tokenUsage.total_tokens} tokens
            </p>
          ) : null}

          <div className="space-y-1">
            {summary.blocks.map((block) => {
              const meta = BLOCK_LABELS[block.key] ?? { label: block.key, layer: "" };
              const rate = block.hit_rate;
              const pct = rate === null ? 0 : Math.round(rate * 100);
              return (
                <div key={block.key} className="flex items-center gap-2">
                  <span className="w-24 shrink-0 text-[10px] text-chalk-dim">
                    {meta.label}
                  </span>
                  <span className="w-12 shrink-0 text-[9px] text-chalk-faint">
                    {meta.layer}
                  </span>
                  <div className="h-2 min-w-0 flex-1 overflow-hidden bg-ink/20">
                    <div
                      className={"h-full " + layerColor(meta.layer)}
                      style={{ width: pct + "%" }}
                    />
                  </div>
                  <span className="w-10 shrink-0 text-right text-[10px] text-chalk-faint">
                    {rate === null ? "—" : pct + "%"}
                  </span>
                </div>
              );
            })}
          </div>

          <div className="border-t border-ink/30 pt-2">
            <div className="mb-1.5 flex items-center justify-between text-[9px] uppercase tracking-[0.08em] text-chalk-faint">
              <span>模块用量</span>
              <span>缓存 / tokens</span>
            </div>
            {moduleRows.length > 0 ? (
              <div className="space-y-1">
                {moduleRows.map((module) => (
                  <div key={module.moduleId} className="grid grid-cols-[minmax(0,1fr)_66px_66px_76px] items-center gap-2">
                    <span className="truncate text-[10px] text-chalk-dim" title={module.moduleId}>
                      {module.name}
                    </span>
                    <span className="text-right text-[9px] text-emerald" title="语义上下文缓存命中率">
                      ctx {module.semanticRate === null ? "—" : Math.round(module.semanticRate * 100) + "%"}
                    </span>
                    <span className="text-right text-[9px] text-sky-300" title="模型服务端 prompt cache 命中率">
                      llm {module.modelRate === null ? "—" : Math.round(module.modelRate * 100) + "%"}
                    </span>
                    <span className="text-right text-[9px] tabular-nums text-chalk-faint" title={`${module.calls} 次调用`}>
                      {module.totalTokens.toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-[9px] text-chalk-faint">还没有模块级模型用量。</p>
            )}
          </div>

          {tokenUsage && tokenUsage.features.length > 0 ? (
            <div className="border-t border-ink/30 pt-2">
              <div className="mb-1.5 flex items-center justify-between text-[9px] uppercase tracking-[0.08em] text-chalk-faint">
                <span>功能用量</span>
                <span>模型缓存 / tokens</span>
              </div>
              <div className="space-y-1">
                {tokenUsage.features.map((feature) => (
                  <div key={feature.feature} className="grid grid-cols-[minmax(0,1fr)_72px_76px] items-center gap-2">
                    <span className="truncate text-[10px] text-chalk-dim">
                      {FEATURE_LABELS[feature.feature ?? ""] ?? feature.feature}
                    </span>
                    <span className="text-right text-[9px] text-sky-300">
                      {feature.server_hit_rate === null ? "—" : Math.round(feature.server_hit_rate * 100) + "%"}
                    </span>
                    <span className="text-right text-[9px] tabular-nums text-chalk-faint">
                      {feature.total_tokens.toLocaleString()}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          <button
            type="button"
            onClick={() => void refresh()}
            className="mt-1 w-full rounded-none border border-ink/50 px-2 py-1 text-[10px] text-chalk-faint hover:bg-ink/10 hover:text-chalk"
          >
            刷新
          </button>
        </div>
      ) : (
        <p className="mt-2 text-[10px] text-chalk-faint">暂无缓存统计（还没有子 Agent 生成过）。</p>
      )}
    </div>
  );
}
