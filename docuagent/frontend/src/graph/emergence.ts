/** Which nodes and edges are new, and how long their entrance has left to run.
 *
 *  Kept separate from the components for the usual reason in this codebase: the timing
 *  rules are the part worth asserting, and they need no DOM. A component asks "is this
 *  node still arriving, and how far through?" and renders accordingly.
 *
 *  ## Why this is diff-driven rather than stream-driven
 *
 *  Stage 2's SSE protocol emits one event per node, and the obvious design is to animate
 *  on each event. But the architecture also arrives whole today (a single JSON response),
 *  and will keep arriving whole on reload and when opening an existing project. Deriving
 *  "what is new" from consecutive architectures covers both: a stream produces a series
 *  of one-node diffs, and a bulk response produces one large diff. The animation code
 *  does not care which happened, so wiring up SSE later changes no rendering logic.
 *
 *  A reload must NOT replay the whole graph as an arrival, so the first architecture a
 *  session sees is adopted silently. Only later additions animate.
 */

export const NODE_EMERGE_MS = 420;
export const EDGE_DRAW_MS = 520;
/** Each subsequent node in one batch starts slightly later, so a bulk arrival reads as
 *  the graph growing rather than everything flashing at once. Capped so a 40-node batch
 *  does not take half a minute to finish. */
export const STAGGER_MS = 55;
export const MAX_STAGGER_MS = 900;

export interface EmergenceState {
  /** Node id to the timestamp its entrance began. */
  nodes: Map<string, number>;
  /** Edge id to the timestamp its draw-in began. */
  edges: Map<string, number>;
  /** Ids seen at least once, so a node is never animated twice. */
  known: Set<string>;
  knownEdges: Set<string>;
  /** False until the first architecture has been adopted. */
  primed: boolean;
}

export function emptyEmergence(): EmergenceState {
  return {
    nodes: new Map(),
    edges: new Map(),
    known: new Set(),
    knownEdges: new Set(),
    primed: false,
  };
}

/** Fold a new set of ids into the state, returning a new state.
 *
 *  The first call primes: everything present is recorded as known with no animation,
 *  because an existing project opening is not an arrival. Later calls animate whatever
 *  was not already known.
 */
export function observe(
  state: EmergenceState,
  nodeIds: string[],
  edgeIds: string[],
  now: number,
): EmergenceState {
  const known = new Set(state.known);
  const knownEdges = new Set(state.knownEdges);
  const nodes = new Map(state.nodes);
  const edges = new Map(state.edges);

  if (!state.primed) {
    for (const id of nodeIds) known.add(id);
    for (const id of edgeIds) knownEdges.add(id);
    return { nodes, edges, known, knownEdges, primed: true };
  }

  // Sorted so the stagger order is deterministic, matching the layout engine's
  // tie-breaking. Without this, two runs could animate the same batch in different
  // orders and the test could not assert on it.
  const arrivingNodes = nodeIds.filter((id) => !known.has(id)).sort();
  arrivingNodes.forEach((id, index) => {
    nodes.set(id, now + Math.min(index * STAGGER_MS, MAX_STAGGER_MS));
    known.add(id);
  });

  const arrivingEdges = edgeIds.filter((id) => !knownEdges.has(id)).sort();
  arrivingEdges.forEach((id, index) => {
    // Edges wait for their endpoints: drawing a line to a node that has not appeared
    // yet looks like a bug rather than a sequence.
    const delay = Math.min(index * STAGGER_MS, MAX_STAGGER_MS);
    edges.set(id, now + delay + NODE_EMERGE_MS * 0.6);
    knownEdges.add(id);
  });

  return { nodes, edges, known, knownEdges, primed: true };
}

/** First-architecture variant that animates the initial nodes/edges instead of
 *  adopting them silently. Used by the post-confirmation reveal, where the graph's
 *  first appearance IS the payoff. */
export function observeFirst(
  state: EmergenceState,
  nodeIds: string[],
  edgeIds: string[],
  now: number,
): EmergenceState {
  const known = new Set(state.known);
  const knownEdges = new Set(state.knownEdges);
  const nodes = new Map(state.nodes);
  const edges = new Map(state.edges);
  const arrivingNodes = nodeIds.filter((id) => !known.has(id)).sort();
  arrivingNodes.forEach((id, index) => {
    nodes.set(id, now + Math.min(index * STAGGER_MS, MAX_STAGGER_MS));
    known.add(id);
  });
  const arrivingEdges = edgeIds.filter((id) => !knownEdges.has(id)).sort();
  arrivingEdges.forEach((id, index) => {
    const delay = Math.min(index * STAGGER_MS, MAX_STAGGER_MS);
    edges.set(id, now + delay + NODE_EMERGE_MS * 0.6);
    knownEdges.add(id);
  });
  return { nodes, edges, known, knownEdges, primed: true };
}

/** Drop finished entries so the maps do not grow across a long session. */
export function prune(state: EmergenceState, now: number): EmergenceState {
  const nodes = new Map(state.nodes);
  const edges = new Map(state.edges);
  for (const [id, startedAt] of nodes) {
    if (now - startedAt > NODE_EMERGE_MS) nodes.delete(id);
  }
  for (const [id, startedAt] of edges) {
    if (now - startedAt > EDGE_DRAW_MS) edges.delete(id);
  }
  return { ...state, nodes, edges };
}

/** 0 before the entrance starts, 1 once finished. Used as an animation clock so a
 *  component can render a partial state on the first frame after mounting. */
export function nodeProgress(
  state: EmergenceState,
  id: string,
  now: number,
): number {
  const startedAt = state.nodes.get(id);
  if (startedAt === undefined) return 1;
  return clamp01((now - startedAt) / NODE_EMERGE_MS);
}

export function edgeProgress(
  state: EmergenceState,
  id: string,
  now: number,
): number {
  const startedAt = state.edges.get(id);
  if (startedAt === undefined) return 1;
  return clamp01((now - startedAt) / EDGE_DRAW_MS);
}

/** True while anything is still arriving, so the render loop knows to keep ticking. */
export function isAnimating(state: EmergenceState, now: number): boolean {
  for (const startedAt of state.nodes.values()) {
    if (now - startedAt <= NODE_EMERGE_MS) return true;
  }
  for (const startedAt of state.edges.values()) {
    if (now - startedAt <= EDGE_DRAW_MS) return true;
  }
  return false;
}

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}
