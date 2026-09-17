/** Contract-Registry projection overlay (§3 step3, with §3 step3 "4-级语义缩放").

 *  Renders the typed Contract Registry as a *collapsed cluster* hanging off the
 *  owning architecture module by default — never as first-class graph nodes, so the
 *  diagram stays about modules and their dependencies, with contracts as a detail
 *  layer you expand on demand. Aggregate edges (owns / depends_on / uses) are drawn
 *  only when the overlay is expanded past the cluster level.
 *
 *  §3 step3 semantic zoom: the detail level is driven by the camera scale, so the
 *  same cluster progressively reveals more as you zoom in:
 *    cluster → row → field → symbol
 *  The manual "契约" toggle is the master collapse: collapsed forces `cluster`,
 *  expanded lets the zoom refine row/field/symbol (you never auto-collapse while
 *  the user explicitly asked to see contracts).
 *
 *  Positions are in graph coordinates (the same space `layout.nodes` uses); the
 *  parent canvas applies the camera transform, so this layer scales with everything
 *  else. The overlay is pure presentation: no layout, no emergence, no focus.
 */

import { useMemo } from "react";
import type { RegistryEdge, RegistryNode, RegistryType } from "../codeintel/api";

export const REGISTRY_TYPE_COLORS: Record<RegistryType, string> = {
  public_api: "#9fd0ff",
  data_schema: "#53E3C7",
  commands_events: "#FFC857",
  config_policy: "#B18CFF",
  shared_kernel: "#6CFFA8",
  vocabulary: "#FFA0C0",
};

export const REGISTRY_TYPE_LABELS: Record<RegistryType, string> = {
  public_api: "公开接口",
  data_schema: "数据模型",
  commands_events: "命令事件",
  config_policy: "配置策略",
  shared_kernel: "共享内核",
  vocabulary: "词汇表",
};

/** Four-level semantic zoom for the registry overlay. */
export type RegistryZoomLevel = "cluster" | "row" | "field" | "symbol";

// Camera-scale thresholds (the graph scale lives in [0.3, 2.0]; see graph/zoom.ts).
const ZOOM_ROW = 0.6;
const ZOOM_FIELD = 1.0;
const ZOOM_SYMBOL = 1.5;

const LEVEL_RANK: Record<RegistryZoomLevel, number> = {
  cluster: 0,
  row: 1,
  field: 2,
  symbol: 3,
};

/** Map a raw camera scale to the registry semantic-zoom level (before the
 *  manual-collapse override is applied). Pure + unit-testable. */
export function registryZoomLevel(scale: number): RegistryZoomLevel {
  if (scale <= ZOOM_ROW) return "cluster";
  if (scale <= ZOOM_FIELD) return "row";
  if (scale <= ZOOM_SYMBOL) return "field";
  return "symbol";
}

/** Final level after the manual toggle: collapsed forces `cluster`; expanded
 *  never drops below `row` even when zoomed out. Pure + unit-testable. */
export function resolveRegistryLevel(
  expanded: boolean,
  scale: number,
): RegistryZoomLevel {
  const z = registryZoomLevel(scale);
  if (!expanded) return "cluster";
  return LEVEL_RANK[z] >= LEVEL_RANK.row ? z : "row";
}

interface LevelGeom {
  /** Vertical gap between stacked entries of one module's cluster. */
  gap: number;
  /** Card width in graph units. */
  width: number;
}

const LEVEL_GEOM: Record<RegistryZoomLevel, LevelGeom> = {
  cluster: { gap: 0, width: 0 },
  row: { gap: 24, width: 132 },
  field: { gap: 34, width: 188 },
  symbol: { gap: 52, width: 224 },
};

const STATUS_STYLE: Record<string, { label: string; color: string }> = {
  active: { label: "active", color: "#53E3C7" },
  stale: { label: "stale", color: "#FFC857" },
  proposed: { label: "proposed", color: "#FFC857" },
  planned: { label: "planned", color: "#9fd0ff" },
  deprecated: { label: "deprecated", color: "#8a93a3" },
  orphan: { label: "orphan", color: "#FF6B6B" },
  unregistered: { label: "unreg", color: "#FF6B6B" },
};

export interface ModuleBox {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface RegistryOverlayProps {
  nodes: RegistryNode[];
  edges: RegistryEdge[];
  /** Graph-coordinate positions of the owning architecture modules, keyed by id. */
  moduleBoxes: Record<string, ModuleBox>;
  /** Collapsed shows only a count badge per module; expanded reveals entries whose
   *  detail level is driven by `zoomScale` (row / field / symbol). */
  expanded: boolean;
  /** Current camera scale; selects the semantic-zoom level when expanded. */
  zoomScale: number;
  /** Registry types currently enabled by the filter. Empty set = nothing shown. */
  typeFilter: Set<RegistryType>;
  onSelectNode?: (node: RegistryNode) => void;
  /** Hovering a contract entry highlights its downstream impact on the canvas:
   *  the owner module and everything that depends on it stay lit, the rest dims.
   *  Null on leave. Only fired from the expanded detail entries. */
  onContractHover?: (contractId: string | null) => void;
}

const EDGE_STYLE: Record<RegistryEdge["kind"], { stroke: string; dash: string }> = {
  owns: { stroke: "rgba(157, 208, 255, 0.35)", dash: "3 3" },
  depends_on: { stroke: "rgba(108, 255, 168, 0.6)", dash: "" },
  uses: { stroke: "rgba(177, 140, 255, 0.6)", dash: "2 4" },
};

export function RegistryOverlay({
  nodes,
  edges,
  moduleBoxes,
  expanded,
  zoomScale,
  typeFilter,
  onSelectNode,
  onContractHover,
}: RegistryOverlayProps) {
  const level = useMemo(
    () => resolveRegistryLevel(expanded, zoomScale),
    [expanded, zoomScale],
  );

  const visibleNodes = useMemo(
    () => nodes.filter((n) => typeFilter.has(n.type)),
    [nodes, typeFilter],
  );
  const visibleEdges = useMemo(
    () => edges.filter((e) => typeFilter.has(edgeTypeOf(e, visibleNodes))),
    [edges, visibleNodes, typeFilter],
  );

  // Group visible nodes by owning module so we can lay each module's cluster out.
  const clusters = useMemo(() => {
    const byOwner = new Map<string, RegistryNode[]>();
    for (const n of visibleNodes) {
      if (!n.owner) continue;
      const list = byOwner.get(n.owner) ?? [];
      list.push(n);
      byOwner.set(n.owner, list);
    }
    return byOwner;
  }, [visibleNodes]);

  // Deterministic placement: a vertical column just right of the module box. `x`/`y`
  // are the box top-left, so the column sits at right edge + offset; the card height
  // grows with the semantic level (more detail needs more room).
  const chipPos = useMemo(() => {
    const pos = new Map<string, { x: number; y: number; width: number }>();
    const geom = LEVEL_GEOM[level];
    for (const [owner, list] of clusters) {
      const box = moduleBoxes[owner];
      if (!box) continue;
      const colX = box.x + box.width + 30;
      const startY = box.y + 14;
      list.forEach((n, i) => {
        pos.set(n.id, { x: colX, y: startY + i * geom.gap, width: geom.width });
      });
    }
    return pos;
  }, [clusters, moduleBoxes, level]);

  const badgeCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const n of visibleNodes) {
      if (!n.owner) continue;
      counts.set(n.owner, (counts.get(n.owner) ?? 0) + 1);
    }
    return counts;
  }, [visibleNodes]);

  if (visibleNodes.length === 0) return null;

  const showDetail = level !== "cluster";

  return (
    <>
      {/* Aggregate edges (only meaningful when expanded past the cluster level). */}
      {showDetail ? (
        <svg
          className="pointer-events-none absolute left-0 top-0"
          width={1}
          height={1}
          style={{ overflow: "visible" }}
          aria-hidden
        >
          {visibleEdges.map((e, i) => {
            const a = chipPos.get(e.from) ?? centerOf(moduleBoxes[e.from]);
            const b = chipPos.get(e.to) ?? centerOf(moduleBoxes[e.to]);
            if (!a || !b) return null;
            const style = EDGE_STYLE[e.kind];
            return (
              <line
                key={`e-${i}`}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={style.stroke}
                strokeWidth={1}
                strokeDasharray={style.dash || undefined}
              />
            );
          })}
        </svg>
      ) : null}

      {/* Count badges (cluster) or detail entries (row / field / symbol). */}
      {!showDetail
        ? Array.from(badgeCounts.entries()).map(([owner, count]) => {
            const box = moduleBoxes[owner];
            if (!box) return null;
            return (
              <div
                key={`badge-${owner}`}
                className="absolute z-10 rounded-full border border-ink-dim/40 bg-paper-raise/85 px-2 py-0.5 font-mono text-[9px] text-chalk-dim"
                style={{
                  left: box.x + box.width,
                  top: box.y,
                  transform: "translate(0, -100%)",
                }}
                title={`契约目录：${count} 条（放大查看明细）`}
              >
                ⟐ {count}
              </div>
            );
          })
        : visibleNodes.map((n) => {
            const p = chipPos.get(n.id);
            if (!p) return null;
            const color = REGISTRY_TYPE_COLORS[n.type];
            const dim =
              n.status === "deprecated" ||
              n.status === "orphan" ||
              n.status === "unregistered";
            const status =
              STATUS_STYLE[n.status] ?? { label: n.status, color: "#8a93a3" };
            const showMeta = level === "field" || level === "symbol";
            const showFile = level === "symbol";
            return (
              <button
                key={n.id}
                type="button"
                className="absolute z-20 flex flex-col items-start gap-0.5 rounded border px-1.5 py-0.5 text-left font-mono leading-none"
                style={{
                  left: p.x,
                  top: p.y,
                  width: p.width,
                  transform: "translate(0, -50%)",
                  borderColor: color,
                  background: "rgba(17, 22, 29, 0.92)",
                  color: dim ? "#8a93a3" : "#e8eef5",
                  opacity: dim ? 0.65 : 1,
                  textDecoration:
                    n.status === "deprecated" ? "line-through" : "none",
                  cursor: "pointer",
                }}
                title={`${REGISTRY_TYPE_LABELS[n.type]} · ${n.status}${
                  n.file ? ` · ${n.file}` : ""
                }`}
                onClick={() => onSelectNode?.(n)}
                onMouseEnter={() => onContractHover?.(n.id)}
                onMouseLeave={() => onContractHover?.(null)}
              >
                <span className="flex w-full items-center gap-1">
                  <span
                    className="inline-block h-1.5 w-1.5 shrink-0 rounded-full"
                    style={{ background: color }}
                  />
                  <span
                    className={
                      level === "symbol"
                        ? "truncate text-[11px] font-semibold"
                        : level === "field"
                          ? "truncate text-[10px] font-semibold"
                          : "truncate text-[9px]"
                    }
                  >
                    {n.name}
                  </span>
                  {n.public ? (
                    <span className="ml-auto shrink-0 rounded-sm bg-white/10 px-1 text-[7px] uppercase text-chalk-dim">
                      pub
                    </span>
                  ) : null}
                </span>
                {showMeta ? (
                  <span className="flex w-full items-center gap-1">
                    <span className="truncate text-[8px] text-chalk-dim">
                      {REGISTRY_TYPE_LABELS[n.type]}
                    </span>
                    <span
                      className="ml-auto shrink-0 rounded-sm px-1 text-[7px]"
                      style={{
                        color: status.color,
                        border: `1px solid ${status.color}`,
                      }}
                    >
                      {status.label}
                    </span>
                  </span>
                ) : null}
                {showFile && n.file ? (
                  <span className="w-full truncate text-[7.5px] text-chalk-faint">
                    {n.file}
                    {n.line ? `:${n.line}` : ""}
                  </span>
                ) : null}
              </button>
            );
          })}
    </>
  );
}

/** Map an edge to a registry type so the type filter can hide the whole edge. */
function edgeTypeOf(e: RegistryEdge, nodes: RegistryNode[]): RegistryType {
  const src = nodes.find((n) => n.id === e.from);
  if (src) return src.type;
  // depends_on / uses target a module, not a registry node; fall back to the
  // source if it is a module too. Default to public_api so the edge stays visible.
  return "public_api";
}

function centerOf(box: ModuleBox | undefined): { x: number; y: number } | null {
  if (!box) return null;
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}
