/** Focus entry/exit as a state machine, independent of the DOM.
 *
 *  `decideFocus` already has its own tests; these cover the contract between it and the
 *  camera, which is where an oscillating focus layer would come from: exit restores the
 *  scale below the entry threshold, so the next event cannot immediately re-enter.
 */

import { describe, expect, it } from "vitest";
import {
  decideFocus,
  centerOn,
  FOCUS_ENTER_SCALE,
  FOCUS_EXIT_SCALE,
  FOCUS_RESTORE_SCALE,
  INTENT_MIN_COVERAGE,
  INTENT_MIN_DELTA,
  MAX_SCALE,
  nodeCoverage,
} from "./zoom";
import { CONVERSATION_NODE_ID } from "./conversationNode";

const VIEWPORT = { width: 1200, height: 800 };
const NODE = { x: 0, y: 0, width: 420, height: 132 };

describe("the exit camera cannot re-trigger entry", () => {
  it("restores below the entry threshold", () => {
    // The whole reason FOCUS_RESTORE_SCALE exists. If it landed at or above the entry
    // threshold, releasing a gesture would bounce the user straight back in.
    expect(FOCUS_RESTORE_SCALE).toBeLessThan(FOCUS_ENTER_SCALE);
  });

  it("restores at or below the exit threshold, so the band is not re-armed", () => {
    expect(FOCUS_RESTORE_SCALE).toBeLessThanOrEqual(FOCUS_EXIT_SCALE);
  });

  it("holds instead of entering right after an exit", () => {
    // Same node still under the cursor, full coverage, plenty of intent: the only thing
    // stopping re-entry is the restored scale.
    const decision = decideFocus({
      scale: FOCUS_RESTORE_SCALE,
      focusedId: null,
      hoveredId: "core",
      coverage: 1,
      intent: INTENT_MIN_DELTA * 4,
    });
    expect(decision.action).toBe("hold");
  });
});

describe("entering by camera flight", () => {
  it("lands past the entry threshold, so camera and focus agree", () => {
    // Home / Outline-Enter set focus directly. If the camera stayed below the entry
    // threshold, the next wheel notch would read as "already exiting".
    const camera = centerOn(NODE, VIEWPORT, FOCUS_ENTER_SCALE);
    expect(camera.scale).toBeGreaterThanOrEqual(FOCUS_ENTER_SCALE);

    const decision = decideFocus({
      scale: camera.scale,
      focusedId: "core",
      hoveredId: null,
      coverage: 0,
      intent: 0,
    });
    expect(decision.action).toBe("hold");
  });

  it("centers the node it flew to", () => {
    const camera = centerOn(NODE, VIEWPORT, FOCUS_ENTER_SCALE);
    const centerX = camera.x + (NODE.x + NODE.width / 2) * camera.scale;
    const centerY = camera.y + (NODE.y + NODE.height / 2) * camera.scale;
    expect(centerX).toBeCloseTo(VIEWPORT.width / 2);
    expect(centerY).toBeCloseTo(VIEWPORT.height / 2);
  });
});

describe("the conversation is focused like any other node", () => {
  it("needs the same intent gating as a module", () => {
    // No special case: a flick over the conversation must not teleport into it either.
    const flick = decideFocus({
      scale: FOCUS_ENTER_SCALE,
      focusedId: null,
      hoveredId: CONVERSATION_NODE_ID,
      coverage: 1,
      intent: INTENT_MIN_DELTA - 1,
    });
    expect(flick.action).toBe("hold");

    const deliberate = decideFocus({
      scale: FOCUS_ENTER_SCALE,
      focusedId: null,
      hoveredId: CONVERSATION_NODE_ID,
      coverage: 1,
      intent: INTENT_MIN_DELTA,
    });
    expect(deliberate).toEqual({ action: "enter", id: CONVERSATION_NODE_ID });
  });

  it("exits on the same threshold as a module", () => {
    const decision = decideFocus({
      scale: FOCUS_EXIT_SCALE,
      focusedId: CONVERSATION_NODE_ID,
      hoveredId: null,
      coverage: 0,
      intent: 0,
    });
    expect(decision.action).toBe("exit");
  });

  it("is large enough at the entry scale to pass the coverage gate", () => {
    const coverage = nodeCoverage(NODE, FOCUS_ENTER_SCALE, VIEWPORT);
    expect(coverage).toBeGreaterThanOrEqual(INTENT_MIN_COVERAGE);
  });
});

describe("the coverage gate is actually reachable", () => {
  // Guards a bug this suite found: INTENT_MIN_COVERAGE was 0.25, which no node can
  // reach at any legal scale, so zoom-to-enter did nothing and only double-click
  // worked. These assert the property, not the constant, so changing a node's size
  // fails the test instead of silently disabling the gesture again.
  const MODULE = { width: 208, height: 116 };

  it("opens for a module below MAX_SCALE", () => {
    expect(nodeCoverage(MODULE, MAX_SCALE, VIEWPORT)).toBeGreaterThan(
      INTENT_MIN_COVERAGE,
    );
  });

  it("opens for the conversation below MAX_SCALE", () => {
    expect(nodeCoverage(NODE, MAX_SCALE, VIEWPORT)).toBeGreaterThan(
      INTENT_MIN_COVERAGE,
    );
  });

  it("stays shut when a node is small on screen", () => {
    // The gate still has to mean something: at map-band scale nothing should qualify.
    expect(nodeCoverage(MODULE, 0.75, VIEWPORT)).toBeLessThan(INTENT_MIN_COVERAGE);
  });

  it("lets a deliberate zoom on a module reach focus", () => {
    const decision = decideFocus({
      scale: FOCUS_ENTER_SCALE,
      focusedId: null,
      hoveredId: "core",
      coverage: nodeCoverage(MODULE, FOCUS_ENTER_SCALE, VIEWPORT),
      intent: INTENT_MIN_DELTA,
    });
    expect(decision).toEqual({ action: "enter", id: "core" });
  });
});

describe("focus never oscillates at the boundary", () => {
  it("holds across the whole hysteresis band while focused", () => {
    for (const scale of [FOCUS_EXIT_SCALE + 0.01, 1.5, FOCUS_ENTER_SCALE - 0.01]) {
      expect(
        decideFocus({
          scale,
          focusedId: "core",
          hoveredId: "core",
          coverage: 1,
          intent: INTENT_MIN_DELTA * 2,
        }).action,
      ).toBe("hold");
    }
  });

  it("holds across the same band while not focused", () => {
    // The band means "keep whatever mode you are in", in both directions.
    for (const scale of [FOCUS_EXIT_SCALE + 0.01, 1.5, FOCUS_ENTER_SCALE - 0.01]) {
      expect(
        decideFocus({
          scale,
          focusedId: null,
          hoveredId: "core",
          coverage: 1,
          intent: INTENT_MIN_DELTA * 2,
        }).action,
      ).toBe("hold");
    }
  });
});
