/** Single source of truth for selection and commands.
 *
 *  The canvas is this state's spatial projection; the Outline is its linear one. Both
 *  dispatch the same commands, which is what makes keyboard parity structural rather
 *  than a set of extra shortcuts bolted on later. Adding the Outline after the canvas
 *  had shipped would have meant inventing a "selected node" concept the canvas never
 *  had, and then threading it back through focus and window-bar behavior.
 */

import { useCallback, useMemo, useRef, useState } from "react";
import type { Architecture, Camera, NodeStatus, UiState } from "./types";
import { WINDOW_BAR_SLOTS } from "./constants";
import { CONVERSATION_NODE_ID, isConversationNode } from "./conversationNode";

export interface GraphSelection {
  nodeId: string | null;
  edgeId: string | null;
  groupId: string | null;
}

export type GraphCommand =
  | { type: "select"; nodeId: string | null }
  | { type: "selectEdge"; edgeId: string | null }
  | { type: "selectGroup"; groupId: string | null }
  | { type: "focus"; nodeId: string }
  | { type: "exitFocus" }
  /** Fly to the conversation and enter it — the "go home" gesture. Bound to Home, and
   *  to Esc when nothing is focused, so there is always one keystroke back to where the
   *  user can type. Without it, zooming out to a 40-node map leaves no obvious way back
   *  into the dialogue. */
  | { type: "focusConversation" }
  | { type: "ask"; nodeId: string }
  | { type: "editRequirement"; nodeId: string }
  | { type: "toggleWindowBar"; nodeId: string }
  | { type: "jumpToSlot"; slot: number }
  | { type: "togglePin"; nodeId: string }
  | { type: "toggleOutlineGroup"; groupId: string }
  | { type: "fit" }
  | { type: "centerNode"; nodeId: string }
  /** A node was dragged to a place the user chose.
   *
   *  Pins it in the same command rather than leaving that to a second step: an unpinned
   *  node blends back toward its computed slot on the next layout pass, so dragging
   *  without pinning would visibly undo itself. Dropping a node IS placing it.
   */
  | { type: "moveNode"; nodeId: string; x: number; y: number }
  /** Release a node back to the layout engine. The pointer equivalent of undoing a
   *  drag, and the only way back to automatic placement once a node is pinned. */
  | { type: "releaseNode"; nodeId: string };

export interface GraphStore {
  selection: GraphSelection;
  focusedId: string | null;
  camera: Camera;
  /** A camera command dispatched while no canvas was mounted to handle it.
   *
   *  The Outline can ask to center a node, which switches projections — but the command
   *  fires before the canvas mounts, so a plain effect subscriber never sees it. The
   *  canvas claims this on mount instead of the request being silently dropped.
   */
  pendingCamera: GraphCommand | null;
  claimPendingCamera: () => GraphCommand | null;
  windowBar: string[];
  pinned: Record<string, boolean>;
  outlineExpanded: string[];
  statuses: Record<string, NodeStatus>;
  setCamera: (camera: Camera) => void;
  dispatch: (command: GraphCommand) => void;
  /** Consumers (canvas, focus layer) register side effects for commands that need a
   *  viewport, so the store itself stays free of DOM concerns and testable. */
  registerEffect: (handler: (command: GraphCommand) => void) => () => void;
}

export function useGraphStore(
  architecture: Architecture | null,
  initial: UiState | null,
): GraphStore {
  const [selection, setSelection] = useState<GraphSelection>({
    nodeId: null,
    edgeId: null,
    groupId: null,
  });
  const [focusedId, setFocusedId] = useState<string | null>(null);
  const [camera, setCamera] = useState<Camera>(initial?.camera ?? { x: 0, y: 0, scale: 1 });
  const [windowBar, setWindowBar] = useState<string[]>(initial?.window_bar ?? []);
  const [outlineExpanded, setOutlineExpanded] = useState<string[]>(
    initial?.outline_expanded ?? [],
  );
  const [pinned, setPinned] = useState<Record<string, boolean>>(() => {
    const map: Record<string, boolean> = {};
    for (const [id, node] of Object.entries(initial?.nodes ?? {})) {
      if (node.pinned) map[id] = true;
    }
    return map;
  });

  // Stage 1 has no execution, so every node is pending. Stage 5 replaces this.
  // The conversation is excluded: it is never generated, so "待生成" would be wrong,
  // and a status dot on it would imply it participates in scheduling.
  const statuses = useMemo(() => {
    const map: Record<string, NodeStatus> = {};
    for (const module of architecture?.modules ?? []) {
      if (isConversationNode(module.id)) continue;
      map[module.id] = "pending";
    }
    return map;
  }, [architecture]);

  const effects = useRef(new Set<(command: GraphCommand) => void>());
  const pendingCamera = useRef<GraphCommand | null>(null);

  const registerEffect = useCallback((handler: (command: GraphCommand) => void) => {
    effects.current.add(handler);
    return () => {
      effects.current.delete(handler);
    };
  }, []);

  const claimPendingCamera = useCallback((): GraphCommand | null => {
    const claimed = pendingCamera.current;
    pendingCamera.current = null;
    return claimed;
  }, []);

  const dispatch = useCallback((command: GraphCommand) => {
    switch (command.type) {
      case "select":
        setSelection({ nodeId: command.nodeId, edgeId: null, groupId: null });
        break;
      case "selectEdge":
        setSelection({ nodeId: null, edgeId: command.edgeId, groupId: null });
        break;
      case "selectGroup":
        setSelection({ nodeId: null, edgeId: null, groupId: command.groupId });
        break;
      case "focus":
        setSelection({ nodeId: command.nodeId, edgeId: null, groupId: null });
        setFocusedId(command.nodeId);
        break;
      case "focusConversation":
        setSelection({ nodeId: CONVERSATION_NODE_ID, edgeId: null, groupId: null });
        setFocusedId(CONVERSATION_NODE_ID);
        break;
      case "exitFocus":
        setFocusedId(null);
        break;
      case "toggleWindowBar":
        setWindowBar((current) =>
          current.includes(command.nodeId)
            ? current.filter((id) => id !== command.nodeId)
            : current.length >= WINDOW_BAR_SLOTS
              ? current
              : [...current, command.nodeId],
        );
        break;
      case "togglePin":
        setPinned((current) => ({ ...current, [command.nodeId]: !current[command.nodeId] }));
        break;
      case "moveNode":
        // The coordinates themselves live in the canvas, which owns layout; the store
        // only records that this node is now placed by hand.
        setPinned((current) => ({ ...current, [command.nodeId]: true }));
        break;
      case "releaseNode":
        setPinned((current) => {
          const next = { ...current };
          delete next[command.nodeId];
          return next;
        });
        break;
      case "toggleOutlineGroup":
        setOutlineExpanded((current) =>
          current.includes(command.groupId)
            ? current.filter((id) => id !== command.groupId)
            : [...current, command.groupId],
        );
        break;
      default:
        break;
    }

    // Camera commands need a viewport. If nothing is listening — the Outline is showing
    // and the canvas has not mounted — hold the request so the canvas can honour it once
    // it appears, rather than dropping it.
    if (
      (command.type === "centerNode" || command.type === "fit") &&
      effects.current.size === 0
    ) {
      pendingCamera.current = command;
    }

    for (const handler of effects.current) handler(command);
  }, []);

  return {
    selection,
    focusedId,
    camera,
    pendingCamera: pendingCamera.current,
    claimPendingCamera,
    windowBar,
    pinned,
    outlineExpanded,
    statuses,
    setCamera,
    dispatch,
    registerEffect,
  };
}
