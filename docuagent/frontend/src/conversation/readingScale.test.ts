import { describe, expect, it } from "vitest";
import {
  CONVERSATION_DEFAULT_SCALE,
  CONVERSATION_MAX_SCALE,
  CONVERSATION_MIN_SCALE,
} from "./viewMode";
import {
  readReadingScale,
  READING_SCALE_KEY,
  saveReadingScale,
} from "./readingScale";

function memoryStorage(initial: Record<string, string> = {}) {
  const values = { ...initial };
  return {
    getItem(key: string) {
      return values[key] ?? null;
    },
    setItem(key: string, value: string) {
      values[key] = value;
    },
  };
}

describe("readingScale", () => {
  it("falls back to the default when nothing is stored", () => {
    expect(readReadingScale(memoryStorage())).toBe(CONVERSATION_DEFAULT_SCALE);
  });

  it("clamps invalid and out-of-range values", () => {
    expect(readReadingScale(memoryStorage({ [READING_SCALE_KEY]: "oops" }))).toBe(
      CONVERSATION_DEFAULT_SCALE,
    );
    expect(readReadingScale(memoryStorage({ [READING_SCALE_KEY]: "9" }))).toBe(
      CONVERSATION_MAX_SCALE,
    );
    expect(readReadingScale(memoryStorage({ [READING_SCALE_KEY]: "0.1" }))).toBe(
      CONVERSATION_MIN_SCALE,
    );
  });

  it("saves a scale for the next session", () => {
    const storage = memoryStorage();
    saveReadingScale(storage, 1.4);
    expect(readReadingScale(storage)).toBe(1.4);
  });
});
