/** Linear projection of the same graph state the canvas draws spatially.
 *
 *  Not a degraded fallback: every canvas action has an equivalent here, because a
 *  zoomable canvas is hostile to both keyboard and screen reader. Position on the
 *  canvas carries dependency meaning, and absolute-positioned nodes are announced in
 *  model-output order, so that meaning is simply absent in the linear reading. This
 *  view restores it.
 *
 *  It also gives layout tests a stable assertion surface, and answers "which nodes are
 *  stuck" at a glance during parallel generation, where the canvas needs panning.
 */

import { useMemo } from "react";
import {
  EDGE_STYLES,
  NODE_STATUS_LABELS,
  type Architecture,
  type NodeStatus,
} from "../graph/types";
import type { GraphCommand } from "../graph/store";
import { isConversationNode } from "../graph/conversationNode";
import type { NarrationLine } from "../graph/narration";

interface OutlineViewProps {
  architecture: Architecture;
  statuses: Record<string, NodeStatus>;
  selectedId: string | null;
  windowBar: string[];
  pinned: Record<string, boolean>;
  expanded: string[];
  layers: Map<string, number>;
  dispatch: (command: GraphCommand) => void;
  /** Latest conversation line, so this projection reports it too. */
  narration?: NarrationLine | null;
}

export function OutlineView({
  architecture,
  statuses,
  selectedId,
  windowBar,
  pinned,
  expanded,
  layers,
  dispatch,
  narration = null,
}: OutlineViewProps) {
  const grouped = useMemo(() => {
    const groups = new Map<string, { label: string; members: typeof architecture.modules }>();
    const loose: typeof architecture.modules = [];
    for (const group of architecture.groups) {
      groups.set(group.id, { label: group.label, members: [] });
    }
    for (const module of architecture.modules) {
      const key = module.group;
      if (key && groups.has(key)) groups.get(key)!.members.push(module);
      else loose.push(module);
    }
    return { groups, loose };
  }, [architecture]);

  const incoming = useMemo(() => {
    const map = new Map<string, typeof architecture.edges>();
    for (const edge of architecture.edges) {
      if (!map.has(edge.to)) map.set(edge.to, []);
      map.get(edge.to)!.push(edge);
    }
    return map;
  }, [architecture.edges]);

  const outgoing = useMemo(() => {
    const map = new Map<string, typeof architecture.edges>();
    for (const edge of architecture.edges) {
      if (!map.has(edge.from)) map.set(edge.from, []);
      map.get(edge.from)!.push(edge);
    }
    return map;
  }, [architecture.edges]);

  const handleKeyDown = (event: React.KeyboardEvent<HTMLLIElement>, id: string): void => {
    switch (event.key) {
      case "Enter":
        event.preventDefault();
        dispatch({ type: "focus", nodeId: id });
        break;
      case "?":
        event.preventDefault();
        dispatch({ type: "ask", nodeId: id });
        break;
      case "e":
      case "E":
        event.preventDefault();
        dispatch({ type: "editRequirement", nodeId: id });
        break;
      case "d":
      case "D":
        event.preventDefault();
        dispatch({ type: "toggleWindowBar", nodeId: id });
        break;
      case "p":
      case "P":
        event.preventDefault();
        dispatch({ type: "togglePin", nodeId: id });
        break;
      case " ":
        event.preventDefault();
        dispatch({ type: "centerNode", nodeId: id });
        break;
      default:
        break;
    }
  };

  const renderModule = (module: Architecture["modules"][number]) => {
    // Absent for the conversation node: it is never generated, so announcing "待生成"
    // to a screen reader would be wrong rather than merely redundant.
    const status = statuses[module.id];
    const dependencies = outgoing.get(module.id) ?? [];
    const dependents = incoming.get(module.id) ?? [];
    const selected = module.id === selectedId;
    const layerLabel = isConversationNode(module.id)
      ? "对话流"
      : `第 ${(layers.get(module.id) ?? 0) + 1} 层`;
    return (
      <li
        key={module.id}
        tabIndex={0}
        role="treeitem"
        aria-selected={selected}
        aria-label={
          status
            ? `${module.name}，${NODE_STATUS_LABELS[status]}，${layerLabel}`
            : narration && isConversationNode(module.id)
              ? `${module.name}，${layerLabel}，${narration.pending ? "思考中" : "最新"}：${narration.text}`
              : `${module.name}，${layerLabel}`
        }
        onKeyDown={(event) => handleKeyDown(event, module.id)}
        onFocus={() => dispatch({ type: "select", nodeId: module.id })}
        onClick={() => dispatch({ type: "select", nodeId: module.id })}
        className={`cursor-pointer rounded px-2 py-1.5 outline-none transition-colors duration-150 ease-ui focus-visible:ring-1 focus-visible:ring-ink ${
          selected ? "bg-ink-ghost/70" : "hover:bg-paper-float/70"
        }`}
      >
        <div className="flex items-baseline gap-2">
          <span className="truncate font-body text-[12px] text-chalk">{module.name}</span>
          {status ? (
            <span className="ml-auto shrink-0 font-mono text-[9px] text-chalk-faint">
              {NODE_STATUS_LABELS[status]}
            </span>
          ) : null}
          {pinned[module.id] ? (
            <span className="shrink-0 text-[9px] text-ink" title="已钉住">
              ⌖
            </span>
          ) : null}
          {windowBar.includes(module.id) ? (
            <span className="shrink-0 text-[9px] text-vermilion" title="在窗口栏">
              ●
            </span>
          ) : null}
        </div>
        {/* The conversation shows its latest line here too. Parity is structural in this
            view: anything the canvas conveys spatially has to be readable linearly. */}
        {isConversationNode(module.id) && narration ? (
          <p className="mt-0.5 line-clamp-2 font-body text-[10.5px] leading-snug text-chalk-dim">
            {narration.pending ? "思考中：" : ""}
            {narration.text}
          </p>
        ) : (
          <p className="mt-0.5 line-clamp-2 font-body text-[10.5px] leading-snug text-chalk-dim">
            {module.brief || module.responsibility}
          </p>
        )}
        {dependencies.length > 0 || dependents.length > 0 ? (
          <ul className="mt-1 space-y-0.5">
            {dependencies.map((edge) => (
              <li
                key={`out-${edge.to}-${edge.kind}`}
                className="font-mono text-[9px] text-chalk-faint"
              >
                依赖 → {edge.to}（{EDGE_STYLES[edge.kind].label}）
              </li>
            ))}
            {dependents.map((edge) => (
              <li
                key={`in-${edge.from}-${edge.kind}`}
                className="font-mono text-[9px] text-chalk-faint"
              >
                被依赖 ← {edge.from}（{EDGE_STYLES[edge.kind].label}）
              </li>
            ))}
          </ul>
        ) : null}
      </li>
    );
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-baseline justify-between px-3 py-2">
        <h2 className="font-mono text-[10px] uppercase tracking-[0.18em] text-chalk-dim">
          Outline
        </h2>
        <span className="font-mono text-[9px] text-chalk-faint">
          {architecture.modules.length} 节点
        </span>
      </div>
      <p className="px-3 pb-2 font-mono text-[9px] leading-relaxed text-chalk-faint">
        Enter 进入 · ? 提问 · E 编辑 · D 窗口栏 · P 钉住 · 空格 居中
      </p>
      <ul role="tree" aria-label="架构节点大纲" className="flex-1 space-y-1 overflow-y-auto px-2 pb-3">
        {[...grouped.groups.entries()].map(([groupId, group]) => {
          const open = expanded.includes(groupId) || expanded.length === 0;
          return (
            <li key={groupId} role="treeitem" aria-expanded={open}>
              <button
                type="button"
                onClick={() => dispatch({ type: "toggleOutlineGroup", groupId })}
                className="flex w-full items-center gap-1.5 rounded px-2 py-1 text-left font-mono text-[10px] uppercase tracking-widest text-ink outline-none hover:bg-paper-float/60 focus-visible:ring-1 focus-visible:ring-ink"
              >
                <span aria-hidden>{open ? "▾" : "▸"}</span>
                <span className="truncate">{group.label}</span>
                <span className="ml-auto text-chalk-faint">{group.members.length}</span>
              </button>
              {open ? (
                <ul role="group" className="ml-3 space-y-1 border-l border-ink-ghost pl-2">
                  {group.members.map(renderModule)}
                </ul>
              ) : null}
            </li>
          );
        })}
        {grouped.loose.map(renderModule)}
      </ul>
    </div>
  );
}
