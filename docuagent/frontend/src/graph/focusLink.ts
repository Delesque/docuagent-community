/** `#focus=<module-id>` deep links (B4).
 *
 *  A graph view is an addressable artifact: pasting a link with `#focus=auth`
 *  opens the graph with that node selected and centered, and selecting a node
 *  updates the hash so "share what I'm looking at" is a URL copy. The hash is
 *  the whole contract — no server state, no history entries (replaceState only,
 *  so back/forward stay about pages, not nodes).
 */

export function parseFocusHash(hash: string): string | null {
  const match = /(?:^|[#&])focus=([A-Za-z0-9_-]+)/.exec(hash || "");
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

export function focusHashFor(moduleId: string | null): string | null {
  if (!moduleId) return null;
  return `#focus=${encodeURIComponent(moduleId)}`;
}

/** Sync the hash to the current selection without adding history entries. */
export function replaceFocusHash(moduleId: string | null): void {
  if (typeof window === "undefined" || typeof window.history === "undefined") return;
  const hash = focusHashFor(moduleId);
  const url = hash
    ? `${window.location.pathname}${window.location.search}${hash}`
    : `${window.location.pathname}${window.location.search}`;
  window.history.replaceState(null, "", url);
}
