import { describe, expect, it } from "vitest";
import {
  EDGE_DRAW_MS,
  MAX_STAGGER_MS,
  NODE_EMERGE_MS,
  STAGGER_MS,
  edgeProgress,
  emptyEmergence,
  isAnimating,
  nodeProgress,
  observe,
  prune,
} from "./emergence";

const T0 = 10_000;

describe("the first architecture is adopted silently", () => {
  it("does not animate an existing project on open", () => {
    // Opening a finished project must not replay its whole construction.
    const state = observe(emptyEmergence(), ["a", "b"], ["a b uses"], T0);

    expect(state.nodes.size).toBe(0);
    expect(state.edges.size).toBe(0);
    expect(isAnimating(state, T0)).toBe(false);
    expect(nodeProgress(state, "a", T0)).toBe(1);
  });

  it("still records what it saw, so later additions are recognised as new", () => {
    let state = observe(emptyEmergence(), ["a"], [], T0);
    state = observe(state, ["a", "b"], [], T0);

    expect(state.nodes.has("a")).toBe(false);
    expect(state.nodes.has("b")).toBe(true);
  });
});

describe("later arrivals animate", () => {
  function primed() {
    return observe(emptyEmergence(), [], [], T0);
  }

  it("starts a node at zero progress and finishes at one", () => {
    const state = observe(primed(), ["a"], [], T0);

    expect(nodeProgress(state, "a", T0)).toBe(0);
    expect(nodeProgress(state, "a", T0 + NODE_EMERGE_MS / 2)).toBeCloseTo(0.5);
    expect(nodeProgress(state, "a", T0 + NODE_EMERGE_MS)).toBe(1);
  });

  it("clamps rather than overshooting after the entrance ends", () => {
    const state = observe(primed(), ["a"], [], T0);
    expect(nodeProgress(state, "a", T0 + NODE_EMERGE_MS * 10)).toBe(1);
  });

  it("staggers a batch so the graph grows instead of flashing", () => {
    const state = observe(primed(), ["a", "b", "c"], [], T0);

    // First is immediate; each subsequent one starts later.
    expect(nodeProgress(state, "a", T0)).toBe(0);
    expect(nodeProgress(state, "b", T0)).toBe(0);
    expect(state.nodes.get("b")! - state.nodes.get("a")!).toBe(STAGGER_MS);
    expect(state.nodes.get("c")! - state.nodes.get("a")!).toBe(STAGGER_MS * 2);
  });

  it("caps the stagger so a large batch still completes promptly", () => {
    const many = Array.from({ length: 60 }, (_, i) => `n${String(i).padStart(2, "0")}`);
    const state = observe(primed(), many, [], T0);
    const offsets = many.map((id) => state.nodes.get(id)! - T0);

    expect(Math.max(...offsets)).toBe(MAX_STAGGER_MS);
  });

  it("orders the stagger deterministically", () => {
    const first = observe(primed(), ["c", "a", "b"], [], T0);
    const second = observe(primed(), ["b", "c", "a"], [], T0);

    for (const id of ["a", "b", "c"]) {
      expect(first.nodes.get(id)).toBe(second.nodes.get(id));
    }
  });

  it("never animates the same node twice", () => {
    let state = observe(primed(), ["a"], [], T0);
    const firstStart = state.nodes.get("a");
    state = observe(state, ["a"], [], T0 + 5_000);

    expect(state.nodes.get("a")).toBe(firstStart);
  });
});

describe("edges wait for their endpoints", () => {
  function primed() {
    return observe(emptyEmergence(), [], [], T0);
  }

  it("starts drawing after the node entrance is underway", () => {
    // Drawing a line to a node that has not appeared yet reads as a glitch.
    const state = observe(primed(), ["a"], ["a b uses"], T0);

    expect(state.edges.get("a b uses")!).toBeGreaterThan(state.nodes.get("a")!);
    expect(edgeProgress(state, "a b uses", T0)).toBe(0);
  });

  it("completes its draw-in", () => {
    const state = observe(primed(), [], ["a b uses"], T0);
    const startedAt = state.edges.get("a b uses")!;

    expect(edgeProgress(state, "a b uses", startedAt)).toBe(0);
    expect(edgeProgress(state, "a b uses", startedAt + EDGE_DRAW_MS)).toBe(1);
  });

  it("treats an unknown edge as already drawn", () => {
    // Anything not mid-animation renders at rest, which is what a reload needs.
    expect(edgeProgress(primed(), "nope", T0)).toBe(1);
  });
});

describe("the animation clock", () => {
  function primed() {
    return observe(emptyEmergence(), [], [], T0);
  }

  it("reports activity while anything is arriving", () => {
    const state = observe(primed(), ["a"], [], T0);
    expect(isAnimating(state, T0)).toBe(true);
    expect(isAnimating(state, T0 + NODE_EMERGE_MS - 1)).toBe(true);
  });

  it("goes quiet once everything has landed", () => {
    const state = observe(primed(), ["a"], ["a b uses"], T0);
    const latest = Math.max(
      ...[...state.nodes.values()].map((t) => t + NODE_EMERGE_MS),
      ...[...state.edges.values()].map((t) => t + EDGE_DRAW_MS),
    );
    expect(isAnimating(state, latest + 1)).toBe(false);
  });

  it("prunes finished entries so the maps stay bounded", () => {
    let state = observe(primed(), ["a", "b"], ["a b uses"], T0);
    state = prune(state, T0 + EDGE_DRAW_MS + MAX_STAGGER_MS + NODE_EMERGE_MS + 1);

    expect(state.nodes.size).toBe(0);
    expect(state.edges.size).toBe(0);
    // Pruning must not forget what it has seen, or the node animates again.
    expect(state.known.has("a")).toBe(true);
    expect(state.knownEdges.has("a b uses")).toBe(true);
  });

  it("keeps unfinished entries when pruning mid-flight", () => {
    let state = observe(primed(), ["a"], [], T0);
    state = prune(state, T0 + NODE_EMERGE_MS / 2);

    expect(state.nodes.size).toBe(1);
  });
});
