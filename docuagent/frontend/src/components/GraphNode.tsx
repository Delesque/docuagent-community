/** A single architecture node.
 *
 *  Inner content is inverse-scaled: the canvas sets `--scale`, this body divides by it,
 *  so physical font size stays constant and a growing box reveals more rows rather than
 *  magnifying the same rows. Text therefore always rasterizes at native resolution,
 *  which is what removes the blur that normally makes a zoom transition look cheap.
 *
 *  The border is a pre-baked bitmap, not an SVG filter. See borderBaker.ts for why.
 */

import { memo, useMemo, useRef } from "react";
import { bakeBorder, variantFor } from "../graph/borderBaker";
import { NODE_STATUS_LABELS, type GraphModule, type NodeStatus } from "../graph/types";
import { codeOpacity, signatureOpacity, tailLineCount } from "../graph/zoom";
import { isConversationNode } from "../graph/conversationNode";
import type { NarrationLine } from "../graph/narration";
import type { LaidOutNode } from "../graph/layout";
import type { SnapCandidate, SnapResult } from "../graph/snap";

interface GraphNodeProps {
  module: GraphModule;
  layout: LaidOutNode;
  /** Absent for the conversation node, which is never generated and so has no
   *  generation status. */
  status?: NodeStatus;
  scale: number;
  selected: boolean;
  hovered: boolean;
  inWindowBar: boolean;
  /** True when the code-intel projection could not resolve this module's declared
   *  path to indexed source (stale / drift). Adds a purple marker — the diagram
   *  only projects this aggregate signal, it never paints symbols as nodes. */
  codeStale?: boolean;
  /** True when an upstream contract changed and this module is downstream of it
   *  (impact propagation). Amber marker — re-adaptation is advised, not drift. */
  adaptPending?: boolean;
  /** Code-intel surface counts for this module: public API symbols and registry
   *  contracts. Rendered as one quiet mono line; absent when unknown. */
  surfaceCounts?: { api: number; contracts: number } | null;
  /** Provenance badge for this module: anchored confirmed facts and pending
   *  guesses. Pending > 0 nudges the user toward the 来源确认 ledger. */
  provenanceBadge?: { confirmed: number; pending: number } | null;
  /** User-facing local usage: semantic context cache, provider cache and tokens. */
  usage?: {
    contextHitRate: number | null;
    serverHitRate: number | null;
    totalTokens: number;
  } | null;
  /** True while a contract-impact hover highlights another propagation path:
   *  this module is temporarily dimmed, not unmounted, so layout stays stable. */
  dimmed?: boolean;
  /** True when the user placed this node by hand, so the layout engine no longer moves
   *  it. Shown as a marker: an unexplained node that ignores the layout is confusing. */
  pinned?: boolean;
  /** Stage 5 fills this from the per-node ring buffer. Empty until then. */
  tail?: string[];
  progress?: { file: string; added: number; removed: number } | null;
  /** Entrance progress, 0 to 1. 1 means fully arrived. */
  emergence?: number;
  /** Unresolved notes/questions attached to this node. */
  noteCount?: number;
  /** Latest thing said, for the conversation node only. It is the one node whose content
   *  changes while the user is looking elsewhere, so it keeps a line of text at scales
   *  where every other node is just a dot and a name. */
  narration?: NarrationLine | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
  onActivate: (id: string) => void;
  /** Long-press without dragging toggles the node in the window bar. */
  onLongPress?: (id: string) => void;
  /** Live drag position in graph coordinates. Fires on every pointer move so the node
   *  tracks the cursor; the canvas holds the value and persists it on release. */
  onDrag?: (id: string, x: number, y: number) => void;
  /** Drag finished at these coordinates. Separate from `onDrag` so the pin and the
   *  debounced write happen once, not on every frame of the gesture. */
  onDragEnd?: (id: string, x: number, y: number) => void;
  /** Shift-drag straight-edge snapping. Returns the snapped position and all guides. */
  onSnap?: (id: string, x: number, y: number) => SnapResult;
  /** Report the guides currently under the pointer so the canvas can draw them. */
  onSnapGuides?: (id: string | null, guides: SnapCandidate[]) => void;
}

/** Pointer travel, in screen pixels, before a press becomes a drag.
 *
 *  Without a threshold every click would register as a one-pixel drag and pin the node,
 *  so selecting a node would quietly freeze its position. 4px is below what a deliberate
 *  drag produces and above normal jitter while clicking.
 */
const DRAG_THRESHOLD = 4;
const LONG_PRESS_MS = 450;

const STATUS_COLORS: Record<NodeStatus, string> = {
  pending: "#2FBF70",
  queued: "#65A67F",
  running: "#6CFFA8",
  review: "#FFC857",
  applied: "#53E3C7",
  verified: "#6CFFA8",
  done: "#53E3C7",
  failed: "#FF5C63",
  blocked: "#FFC857",
  stopped: "#9FE3BB",
  skipped: "#65A67F",
  stale: "#B18CFF",
};

function formatTokens(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}m`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`;
  return String(value);
}

export const GraphNode = memo(function GraphNode({
  module,
  layout,
  status,
  scale,
  selected,
  hovered,
  inWindowBar,
  codeStale = false,
  adaptPending = false,
  surfaceCounts = null,
  provenanceBadge = null,
  usage = null,
  dimmed = false,
  pinned = false,
  tail = [],
  progress = null,
  emergence = 1,
  noteCount = 0,
  narration = null,
  onSelect,
  onHover,
  onActivate,
  onLongPress,
  onDrag,
  onDragEnd,
  onSnap,
  onSnapGuides,
}: GraphNodeProps) {
  // Held in a ref, not state: a drag updates on every pointer move, and re-rendering
  // this component to store the gesture's own bookkeeping would fight the movement it
  // exists to track. The visible position comes from `layout`, which the canvas updates.
  const drag = useRef<{
    pointerId: number;
    startX: number;
    startY: number;
    originX: number;
    originY: number;
    active: boolean;
  } | null>(null);
  const longPressTimer = useRef<number | null>(null);

  const clearLongPress = (): void => {
    if (longPressTimer.current !== null) {
      window.clearTimeout(longPressTimer.current);
      longPressTimer.current = null;
    }
  };

  const border = useMemo(
    () =>
      bakeBorder({
        width: layout.width,
        height: layout.height,
        variant: variantFor(module.id),
        color: selected ? "rgba(255, 92, 99, 0.8)" : "rgba(108, 255, 168, 0.5)",
      }),
    [layout.width, layout.height, module.id, selected],
  );

  const lines = tailLineCount(scale);
  const codeAlpha = codeOpacity(scale);
  const signatureAlpha = signatureOpacity(scale);
  const visibleTail = lines > 0 ? tail.slice(-lines) : [];

  // The conversation has no file, no status dot, and no code tail: it is the surface the
  // graph was designed on, not a module that gets generated.
  const isConversation = isConversationNode(module.id);

  // Determine verification status for visual indicator
  const hasVerificationError = status === "failed";
  const isVerified = status === "verified";

  // Emergence rises slightly rather than scaling: scaling would need the border bitmap
  // rebaked at every intermediate size, which is the cost the baker exists to avoid.
  const arriving = emergence < 1;
  const eased = arriving ? 1 - (1 - emergence) ** 3 : 1;

  return (
    <div
      className="absolute select-none transition-opacity duration-150"
      style={{
        left: layout.x,
        top: layout.y,
        width: layout.width,
        height: layout.height,
        // Inverse scale lives on the inner body, so the box itself is untransformed
        // here and the compositor can cache the baked border bitmap.
        ["--inv" as string]: 1 / scale,
        opacity: dimmed ? 0.12 : undefined,
        ...(arriving
          ? {
              opacity: dimmed ? Math.min(eased, 0.12) : eased,
              transform: `translateY(${(1 - eased) * 14}px)`,
            }
          : null),
      }}
      data-node-id={module.id}
    >
      <div
        role="button"
        tabIndex={-1}
        aria-label={
          status
            ? `${module.name}，${NODE_STATUS_LABELS[status]}`
            : narration
              ? `${module.name}，${narration.pending ? "思考中" : "最新"}：${narration.text}`
              : module.name
        }
        onPointerEnter={() => onHover(module.id)}
        onPointerLeave={() => {
          onHover(null);
          clearLongPress();
        }}
        onPointerDown={(event) => {
          // Stopping propagation is what keeps a node drag from also panning the canvas:
          // the viewport's own pointerdown starts a pan and clears the selection.
          event.stopPropagation();
          onSelect(module.id);
          onSnapGuides?.(null, []);
          longPressTimer.current = window.setTimeout(() => {
            if (!drag.current?.active) onLongPress?.(module.id);
          }, LONG_PRESS_MS);
          if (event.button !== 0 || !onDrag) return;
          drag.current = {
            pointerId: event.pointerId,
            startX: event.clientX,
            startY: event.clientY,
            originX: layout.x,
            originY: layout.y,
            active: false,
          };
          // Capture on the element, so the gesture survives the cursor outrunning the
          // node — which it will, since the node moves only as fast as React re-renders.
          event.currentTarget.setPointerCapture?.(event.pointerId);
        }}
        onPointerMove={(event) => {
          const gesture = drag.current;
          if (!gesture || gesture.pointerId !== event.pointerId || !onDrag) return;
          const dx = event.clientX - gesture.startX;
          const dy = event.clientY - gesture.startY;
          if (!gesture.active && Math.hypot(dx, dy) < DRAG_THRESHOLD) return;
          gesture.active = true;
          clearLongPress();
          // Screen delta over camera scale: at 2x zoom the cursor travels twice as far
          // as the node should, and skipping this makes dragging feel like it slips.
          const rawX = gesture.originX + dx / scale;
          const rawY = gesture.originY + dy / scale;
          if (event.shiftKey && onSnap) {
            const snapped = onSnap(module.id, rawX, rawY);
            onDrag(module.id, snapped.x, snapped.y);
            onSnapGuides?.(module.id, snapped.candidates);
          } else {
            onDrag(module.id, rawX, rawY);
            onSnapGuides?.(module.id, []);
          }
        }}
        onPointerUp={(event) => {
          const gesture = drag.current;
          drag.current = null;
          clearLongPress();
          if (!gesture || gesture.pointerId !== event.pointerId) return;
          event.currentTarget.releasePointerCapture?.(event.pointerId);
          // A press that never crossed the threshold was a click, not a drag: it must not
          // pin the node.
          if (!gesture.active) return;
          const dx = event.clientX - gesture.startX;
          const dy = event.clientY - gesture.startY;
          const rawX = gesture.originX + dx / scale;
          const rawY = gesture.originY + dy / scale;
          const final = event.shiftKey && onSnap
            ? onSnap(module.id, rawX, rawY)
            : { x: rawX, y: rawY, snapped: false, edgeId: null, candidates: [] as SnapCandidate[] };
          onDragEnd?.(module.id, final.x, final.y);
          onSnapGuides?.(null, []);
        }}
        onPointerCancel={() => {
          drag.current = null;
          clearLongPress();
          onSnapGuides?.(null, []);
        }}
        onDoubleClick={(event) => {
          event.stopPropagation();
          onActivate(module.id);
        }}
        className="relative h-full w-full overflow-hidden bg-paper-float/95 transition-shadow duration-150 ease-ui"
        style={{
          cursor: onDrag ? "grab" : "pointer",
          backgroundImage: `url(${border})`,
          backgroundSize: "100% 100%",
          backgroundRepeat: "no-repeat",
          borderRadius: "1px",
          boxShadow: hovered
            ? "inset 0 0 0 1px rgba(108,255,168,0.3), 0 6px 22px rgba(0,0,0,0.42)"
            : "inset 0 0 0 1px rgba(108,255,168,0.16)",
        }}
      >
        {scale < 0.8 && !isConversation ? (
          <div className="absolute inset-x-0 top-0 z-10 border-b border-ink/40 bg-paper-raise/95 px-2 py-1 text-center font-mono text-[12px] uppercase tracking-[0.12em] text-chalk">
            {module.name}
          </div>
        ) : null}
        <div
          className="absolute left-0 top-0 origin-top-left"
          style={{
            transform: "scale(var(--inv))",
            width: `calc(${layout.width}px / var(--inv))`,
            height: `calc(${layout.height}px / var(--inv))`,
          }}
        >
          <div className="flex h-full flex-col gap-1 p-3">
            <div className="flex items-center gap-2">
              {status ? (
                <span
                  aria-hidden
                  className={`h-2 w-2 shrink-0 rounded-full ${
                    status === "running" ? "animate-pulse" : ""
                  }`}
                  style={{ backgroundColor: STATUS_COLORS[status] }}
                />
              ) : null}
              <strong className="truncate font-body text-[12px] font-semibold text-chalk">
                {module.name}
              </strong>
              {inWindowBar ? (
                <span aria-hidden className="shrink-0 text-[9px] text-vermilion">
                  ●
                </span>
              ) : null}
              {noteCount > 0 ? (
                <span
                  className="shrink-0 rounded bg-vermilion/15 px-1 font-mono text-[8px] text-vermilion"
                  title={`${noteCount} 条未完成备注`}
                >
                  {noteCount}
                </span>
              ) : null}
              {/* Verification status indicators */}
              {isVerified && scale >= 0.5 ? (
                <span
                  className="shrink-0 text-[10px] text-emerald"
                  title="验证通过"
                  aria-label="验证通过"
                >
                  ✓
                </span>
              ) : null}
              {hasVerificationError && scale >= 0.5 ? (
                <span
                  className="shrink-0 text-[10px] text-vermilion"
                  title="验证失败（双击查看详情）"
                  aria-label="验证失败"
                >
                  ✗
                </span>
              ) : null}
              {/* A hand-placed node looks identical to a computed one otherwise, so there
                  is no way to tell why one node stopped following the layout. */}
              {pinned ? (
                <span className="shrink-0 text-[9px] text-ink" title="已手动摆放（R 复位）">
                  ⌖
                </span>
              ) : null}
              {codeStale ? (
                <span
                  className="shrink-0 rounded bg-chalk-faint/15 px-1 font-mono text-[8px] text-chalk-faint"
                  title="代码索引无法解析该模块声明路径（可能已重命名/移动，代码与架构图漂移）"
                  aria-label="代码漂移"
                >
                  ↻
                </span>
              ) : null}
              {adaptPending && scale >= 0.5 ? (
                <span
                  className="shrink-0 rounded bg-[#FFC857]/20 px-1 font-mono text-[8px] text-[#B8860B]"
                  title="上游接口已变更，该模块需要重新适配（见契约目录节点的过期条目）"
                  aria-label="待适配"
                >
                  待适配
                </span>
              ) : null}
            </div>

            {/* The conversation keeps full opacity across the whole range.
                `signatureOpacity` fades a module's brief out because streamed code
                fades in to replace it — the conversation has no code, so the same fade
                would leave it blank for no reason. At the reveal camera's resting scale
                it measured 0.36, which is illegible exactly when the user is looking at
                the graph to see whether the agent is still working. */}
            {scale >= 1.2 && !isConversation ? (
              <div className="min-h-0 flex-1 overflow-hidden rounded-none border border-ink/30 bg-paper/80 p-2 font-mono">
                <div className="flex items-center gap-1.5 text-[9px] text-chalk-faint">
                  <span className="text-ink">$</span>
                  <span className="truncate">{module.id}</span>
                  {surfaceCounts ? (
                    <span
                      className="ml-auto shrink-0 tabular-nums"
                      title="公开 API 符号数 · 契约条目数（来自代码索引与契约注册表）"
                    >
                      API {surfaceCounts.api} · 契约 {surfaceCounts.contracts}
                    </span>
                  ) : provenanceBadge ? (
                    <span className="ml-auto shrink-0" />
                  ) : null}
                  {provenanceBadge ? (
                    <span
                      className={`shrink-0 tabular-nums ${provenanceBadge.pending > 0 ? "text-[#FFC857]" : "text-[#53E3C7]"}`}
                      title={`来源条目：已确认 ${provenanceBadge.confirmed} · 待确认 ${provenanceBadge.pending}${provenanceBadge.pending > 0 ? "（在来源确认面板逐条处理）" : ""}`}
                    >
                      ✓{provenanceBadge.confirmed}
                      {provenanceBadge.pending > 0 ? ` ?${provenanceBadge.pending}` : ""}
                    </span>
                  ) : null}
                </div>
                <p className="mt-1 line-clamp-2 text-[9.5px] leading-snug text-chalk-dim">
                  {module.responsibility || module.brief}
                </p>
                {usage ? (
                  <p className="mt-1 truncate font-mono text-[8.5px] text-chalk-faint" title="语义缓存 · 模型缓存 · 消耗 tokens">
                    ctx {usage.contextHitRate === null ? "—" : Math.round(usage.contextHitRate * 100) + "%"}
                    {" · "}llm {usage.serverHitRate === null ? "—" : Math.round(usage.serverHitRate * 100) + "%"}
                    {" · "}{formatTokens(usage.totalTokens)} tok
                  </p>
                ) : null}
              </div>
            ) : isConversation || signatureAlpha > 0 ? (
              <div
                style={{ opacity: isConversation ? 1 : signatureAlpha }}
                className="min-h-0"
              >
                <p className="line-clamp-2 font-body text-[10.5px] leading-snug text-chalk-dim">
                  {module.brief || module.responsibility}
                </p>
                {usage && scale >= 0.8 && !isConversation ? (
                  <p
                    className="mt-1 truncate font-mono text-[8px] text-chalk-faint"
                    title="语义缓存 · 模型缓存 · 消耗 tokens"
                  >
                    ctx {usage.contextHitRate === null ? "—" : Math.round(usage.contextHitRate * 100) + "%"}
                    {" · "}llm {usage.serverHitRate === null ? "—" : Math.round(usage.serverHitRate * 100) + "%"}
                    {" · "}{formatTokens(usage.totalTokens)} tok
                  </p>
                ) : null}
                {progress ? (
                  <div className="mt-1 flex items-center gap-2 font-mono text-[9px] text-chalk-faint">
                    <span className="truncate">{progress.file}</span>
                    <span className="text-ink">+{progress.added}</span>
                    <span className="text-vermilion">-{progress.removed}</span>
                  </div>
                ) : isConversation ? (
                  // No path line: "路径待定" would promise a file that is never coming.
                  // The latest line takes its place, so zooming out during a long
                  // interview does not hide the fact that the agent is still talking.
                  <div className="mt-1 flex items-baseline gap-1.5">
                    {narration ? (
                      <>
                        {narration.pending ? (
                          <span
                            aria-hidden
                            className="shrink-0 animate-pulse font-mono text-[9px] text-ink"
                          >
                            ◌
                          </span>
                        ) : null}
                        <span
                          className={`truncate font-body text-[9.5px] ${
                            narration.role === "user" ? "text-chalk-faint" : "text-chalk-dim"
                          }`}
                        >
                          {narration.text}
                        </span>
                      </>
                    ) : (
                      <span className="truncate font-mono text-[9px] text-chalk-faint">
                        放大进入对话
                      </span>
                    )}
                  </div>
                ) : null}
              </div>
            ) : null}

            {codeAlpha > 0 && visibleTail.length > 0 ? (
              <pre
                aria-hidden
                style={{ opacity: codeAlpha }}
                className="m-0 min-h-0 flex-1 overflow-hidden whitespace-pre font-mono text-[9.5px] leading-[1.35] text-chalk-dim"
              >
                {visibleTail.join("\n")}
              </pre>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
});
