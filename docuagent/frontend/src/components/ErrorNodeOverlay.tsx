/** Error-node overlay (P1: unified error nodes on the architecture graph).
 *
 *  Renders one severity-colored badge per owner node that has active errors,
 *  hanging off the module box the same way RegistryOverlay hangs its contract
 *  cluster — errors are never first-class layout nodes, the diagram stays about
 *  modules. Clicking a badge expands a detail card with the error records and
 *  their allowed actions. Owners that do not map onto a graph node (e.g. the
 *  project-level fallback) stack in the top-left corner so the failure is still
 *  visible.
 *
 *  Pure presentation: no data fetching, no retry logic — actions are reported
 *  through `onAction` and handled by the app layer.
 */

import { useState } from "react";
import type { ErrorNode, ErrorNodeSeverity, ErrorNodeSource } from "../api/errorNodes";

export interface ModuleBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

export const ERROR_SEVERITY_COLORS: Record<ErrorNodeSeverity, string> = {
  critical: "#FF6B6B",
  warning: "#FFC857",
  info: "#9fd0ff",
};

export const ERROR_SOURCE_LABELS: Record<ErrorNodeSource, string> = {
  generation: "生成",
  verification: "验证",
  documentation: "文档",
  handoff: "交接",
  system: "系统",
};

/** Only active errors belong on the graph; resolved ones are history. */
export function activeErrorNodes(nodes: ErrorNode[]): ErrorNode[] {
  return nodes.filter((node) => node.status === "active");
}

/** Group active errors by owner, preserving first-seen order for stable output. */
export function groupActiveErrorsByOwner(nodes: ErrorNode[]): Map<string, ErrorNode[]> {
  const byOwner = new Map<string, ErrorNode[]>();
  for (const node of activeErrorNodes(nodes)) {
    const list = byOwner.get(node.owner_node_id) ?? [];
    list.push(node);
    byOwner.set(node.owner_node_id, list);
  }
  return byOwner;
}

/** Where the badge for one owner sits, in graph coordinates.
 *  Anchored owners get their module box's top-left corner; unknown owners stack
 *  down the left edge so a project-level failure is still visible. Pure. */
export function errorBadgeOrigin(
  owner: string,
  moduleBoxes: Record<string, ModuleBox>,
  fallbackIndex: number,
): { x: number; y: number; anchored: boolean } {
  const box = moduleBoxes[owner];
  if (box) return { x: box.x, y: box.y, anchored: true };
  return { x: 24, y: 24 + fallbackIndex * 44, anchored: false };
}

interface ErrorNodeOverlayProps {
  nodes: ErrorNode[];
  moduleBoxes: Record<string, ModuleBox>;
  onAction?: (node: ErrorNode, actionId: string) => void;
}

export function ErrorNodeOverlay({ nodes, moduleBoxes, onAction }: ErrorNodeOverlayProps) {
  const [expandedOwner, setExpandedOwner] = useState<string | null>(null);

  const groups = groupActiveErrorsByOwner(nodes);
  if (groups.size === 0) return null;

  let fallbackIndex = 0;

  return (
    <>
      {Array.from(groups.entries()).map(([owner, errors]) => {
        const origin = errorBadgeOrigin(owner, moduleBoxes, fallbackIndex);
        if (!origin.anchored) fallbackIndex += 1;
        const worst = errors.some((e) => e.severity === "critical")
          ? "critical"
          : errors.some((e) => e.severity === "warning")
            ? "warning"
            : "info";
        const color = ERROR_SEVERITY_COLORS[worst];
        const expanded = expandedOwner === owner;
        return (
          <div key={`err-${owner}`} className="absolute z-30" style={{ left: origin.x, top: origin.y }}>
            <button
              type="button"
              aria-expanded={expanded}
              aria-label={`错误节点：${owner}（${errors.length} 条）`}
              className="flex min-h-[44px] items-center gap-1 rounded-full border px-2.5 py-1 font-mono text-[10px] leading-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
              style={{
                transform: origin.anchored ? "translate(-4px, -120%)" : "none",
                borderColor: color,
                background: "rgba(17, 22, 29, 0.94)",
                color,
                cursor: "pointer",
              }}
              title={errors.map((e) => e.title).join("\n")}
              onClick={() => setExpandedOwner(expanded ? null : owner)}
            >
              <span aria-hidden>⚠</span>
              <span>{errors.length}</span>
            </button>
            {expanded ? (
              <div
                className="absolute z-40 flex flex-col gap-2 rounded border p-2 font-mono text-[10px]"
                style={{
                  top: 24,
                  left: 12,
                  width: 264,
                  borderColor: color,
                  background: "rgba(17, 22, 29, 0.96)",
                  color: "#e8eef5",
                }}
                role="region"
                aria-label={`错误详情：${owner}`}
              >
                {errors.map((error) => (
                  <div key={error.id} className="flex flex-col gap-1">
                    <div className="flex items-center gap-1">
                      <span
                        className="rounded-sm px-1 text-[8px]"
                        style={{ color, border: `1px solid ${color}` }}
                      >
                        {ERROR_SOURCE_LABELS[error.source] ?? error.source}
                      </span>
                      <span className="truncate font-semibold">{error.title}</span>
                    </div>
                    <div className="max-h-24 overflow-y-auto whitespace-pre-wrap break-all text-[9px] text-chalk-dim">
                      {error.detail}
                    </div>
                    <div className="text-[8px] text-chalk-faint">
                      重试 {error.retry_count}/{error.max_retries}
                      {error.code_task_id ? ` · 代码任务 ${error.code_task_id}` : ""}
                    </div>
                    <div className="flex flex-wrap gap-1">
                      {error.actions.map((action) => (
                        <button
                          key={action.id}
                          type="button"
                          className="min-h-[44px] rounded border border-white/20 bg-white/5 px-2.5 text-[9px] text-chalk hover:bg-white/15 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white/60"
                          onClick={() => onAction?.(error, action.id)}
                        >
                          {action.label}
                        </button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}
    </>
  );
}
