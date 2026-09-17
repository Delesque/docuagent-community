import { describe, expect, it } from "vitest";
import {
  NARRATION_LIMIT,
  latestNarration,
  narrationBadge,
  type NarrationSource,
} from "./narration";
import { MAX_SCALE, MIN_SCALE, signatureOpacity } from "./zoom";
import { REVEAL_SCALE } from "./reveal";

const agent = (text: string, pending = false): NarrationSource => ({
  role: "agent",
  text,
  pending,
});
const user = (text: string): NarrationSource => ({ role: "user", text });

describe("which line the conversation node shows", () => {
  it("shows nothing before the conversation starts", () => {
    expect(latestNarration([])).toBeNull();
  });

  it("shows the most recent turn", () => {
    const line = latestNarration([agent("第一句"), agent("第二句")]);
    expect(line?.text).toBe("第二句");
  });

  it("shows the user's own answer when that is the latest state", () => {
    // Showing the agent's previous question after the user has answered it would look
    // stale — the answer is what just happened.
    const line = latestNarration([agent("平台是什么？"), user("Windows 优先")]);
    expect(line?.text).toBe("Windows 优先");
    expect(line?.role).toBe("user");
  });

  it("skips turns with no readable text", () => {
    const line = latestNarration([agent("有内容"), agent("   "), agent("\n\n")]);
    expect(line?.text).toBe("有内容");
  });

  it("returns null when every turn is blank", () => {
    expect(latestNarration([agent("  "), agent("\n")])).toBeNull();
  });

  it("drops the speaker prefix the node label already implies", () => {
    expect(latestNarration([agent("DocuAgent: 让我想想")])?.text).toBe("让我想想");
    expect(latestNarration([agent("DocuAgent：让我想想")])?.text).toBe("让我想想");
  });

  it("takes the first line of a multi-line turn", () => {
    // A node is one line tall at this scale; the rest would be clipped anyway.
    const line = latestNarration([agent("摘要行\n细节一\n细节二")]);
    expect(line?.text).toBe("摘要行");
  });

  it("truncates to something that fits one line", () => {
    const line = latestNarration([agent("字".repeat(200))]);
    expect(line!.text.length).toBeLessThanOrEqual(NARRATION_LIMIT);
    expect(line!.text.endsWith("…")).toBe(true);
  });

  it("respects a caller-supplied limit", () => {
    const line = latestNarration([agent("x".repeat(80))], 20);
    expect(line!.text).toHaveLength(20);
  });

  it("reports a turn still being composed", () => {
    // The case the feature exists for: zoomed out while the agent is working.
    const line = latestNarration([agent("让我想想", true)]);
    expect(line?.pending).toBe(true);
  });

  it("does not mark a finished turn as pending", () => {
    expect(latestNarration([agent("已完成")])?.pending).toBe(false);
  });
});

/** Mirrors the opacity rule in GraphNode, so the reason for the exception is asserted
 *  rather than only explained in a comment. */
function narrationOpacity(scale: number, isConversation: boolean): number {
  return isConversation ? 1 : signatureOpacity(scale);
}

describe("the narration stays legible where the camera parks", () => {
  it("is fully opaque at the scale the reveal stops at", () => {
    // The bug this guards: `signatureOpacity` fades a module's brief out because
    // streamed code fades in to replace it. The conversation has no code, so the same
    // fade left it at 0.36 opacity — illegible at exactly the moment the user is looking
    // at the graph to see whether the agent is still working.
    expect(narrationOpacity(REVEAL_SCALE, true)).toBe(1);
    expect(narrationOpacity(REVEAL_SCALE, false)).toBeLessThan(0.5);
  });

  it("never fades the conversation anywhere in the camera band", () => {
    for (const scale of [MIN_SCALE, 0.5, 0.75, REVEAL_SCALE, 1.3, MAX_SCALE]) {
      expect(narrationOpacity(scale, true)).toBe(1);
    }
  });

  it("still fades a module's brief, which is the behaviour being preserved", () => {
    expect(narrationOpacity(1.3, false)).toBe(0);
  });
});

describe("the badge for when even one line will not fit", () => {
  it("says nothing when there is no conversation", () => {
    expect(narrationBadge(null)).toBeNull();
  });

  it("reports work in progress", () => {
    expect(narrationBadge(latestNarration([agent("思考", true)]))).toBe("思考中");
  });

  it("distinguishes the agent having replied from the user having submitted", () => {
    expect(narrationBadge(latestNarration([agent("答案")]))).toBe("已回复");
    expect(narrationBadge(latestNarration([user("我的回答")]))).toBe("已提交");
  });

  it("prefers pending over role, because that is the useful fact", () => {
    expect(narrationBadge({ text: "x", pending: true, role: "user" })).toBe("思考中");
  });
});
