/** Spatial projection of the graph store.
 *
 *  Zoom applies `transform` to one container, never layout size, so baked border
 *  bitmaps stay valid across a whole gesture and the compositor does the scaling.
 *  Wheel listeners are attached non-passively because `preventDefault()` on a
 *  ctrl+wheel event is the only way to stop the browser's own page zoom.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { EdgeLayer } from "./EdgeLayer";
import { FocusLayer } from "./FocusLayer";
import { GraphNode } from "./GraphNode";
import { Minimap } from "./Minimap";
import {
  GraphToolbox,
  GraphToolInput,
  graphToolNeedsInput,
  type GraphTool,
} from "./GraphToolbox";
import { layoutGraph } from "../graph/layout";
import { cullNodes } from "../graph/culling";
import { highlightModulesForContract, reachHighlightSet } from "../graph/impact";
import { parseFocusHash, replaceFocusHash } from "../graph/focusLink";
import {
  edgeProgress,
  emptyEmergence,
  isAnimating,
  nodeProgress,
  observe,
  observeFirst,
  prune,
} from "../graph/emergence";
import {
  beginRetreat,
  beginReveal,
  finishReveal,
  holdElapsed,
  idleReveal,
  lerpCamera,
  retreatProgress,
  revealCamera,
} from "../graph/reveal";
import { CONVERSATION_NODE_ID } from "../graph/conversationNode";
import type { NarrationLine } from "../graph/narration";
import type { GraphCommand } from "../graph/store";
import type { Architecture, Camera, NodeStatus } from "../graph/types";
import {
  RegistryOverlay,
  REGISTRY_TYPE_COLORS,
  REGISTRY_TYPE_LABELS,
  type ModuleBox,
} from "./RegistryOverlay";
import type {
  RegistryEdge,
  RegistryNode,
  RegistryType,
} from "../codeintel/api";
import type { ErrorNode } from "../api/errorNodes";
import { ErrorNodeOverlay } from "./ErrorNodeOverlay";
import { ErrorEdgeIndicators } from "./ErrorEdgeIndicators";
import { snapPosition, type SnapCandidate } from "../graph/snap";
import type { NodeAttachment, NodeAttachments, TaskItem, TaskPlan } from "../api";
import {
  accumulatedIntent,
  bandFor,
  clampScale,
  decideFocus,
  FOCUS_ENTER_SCALE,
  FOCUS_RESTORE_SCALE,
  fitCamera,
  centerOn,
  type IntentSample,
  nodeCoverage,
  pruneIntent,
  scaleStepFor,
  zoomAtPoint,
} from "../graph/zoom";

interface GraphCanvasProps {
  architecture: Architecture;
  camera: Camera;
  statuses: Record<string, NodeStatus>;
  selectedNodeId: string | null;
  selectedEdgeId: string | null;
  focusedId: string | null;
  windowBar: string[];
  pinned: Record<string, boolean>;
  previousPositions: Record<string, { x: number; y: number; pinned: boolean }>;
  reducedMotion: boolean;
  onCamera: (camera: Camera) => void;
  dispatch: (command: GraphCommand) => void;
  onLayout?: (layers: Map<string, number>) => void;
  /** Rendered inside the focus frame when the conversation node is focused. Passed in
   *  rather than imported so the canvas stays unaware of the interview's state. */
  conversationContent?: React.ReactNode;
  /** Resolved node positions, reported so they can be persisted.
   *
   *  Only the canvas knows where nodes actually are — the layout engine runs here. The
   *  previous code persisted `{x: 0, y: 0}` for pinned nodes, which meant pinning one
   *  and reloading teleported it to the origin, and made layout inertia impossible
   *  because there were no real coordinates to blend toward.
   */
  onPositions?: (
    positions: Record<string, { x: number; y: number; pinned: boolean }>,
  ) => void;
  /** Latest line of the conversation, shown on its node at scales where reading the
   *  focus panel is not possible. */
  narration?: NarrationLine | null;
  /** Lets the canvas handle camera commands dispatched from the Outline, which has no
   *  viewport of its own. */
  registerEffect?: (handler: (command: GraphCommand) => void) => () => void;
  /** Takes a camera request that was made while this canvas was unmounted. */
  claimPendingCamera?: () => GraphCommand | null;
  /** Persist the current hand-placed layout immediately. */
  onSaveLayout?: () => void;
  /** Clear all manual placements and return to the automatic layout. */
  onRestoreDefault?: () => void;
  /** Whether the backend has a previous architecture document to restore. */
  canUndo?: boolean;
  /** Restore the most recent architecture edit. */
  onUndo?: () => void;
  /** First appearance of a graph after user confirmation: reveal directly, without
   *  going through the full-screen conversation focus first. */
  revealOnOpen?: boolean;
  /** Called when the user confirms a stale module is fixed. */
  onClearStale?: (moduleId: string) => void;
  tasks?: TaskPlan | null;
  /** Live ring-buffer lines per module, fed by task reasoning events. */
  taskTails?: Record<string, string[]>;
  /** Whether a generation wave is running; drives the focus layer's live output. */
  taskStreaming?: boolean;
  attachments?: NodeAttachments;
  onAddAttachment?: (moduleId: string, type: "note", text: string) => void;
  /** Run the backend verification for the task bound to a module. */
  onVerifyTask?: (taskId: string) => void;
  /** Apply the full task patch for a module's task. */
  onApplyTask?: (taskId: string) => void;
  onResumeTask?: (taskId: string) => void;
  /** Apply selected diff hunks for one patch file. */
  onApplyHunks?: (
    taskId: string,
    file: string,
    hunkIds: number[],
  ) => boolean | Promise<boolean>;
  /** Persist a user edit to one proposed file before applying. */
  onSavePatch?: (
    taskId: string,
    file: string,
    content: string,
    baseAfter: string,
  ) => boolean | Promise<boolean>;
  /** Run a whitelisted shell command in the focused node's terminal. */
  onTerminalExec?: (command: string) => void;
  /** Analyze UI trajectory feedback and dispatch a micro task. */
  onArchiveModule?: (moduleId: string) => void;
  /** Open the code-intel panel for a module (double-click a node). Lets the diagram
   *  drive the editor linkage without the canvas knowing about panels. */
  onOpenModuleCode?: (moduleId: string) => void;
  /** Module ids whose declared path no longer resolves to indexed source (stale
   *  projection from the code-intel backend). */
  codeStaleById?: Record<string, boolean>;
  /** Module ids downstream of a changed contract (impact propagation). These
   *  modules are not broken themselves — their upstream interface moved. */
  adaptPendingById?: Record<string, boolean>;
  /** Per-module code-intel surface counts (public API symbols, registry entries). */
  surfaceCountsById?: Record<string, { api: number; contracts: number }>;
  /** Per-module provenance badge (anchored confirmed / pending claim counts). */
  provenanceBadgeById?: Record<string, { confirmed: number; pending: number }>;
  /** Per-module cache rates and token totals, shown on the node when zoomed in. */
  usageByModule?: Record<
    string,
    {
      contextHitRate: number | null;
      serverHitRate: number | null;
      totalTokens: number;
    }
  >;
  /** §3 step3: typed Contract Registry projection to overlay. When present and not
   *  empty, a "契约" control toggles the collapsed cluster / expanded chips. */
  registryNodes?: RegistryNode[];
  registryEdges?: RegistryEdge[];
  /** Whether the registry overlay is expanded (chips + edges) or collapsed (badge). */
  registryExpanded?: boolean;
  /** Registry types currently shown. Empty set hides the overlay entirely. */
  registryTypeFilter?: Set<RegistryType>;
  onToggleRegistry?: () => void;
  onSetRegistryFilter?: (next: Set<RegistryType>) => void;
  /** P1: unified error nodes. Active errors render as badges next to their owner
   *  module; expanded badges expose the actions the error record allows. */
  errorNodes?: ErrorNode[];
  onErrorNodeAction?: (node: ErrorNode, actionId: string) => void;
}

/** Arrow-key nudge distances for placing a node without a pointer. Shift is the coarse
 *  step, so crossing a wide graph does not take fifty presses. */
const NUDGE_FINE = 8;
const NUDGE_COARSE = 48;
const NUDGE_KEYS = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]);
const TOOL_BUTTON_CLASS =
  "inline-flex h-8 items-center gap-1.5 rounded-md border border-ink-dim/45 bg-paper-raise/90 px-3 font-mono text-[11px] text-chalk-dim transition-colors duration-150 ease-ui hover:border-ink hover:text-chalk focus-visible:ring-1 focus-visible:ring-ink";

export function GraphCanvas({
  architecture,
  camera,
  statuses,
  selectedNodeId,
  selectedEdgeId,
  focusedId,
  windowBar,
  pinned,
  previousPositions,
  reducedMotion,
  onCamera,
  dispatch,
  onLayout,
  conversationContent,
  onPositions,
  narration = null,
  registerEffect,
  claimPendingCamera,
  onSaveLayout,
  onRestoreDefault,
  canUndo = false,
  onUndo,
  revealOnOpen = false,
  onClearStale,
  tasks = null,
  taskTails = {},
  taskStreaming = false,
  attachments = {},
  onAddAttachment,
  onVerifyTask,
  onApplyTask,
  onResumeTask,
  onApplyHunks,
  onSavePatch,
  onTerminalExec,
  onArchiveModule,
  onOpenModuleCode,
  codeStaleById,
  adaptPendingById,
  surfaceCountsById,
  provenanceBadgeById,
  usageByModule,
  registryNodes,
  registryEdges,
  registryExpanded = false,
  registryTypeFilter,
  onToggleRegistry,
  onSetRegistryFilter,
  errorNodes,
  onErrorNodeAction,
}: GraphCanvasProps) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);
  const [hoveredEdge, setHoveredEdge] = useState<string | null>(null);
  /** Contract entry currently hovered in the registry overlay. Drives the
   *  impact highlight: its owner + downstream stay lit, the rest dims. */
  const [hoverContractId, setHoverContractId] = useState<string | null>(null);
  /** Node finder (Ctrl+F / 搜索 button): query + open state. */
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  /** Reach mode: dim everything outside the selected node's dependency reach. */
  const [reachMode, setReachMode] = useState(false);
  const [viewport, setViewport] = useState({ width: 1200, height: 800 });
  const [graphTool, setGraphTool] = useState<GraphTool | null>(null);
  const [toolTarget, setToolTarget] = useState<string | null>(null);
  const [toolDraft, setToolDraft] = useState("");
  const [toolNotice, setToolNotice] = useState<string | null>(null);
  const noticeTimer = useRef<number | null>(null);
  const intent = useRef<IntentSample[]>([]);
  const panning = useRef<{ x: number; y: number; camera: Camera } | null>(null);
  const lastPointer = useRef({ x: 0, y: 0 });

  /** Where nodes were on the last layout pass, so a new node does not reshuffle the
   *  graph and cost the user their spatial memory.
   *
   *  Seeded from persisted positions, then updated after every pass. Held in a ref
   *  because feeding the result back through state would re-run the layout that
   *  produced it. Pins always win: an explicit placement is not something to blend.
   */
  const settled = useRef<Record<string, { x: number; y: number; pinned: boolean }>>({});
  const seeded = useRef(false);
  if (!seeded.current) {
    seeded.current = true;
    for (const [id, position] of Object.entries(previousPositions)) {
      settled.current[id] = { ...position };
    }
  }

  const previous = useMemo(() => {
    const merged: Record<string, { x: number; y: number; pinned: boolean }> = {};
    for (const [id, position] of Object.entries(settled.current)) {
      // `settled` already holds this pass's positions, so pinning a node mid-session
      // finds its current coordinates here without a separate lookup.
      merged[id] = { ...position, pinned: pinned[id] ?? position.pinned };
    }
    return merged;
  }, [architecture, pinned]);

  /** Positions the user is dragging right now, or has dragged this session.
   *
   *  Kept in state rather than a ref because the node has to visibly follow the cursor,
   *  and fed back into `layoutGraph` as pinned `previous` entries so edges re-route to
   *  the new position instead of staying attached to where the node used to be. That is
   *  the whole reason this goes through the layout engine rather than just offsetting the
   *  node's CSS: an edge that does not follow its node is worse than a node that cannot
   *  be moved.
   */
  const [dragged, setDragged] = useState<Record<string, { x: number; y: number }>>({});
  const [snapNodeId, setSnapNodeId] = useState<string | null>(null);
  const [snapGuides, setSnapGuides] = useState<SnapCandidate[]>([]);
  const [savedFlash, setSavedFlash] = useState(false);
  const savedTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (savedTimer.current) window.clearTimeout(savedTimer.current);
      if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    };
  }, []);

  const handleSaveLayout = useCallback(() => {
    onSaveLayout?.();
    setSavedFlash(true);
    if (savedTimer.current) window.clearTimeout(savedTimer.current);
    savedTimer.current = window.setTimeout(() => setSavedFlash(false), 1400);
  }, [onSaveLayout]);

  const showToolNotice = useCallback((text: string) => {
    setToolNotice(text);
    if (noticeTimer.current) window.clearTimeout(noticeTimer.current);
    noticeTimer.current = window.setTimeout(() => setToolNotice(null), 2600);
  }, []);

  const layout = useMemo(
    () => {
      const seeded = { ...previous };
      for (const [id, position] of Object.entries(dragged)) {
        seeded[id] = { ...position, pinned: true };
      }
      return layoutGraph(architecture.modules, architecture.edges, architecture.groups, {
        previous: seeded,
      });
    },
    [architecture, previous, dragged],
  );

  useEffect(() => {
    const next: Record<string, { x: number; y: number; pinned: boolean }> = {};
    for (const [id, node] of layout.nodes) {
      next[id] = { x: node.x, y: node.y, pinned: node.pinned };
    }
    settled.current = next;
  }, [layout]);

  const layers = useMemo(() => {
    const map = new Map<string, number>();
    for (const [id, node] of layout.nodes) map.set(id, node.layer);
    return map;
  }, [layout]);

  const alwaysVisible = useMemo(() => {
    const ids = new Set<string>([CONVERSATION_NODE_ID]);
    if (focusedId) ids.add(focusedId);
    if (selectedNodeId) ids.add(selectedNodeId);
    if (hoveredNode) ids.add(hoveredNode);
    for (const id of Object.keys(dragged)) ids.add(id);
    return ids;
  }, [dragged, focusedId, hoveredNode, selectedNodeId]);

  const visibleModules = useMemo(() => {
    const visible = cullNodes(
      architecture.modules.map((module) => module.id),
      layout.nodes,
      camera,
      viewport,
      alwaysVisible,
    );
    return architecture.modules.filter((module) => visible.has(module.id));
  }, [alwaysVisible, architecture.modules, camera, layout.nodes, viewport]);

  // Contract-impact hover: which modules stay lit while a registry entry is hovered.
  const contractHighlight = useMemo(
    () =>
      highlightModulesForContract(registryNodes ?? [], registryEdges ?? [], hoverContractId),
    [hoverContractId, registryEdges, registryNodes],
  );

  // Reach mode: selected node plus its full upstream/downstream dependency closure.
  const reachHighlight = useMemo(
    () =>
      reachMode
        ? reachHighlightSet(architecture.edges, selectedNodeId)
        : null,
    [reachMode, selectedNodeId, architecture.edges],
  );

  // The two highlight sources compose (contract hover wins where both apply).
  const activeHighlight = useMemo(() => {
    if (contractHighlight && reachHighlight) {
      return new Set([...contractHighlight].filter((id) => reachHighlight.has(id)));
    }
    return contractHighlight ?? reachHighlight;
  }, [contractHighlight, reachHighlight]);

  // Deep link: honor #focus=<id> once on mount; mirror selection to the hash after.
  useEffect(() => {
    const focusId = parseFocusHash(window.location.hash);
    if (focusId && architecture.modules.some((module) => module.id === focusId)) {
      dispatch({ type: "select", nodeId: focusId });
      dispatch({ type: "centerNode", nodeId: focusId });
    }
    // Intentionally mount-only: later hash edits come from selection sync below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    replaceFocusHash(selectedNodeId);
  }, [selectedNodeId]);

  // Ctrl+F / Esc shortcuts for the node finder.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "f") {
        event.preventDefault();
        setSearchOpen(true);
      } else if (event.key === "Escape") {
        setSearchOpen(false);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const searchResults = useMemo(() => {
    const query = searchQuery.trim().toLowerCase();
    if (!query) return [];
    return architecture.modules
      .filter(
        (module) =>
          module.id.toLowerCase().includes(query) ||
          module.name.toLowerCase().includes(query),
      )
      .slice(0, 8);
  }, [architecture.modules, searchQuery]);

  // §3 step3: module box geometry (graph coordinates) for the registry overlay.
  const moduleBoxes = useMemo(() => {
    const boxes: Record<string, ModuleBox> = {};
    for (const [id, node] of layout.nodes) {
      boxes[id] = { x: node.x, y: node.y, width: node.width, height: node.height };
    }
    return boxes;
  }, [layout.nodes]);

  useEffect(() => {
    onLayout?.(layers);
  }, [layers, onLayout]);

  useEffect(() => {
    if (!onPositions) return;
    const positions: Record<string, { x: number; y: number; pinned: boolean }> = {};
    for (const [id, node] of layout.nodes) {
      positions[id] = { x: node.x, y: node.y, pinned: node.pinned };
    }
    onPositions(positions);
  }, [layout.nodes, onPositions]);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (!entry) return;
      setViewport({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  // Node emergence and edge draw-in.
  //
  // Driven by diffing consecutive architectures rather than by stream events, so the
  // same code animates a live SSE stream (a series of one-node diffs) and a bulk
  // response (one large diff). The first architecture of a session is adopted silently:
  // opening an existing project must not replay its construction.
  const [emergence, setEmergence] = useState(emptyEmergence);
  const [clock, setClock] = useState(() => performance.now());

  const nodeIds = useMemo(
    () => architecture.modules.map((module) => module.id),
    [architecture.modules],
  );
  const edgeIds = useMemo(() => layout.edges.map((edge) => edge.id), [layout.edges]);

  useEffect(() => {
    const now = performance.now();
    setEmergence((current) => {
      const next = revealOnOpen
        ? observeFirst(current, nodeIds, edgeIds, now)
        : observe(current, nodeIds, edgeIds, now);
      return reducedMotion ? { ...next, nodes: new Map(), edges: new Map() } : next;
    });
    setClock(now);
  }, [edgeIds, nodeIds, reducedMotion, revealOnOpen]);

  // Ticks only while something is arriving, so an idle graph costs no frames.
  useEffect(() => {
    if (reducedMotion || !isAnimating(emergence, performance.now())) return;
    let raf = 0;
    const tick = (): void => {
      const now = performance.now();
      setClock(now);
      if (isAnimating(emergence, now)) {
        raf = requestAnimationFrame(tick);
      } else {
        setEmergence((current) => prune(current, now));
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [emergence, reducedMotion]);

  const edgeDrawProgress = useCallback(
    (edgeId: string) => edgeProgress(emergence, edgeId, clock),
    [clock, emergence],
  );

  /** The camera move that introduces the graph. Declared before the wheel handler so
   *  that handler can cancel it: any deliberate input outranks the animation. */
  const [reveal, setReveal] = useState(idleReveal);
  const revealing = reveal.phase === "holding" || reveal.phase === "retreating";
  const [focusLeaving, setFocusLeaving] = useState(false);
  const focusLeavingRef = useRef(false);
  const exitTimer = useRef<number | null>(null);

  useEffect(() => {
    return () => {
      if (exitTimer.current) window.clearTimeout(exitTimer.current);
    };
  }, []);

  const band = bandFor(camera.scale);

  const focusedModule = useMemo(
    () => architecture.modules.find((module) => module.id === focusedId) ?? null,
    [architecture.modules, focusedId],
  );
  const focusedTask: TaskItem | null =
    tasks?.tasks.find((task) => task.module_id === focusedModule?.id) ?? null;
  const focusedAttachments: NodeAttachment[] =
    focusedModule?.id ? attachments[focusedModule.id] ?? [] : [];
  const attachmentCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const [moduleId, items] of Object.entries(attachments)) {
      counts[moduleId] = items.filter((attachment) => !attachment.resolved).length;
    }
    return counts;
  }, [attachments]);
  const focusedNode = focusedId ? layout.nodes.get(focusedId) : undefined;
  const focusSource = focusedNode
    ? {
        x: focusedNode.x * camera.scale + camera.x,
        y: focusedNode.y * camera.scale + camera.y,
        width: focusedNode.width * camera.scale,
        height: focusedNode.height * camera.scale,
      }
    : null;

  const handleWheel = useCallback(
    (event: WheelEvent) => {
      const element = viewportRef.current;
      if (!element) return;
      const target = event.target as HTMLElement | null;
      // Plain wheel scrolls scrollable panels; ctrl/meta wheel is the zoom gesture
      // even over a focused node's output, so zooming out can leave a node.
      if (target?.closest?.("[data-scrollable]") && !event.ctrlKey && !event.metaKey) return;
      const rect = element.getBoundingClientRect();
      const pointer = { x: event.clientX - rect.left, y: event.clientY - rect.top };
      lastPointer.current = { x: event.clientX, y: event.clientY };

      // Any deliberate input outranks the introduction. Cancelling rather than queueing
      // means the user never has to wait out an animation to regain the camera.
      if (revealing) {
        setReveal(finishReveal());
        dispatch({ type: "exitFocus" });
      }

      if (!event.ctrlKey && !event.metaKey) {
        event.preventDefault();
        onCamera({ ...camera, x: camera.x - event.deltaX, y: camera.y - event.deltaY });
        return;
      }

      // Browser page zoom also arrives here; suppressing it is the whole point of a
      // non-passive listener.
      event.preventDefault();
      const now = performance.now();
      intent.current = pruneIntent([...intent.current, { at: now, delta: event.deltaY }], now);

      const step = scaleStepFor(event.deltaY, event.deltaMode);
      const nextCamera = zoomAtPoint(camera, camera.scale * step, pointer);
      onCamera(nextCamera);

      const hovered = hoveredNode ? layout.nodes.get(hoveredNode) : undefined;
      const coverage = hovered
        ? nodeCoverage(hovered, nextCamera.scale, viewport)
        : 0;
      const decision = decideFocus({
        scale: nextCamera.scale,
        focusedId,
        hoveredId: hoveredNode,
        coverage,
        intent: accumulatedIntent(intent.current, now),
        reducedMotion,
      });
      if (decision.action === "enter") {
        intent.current = [];
        dispatch({ type: "focus", nodeId: decision.id });
      } else if (decision.action === "exit") {
        // Camera restore is deliberately NOT applied here: the wheel event that
        // triggered the exit already moved the camera below the threshold, and
        // snapping it again would fight the gesture still in progress.
        intent.current = [];
        dispatch({ type: "exitFocus" });
      }
    },
    [
      camera,
      dispatch,
      focusedId,
      hoveredNode,
      layout.nodes,
      onCamera,
      reducedMotion,
      revealing,
      viewport,
    ],
  );

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    element.addEventListener("wheel", handleWheel, { passive: false });
    return () => element.removeEventListener("wheel", handleWheel);
  }, [handleWheel]);

  const handlePointerDown = (event: React.PointerEvent<HTMLDivElement>): void => {
    lastPointer.current = { x: event.clientX, y: event.clientY };
    if (event.button !== 0 && event.button !== 1) return;
    panning.current = { x: event.clientX, y: event.clientY, camera };
    (event.target as HTMLElement).setPointerCapture?.(event.pointerId);
    dispatch({ type: "select", nodeId: null });
  };

  const handlePointerMove = (event: React.PointerEvent<HTMLDivElement>): void => {
    lastPointer.current = { x: event.clientX, y: event.clientY };
    const origin = panning.current;
    if (!origin) return;
    onCamera({
      ...origin.camera,
      x: origin.camera.x + (event.clientX - origin.x),
      y: origin.camera.y + (event.clientY - origin.y),
    });
  };

  const endPan = (): void => {
    panning.current = null;
  };

  const handleNodeDrag = useCallback((nodeId: string, x: number, y: number) => {
    setDragged((current) => ({ ...current, [nodeId]: { x, y } }));
  }, []);

  /** Dropping a node pins it.
   *
   *  Without the pin the layout engine blends an unpinned node back toward its computed
   *  slot on the next pass, so the node would drift out from under the cursor moments
   *  after being placed. `moveNode` also makes the placement persist, because the shell
   *  writes pinned nodes to `ui-state.json`.
   */
  const handleNodeDragEnd = useCallback(
    (nodeId: string, x: number, y: number) => {
      setDragged((current) => ({ ...current, [nodeId]: { x, y } }));
      dispatch({ type: "moveNode", nodeId, x, y });
    },
    [dispatch],
  );

  const handleSnap = useCallback(
    (nodeId: string, x: number, y: number) =>
      snapPosition(nodeId, x, y, architecture.edges, layout.nodes),
    [architecture.edges, layout.nodes],
  );

  const handleSnapGuides = useCallback(
    (id: string | null, guides: SnapCandidate[]) => {
      setSnapNodeId(id);
      setSnapGuides(guides);
    },
    [],
  );

  /** Give a node back to the layout engine.
   *
   *  The dragged position has to be dropped here as well as unpinned in the store: it is
   *  seeded into the layout as a pinned `previous` entry, so leaving it behind would hold
   *  the node in place even after the pin was gone.
   */
  const releaseNode = useCallback((nodeId: string) => {
    setDragged((current) => {
      if (!(nodeId in current)) return current;
      const next = { ...current };
      delete next[nodeId];
      return next;
    });
  }, []);

  const fit = useCallback(() => {
    onCamera(fitCamera({ width: layout.width, height: layout.height }, viewport));
  }, [layout.height, layout.width, onCamera, viewport]);

  const handleRestoreDefault = useCallback(() => {
    // Drop every remembered position so the layout engine starts from a clean graph,
    // not from the hand-placed coordinates that made the layout look unchanged.
    settled.current = {};
    setDragged({});
    for (const nodeId of Object.keys(pinned)) {
      dispatch({ type: "releaseNode", nodeId });
    }
    onRestoreDefault?.();
    fit();
  }, [dispatch, fit, onRestoreDefault, pinned]);

  /** Set the opening camera exactly once, so the user never lands on empty space.
   *
   *  Two paths, and only one may run:
   *  - Conversation focused: the reveal sequence owns the camera. Fitting first would
   *    make the graph jump and then slide.
   *  - Anything else (a finished project opened cold): fit the whole graph.
   *
   *  Both wait for focus to settle. The shell sets focus in the same commit that first
   *  supplies an architecture, so acting on the first pass would fit, then reveal —
   *  two camera moves for one arrival.
   */
  const openingCameraSet = useRef(false);
  useEffect(() => {
    if (openingCameraSet.current || layout.nodes.size === 0) return;

    if (revealOnOpen) {
      openingCameraSet.current = true;
      const conversation = layout.nodes.get(CONVERSATION_NODE_ID);
      if (conversation) onCamera(revealCamera(conversation, viewport));
      return;
    }

    if (focusedId === CONVERSATION_NODE_ID) {
      openingCameraSet.current = true;
      // Reduced motion skips the retreat, so it needs the fit instead.
      if (reducedMotion) fit();
      return;
    }
    if (focusedId !== null) {
      openingCameraSet.current = true;
      fit();
      return;
    }

    // Focus is still null. It may be about to arrive in the next commit, or this may be
    // a project opened with no focus at all — a frame's grace distinguishes them without
    // needing the shell and the canvas to agree on ordering.
    const timer = window.setTimeout(() => {
      if (openingCameraSet.current) return;
      openingCameraSet.current = true;
      fit();
    }, 0);
    return () => window.clearTimeout(timer);
  }, [fit, focusedId, layout.nodes, onCamera, reducedMotion, revealOnOpen, viewport]);

  const centerNode = useCallback(
    (nodeId: string) => {
      const node = layout.nodes.get(nodeId);
      if (!node) return;
      onCamera(centerOn(node, viewport, Math.max(camera.scale, 0.9)));
    },
    [camera.scale, layout.nodes, onCamera, viewport],
  );

  /** Camera commands the store dispatches from elsewhere — the Outline's Space and its
   *  fit action — are handled here, because only the canvas has a viewport.
   *
   *  Previously these were hung on `window.__docuagentCenter` for the Stage 1 shell to
   *  call. That indirection went stale when the shell changed: the globals were still
   *  assigned but nothing read them, so centering from the Outline silently did nothing.
   *  Going through `registerEffect` means the wiring is the same mechanism every other
   *  command already uses.
   */
  useEffect(() => {
    if (!registerEffect) return;
    const unregister = registerEffect((command) => {
      if (command.type === "centerNode") centerNode(command.nodeId);
      if (command.type === "fit") fit();
      // The store clears the pin; the dragged coordinate lives here and has to go too.
      if (command.type === "releaseNode") releaseNode(command.nodeId);
      if (command.type === "togglePin" && pinned[command.nodeId]) {
        // Unpinning via P is the same intent as releasing: hand the node back to layout.
        releaseNode(command.nodeId);
      }
    });

    // Honour a request made before this canvas existed — Space in the Outline both
    // switches projection and asks to center, and the switch necessarily happens first.
    const pending = claimPendingCamera?.();
    if (pending?.type === "centerNode") centerNode(pending.nodeId);
    if (pending?.type === "fit") fit();

    return unregister;
  }, [centerNode, claimPendingCamera, fit, pinned, registerEffect, releaseNode]);

  /** Fly to a node and enter it, used by Home and by the Outline's Enter key.
   *
   *  Lands past FOCUS_ENTER_SCALE so the camera state agrees with the focus state.
   *  Leaving them inconsistent means the first wheel notch after arriving reads as
   *  "already below the exit threshold" and drops the user straight back out.
   */
  const enterNode = useCallback(
    (nodeId: string) => {
      const node = layout.nodes.get(nodeId);
      if (!node) return;
      onCamera(centerOn(node, viewport, FOCUS_ENTER_SCALE));
      dispatch({ type: "focus", nodeId });
    },
    [dispatch, layout.nodes, onCamera, viewport],
  );

  const handleNodeSelect = useCallback(
    (nodeId: string) => {
      dispatch({ type: "select", nodeId });
      if (!graphTool) return;

      if (graphToolNeedsInput(graphTool)) {
        setToolTarget(nodeId);
        return;
      }

      const finishAction = (): void => {
        setGraphTool(null);
        setToolTarget(null);
        setToolDraft("");
      };

      switch (graphTool) {
        case "focus":
          finishAction();
          enterNode(nodeId);
          break;
        case "verify": {
          const task = tasks?.tasks.find((item) => item.module_id === nodeId);
          finishAction();
          if (task?.status === "applied") {
            onVerifyTask?.(task.id);
          } else if (task?.status === "verified") {
            showToolNotice("该任务已经验证通过");
          } else if (task) {
            showToolNotice("该任务还没有可验证的应用补丁");
          } else {
            showToolNotice("该模块还没有任务");
          }
          break;
        }
        case "archive":
          finishAction();
          if ((attachments[nodeId] ?? []).some(
            (attachment) => !attachment.resolved && !attachment.archived,
          )) {
            onArchiveModule?.(nodeId);
          } else {
            showToolNotice("该节点没有未完成附件");
          }
          break;
        default:
          setToolTarget(nodeId);
      }
    },
    [
      attachments,
      architecture.modules,
      dispatch,
      enterNode,
      graphTool,
      onArchiveModule,
      onVerifyTask,
      showToolNotice,
      tasks,
    ],
  );

  const refreshHoverFromPointer = useCallback(() => {
    const element = document.elementFromPoint(
      lastPointer.current.x,
      lastPointer.current.y,
    );
    const nodeElement = element?.closest?.("[data-node-id]");
    setHoveredNode(nodeElement?.getAttribute("data-node-id") ?? null);
  }, []);

  /** Leave focus and pull the camera back to FOCUS_RESTORE_SCALE.
   *
   *  Restoring below the entry threshold is what stops a held gesture from
   *  re-triggering entry on the very next event. `decideFocus` assumes this happens.
   */
  const exitFocus = useCallback(() => {
    if (focusLeavingRef.current) return;
    focusLeavingRef.current = true;
    setFocusLeaving(true);
    exitTimer.current = window.setTimeout(() => {
      const node = focusedId ? layout.nodes.get(focusedId) : undefined;
      if (node) {
        onCamera(centerOn(node, viewport, FOCUS_RESTORE_SCALE));
      }
      intent.current = [];
      dispatch({ type: "exitFocus" });
      focusLeavingRef.current = false;
      setFocusLeaving(false);
      window.setTimeout(refreshHoverFromPointer, reducedMotion ? 0 : 40);
    }, reducedMotion ? 0 : 170);
  }, [
    dispatch,
    focusedId,
    layout.nodes,
    onCamera,
    reducedMotion,
    refreshHoverFromPointer,
    viewport,
  ]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent): void => {
      // Home always goes back to the conversation, from anywhere. Esc does too, but
      // only once focus is already clear — inside a node Esc means "back to the graph",
      // and overloading it would make one keystroke skip a level.
      if (event.key === "Home") {
        event.preventDefault();
        dispatch({ type: "focusConversation" });
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        if (focusedId === CONVERSATION_NODE_ID) {
          exitFocus();
        } else {
          // Esc is the same "go home" gesture as Home: from anywhere on the graph,
          // one keystroke returns to the conversation node.
          dispatch({ type: "focusConversation" });
        }
        return;
      }
      // Typing inside the focused conversation must not be intercepted.
      const target = event.target as HTMLElement | null;
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) return;
      if (target?.isContentEditable) return;

      if (event.key === "f" && !event.ctrlKey && !event.metaKey && !focusedId) {
        event.preventDefault();
        fit();
        return;
      }

      // Keyboard equivalent of dragging. A pointer-only way to place a node would make
      // manual layout unreachable from the Outline, which is the accessible projection —
      // and arrow-nudging is also how you place a node precisely.
      if (selectedNodeId && !focusedId && NUDGE_KEYS.has(event.key)) {
        const node = layout.nodes.get(selectedNodeId);
        if (!node) return;
        event.preventDefault();
        const step = event.shiftKey ? NUDGE_COARSE : NUDGE_FINE;
        const dx = event.key === "ArrowLeft" ? -step : event.key === "ArrowRight" ? step : 0;
        const dy = event.key === "ArrowUp" ? -step : event.key === "ArrowDown" ? step : 0;
        const rawX = node.x + dx;
        const rawY = node.y + dy;
        if (event.shiftKey) {
          const snapped = snapPosition(selectedNodeId, rawX, rawY, architecture.edges, layout.nodes);
          handleNodeDragEnd(selectedNodeId, snapped.x, snapped.y);
        } else {
          handleNodeDragEnd(selectedNodeId, rawX, rawY);
        }
        return;
      }

      // Hand a hand-placed node back to the layout engine.
      if (selectedNodeId && !focusedId && (event.key === "r" || event.key === "R")) {
        event.preventDefault();
        dispatch({ type: "releaseNode", nodeId: selectedNodeId });
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [
    architecture.edges,
    dispatch,
    exitFocus,
    fit,
    focusedId,
    handleNodeDragEnd,
    layout.nodes,
    selectedNodeId,
  ]);

  /** Arming and running the introduction.
   *
   *  Runs once, when the first architecture arrives while the conversation is focused:
   *  hold so the user can finish reading, then retreat until the graph is visible below
   *  the conversation node, then stop. Dropping the user straight onto the canvas would
   *  lose their place; leaving them in the conversation would hide what was just built.
   *
   *  Not streaming — the architecture arrives as one response, and this is presentation
   *  layered on top.
   */
  const revealArmed = useRef(false);

  useEffect(() => {
    if (revealArmed.current) return;
    if (layout.nodes.size === 0) return;

    // Wait for focus to settle before deciding. The shell dispatches
    // `focusConversation` in the same commit that first supplies an architecture, so
    // reading `focusedId` on the first pass can see null and skip the introduction
    // permanently. Arming only once focus is actually on the conversation — or once
    // it is on something else, which means the user is already navigating — keeps the
    // decision honest without needing the two to be ordered.
    if (focusedId === null) return;

    revealArmed.current = true;
    // Reduced motion gets the end state with no travel.
    if (reducedMotion || focusedId !== CONVERSATION_NODE_ID) {
      setReveal(finishReveal());
      return;
    }
    setReveal(beginReveal(performance.now()));
  }, [focusedId, layout.nodes.size, reducedMotion]);

  useEffect(() => {
    if (reveal.phase === "idle" || reveal.phase === "done") return;

    let raf = 0;
    const step = (): void => {
      const now = performance.now();

      if (reveal.phase === "holding") {
        if (holdElapsed(reveal, now)) {
          const node = layout.nodes.get(CONVERSATION_NODE_ID);
          if (!node) {
            setReveal(finishReveal());
            return;
          }
          setReveal(beginRetreat(now, camera, revealCamera(node, viewport)));
          return;
        }
        raf = requestAnimationFrame(step);
        return;
      }

      const progress = retreatProgress(reveal, now);
      if (reveal.from && reveal.to) {
        onCamera(lerpCamera(reveal.from, reveal.to, progress));
      }
      if (progress >= 1) {
        // Focus is released only at the end: leaving it earlier would let the wheel
        // fight the animation still in flight.
        dispatch({ type: "exitFocus" });
        setReveal(finishReveal());
        return;
      }
      raf = requestAnimationFrame(step);
    };

    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
    // `camera` is read once when the retreat begins, deliberately not tracked: adding it
    // would restart this effect on every frame the animation itself causes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reveal, dispatch, layout.nodes, onCamera, viewport]);

  // The store dispatches `focusConversation` without knowing the viewport, so the
  // camera move happens here once focus lands on it.
  const flownTo = useRef<string | null>(null);
  useEffect(() => {
    if (!focusedId) {
      flownTo.current = null;
      return;
    }
    // The reveal owns the camera while it runs; flying to the focused node at the same
    // time would produce two animations pulling in opposite directions.
    if (revealing) return;
    if (flownTo.current === focusedId) return;
    const node = layout.nodes.get(focusedId);
    if (!node) return;
    flownTo.current = focusedId;
    // Only fly if the camera is not already there, so entering by zoom does not fight
    // the gesture that got the user in.
    if (camera.scale < FOCUS_ENTER_SCALE) {
      onCamera(centerOn(node, viewport, FOCUS_ENTER_SCALE));
    }
  }, [camera.scale, focusedId, layout.nodes, onCamera, revealing, viewport]);

  return (
    <div
      ref={viewportRef}
      className="relative h-full w-full overflow-hidden"
      style={{ cursor: panning.current ? "grabbing" : "grab", touchAction: "none" }}
      onPointerDown={handlePointerDown}
      onPointerMove={handlePointerMove}
      onPointerUp={endPan}
      onPointerCancel={endPan}
    >
      <div
        className="absolute left-0 top-0 origin-top-left"
        style={{
          transform: `translate3d(${camera.x}px, ${camera.y}px, 0) scale(${camera.scale})`,
          transition: reducedMotion ? "none" : "transform 90ms cubic-bezier(0.2,0.8,0.2,1)",
          willChange: "transform",
          ["--scale" as string]: camera.scale,
        }}
      >
        {layout.groups.map((group) => (
          <div
            key={group.id}
            className="absolute"
            style={{
              left: group.x,
              top: group.y,
              width: group.width,
              height: group.height,
              border: "1px dashed rgba(176, 139, 216, 0.5)",
              borderRadius: 18,
              background: "rgba(176, 139, 216, 0.045)",
            }}
            onPointerDown={(event) => {
              event.stopPropagation();
              dispatch({ type: "selectGroup", groupId: group.id });
            }}
          >
            <span
              className="absolute left-3 top-1 font-mono text-[9.5px] uppercase tracking-[0.16em]"
              style={{ color: "rgba(176, 139, 216, 0.9)", transform: "scale(var(--inv, 1))" }}
            >
              {group.kind === "framework" ? "框架" : "分层"} · {group.label}
            </span>
          </div>
        ))}

        {snapNodeId && snapGuides.length > 0
          ? snapGuides.map((guide, index) => (
              <div
                key={`${guide.edgeId}-${index}`}
                className="pointer-events-none absolute z-10"
                style={{
                  left: 0,
                  right: 0,
                  top: guide.y,
                  borderTop: "1px dashed rgba(255, 92, 99, 0.75)",
                }}
                aria-hidden
              />
            ))
          : null}

        <EdgeLayer
          edges={layout.edges}
          width={layout.width}
          height={layout.height}
          selectedEdgeId={selectedEdgeId}
          hoveredEdgeId={hoveredEdge}
          highlightedNodeId={selectedNodeId ?? hoveredNode}
          highlightedModuleIds={activeHighlight}
          onSelect={(id) => dispatch({ type: "selectEdge", edgeId: id })}
          onHover={setHoveredEdge}
          drawProgress={edgeDrawProgress}
        />

        {visibleModules.map((module) => {
          const node = layout.nodes.get(module.id);
          if (!node) return null;
          return (
            <GraphNode
              key={module.id}
              module={module}
              layout={node}
              // Undefined for the conversation node, which suppresses the status dot.
              status={statuses[module.id]}
              scale={camera.scale}
              selected={module.id === selectedNodeId}
              hovered={module.id === hoveredNode}
              inWindowBar={windowBar.includes(module.id)}
              pinned={node.pinned || Boolean(pinned[module.id])}
              codeStale={codeStaleById?.[module.id] ?? false}
              adaptPending={adaptPendingById?.[module.id] ?? false}
              surfaceCounts={surfaceCountsById?.[module.id] ?? null}
              provenanceBadge={provenanceBadgeById?.[module.id] ?? null}
              usage={usageByModule?.[module.id] ?? null}
              dimmed={activeHighlight !== null && !activeHighlight.has(module.id)}
              noteCount={attachmentCounts[module.id] ?? 0}
              emergence={nodeProgress(emergence, module.id, clock)}
              narration={module.id === CONVERSATION_NODE_ID ? narration : null}
              tail={taskTails[module.id] ?? []}
              onSelect={handleNodeSelect}
              onHover={setHoveredNode}
              onActivate={(id) => {
                dispatch({ type: "focus", nodeId: id });
                // Double-click a module node to open its code-intel public API in
                // the panel (the diagram -> code linkage). The conversation node has
                // no code, so it is skipped.
                if (id !== CONVERSATION_NODE_ID) onOpenModuleCode?.(id);
              }}
              onLongPress={(id) => dispatch({ type: "toggleWindowBar", nodeId: id })}
              onDrag={handleNodeDrag}
              onDragEnd={handleNodeDragEnd}
              onSnap={handleSnap}
              onSnapGuides={handleSnapGuides}
            />
          );
        })}

        {registryNodes && registryNodes.length > 0 && registryTypeFilter ? (
          <RegistryOverlay
            nodes={registryNodes}
            edges={registryEdges ?? []}
            moduleBoxes={moduleBoxes}
            expanded={registryExpanded}
            zoomScale={camera.scale}
            typeFilter={registryTypeFilter}
            onContractHover={setHoverContractId}
            onSelectNode={(node) => {
              // Double-clicking a registry chip opens its source in the user's own
              // editor via a file:// deep link — same principle as code-intel symbols.
              if (node.file) {
                const root = "";
                const base = "file://" + root.replace(/\\/g, "/").replace(/\/+$/, "");
                const url = `${base}/${node.file.replace(/^\/+/, "")}#L${node.line ?? 1}`;
                window.open(url, "_blank");
              }
            }}
          />
        ) : null}

        {errorNodes && errorNodes.length > 0 ? (
          <ErrorNodeOverlay
            nodes={errorNodes}
            moduleBoxes={moduleBoxes}
            onAction={onErrorNodeAction}
          />
        ) : null}

      </div>

      {/* P1-2: off-screen error reminders. Screen space — outside the camera
          transform, so they stay pinned to the viewport edges while the graph
          pans and zooms underneath. In focus mode they ARE the focus-layer
          reminders (every other module is off-screen by definition); clicking
          one leaves focus first, then flies to the owner. */}
      {errorNodes && errorNodes.length > 0 ? (
        <ErrorEdgeIndicators
          errorNodes={errorNodes}
          moduleBoxes={moduleBoxes}
          camera={camera}
          viewport={viewport}
          onFocusOwner={(owner) => {
            if (focusedId) {
              exitFocus();
              window.setTimeout(
                () => centerNode(owner),
                reducedMotion ? 60 : 260,
              );
            } else {
              dispatch({ type: "centerNode", nodeId: owner });
            }
          }}
        />
      ) : null}

      <GraphToolbox
        activeTool={graphTool}
        onSelect={(tool) => {
          setGraphTool(tool);
          setToolTarget(null);
          setToolDraft("");
        }}
      />

      {graphTool && toolTarget ? (
        <GraphToolInput
          tool={graphTool}
          draft={toolDraft}
          onChange={setToolDraft}
          onSubmit={() => {
            const target = toolTarget;
            const value = toolDraft.trim();
            if (!target || !value) return;
            if (graphTool === "note") {
              onAddAttachment?.(target, graphTool, value);
            } else {
              return;
            }
            setToolTarget(null);
            setGraphTool(null);
            setToolDraft("");
          }}
          onCancel={() => {
            setToolTarget(null);
            setGraphTool(null);
            setToolDraft("");
          }}
        />
      ) : null}

      {toolNotice ? (
        <div
          role="status"
          className="absolute left-1/2 top-14 z-30 -translate-x-1/2 border border-ink/60 bg-paper-raise/95 px-3 py-1.5 font-mono text-[10px] text-chalk"
        >
          {toolNotice}
        </div>
      ) : null}

      <Minimap
        layout={layout}
        camera={camera}
        viewport={viewport}
        selectedNodeId={selectedNodeId}
        onJump={(point) => {
          onCamera({
            ...camera,
            x: viewport.width / 2 - point.x * camera.scale,
            y: viewport.height / 2 - point.y * camera.scale,
          });
        }}
      />

      <div className="pointer-events-none absolute left-3 top-3 flex items-center gap-2 font-mono text-[9.5px] text-chalk-faint">
        <span className="rounded bg-paper-raise/85 px-2 py-1">
          {band === "map" ? "MAP" : "GRAPH"} · {Math.round(camera.scale * 100)}%
        </span>
        <span className="rounded bg-paper-raise/85 px-2 py-1">
          Ctrl+滚轮 缩放 · 拖空白 平移 · 拖节点 摆放 · 方向键 微调 · R 复位
        </span>
      </div>

      <div className="absolute right-3 top-16 flex items-center gap-1.5">
        <button
          type="button"
          onClick={() => setSearchOpen((open) => !open)}
          className={`${TOOL_BUTTON_CLASS} ${searchOpen ? "border-ink bg-ink/15 text-chalk" : ""}`}
          title="搜索模块并跳转（Ctrl+F）"
        >
          搜索
        </button>
        <button
          type="button"
          onClick={() => setReachMode((mode) => !mode)}
          disabled={!selectedNodeId}
          className={`${TOOL_BUTTON_CLASS} ${reachMode ? "border-ink bg-ink/15 text-chalk" : ""} ${selectedNodeId ? "" : "cursor-not-allowed opacity-40"}`}
          title={
            selectedNodeId
              ? reachMode
                ? "关闭可达模式（恢复全部显示）"
                : "高亮选中模块的上游与下游依赖，其余暂隐"
              : "先选中一个模块再开启可达模式"
          }
        >
          {reachMode ? "可达 ✓" : "可达"}
        </button>
        <button
          type="button"
          onClick={onUndo}
          disabled={!canUndo}
          className={`${TOOL_BUTTON_CLASS} ${canUndo ? "" : "cursor-not-allowed opacity-40"}`}
          title={canUndo ? "撤销上一次架构修改" : "没有可撤销的架构修改"}
        >
          撤销
        </button>
        <button type="button" onClick={handleSaveLayout} className={TOOL_BUTTON_CLASS} title="立即保存当前布局">
          保存布局
        </button>
        {savedFlash ? (
          <span className="font-mono text-[10px] text-chalk-dim" aria-live="polite">
            已保存
          </span>
        ) : null}
        <button type="button" onClick={handleRestoreDefault} className={TOOL_BUTTON_CLASS} title="清除手动摆放，恢复自动布局">
          恢复默认
        </button>
      </div>

      {searchOpen ? (
        <div className="absolute right-3 top-28 z-30 w-64 overflow-hidden rounded-md border border-ink-dim/50 bg-paper-raise/95 shadow-xl">
          <input
            autoFocus
            value={searchQuery}
            onChange={(event) => setSearchQuery(event.target.value)}
            onKeyDown={(event) => {
              const first = searchResults[0];
              if (event.key === "Enter" && first) {
                dispatch({ type: "select", nodeId: first.id });
                dispatch({ type: "centerNode", nodeId: first.id });
              } else if (event.key === "Escape") {
                setSearchOpen(false);
              }
            }}
            placeholder="搜索模块名或 ID…"
            className="w-full border-b border-ink-ghost bg-transparent px-3 py-2 font-mono text-[11px] text-chalk outline-none placeholder:text-chalk-faint"
          />
          {searchQuery.trim() ? (
            searchResults.length > 0 ? (
              <ul className="max-h-56 overflow-y-auto py-1">
                {searchResults.map((module) => (
                  <li key={module.id}>
                    <button
                      type="button"
                      onClick={() => {
                        dispatch({ type: "select", nodeId: module.id });
                        dispatch({ type: "centerNode", nodeId: module.id });
                        setSearchOpen(false);
                        setSearchQuery("");
                      }}
                      className="flex w-full flex-col items-start px-3 py-1.5 text-left transition-colors hover:bg-ink/10"
                    >
                      <span className="font-body text-[11.5px] text-chalk">{module.name}</span>
                      <span className="font-mono text-[9.5px] text-chalk-faint">{module.id}</span>
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-3 py-2 font-body text-[11px] text-chalk-dim">没有匹配的模块。</p>
            )
          ) : (
            <p className="px-3 py-2 font-mono text-[9.5px] text-chalk-faint">
              Enter 跳到第一个结果 · Esc 关闭
            </p>
          )}
        </div>
      ) : null}

      {registryNodes && registryNodes.length > 0 && registryTypeFilter && onToggleRegistry && onSetRegistryFilter ? (
        <div className="absolute right-3 top-28 flex max-w-[16rem] flex-col items-end gap-1.5">
          <button
            type="button"
            onClick={onToggleRegistry}
            className={TOOL_BUTTON_CLASS}
            title={registryExpanded ? "折叠契约目录（仅显示计数徽标）" : "展开契约目录（按缩放在 行→字段→符号 间细化，越近越详细）"}
          >
            {registryExpanded ? "契约 ▾" : "契约 ▸"} · {registryNodes.length}
          </button>
          <div className="flex flex-wrap justify-end gap-1">
            {(Object.keys(REGISTRY_TYPE_LABELS) as RegistryType[]).map((t) => {
              const on = registryTypeFilter.has(t);
              const color = REGISTRY_TYPE_COLORS[t];
              return (
                <button
                  key={t}
                  type="button"
                  onClick={() => {
                    const next = new Set(registryTypeFilter);
                    if (next.has(t)) next.delete(t);
                    else next.add(t);
                    onSetRegistryFilter(next);
                  }}
                  className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[9px] transition-opacity"
                  style={{
                    borderColor: on ? color : "rgba(120,128,140,0.4)",
                    color: on ? "#e8eef5" : "#8a93a3",
                    opacity: on ? 1 : 0.5,
                    background: on ? "rgba(17,22,29,0.92)" : "transparent",
                  }}
                  title={`${REGISTRY_TYPE_LABELS[t]}（点击${on ? "隐藏" : "显示"}）`}
                >
                  <span className="inline-block h-1.5 w-1.5 rounded-full" style={{ background: color }} />
                  {REGISTRY_TYPE_LABELS[t]}
                </button>
              );
            })}
          </div>
        </div>
      ) : null}

      {focusedModule ? (
        <FocusLayer
          module={focusedModule}
          reducedMotion={reducedMotion}
          stale={statuses[focusedModule.id] === "stale"}
          onClearStale={onClearStale ? () => onClearStale(focusedModule.id) : undefined}
          task={focusedTask}
          taskStream={taskTails[focusedModule.id] ?? []}
          taskStreaming={taskStreaming}
          attachments={focusedAttachments}
          onApplyHunks={
            focusedTask && onApplyHunks
              ? (file, hunkIds) => onApplyHunks(focusedTask.id, file, hunkIds)
              : undefined
          }
          onSavePatch={
            focusedTask && onSavePatch
              ? (file, content, baseAfter) =>
                  onSavePatch(focusedTask.id, file, content, baseAfter)
              : undefined
          }
          onVerifyTask={onVerifyTask}
          onResumeTask={onResumeTask}
          onApplyTask={
            focusedTask && onApplyTask
              ? () => onApplyTask(focusedTask.id)
              : undefined
          }
          onTerminalExec={onTerminalExec}
          source={revealing ? null : focusSource}
          target={{ width: viewport.width, height: viewport.height }}
          leaving={focusLeaving}
          onExit={exitFocus}
        >
          {conversationContent}
        </FocusLayer>
      ) : null}
    </div>
  );
}

export function scaleAfterExit(scale: number): number {
  return clampScale(Math.min(scale, 1.2));
}
