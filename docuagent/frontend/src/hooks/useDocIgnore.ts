import { useCallback, useEffect, useState } from "react";
import { fetchDocIgnore, setDocIgnore, type DocIgnoreState } from "../api";

/** Which entries of the merged ignore view come from the project itself
 *  (everything the built-in list does not already cover). */
export function projectIgnoredDirs(state: DocIgnoreState): string[] {
  const defaults = new Set(state.defaults);
  return state.ignored_dirs.filter((name) => !defaults.has(name));
}

/** The project's documentation-ignore list: directories that never get a
 *  navigation document. Loaded once per workspace; adds and removals write
 *  through immediately and adopt the merged view the backend reports, so the
 *  UI can never drift from what is actually in force. */
export function useDocIgnore(path: string | null) {
  const [state, setState] = useState<DocIgnoreState | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!path) return;
    let cancelled = false;
    setError(null);
    fetchDocIgnore(path)
      .then((value) => {
        if (!cancelled) setState(value);
      })
      .catch((cause: unknown) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : String(cause));
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  const update = useCallback(
    async (names: string[]) => {
      if (!path) return;
      setBusy(true);
      setError(null);
      try {
        setState(await setDocIgnore(path, names));
      } catch (cause: unknown) {
        setError(cause instanceof Error ? cause.message : String(cause));
      } finally {
        setBusy(false);
      }
    },
    [path],
  );

  const add = useCallback(
    (name: string) => {
      const trimmed = name.trim();
      if (!state || !trimmed || state.ignored_dirs.includes(trimmed)) return false;
      void update([...projectIgnoredDirs(state), trimmed]);
      return true;
    },
    [state, update],
  );

  const remove = useCallback(
    (name: string) => {
      if (!state) return;
      void update(projectIgnoredDirs(state).filter((entry) => entry !== name));
    },
    [state, update],
  );

  return { state, projectDirs: state ? projectIgnoredDirs(state) : [], busy, error, add, remove };
}
