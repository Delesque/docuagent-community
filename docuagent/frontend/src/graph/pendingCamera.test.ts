/** Camera commands crossing a projection switch.
 *
 *  The Outline has no viewport, so Space (center this node) has to be honoured by the
 *  canvas. But asking for it also switches projections, and the switch necessarily
 *  happens before the canvas mounts — so a plain subscriber never sees the command. This
 *  was silently dropping the request until a probe caught it.
 *
 *  Modelled here rather than driven through React: what matters is the handoff rule, and
 *  the store's queue-then-claim contract is what encodes it.
 */

import { describe, expect, it } from "vitest";
import type { GraphCommand } from "./store";

/** Mirrors the store's pending-camera logic. */
function makeQueue() {
  let pending: GraphCommand | null = null;
  const handlers = new Set<(c: GraphCommand) => void>();

  return {
    dispatch(command: GraphCommand) {
      const cameraCommand = command.type === "centerNode" || command.type === "fit";
      if (cameraCommand && handlers.size === 0) pending = command;
      for (const handler of handlers) handler(command);
    },
    register(handler: (c: GraphCommand) => void) {
      handlers.add(handler);
      return () => handlers.delete(handler);
    },
    claim(): GraphCommand | null {
      const claimed = pending;
      pending = null;
      return claimed;
    },
    hasPending: () => pending !== null,
  };
}

describe("a camera command with nothing mounted to handle it", () => {
  it("is held rather than dropped", () => {
    const queue = makeQueue();
    queue.dispatch({ type: "centerNode", nodeId: "core" });
    expect(queue.hasPending()).toBe(true);
  });

  it("is delivered to the canvas when it mounts", () => {
    const queue = makeQueue();
    const moves: string[] = [];

    // Outline showing: Space asks to center a node.
    queue.dispatch({ type: "centerNode", nodeId: "core" });

    // Projection switches, canvas mounts, subscribes, then claims.
    queue.register((command) => {
      if (command.type === "centerNode") moves.push(`live:${command.nodeId}`);
    });
    const claimed = queue.claim();
    if (claimed?.type === "centerNode") moves.push(`claimed:${claimed.nodeId}`);

    expect(moves).toEqual(["claimed:core"]);
  });

  it("is claimed exactly once", () => {
    // A second canvas mount must not re-center on a stale request.
    const queue = makeQueue();
    queue.dispatch({ type: "fit" });

    expect(queue.claim()).toEqual({ type: "fit" });
    expect(queue.claim()).toBeNull();
  });

  it("is not queued when a canvas is already listening", () => {
    // The live path stays the normal one; queueing is only for the gap.
    const queue = makeQueue();
    const moves: string[] = [];
    queue.register((command) => {
      if (command.type === "centerNode") moves.push(`live:${command.nodeId}`);
    });

    queue.dispatch({ type: "centerNode", nodeId: "core" });

    expect(moves).toEqual(["live:core"]);
    expect(queue.hasPending()).toBe(false);
  });

  it("keeps only the latest request", () => {
    // Two Space presses before the canvas appears should land on the second node, not
    // replay a queue.
    const queue = makeQueue();
    queue.dispatch({ type: "centerNode", nodeId: "first" });
    queue.dispatch({ type: "centerNode", nodeId: "second" });

    expect(queue.claim()).toEqual({ type: "centerNode", nodeId: "second" });
    expect(queue.claim()).toBeNull();
  });

  it("ignores commands that do not need a viewport", () => {
    // Selection and focus are handled by the store itself and survive the switch.
    const queue = makeQueue();
    queue.dispatch({ type: "select", nodeId: "core" });
    queue.dispatch({ type: "togglePin", nodeId: "core" });

    expect(queue.hasPending()).toBe(false);
  });
});
