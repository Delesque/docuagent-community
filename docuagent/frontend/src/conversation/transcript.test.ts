import { describe, expect, it } from "vitest";
import {
  EMPTY_TRANSCRIPT,
  transcriptReducer,
} from "./transcript";

const agentMessage = {
  role: "agent" as const,
  text: "hello",
};

describe("transcriptReducer", () => {
  it("replaces the message and pushes the previous one into history", () => {
    const first = transcriptReducer(EMPTY_TRANSCRIPT, {
      type: "message",
      message: agentMessage,
    });
    const second = transcriptReducer(first, {
      type: "message",
      message: { role: "user", text: "world" },
    });

    expect(second.history).toEqual([agentMessage]);
    expect(second.message).toEqual({ role: "user", text: "world" });
  });

  it("updates the current message without touching history", () => {
    const state = transcriptReducer(EMPTY_TRANSCRIPT, {
      type: "message",
      message: agentMessage,
    });
    const updated = transcriptReducer(state, {
      type: "updateMessage",
      message: { ...agentMessage, instant: true },
    });

    expect(updated.history).toEqual([]);
    expect(updated.message?.instant).toBe(true);
  });

  it("only attaches thinking chips to agent messages", () => {
    const agent = transcriptReducer(EMPTY_TRANSCRIPT, {
      type: "message",
      message: agentMessage,
    });
    const withThinking = transcriptReducer(agent, {
      type: "setThinking",
      thinking: "reasoning",
      fallback: "local",
    });
    expect(withThinking.message?.thinking).toBe("reasoning");
    expect(withThinking.message?.chips?.length).toBe(1);

    const user = transcriptReducer(EMPTY_TRANSCRIPT, {
      type: "message",
      message: { role: "user", text: "why" },
    });
    const unchanged = transcriptReducer(user, {
      type: "setThinking",
      thinking: "reasoning",
      fallback: "local",
    });
    expect(unchanged.message?.thinking).toBeUndefined();
  });

  it("resets to the empty transcript", () => {
    const state = transcriptReducer(EMPTY_TRANSCRIPT, {
      type: "message",
      message: agentMessage,
    });
    expect(transcriptReducer(state, { type: "reset" })).toEqual(EMPTY_TRANSCRIPT);
  });
});
