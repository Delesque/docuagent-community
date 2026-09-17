export interface PatchDraft {
  /** Patch content loaded from the server when editing started. */
  base: string;
  /** Current editor content. */
  content: string;
}

export type PatchDraftMap = Record<string, PatchDraft>;

export function upsertPatchDraft(
  drafts: PatchDraftMap,
  path: string,
  base: string,
  content: string,
): PatchDraftMap {
  return {
    ...drafts,
    [path]: { base, content },
  };
}

export function clearPatchDraft(
  drafts: PatchDraftMap,
  path: string,
): PatchDraftMap {
  if (!(path in drafts)) return drafts;
  const next = { ...drafts };
  delete next[path];
  return next;
}

export function isPatchDraftStale(serverContent: string, draft?: PatchDraft): boolean {
  return draft !== undefined && draft.base !== serverContent;
}
