import { describe, expect, it } from "vitest";
import {
  chipLabelForMessageText,
  chipLabelForThinkingLine,
  pendingThinkingChip,
  randomThinkingLine,
  thinkingChip,
  thinkingChipForText,
  THINKING_CHIP_LABEL,
  THINKING_LINE_LABELS,
  THINKING_LINES,
} from "./thinking";

describe("every thinking line can show its reasoning", () => {
  // The bug this file exists for: the chip used to be attached by writing the literal
  // "看看" into a chip list and letting `splitByChips` find it inside the message text.
  // "看看" is a substring of "让我看看" and of no other phrase in the set, so four of the
  // five lines rendered with no chip and their reasoning was unreachable. Which line got
  // an affordance was decided by Chinese substring overlap.
  it("embeds each phrase's own clickable word", () => {
    for (const line of THINKING_LINES) {
      const label = chipLabelForThinkingLine(line);
      expect(line).toContain(label);
      expect(pendingThinkingChip(line).text).toBe(label);
      expect(THINKING_LINE_LABELS[line]).toBe(label);
    }
  });

  it("marks a pending chip as local, because no reasoning has arrived yet", () => {
    const chip = pendingThinkingChip("让我想想");
    expect(chip.type).toBe("view");
    expect(chip.localSummary).toBe(true);
    expect(chip.detail).toBeTruthy();
    expect(chip.text).toBe("想想");
  });
});

describe("thinkingChip", () => {
  it("passes the model's own reasoning through unlabelled", () => {
    const chip = thinkingChip("我选择拆分模块，因为职责重叠。", "fallback", "让我想想");
    expect(chip.detail).toBe("我选择拆分模块，因为职责重叠。");
    expect(chip.localSummary).toBe(false);
    expect(chip.text).toBe("想想");
  });

  it("labels a local fallback so it is never passed off as reasoning", () => {
    // The distinction the whole `localSummary` flag exists for: a locally composed
    // summary presented as the model's thinking is a lie about where it came from.
    const chip = thinkingChip("", "本地拼的摘要");
    expect(chip.detail).toBe("本地拼的摘要");
    expect(chip.localSummary).toBe(true);
    expect(chip.text).toBe(THINKING_CHIP_LABEL);
  });

  it("treats whitespace-only reasoning as absent", () => {
    const chip = thinkingChip("   \n  ", "fallback");
    expect(chip.detail).toBe("fallback");
    expect(chip.localSummary).toBe(true);
  });

  it("always produces a view chip, never an input", () => {
    // An input chip would be routed to a dialog tab instead of expanding inline.
    expect(thinkingChip("x", "y").type).toBe("view");
  });

  it("derives the label from the thinking page's sentence", () => {
    expect(thinkingChipForText("DocuAgent: 别吵，我在思考", "reasoning", "fallback").text).toBe("思考");
    expect(chipLabelForMessageText("DocuAgent: 让我看看")).toBe("看看");
  });
});

describe("randomThinkingLine", () => {
  it("only returns phrases from the set", () => {
    for (let index = 0; index < 50; index += 1) {
      expect(THINKING_LINES).toContain(randomThinkingLine());
    }
  });

  it("can reach every phrase", () => {
    // Pins that no phrase is unreachable — the previous code picked from
    // `1 + floor(random * (length - 1))`, which could never return index 0.
    const seen = new Set<string>();
    for (let index = 0; index < THINKING_LINES.length; index += 1) {
      seen.add(randomThinkingLine(() => index / THINKING_LINES.length));
    }
    expect(seen.size).toBe(THINKING_LINES.length);
  });

  it("does not run off the end when random returns 1", () => {
    // Math.random never returns exactly 1, but an injected generator can, and an
    // undefined phrase would render as the string "undefined" on screen.
    expect(THINKING_LINES).toContain(randomThinkingLine(() => 1));
  });
});
