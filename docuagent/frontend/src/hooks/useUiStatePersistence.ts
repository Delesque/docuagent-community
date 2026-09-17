import { useCallback, useEffect, useRef } from "react";
import { saveUiState, type WorkspaceInfo } from "../api";
import type { GraphStore } from "../graph/store";
import type { Camera } from "../graph/types";

export interface UiStatePersistenceOptions {
  workspace: WorkspaceInfo | null;
  store: GraphStore;
}

export interface UiStatePersistence {
  handlePositions: (positions: Record<string, { x: number; y: number; pinned: boolean }>) => void;
  setCamera: (camera: Camera) => void;
  saveLayoutNow: () => void;
  restoreDefaultLayout: () => void;
}

/** Layout persistence for the graph canvas.
 *
 *  Positions are held in a ref rather than state because they change on every layout
 *  pass; re-rendering the shell for them would fight the animation they feed. Camera
 *  changes are debounced, while pin changes write immediately.
 */
export function useUiStatePersistence({ workspace, store }: UiStatePersistenceOptions): UiStatePersistence {
  const positionsRef = useRef<Record<string, { x: number; y: number; pinned: boolean }>>({});
  const persistTimer = useRef(0);

  const handlePositions = useCallback(
    (positions: Record<string, { x: number; y: number; pinned: boolean }>) => {
      positionsRef.current = positions;
    },
    [],
  );

  useEffect(() => () => window.clearTimeout(persistTimer.current), []);

  const persistUiState = useCallback(
    (camera: Camera) => {
      if (!workspace?.path) return;
      const path = workspace.path;
      window.clearTimeout(persistTimer.current);
      persistTimer.current = window.setTimeout(() => {
        // Real coordinates reported by the canvas. Persisting only pinned nodes with
        // {0,0} used to teleport them to the origin on reload, and left layout inertia
        // with nothing to blend toward.
        const nodes: Record<string, { x: number; y: number; pinned: boolean }> = {};
        for (const [id, position] of Object.entries(positionsRef.current)) {
          if (position.pinned || store.pinned[id]) {
            nodes[id] = { ...position, pinned: true };
          }
        }
        void saveUiState(path, {
          ui_state_version: 1,
          camera,
          nodes,
          window_bar: store.windowBar,
          outline_expanded: store.outlineExpanded,
        }).catch(() => undefined);
      }, 600);
    },
    [store.pinned, store.windowBar, store.outlineExpanded, workspace?.path],
  );

  const setCamera = useCallback(
    (camera: Camera) => {
      store.setCamera(camera);
      persistUiState(camera);
    },
    [store, persistUiState],
  );

  const saveLayoutNow = useCallback(() => {
    if (!workspace?.path) return;
    const nodes: Record<string, { x: number; y: number; pinned: boolean }> = {};
    for (const [id, position] of Object.entries(positionsRef.current)) {
      if (position.pinned || store.pinned[id]) {
        nodes[id] = { ...position, pinned: true };
      }
    }
    void saveUiState(workspace.path, {
      ui_state_version: 1,
      camera: store.camera,
      nodes,
      window_bar: store.windowBar,
      outline_expanded: store.outlineExpanded,
    });
  }, [store.camera, store.outlineExpanded, store.pinned, store.windowBar, workspace?.path]);

  const restoreDefaultLayout = useCallback(() => {
    if (!workspace?.path) return;
    for (const id of Object.keys(store.pinned)) {
      store.dispatch({ type: "releaseNode", nodeId: id });
    }
    store.dispatch({ type: "fit" });
    void saveUiState(workspace.path, {
      ui_state_version: 1,
      camera: store.camera,
      nodes: {},
      window_bar: store.windowBar,
      outline_expanded: store.outlineExpanded,
    });
  }, [store, workspace?.path]);

  // Dragging a node changes layout without touching the camera, so the camera-driven
  // save above never fired for it and a hand-placed node was lost on reload. Keyed on
  // the pin set, which is what `moveNode` and `releaseNode` change.
  useEffect(() => {
    if (Object.keys(store.pinned).length === 0) return;
    persistUiState(store.camera);
  }, [store.pinned, store.camera, persistUiState]);

  return { handlePositions, setCamera, saveLayoutNow, restoreDefaultLayout };
}
