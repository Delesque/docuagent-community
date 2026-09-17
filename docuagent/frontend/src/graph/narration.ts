/** What the conversation node shows when it is too small to read.
 *
 *  Zoomed out to the map band, every other node collapses to a status dot and a name —
 *  there is nothing else worth pixels at that size. The conversation is different: it is
 *  the only node whose content changes while you are looking away from it. If it also
 *  collapsed to a label, zooming out during a long interview would hide the fact that
 *  the agent is still talking, and the user would have to zoom back in to check.
 *
 *  So it carries one line: the latest thing said. Not a transcript — a single line is
 *  the most that stays legible at map scale, and more would turn the node into a wall of
 *  text that competes with the graph it sits above.
 */

import { summarize } from "../conversation/viewMode";

/** Enough to read at a glance, short enough to fit one line inside the node. */
export const NARRATION_LIMIT = 52;

export interface NarrationSource {
  role: "agent" | "user";
  text: string;
  /** True while the agent is composing, which is exactly when the user most wants to
   *  know something is happening. */
  pending?: boolean;
}

export interface NarrationLine {
  text: string;
  /** Rendered with a different treatment: an in-progress turn is not a result. */
  pending: boolean;
  /** Whose line it is. A user's own words read differently from the agent's. */
  role: "agent" | "user";
}

/** The line to show, or null when there is nothing yet.
 *
 *  Takes the latest turn regardless of role: after the user answers, their answer IS the
 *  most recent state of the conversation, and showing the agent's previous question
 *  instead would look stale.
 */
export function latestNarration(
  turns: NarrationSource[],
  limit: number = NARRATION_LIMIT,
): NarrationLine | null {
  for (let index = turns.length - 1; index >= 0; index -= 1) {
    const turn = turns[index]!;
    const text = summarize(turn.text, limit);
    if (!text) continue;
    return { text, pending: turn.pending === true, role: turn.role };
  }
  return null;
}

/** A short label for the map band, when even one line is too much.
 *
 *  Below roughly a third of reading scale the node is a few dozen pixels tall and any
 *  sentence is unreadable, so the honest thing is to say only whether the agent is
 *  working. Returns null when there is nothing to report.
 */
export function narrationBadge(line: NarrationLine | null): string | null {
  if (!line) return null;
  if (line.pending) return "思考中";
  return line.role === "agent" ? "已回复" : "已提交";
}
