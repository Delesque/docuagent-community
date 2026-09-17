import { useCallback, useEffect, useState } from "react";
import { fetchApplyMode, setApplyMode, type ApplyMode } from "../api";

/** The project's sandbox-apply switch: review (default) vs auto.
 *
 *  Loaded once per workspace; a failed load keeps the safe default so the UI
 *  never advertises auto-apply when the real mode is unknown. Switching writes
 *  through to the backend and adopts the mode it reports.
 */
export function useApplyMode(path: string | null) {
  const [mode, setModeState] = useState<ApplyMode>("review");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    fetchApplyMode(path)
      .then((value) => {
        if (!cancelled) setModeState(value);
      })
      .catch(() => {
        /* Keep the safe default: review. */
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  const setMode = useCallback(
    async (next: ApplyMode) => {
      if (!path) return;
      setBusy(true);
      try {
        setModeState(await setApplyMode(path, next));
      } finally {
        setBusy(false);
      }
    },
    [path],
  );

  return { mode, setMode, busy };
}
