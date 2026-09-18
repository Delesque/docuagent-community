/** The line shown while the agent is composing, and the chip that opens its reasoning.
 *
 *  These phrases are decoration on a real state: a turn is in flight. The chip beside
 *  them is not decoration — it is the only way to see why the agent asked what it asked.
 *
 *  ## Why this is a module and not a string array in the shell
 *
 *  The chip used to be attached by writing the literal `"看看"` into a chip list and
 *  relying on `splitByChips` finding it inside the message text. That worked for exactly
 *  one phrase — `看看` is a substring of `让我看看` and of nothing else in the set — so
 *  four of the five thinking lines rendered with no chip at all, and the reasoning behind
 *  those turns was unreachable. Which phrase got an affordance was decided by an
 *  accident of Chinese substring overlap.
 *
 *  So the chip is derived here, from data, for every phrase. `thinking.test.ts` asserts
 *  the property that broke — every phrase yields a chip — rather than checking the
 *  phrases themselves, so adding a sixth line cannot silently reintroduce the gap.
 */

import type { Chip } from "../components/shell/TypewriterOutput";

/** Shown while a turn is in flight. Picked at random, so none may depend on its text
 *  being matched or on its position in this list. */
export const THINKING_LINES = [
  "让我看看",
  "让我想想",
  "let me think about this",
  "别吵，我在思考",
  "世界的真理，我已解明",
] as const;

/** The chip's label. One fixed word rather than a slice of the phrase: it has to read as
 *  a button in its own right, including after `让我看看`, where repeating `看看` would
 *  look like a typo rather than a control. */
export const THINKING_CHIP_LABEL = "看看";

/** The word embedded in each thinking line that opens the reasoning, so the chip reads
 *  as part of the sentence instead of a button parked on a separate row. */
export const THINKING_LINE_LABELS: Record<string, string> = {
  "让我看看": "看看",
  "让我想想": "想想",
  "let me think about this": "think",
  "别吵，我在思考": "思考",
  "世界的真理，我已解明": "解明",
};

export function chipLabelForThinkingLine(line: string): string {
  return THINKING_LINE_LABELS[line] ?? THINKING_CHIP_LABEL;
}

export function chipLabelForMessageText(text: string): string {
  const line = THINKING_LINES.find((candidate) => text.includes(candidate));
  return line ? chipLabelForThinkingLine(line) : THINKING_CHIP_LABEL;
}

export function randomThinkingLine(random: () => number = Math.random): string {
  const index = Math.floor(random() * THINKING_LINES.length);
  // Guards against `random()` returning exactly 1, which would index past the end.
  return THINKING_LINES[Math.min(index, THINKING_LINES.length - 1)]!;
}

/** The chip for a turn that is still in flight.
 *
 *  `detail` reports that reasoning is not available yet instead of inventing a summary.
 *  Once the reply lands it carries its own chip with the model's actual reasoning; this
 *  one exists so the affordance is in the same place for every phrase, rather than
 *  appearing only for the phrase that happened to contain the chip word.
 */
export function pendingThinkingChip(line: string): Chip {
  return {
    text: chipLabelForThinkingLine(line),
    type: "view",
    detail: "正在等待模型返回本轮推理。",
    localSummary: true,
  };
}

/** The chip for a completed turn.
 *
 *  `thinking` is the model's own words when the provider exposed a reasoning channel or
 *  the model filled the `thinking` field. When it did neither, `fallback` is shown and
 *  flagged as locally composed — the UI must never present a local summary as reasoning.
 */
export function thinkingChip(
  thinking: string,
  fallback: string,
  line = "",
): Chip {
  const reasoning = thinking.trim();
  return {
    text: line ? chipLabelForThinkingLine(line) : THINKING_CHIP_LABEL,
    type: "view",
    detail: reasoning || fallback,
    localSummary: reasoning.length === 0,
  };
}

/** Chip for a completed thinking page: derives its label from the sentence shown. */
export function thinkingChipForText(
  text: string,
  thinking: string,
  fallback: string,
): Chip {
  const reasoning = thinking.trim();
  return {
    text: chipLabelForMessageText(text),
    type: "view",
    detail: reasoning || fallback,
    localSummary: reasoning.length === 0,
  };
}
