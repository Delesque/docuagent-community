/** Layered (Sugiyama-style) graph layout.
 *
 *  Determinism is a hard requirement, not a nicety: layout output is the stable
 *  assertion surface for tests, since pixel-position assertions on a physics or
 *  force-directed layout are brittle. Every ordering step therefore breaks ties on
 *  node id, and no step consults Math.random or insertion order of a Map built from
 *  model output.
 *
 *  Only build-order edges influence layering. `event` edges are drawn but never move
 *  a node, which is what lets publish/subscribe cycles exist without breaking layout.
 *
 *  The conversation node is the one exception to layering. Module layers advance along
 *  x, so letting it participate would put it at the far left of the entry layer; it
 *  belongs above the graph, spanning it. It is therefore lifted out before layering and
 *  placed by hand, and its edges drop downward into the entry layer instead of
 *  travelling left-to-right like dependency edges.
 *
 *  ## Why edges do not overlap
 *
 *  Three separate mechanisms, because "lines pile up on each other" has three causes and
 *  fixing one leaves the drawing just as unreadable:
 *
 *  1. **Bend slots.** An edge whose endpoints are more than one layer apart used to be
 *     drawn as a single dog-leg straight across the intervening columns, passing through
 *     any node in the way. Such an edge now reserves a thin vertical slot in every layer
 *     it crosses (`BEND_HEIGHT`), exactly like a node reserves a tall one, and is routed
 *     through those slots. Real nodes make room for them, so the edge goes *around*.
 *     These slots also join crossing reduction, which is the standard reason to have
 *     them: an edge that cannot be reordered cannot be uncrossed.
 *  2. **Face anchors.** Every edge at a node used to attach to the exact center of its
 *     left or right face, so ten edges into one node were ten lines on top of each
 *     other. Anchors are now distributed along the face.
 *  3. **Lanes.** Two edges crossing the same gap still shared one mid-x corridor, so
 *     their vertical runs coincided. Each now gets its own lane offset.
 *
 *  All three are index-based over deterministically sorted lists, so none of them cost
 *  reproducibility.
 */

import { BUILD_ORDER_EDGE_KINDS, type GraphEdge, type GraphGroup, type GraphModule } from "./types";
import { isConversationNode } from "./conversationNode";

export const NODE_WIDTH = 208;
export const NODE_HEIGHT = 116;
/** Wider than a node is tall on purpose: the gap has to hold the lane offsets of every
 *  edge crossing it, and a cramped gap puts the corners back on top of each other. */
export const LAYER_GAP = 132;
export const NODE_GAP = 32;
export const GROUP_PADDING = 22;
export const GROUP_HEADER = 24;

/** Vertical space an edge reserves in a layer it only passes through. A bend needs
 *  clearance to turn in, not a node's worth of room. */
export const BEND_HEIGHT = 26;

/** The conversation node is wider than a module: it carries prose, not a signature. */
export const CONVERSATION_WIDTH = 420;
export const CONVERSATION_HEIGHT = 132;
/** Vertical clearance between the conversation node and the graph below it. */
export const CONVERSATION_GAP = 88;

/** Horizontal separation between the vertical runs of two edges crossing one gap. */
const LANE_STEP = 20;
/** Share of a gap the lanes may spread across. The rest keeps the corners clear of
 *  the node faces on either side. */
const LANE_ALLOWANCE = LAYER_GAP * 0.62;
const CORNER_RADIUS = 11;

/** Clearance between the graph's bottom and the first back-edge lane. Half a label's
 *  height would already clear the hover labels, but the lane also needs turning room. */
const BACK_EDGE_CLEARANCE = 28;
/** Vertical separation between the lanes of two back edges. */
const BACK_EDGE_STEP = 20;

/** Key separator. Slugified ids are `[a-zA-Z0-9_-]` and the one reserved id
 *  (`__conversation__`) has no `|` either, so this cannot collide with a real id. */
const SEP = "|";
/** Prefix for a routing slot. Never a real node: those are slugs or the reserved id. */
const BEND_PREFIX = "__bend__";

export interface LaidOutNode {
  id: string;
  x: number;
  y: number;
  width: number;
  height: number;
  layer: number;
  pinned: boolean;
}

export interface LaidOutGroup {
  id: string;
  label: string;
  kind: GraphGroup["kind"];
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface RoutedEdge extends GraphEdge {
  id: string;
  path: string;
  midpoint: { x: number; y: number };
}

export interface LayoutResult {
  nodes: Map<string, LaidOutNode>;
  groups: LaidOutGroup[];
  edges: RoutedEdge[];
  width: number;
  height: number;
}

export interface LayoutOptions {
  /** Previously known positions. Pinned nodes keep them exactly; unpinned nodes blend
   *  toward them so adding one node does not reshuffle the whole graph and lose the
   *  user's spatial memory. */
  previous?: Record<string, { x: number; y: number; pinned: boolean }>;
  inertia?: number;
}

/** A routing slot: where a multi-layer edge turns inside a column it passes through. */
interface Bend {
  id: string;
  x: number;
  y: number;
  layer: number;
}

const byId = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

function round(value: number): number {
  return Math.round(value * 100) / 100;
}

/** Build-order adjacency, deduplicated and deterministically ordered. */
function buildOrderAdjacency(
  moduleIds: string[],
  edges: GraphEdge[],
): { successors: Map<string, string[]>; predecessors: Map<string, string[]> } {
  const known = new Set(moduleIds);
  const successors = new Map<string, string[]>();
  const predecessors = new Map<string, string[]>();
  for (const id of moduleIds) {
    successors.set(id, []);
    predecessors.set(id, []);
  }
  const seen = new Set<string>();
  for (const edge of edges) {
    if (!BUILD_ORDER_EDGE_KINDS.has(edge.kind)) continue;
    if (!known.has(edge.from) || !known.has(edge.to)) continue;
    if (edge.from === edge.to) continue;
    const key = `${edge.from}${SEP}${edge.to}`;
    if (seen.has(key)) continue;
    seen.add(key);
    // `from` depends on `to`, so `to` must be built first and sits in an earlier layer.
    successors.get(edge.to)!.push(edge.from);
    predecessors.get(edge.from)!.push(edge.to);
  }
  for (const list of successors.values()) list.sort(byId);
  for (const list of predecessors.values()) list.sort(byId);
  return { successors, predecessors };
}

/** Longest-path layering. Tolerates cycles defensively: a back edge that would raise
 *  a node above an ancestor is ignored rather than looping forever. Validation already
 *  rejects build-order cycles server-side, but the canvas must never hang on bad data.
 */
export function assignLayers(moduleIds: string[], edges: GraphEdge[]): Map<string, number> {
  const { predecessors } = buildOrderAdjacency(moduleIds, edges);
  const layers = new Map<string, number>();
  const state = new Map<string, "idle" | "active" | "settled">();
  for (const id of moduleIds) state.set(id, "idle");

  const resolve = (id: string): number => {
    const current = state.get(id);
    if (current === "settled") return layers.get(id)!;
    if (current === "active") return layers.get(id) ?? 0; // cycle guard
    state.set(id, "active");
    layers.set(id, 0);
    let depth = 0;
    for (const dependency of predecessors.get(id) ?? []) {
      depth = Math.max(depth, resolve(dependency) + 1);
    }
    layers.set(id, depth);
    state.set(id, "settled");
    return depth;
  };

  for (const id of [...moduleIds].sort(byId)) resolve(id);
  return layers;
}

/** Median heuristic for crossing reduction, with a fixed iteration count so output is
 *  reproducible. Ties fall back to node id. */
function orderWithinLayers(
  layerBuckets: string[][],
  successors: Map<string, string[]>,
  predecessors: Map<string, string[]>,
): string[][] {
  const ordered = layerBuckets.map((bucket) => [...bucket].sort(byId));
  const positionOf = (bucket: string[]): Map<string, number> => {
    const positions = new Map<string, number>();
    bucket.forEach((id, index) => positions.set(id, index));
    return positions;
  };

  const SWEEPS = 4;
  for (let sweep = 0; sweep < SWEEPS; sweep += 1) {
    const downward = sweep % 2 === 0;
    const indices = downward
      ? [...ordered.keys()].slice(1)
      : [...ordered.keys()].slice(0, -1).reverse();
    for (const index of indices) {
      const reference = ordered[downward ? index - 1 : index + 1];
      const bucket = ordered[index];
      if (!reference || !bucket) continue;
      const positions = positionOf(reference);
      const neighborsOf = downward ? predecessors : successors;
      const scored = bucket.map((id) => {
        const neighbors = (neighborsOf.get(id) ?? [])
          .map((neighbor) => positions.get(neighbor))
          .filter((value): value is number => value !== undefined)
          .sort((a, b) => a - b);
        if (neighbors.length === 0) return { id, score: Number.POSITIVE_INFINITY };
        const middle = Math.floor(neighbors.length / 2);
        const score =
          neighbors.length % 2 === 1
            ? neighbors[middle]!
            : (neighbors[middle - 1]! + neighbors[middle]!) / 2;
        return { id, score };
      });
      scored.sort((a, b) => (a.score === b.score ? byId(a.id, b.id) : a.score - b.score));
      ordered[index] = scored.map((entry) => entry.id);
    }
  }
  return ordered;
}

/** Groups are compound nodes: members are pulled adjacent within their layer so the
 *  dashed framework container stays a tight rectangle instead of spanning the graph. */
function clusterGroups(ordered: string[][], moduleGroup: Map<string, string | null>): string[][] {
  return ordered.map((bucket) => {
    const groupOrder: string[] = [];
    const buckets = new Map<string, string[]>();
    for (const id of bucket) {
      const key = moduleGroup.get(id) ?? ` ${id}`;
      if (!buckets.has(key)) {
        buckets.set(key, []);
        groupOrder.push(key);
      }
      buckets.get(key)!.push(id);
    }
    return groupOrder.flatMap((key) => buckets.get(key)!);
  });
}

/** The build-order edges, deduplicated and deterministically ordered.
 *
 *  Shared by the chain builder and the router so the two cannot disagree about which
 *  edges exist — an edge with a reserved slot but no route through it would leave a
 *  visible gap in a layer for no reason.
 */
function uniqueEdges(edges: GraphEdge[], known: Set<string>): GraphEdge[] {
  const seen = new Set<string>();
  const result: GraphEdge[] = [];
  for (const edge of edges) {
    if (!known.has(edge.from) || !known.has(edge.to)) continue;
    if (edge.from === edge.to) continue;
    const key = `${edge.from}${SEP}${edge.to}${SEP}${edge.kind}`;
    if (seen.has(key)) continue;
    seen.add(key);
    result.push(edge);
  }
  return result.sort((a, b) =>
    byId(
      `${a.from}${SEP}${a.to}${SEP}${a.kind}`,
      `${b.from}${SEP}${b.to}${SEP}${b.kind}`,
    ),
  );
}

/** Stable key for one edge, used for its slot ids and its lane assignment. */
export function edgeKey(edge: GraphEdge): string {
  return `${edge.from}${SEP}${edge.to}${SEP}${edge.kind}`;
}

/** Anchor offsets relative to a node's center, per face and edge key.
 *
 *  Shared by routing and by the straightening pass so the two cannot disagree about
 *  where an edge leaves or enters a face. A lone edge sits dead center; more than one
 *  spreads evenly across the middle 70% of the face.
 */
export interface AnchorOffsets {
  outgoing: Map<string, Map<string, number>>;
  incoming: Map<string, Map<string, number>>;
}

export function computeAnchorOffsets(
  edges: GraphEdge[],
  nodeIds: Iterable<string>,
): AnchorOffsets {
  const known = new Set(nodeIds);
  const outgoing = new Map<string, Map<string, number>>();
  const incoming = new Map<string, Map<string, number>>();
  const outEdges = new Map<string, string[]>();
  const inEdges = new Map<string, string[]>();

  for (const id of known) {
    outgoing.set(id, new Map());
    incoming.set(id, new Map());
  }
  for (const edge of edges) {
    if (isConversationNode(edge.from) || isConversationNode(edge.to)) continue;
    if (!known.has(edge.from) || !known.has(edge.to)) continue;
    const key = edgeKey(edge);
    if (!outEdges.has(edge.to)) outEdges.set(edge.to, []);
    outEdges.get(edge.to)!.push(key);
    if (!inEdges.has(edge.from)) inEdges.set(edge.from, []);
    inEdges.get(edge.from)!.push(key);
  }

  const span = NODE_HEIGHT * 0.7;
  const assign = (grouped: Map<string, string[]>, target: Map<string, Map<string, number>>) => {
    for (const [nodeId, keys] of [...grouped.entries()].sort((a, b) => byId(a[0], b[0]))) {
      const sorted = [...keys].sort(byId);
      sorted.forEach((key, index) => {
        const offset =
          sorted.length === 1
            ? 0
            : (index / (sorted.length - 1) - 0.5) * span;
        target.get(nodeId)!.set(key, offset);
      });
    }
  };
  assign(outEdges, outgoing);
  assign(inEdges, incoming);
  return { outgoing, incoming };
}

/** Routing slots for edges that span more than one layer.
 *
 *  Returns, per edge key, the slot ids it passes through in layer order — and registers
 *  each slot in the layer buckets so ordering and vertical placement treat it as a
 *  first-class occupant. This is the classic Sugiyama dummy-vertex step, and it is what
 *  makes a long edge bend around intervening nodes rather than crossing them.
 *
 *  Conversation edges are excluded: the conversation sits above the graph, so its edges
 *  descend rather than travelling through columns.
 */
function reserveBendSlots(
  edges: GraphEdge[],
  layerOf: Map<string, number>,
  buckets: string[][],
  successors: Map<string, string[]>,
  predecessors: Map<string, string[]>,
): Map<string, string[]> {
  const chains = new Map<string, string[]>();

  for (const edge of edges) {
    if (isConversationNode(edge.from) || isConversationNode(edge.to)) continue;
    // Layering puts `to` earlier, so the run goes from `to`'s layer up to `from`'s.
    // A back edge runs the other way (start > end): it travels under the graph along
    // its own lane instead of through layer slots, so it takes no chain here.
    const start = layerOf.get(edge.to);
    const end = layerOf.get(edge.from);
    if (start === undefined || end === undefined) continue;
    if (end - start <= 1) continue;

    const key = edgeKey(edge);
    const chain: string[] = [];
    let entering = edge.to;
    for (let layer = start + 1; layer < end; layer += 1) {
      const slot = `${BEND_PREFIX}${key}${SEP}${layer}`;
      buckets[layer]!.push(slot);
      chain.push(slot);
      // Threaded into adjacency so crossing reduction can position the slot between
      // whatever it actually connects, instead of parking every slot at one end.
      successors.set(entering, [...(successors.get(entering) ?? []), slot]);
      predecessors.set(slot, [entering]);
      entering = slot;
    }
    successors.set(entering, [...(successors.get(entering) ?? []), edge.from]);
    predecessors.set(edge.from, [...(predecessors.get(edge.from) ?? []), entering]);
    chains.set(key, chain);
  }

  for (const list of successors.values()) list.sort(byId);
  for (const list of predecessors.values()) list.sort(byId);
  return chains;
}

function isBend(id: string): boolean {
  return id.startsWith(BEND_PREFIX);
}

export function layoutGraph(
  allModules: GraphModule[],
  edges: GraphEdge[],
  groups: GraphGroup[],
  options: LayoutOptions = {},
): LayoutResult {
  // Lifted out before layering: it spans the graph rather than occupying a layer slot.
  const conversation = allModules.find((module) => isConversationNode(module.id));
  const modules = conversation
    ? allModules.filter((module) => !isConversationNode(module.id))
    : allModules;

  const moduleIds = modules.map((module) => module.id);
  const layers = assignLayers(moduleIds, edges);
  const { successors, predecessors } = buildOrderAdjacency(moduleIds, edges);

  const maxLayer = moduleIds.reduce((max, id) => Math.max(max, layers.get(id) ?? 0), 0);
  const buckets: string[][] = Array.from({ length: maxLayer + 1 }, () => []);
  for (const id of [...moduleIds].sort(byId)) {
    buckets[layers.get(id) ?? 0]!.push(id);
  }

  // Reserve a slot per crossed layer for every long edge, before ordering runs, so the
  // slots take part in crossing reduction and in vertical placement.
  //
  // Slot reservation is a *drawing* concern, not an ordering rule, so it covers every
  // edge kind: an `event` edge is excluded from build order on purpose (publish/
  // subscribe runs both ways), but a long event edge still has to detour around the
  // nodes it skips instead of drawing straight through them. Back edges do not take
  // slots here — they travel under the graph (see routeEdges).
  const known = new Set(moduleIds);
  const routable = uniqueEdges(edges, known);
  const chains = reserveBendSlots(routable, layers, buckets, successors, predecessors);

  const moduleGroup = new Map<string, string | null>();
  for (const module of modules) moduleGroup.set(module.id, module.group);
  for (const group of groups) {
    for (const member of group.members) {
      if (!moduleGroup.get(member)) moduleGroup.set(member, group.id);
    }
  }

  const ordered = clusterGroups(
    orderWithinLayers(buckets, successors, predecessors),
    moduleGroup,
  );

  // Layers advance on x; nodes stack on y. Reading left-to-right matches dependency
  // direction, which is how the graph is described in prose everywhere else.
  const groupSet = new Set(groups.map((group) => group.id));
  const nodes = new Map<string, LaidOutNode>();
  const bends = new Map<string, Bend>();
  const inertia = options.inertia ?? 0.35;
  const previous = options.previous ?? {};

  const heightOf = (id: string): number => (isBend(id) ? BEND_HEIGHT : NODE_HEIGHT);
  const layerExtent = (bucket: string[]): number =>
    bucket.reduce((total, id) => total + heightOf(id), 0) +
    Math.max(0, bucket.length - 1) * NODE_GAP;

  const tallestLayer = ordered.reduce((max, bucket) => {
    const inGroup = bucket.filter((id) => {
      const key = moduleGroup.get(id);
      return key !== null && key !== undefined && groupSet.has(key);
    }).length;
    const padding = inGroup > 0 ? GROUP_HEADER + GROUP_PADDING * 2 : 0;
    return Math.max(max, layerExtent(bucket) + padding);
  }, NODE_HEIGHT);

  ordered.forEach((bucket, layerIndex) => {
    let cursorY = (tallestLayer - layerExtent(bucket)) / 2;
    const x = layerIndex * (NODE_WIDTH + LAYER_GAP);
    for (const id of bucket) {
      if (isBend(id)) {
        // A slot is a routing waypoint, not a node: it holds a y and reserves room, and
        // is deliberately absent from `nodes` so nothing renders or persists it.
        bends.set(id, { id, x: x + NODE_WIDTH / 2, y: cursorY + BEND_HEIGHT / 2, layer: layerIndex });
        cursorY += BEND_HEIGHT + NODE_GAP;
        continue;
      }
      const target = { x, y: cursorY };
      const prior = previous[id];
      let position = target;
      if (prior) {
        position = prior.pinned
          ? { x: prior.x, y: prior.y }
          : {
              x: target.x + (prior.x - target.x) * inertia,
              y: target.y + (prior.y - target.y) * inertia,
            };
      }
      nodes.set(id, {
        id,
        x: position.x,
        y: position.y,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
        layer: layerIndex,
        pinned: prior?.pinned ?? false,
      });
      cursorY += NODE_HEIGHT + NODE_GAP;
    }
  });

  straightenLayers(nodes, edges, moduleIds);

  // Computed before the conversation shift so its top can account for the group
  // header, then recomputed afterwards from the final node positions: pinned members
  // do not move with the shift, so a frame computed earlier would lose them.
  const initialGroups = layoutGroups(groups, nodes);

  let conversationShift = 0;
  if (conversation) {
    conversationShift = placeConversation(conversation.id, nodes, initialGroups, options);
  }
  // Slots move with the graph they route through, or long edges would be routed to
  // waypoints the nodes no longer sit beside.
  if (conversationShift > 0) {
    for (const [id, bend] of bends) {
      bends.set(id, { ...bend, y: bend.y + conversationShift });
    }
  }

  const laidOutGroups = layoutGroups(groups, nodes);
  const routed = routeEdges(edges, nodes, {
    chains,
    bends,
    backLaneBase: computeBounds(nodes, laidOutGroups).height,
  });
  const bounds = computeBounds(nodes, laidOutGroups);
  // Back-edge lanes run below the graph; the bounds must hold them or the camera
  // fit crops the lines. Every lane's lowest point is its own midpoint.
  for (const edge of routed) {
    bounds.height = Math.max(bounds.height, edge.midpoint.y);
  }

  return { nodes, groups: laidOutGroups, edges: routed, ...bounds };
}

/** Put the conversation above the graph and shift everything else down to clear it.
 *
 *  Centering is measured over the module span rather than the group span, so a dashed
 *  framework container that overhangs one side does not visibly push the conversation
 *  off-center. Returns how far the graph was pushed down, so routing slots can follow.
 */
function placeConversation(
  id: string,
  nodes: Map<string, LaidOutNode>,
  groups: LaidOutGroup[],
  options: LayoutOptions,
): number {
  const modules = [...nodes.values()];
  const left = modules.length > 0 ? Math.min(...modules.map((node) => node.x)) : 0;
  const right = modules.length > 0
    ? Math.max(...modules.map((node) => node.x + node.width))
    : CONVERSATION_WIDTH;

  // Groups add a header above their members, so the graph's true top may sit above
  // the topmost node. Clearance is measured from whichever is higher.
  const topNode = modules.length > 0 ? Math.min(...modules.map((node) => node.y)) : 0;
  const topGroup = groups.length > 0 ? Math.min(...groups.map((group) => group.y)) : topNode;
  const graphTop = Math.min(topNode, topGroup);

  const shift = CONVERSATION_HEIGHT + CONVERSATION_GAP - graphTop;
  if (shift > 0) {
    for (const node of nodes.values()) {
      // Pinned nodes hold absolute positions the user chose; moving them would undo
      // that. The conversation clears whatever room is left.
      if (node.pinned) continue;
      nodes.set(node.id, { ...node, y: node.y + shift });
    }
    for (let index = 0; index < groups.length; index += 1) {
      groups[index] = { ...groups[index]!, y: groups[index]!.y + shift };
    }
  }

  const target = {
    x: (left + right) / 2 - CONVERSATION_WIDTH / 2,
    y: 0,
  };
  const prior = options.previous?.[id];
  const position = prior?.pinned ? { x: prior.x, y: prior.y } : target;

  nodes.set(id, {
    id,
    x: position.x,
    y: position.y,
    width: CONVERSATION_WIDTH,
    height: CONVERSATION_HEIGHT,
    // Sentinel layer: above the entry layer, and never produced by `assignLayers`, so
    // any code that groups by layer sorts it to the top without a special case.
    layer: -1,
    pinned: prior?.pinned ?? false,
  });

  return shift > 0 ? shift : 0;
}

/** Where each edge attaches on each node's face.
 *
 *  Without this every edge meets the exact vertical center of a face, so a node with six
 *  dependents has six lines emerging from one pixel and the fan-out is invisible. Slots
 *  are assigned by sorted edge key, so they are stable across renders: an anchor that
 *  moved when an unrelated edge appeared would make the whole graph twitch.
 *
 *  Anchors are spread over the middle 70% of the face; the remaining margin keeps them
 *  clear of the rounded corners of the node's baked border.
 */
function faceAnchors(
  edges: GraphEdge[],
  nodes: Map<string, LaidOutNode>,
): Map<string, number> {
  const anchors = new Map<string, number>();
  const offsets = computeAnchorOffsets(edges, nodes.keys());
  for (const [nodeId, byEdge] of offsets.outgoing) {
    const node = nodes.get(nodeId);
    if (!node) continue;
    for (const [key, offset] of byEdge) {
      anchors.set(`out${SEP}${key}`, node.y + node.height / 2 + offset);
    }
  }
  for (const [nodeId, byEdge] of offsets.incoming) {
    const node = nodes.get(nodeId);
    if (!node) continue;
    for (const [key, offset] of byEdge) {
      anchors.set(`in${SEP}${key}`, node.y + node.height / 2 + offset);
    }
  }
  return anchors;
}

/** Lane offsets, so two edges crossing the same gap do not share a vertical run.
 *
 *  Keyed per (gap, edge): edges are bucketed by the pair of layers they cross, then each
 *  is nudged off the gap's midline by a distinct amount. Deterministic via sorted key.
 */
function laneOffsets(
  edges: GraphEdge[],
  nodes: Map<string, LaidOutNode>,
): Map<string, number> {
  const gaps = new Map<string, string[]>();
  for (const edge of edges) {
    if (isConversationNode(edge.from) || isConversationNode(edge.to)) continue;
    const from = nodes.get(edge.from);
    const to = nodes.get(edge.to);
    if (!from || !to) continue;
    const gap = `${Math.min(to.layer, from.layer)}${SEP}${Math.max(to.layer, from.layer)}`;
    if (!gaps.has(gap)) gaps.set(gap, []);
    gaps.get(gap)!.push(edgeKey(edge));
  }

  const offsets = new Map<string, number>();
  for (const [, keys] of [...gaps.entries()].sort((a, b) => byId(a[0], b[0]))) {
    const sorted = [...keys].sort(byId);
    // Widest usable spread, then capped so lanes never reach the node faces.
    const step = Math.min(LANE_STEP, LANE_ALLOWANCE / Math.max(1, sorted.length));
    sorted.forEach((key, index) => {
      offsets.set(key, (index - (sorted.length - 1) / 2) * step);
    });
  }
  return offsets;
}

/** Nudge node centers so connected edges can run straight.
 *
 *  Runs after the initial stacked placement. For every node, each cross-layer edge
 *  contributes a desired center: the y at which that edge's two face anchors align.
 *  Each layer then takes the median of its nodes' desires, with pinned nodes acting as
 *  fixed obstacles, and reflows with the same minimum gap as the original stack. A few
 *  fixed sweeps keep the result deterministic; no sweep consults Math.random.
 */
function straightenLayers(
  nodes: Map<string, LaidOutNode>,
  edges: GraphEdge[],
  moduleIds: string[],
): void {
  const known = new Set(moduleIds);
  const present = uniqueEdges(
    edges.filter((edge) => !isConversationNode(edge.from) && !isConversationNode(edge.to)),
    known,
  );
  const offsets = computeAnchorOffsets(present, moduleIds);
  const layerOf = new Map<string, number>();
  const layers = new Map<number, string[]>();

  for (const id of moduleIds) {
    const node = nodes.get(id);
    if (!node) continue;
    layerOf.set(id, node.layer);
    if (!layers.has(node.layer)) layers.set(node.layer, []);
    layers.get(node.layer)!.push(id);
  }
  for (const ids of layers.values()) ids.sort(byId);
  const layerKeys = [...layers.keys()].sort((a, b) => a - b);

  const desiredFromEarlier = (id: string): number[] => {
    const currentLayer = layerOf.get(id)!;
    const desired: number[] = [];
    for (const edge of present) {
      if (edge.from === edge.to) continue;
      let neighborId: string | null = null;
      let delta = 0;
      const key = edgeKey(edge);
      if (edge.to === id) {
        neighborId = edge.from;
        const out = offsets.outgoing.get(id)?.get(key) ?? 0;
        const inn = offsets.incoming.get(edge.from)?.get(key) ?? 0;
        delta = out - inn;
      } else if (edge.from === id) {
        neighborId = edge.to;
        const inn = offsets.incoming.get(id)?.get(key) ?? 0;
        const out = offsets.outgoing.get(edge.to)?.get(key) ?? 0;
        delta = inn - out;
      }
      if (!neighborId) continue;
      const neighborLayer = layerOf.get(neighborId)!;
      if (neighborLayer >= currentLayer) continue;
      const neighbor = nodes.get(neighborId);
      if (!neighbor) continue;
      desired.push(neighbor.y + neighbor.height / 2 + delta);
    }
    return desired;
  };

  const median = (values: number[]): number => {
    if (values.length === 0) return 0;
    const sorted = [...values].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    return sorted.length % 2 === 1
      ? sorted[middle]!
      : (sorted[middle - 1]! + sorted[middle]!) / 2;
  };

  for (const layer of layerKeys) {
    const ids = layers.get(layer)!;
    const targets = ids.map((id) => {
      const node = nodes.get(id)!;
      if (node.pinned) {
        return { id, center: node.y + node.height / 2, fixed: true };
      }
      const desired = desiredFromEarlier(id);
      const center = desired.length > 0 ? median(desired) : node.y + node.height / 2;
      return { id, center, fixed: false };
    });
    targets.sort((a, b) =>
      a.center === b.center ? byId(a.id, b.id) : a.center - b.center,
    );

    // Pinned nodes hold their position but still claim the space they occupy, so free
    // nodes settle into the gaps between them instead of stacking on top of them. The
    // old single-cursor sweep let a free node that sorted before a pinned one land on
    // the pinned node's row, because the pinned node only advanced the cursor when the
    // sweep reached it. Fixed nodes are placed first so every free node sees the full
    // set of occupied rows; overlapping pinned nodes are left as the user placed them.
    const placed: Array<{ top: number; bottom: number }> = [];
    for (const target of [...targets].sort((a, b) =>
      a.center === b.center ? byId(a.id, b.id) : a.center - b.center,
    )) {
      if (!target.fixed) continue;
      const node = nodes.get(target.id)!;
      placed.push({ top: node.y, bottom: node.y + node.height });
    }
    for (const target of [...targets].sort((a, b) =>
      a.center === b.center ? byId(a.id, b.id) : a.center - b.center,
    )) {
      if (target.fixed) continue;
      const node = nodes.get(target.id)!;
      let top = target.center - node.height / 2;
      let settled = false;
      while (!settled) {
        settled = true;
        for (const slot of placed) {
          if (top + node.height > slot.top && top < slot.bottom) {
            top = Math.max(top, slot.bottom + NODE_GAP);
            settled = false;
          }
        }
      }
      node.y = top;
      placed.push({ top, bottom: top + node.height });
    }
  }
}

function layoutGroups(
  groups: GraphGroup[],
  nodes: Map<string, LaidOutNode>,
): LaidOutGroup[] {
  const result: LaidOutGroup[] = [];
  for (const group of [...groups].sort((a, b) => byId(a.id, b.id))) {
    const members = group.members
      .map((id) => nodes.get(id))
      .filter((node): node is LaidOutNode => node !== undefined);
    if (members.length === 0) continue;
    const left = Math.min(...members.map((node) => node.x));
    const top = Math.min(...members.map((node) => node.y));
    const right = Math.max(...members.map((node) => node.x + node.width));
    const bottom = Math.max(...members.map((node) => node.y + node.height));
    result.push({
      id: group.id,
      label: group.label,
      kind: group.kind,
      x: left - GROUP_PADDING,
      y: top - GROUP_PADDING - GROUP_HEADER,
      width: right - left + GROUP_PADDING * 2,
      height: bottom - top + GROUP_PADDING * 2 + GROUP_HEADER,
    });
  }
  return result;
}

export interface RouteContext {
  /** Slot ids per edge key, in layer order, for edges spanning 2+ layers. */
  chains?: Map<string, string[]>;
  bends?: Map<string, Bend>;
  /** Bottom of the graph as the layout sees it (nodes plus group frames). Back
   *  edges run below this line. Callers without a layout default to the nodes. */
  backLaneBase?: number;
}

/** Orthogonal routing with rounded corners.
 *
 *  Edges leave the right face of the earlier node and enter the left face of the later
 *  one, at distributed anchors, along their own lane, and — when they span more than one
 *  layer — through the slots reserved for them so they pass between nodes instead of
 *  over them. Same-layer edges bow vertically.
 */
export function routeEdges(
  edges: GraphEdge[],
  nodes: Map<string, LaidOutNode>,
  context: RouteContext = {},
): RoutedEdge[] {
  const present = uniqueEdges(edges, new Set(nodes.keys()));
  const anchors = faceAnchors(present, nodes);
  const lanes = laneOffsets(present, nodes);
  const chains = context.chains ?? new Map<string, string[]>();
  const bends = context.bends ?? new Map<string, Bend>();

  // Back edges (arrow returning to a deeper layer) travel below the graph, each in
  // its own lane. Their order is the deterministic edge order, so the layout stays
  // reproducible.
  const backLanes = new Map<string, number>();
  let backLaneCount = 0;
  for (const edge of present) {
    const from = nodes.get(edge.from);
    const to = nodes.get(edge.to);
    if (!from || !to) continue;
    if (isConversationNode(edge.from) || isConversationNode(edge.to)) continue;
    if (to.layer > from.layer) backLanes.set(edgeKey(edge), backLaneCount++);
  }

  const routed: RoutedEdge[] = [];
  for (const edge of present) {
    const from = nodes.get(edge.from)!;
    const to = nodes.get(edge.to)!;
    const key = edgeKey(edge);
    const id = key;

    // The conversation sits above the graph, so its edges descend from its bottom face
    // instead of running left-to-right like dependency edges.
    if (isConversationNode(edge.to) || isConversationNode(edge.from)) {
      const source = isConversationNode(edge.to) ? to : from;
      const sink = isConversationNode(edge.to) ? from : to;
      const start = { x: source.x + source.width / 2, y: source.y + source.height };
      const end = { x: sink.x + sink.width / 2, y: sink.y };
      routed.push({
        ...edge,
        id,
        path: descendingPath(start, end),
        midpoint: { x: (start.x + end.x) / 2, y: (start.y + end.y) / 2 },
      });
      continue;
    }

    if (to.layer === from.layer) {
      const path = sameLayerPath(to, from, nodes);
      routed.push({
        ...edge,
        id,
        path,
        midpoint: sameLayerMidpoint(to, from, nodes),
      });
      continue;
    }

    // A back edge points against the reading direction: the arrow goes from an
    // earlier layer back to a deeper one. Straight routing would drag one line
    // across every node and hover label between the two faces (KNOWN_ISSUES
    // #12), so it drops below the graph and runs along its own lane there.
    if (to.layer > from.layer) {
      const graphBottom = context.backLaneBase
        ?? Math.max(...[...nodes.values()].map((node) => node.y + node.height));
      const laneY =
        graphBottom + BACK_EDGE_CLEARANCE + (backLanes.get(key) ?? 0) * BACK_EDGE_STEP;
      const startX = from.x + from.width / 2;
      const endX = to.x + to.width / 2;
      routed.push({
        ...edge,
        id,
        path: cornerPath([
          { x: startX, y: from.y + from.height },
          { x: startX, y: laneY },
          { x: endX, y: laneY },
          { x: endX, y: to.y + to.height },
        ]),
        midpoint: { x: (startX + endX) / 2, y: laneY },
      });
      continue;
    }

    // Draw in dependency direction: dependency (to) -> dependent (from).
    const start = {
      x: to.x + to.width,
      y: anchors.get(`out${SEP}${key}`) ?? to.y + to.height / 2,
    };
    const end = {
      x: from.x,
      y: anchors.get(`in${SEP}${key}`) ?? from.y + from.height / 2,
    };
    const lane = lanes.get(key) ?? 0;
    const waypoints = (chains.get(key) ?? [])
      .map((slot) => bends.get(slot))
      .filter((bend): bend is Bend => bend !== undefined);

    let path = orthogonalPath(start, end, lane);
    if (waypoints.length > 0) {
      const direct = path;
      path = pathIntersectsNodes(direct, nodes, new Set([edge.from, edge.to]))
        ? throughWaypoints(start, end, waypoints, lane)
        : direct;
    }
    routed.push({
      ...edge,
      id,
      path,
      midpoint:
        waypoints.length > 0
          ? // The label belongs on the run the eye follows, which for a long edge is a
            // middle slot rather than the straight line between its endpoints — that
            // line passes through the nodes the edge was rerouted to avoid.
            { x: waypoints[Math.floor(waypoints.length / 2)]!.x, y: waypoints[Math.floor(waypoints.length / 2)]!.y }
          : { x: (start.x + end.x) / 2 + lane, y: (start.y + end.y) / 2 },
    });
  }
  return routed;
}

/** Right, down or up, right — with the vertical run offset into this edge's own lane so
 *  two edges crossing the same gap never share it. */
function orthogonalPath(
  start: { x: number; y: number },
  end: { x: number; y: number },
  lane = 0,
): string {
  if (Math.abs(start.y - end.y) < 1) {
    return `M ${round(start.x)} ${round(start.y)} L ${round(end.x)} ${round(end.y)}`;
  }
  const midX = (start.x + end.x) / 2 + lane;
  return cornerPath([
    { x: start.x, y: start.y },
    { x: midX, y: start.y },
    { x: midX, y: end.y },
    { x: end.x, y: end.y },
  ]);
}

/** A long edge, routed through the slots reserved for it in each layer it crosses.
 *
 *  Between consecutive slots the line runs at that slot's height, so it travels through
 *  the horizontal band the layout kept clear for it rather than cutting across the nodes
 *  stacked in those columns.
 */
function throughWaypoints(
  start: { x: number; y: number },
  end: { x: number; y: number },
  waypoints: Bend[],
  lane: number,
): string {
  const points: Array<{ x: number; y: number }> = [{ x: start.x, y: start.y }];
  let previousY = start.y;

  for (const bend of waypoints) {
    // Turn into the slot's band before the column, then run through it.
    const entry = bend.x - NODE_WIDTH / 2 - LAYER_GAP / 2 + lane;
    points.push({ x: entry, y: previousY });
    points.push({ x: entry, y: bend.y });
    points.push({ x: bend.x + NODE_WIDTH / 2, y: bend.y });
    previousY = bend.y;
  }

  const exit = end.x - LAYER_GAP / 2 + lane;
  points.push({ x: exit, y: previousY });
  points.push({ x: exit, y: end.y });
  points.push({ x: end.x, y: end.y });
  return cornerPath(points);
}

/** Vertical drop with rounded corners, for conversation edges. Mirrors
 *  `orthogonalPath` with the axes swapped: down, across at the midpoint, then down. */
function descendingPath(
  start: { x: number; y: number },
  end: { x: number; y: number },
): string {
  if (Math.abs(start.x - end.x) < 1) {
    return `M ${round(start.x)} ${round(start.y)} L ${round(end.x)} ${round(end.y)}`;
  }
  const midY = (start.y + end.y) / 2;
  return cornerPath([
    { x: start.x, y: start.y },
    { x: start.x, y: midY },
    { x: end.x, y: midY },
    { x: end.x, y: end.y },
  ]);
}

/** True when no node stands vertically between two same-layer nodes. */
function nodesBetweenInLayer(
  top: LaidOutNode,
  bottom: LaidOutNode,
  nodes: Map<string, LaidOutNode>,
): boolean {
  for (const node of nodes.values()) {
    if (node.layer !== top.layer) continue;
    if (node === top || node === bottom) continue;
    if (node.y > top.y + top.height && node.y + node.height < bottom.y) return true;
  }
  return false;
}

/** Two nodes in one layer: straight vertical when nothing stands between them,
 *  otherwise bow out to the side so the line never cuts through the node between. */
function sameLayerPath(
  from: LaidOutNode,
  to: LaidOutNode,
  nodes: Map<string, LaidOutNode>,
): string {
  const top = from.y < to.y ? from : to;
  const bottom = from.y < to.y ? to : from;
  const x = from.x + from.width / 2;
  const startY = top.y + top.height;
  const endY = bottom.y;
  if (!nodesBetweenInLayer(top, bottom, nodes)) {
    return `M ${round(x)} ${round(startY)} L ${round(x)} ${round(endY)}`;
  }
  const bow = Math.max(28, Math.abs(endY - startY) / 2);
  return [
    `M ${round(x)} ${round(startY)}`,
    `C ${round(x + bow)} ${round(startY)} ${round(x + bow)} ${round(endY)} ${round(x)} ${round(endY)}`,
  ].join(" ");
}

/** The visual middle of a same-layer bow, for its hover label.
 *
 *  A cubic's midpoint is not the average of its endpoints: at t=0.5 the curve sits three
 *  quarters of the way out toward the control points. Using the endpoint average would
 *  park the label inside the node the bow travels around.
 */
function sameLayerMidpoint(
  from: LaidOutNode,
  to: LaidOutNode,
  nodes: Map<string, LaidOutNode>,
): { x: number; y: number } {
  const top = from.y < to.y ? from : to;
  const bottom = from.y < to.y ? to : from;
  const x = from.x + from.width / 2;
  const startY = top.y + top.height;
  const endY = bottom.y;
  if (!nodesBetweenInLayer(top, bottom, nodes)) {
    return { x, y: (startY + endY) / 2 };
  }
  const bow = Math.max(28, Math.abs(endY - startY) / 2);
  return { x: x + bow * 0.75, y: (startY + endY) / 2 };
}

/** Polyline with rounded corners.
 *
 *  Each interior vertex becomes an arc whose radius is capped by half of its shorter
 *  adjacent segment, so a tight bend degrades toward a sharp corner instead of the arc
 *  overshooting and doubling the line back on itself. Collinear and zero-length vertices
 *  are dropped first, which is what keeps `A` commands out of straight runs.
 */
function cornerPath(raw: Array<{ x: number; y: number }>): string {
  const points: Array<{ x: number; y: number }> = [];
  for (const point of raw) {
    const last = points[points.length - 1];
    if (last && Math.abs(last.x - point.x) < 0.5 && Math.abs(last.y - point.y) < 0.5) {
      continue;
    }
    points.push(point);
  }
  if (points.length < 2) {
    const only = points[0] ?? { x: 0, y: 0 };
    return `M ${round(only.x)} ${round(only.y)}`;
  }

  const segments: string[] = [`M ${round(points[0]!.x)} ${round(points[0]!.y)}`];
  for (let index = 1; index < points.length - 1; index += 1) {
    const previous = points[index - 1]!;
    const corner = points[index]!;
    const next = points[index + 1]!;

    const inLength = Math.hypot(corner.x - previous.x, corner.y - previous.y);
    const outLength = Math.hypot(next.x - corner.x, next.y - corner.y);
    const inUnit = { x: (corner.x - previous.x) / inLength, y: (corner.y - previous.y) / inLength };
    const outUnit = { x: (next.x - corner.x) / outLength, y: (next.y - corner.y) / outLength };

    // Cross product sign gives the turn direction, which is the arc's sweep flag. Zero
    // means the vertex is collinear and there is no corner to round.
    const cross = inUnit.x * outUnit.y - inUnit.y * outUnit.x;
    const radius = Math.min(CORNER_RADIUS, inLength / 2, outLength / 2);
    if (Math.abs(cross) < 1e-6 || radius < 0.5) {
      segments.push(`L ${round(corner.x)} ${round(corner.y)}`);
      continue;
    }

    const entry = { x: corner.x - inUnit.x * radius, y: corner.y - inUnit.y * radius };
    const exit = { x: corner.x + outUnit.x * radius, y: corner.y + outUnit.y * radius };
    segments.push(`L ${round(entry.x)} ${round(entry.y)}`);
    segments.push(
      `A ${round(radius)} ${round(radius)} 0 0 ${cross > 0 ? 1 : 0} ${round(exit.x)} ${round(exit.y)}`,
    );
  }
  const last = points[points.length - 1]!;
  segments.push(`L ${round(last.x)} ${round(last.y)}`);
  return segments.join(" ");
}

/** Sample an SVG path into points, so clearance can be checked without a path parser.
 *  Arc commands are treated as a straight hop to their endpoint, which is conservative:
 *  an arc never bulges outside the corner its two segments already define. */
export function samplePathPoints(path: string): Array<{ x: number; y: number }> {
  const points: Array<{ x: number; y: number }> = [];
  const tokens = path.trim().split(/\s+/);
  let index = 0;
  while (index < tokens.length) {
    const command = tokens[index];
    if (command === "M" || command === "L") {
      points.push({ x: Number(tokens[index + 1]), y: Number(tokens[index + 2]) });
      index += 3;
    } else if (command === "A") {
      points.push({ x: Number(tokens[index + 6]), y: Number(tokens[index + 7]) });
      index += 8;
    } else if (command === "C") {
      points.push({ x: Number(tokens[index + 5]), y: Number(tokens[index + 6]) });
      index += 7;
    } else {
      index += 1;
    }
  }
  return points;
}

/** True when a sampled path crosses any node other than the excluded endpoints. */
function pathIntersectsNodes(
  path: string,
  nodes: Map<string, LaidOutNode>,
  exclude: Set<string>,
): boolean {
  const points = samplePathPoints(path);
  for (let index = 1; index < points.length; index += 1) {
    const from = points[index - 1]!;
    const to = points[index]!;
    const length = Math.hypot(to.x - from.x, to.y - from.y);
    const steps = Math.max(1, Math.ceil(length / 6));
    for (let sample = 0; sample <= steps; sample += 1) {
      const x = from.x + ((to.x - from.x) * sample) / steps;
      const y = from.y + ((to.y - from.y) * sample) / steps;
      for (const [id, node] of nodes) {
        if (exclude.has(id)) continue;
        if (
          x > node.x + 1 &&
          x < node.x + node.width - 1 &&
          y > node.y + 1 &&
          y < node.y + node.height - 1
        ) {
          return true;
        }
      }
    }
  }
  return false;
}

function computeBounds(
  nodes: Map<string, LaidOutNode>,
  groups: LaidOutGroup[],
): { width: number; height: number } {
  let width = 0;
  let height = 0;
  for (const node of nodes.values()) {
    width = Math.max(width, node.x + node.width);
    height = Math.max(height, node.y + node.height);
  }
  for (const group of groups) {
    width = Math.max(width, group.x + group.width);
    height = Math.max(height, group.y + group.height);
  }
  return { width, height };
}
