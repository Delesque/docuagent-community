import { describe, expect, it } from "vitest";
import {
  clearPatchDraft,
  isPatchDraftStale,
  upsertPatchDraft,
} from "./codeViewerDraft";

describe("patch editor drafts", () => {
  it("stores the server base with the edited content", () => {
    const drafts = upsertPatchDraft({}, "src/a.py", "print('v1')\n", "print('v2')\n");
    expect(drafts["src/a.py"]).toEqual({
      base: "print('v1')\n",
      content: "print('v2')\n",
    });
  });

  it("detects when the server patch moved under the draft", () => {
    const drafts = upsertPatchDraft({}, "src/a.py", "v1", "edited");
    expect(isPatchDraftStale("v1", drafts["src/a.py"])).toBe(false);
    expect(isPatchDraftStale("v2", drafts["src/a.py"])).toBe(true);
  });

  it("clears a draft without dropping other files", () => {
    const drafts = upsertPatchDraft(
      upsertPatchDraft({}, "a.py", "a", "a2"),
      "b.py",
      "b",
      "b2",
    );
    const next = clearPatchDraft(drafts, "a.py");
    expect(next["a.py"]).toBeUndefined();
    expect(next["b.py"]?.content).toBe("b2");
  });
});
