import {
  clampConversationScale,
  CONVERSATION_DEFAULT_SCALE,
} from "./viewMode";

export const READING_SCALE_KEY = "docuagent.readingScale";

interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function readReadingScale(storage: StorageLike): number {
  const raw = storage.getItem(READING_SCALE_KEY);
  const parsed = raw === null ? Number.NaN : Number.parseFloat(raw);
  return Number.isFinite(parsed)
    ? clampConversationScale(parsed)
    : CONVERSATION_DEFAULT_SCALE;
}

export function saveReadingScale(storage: StorageLike, scale: number): void {
  storage.setItem(READING_SCALE_KEY, String(scale));
}

export function loadReadingScale(): number {
  return readReadingScale(window.localStorage);
}
