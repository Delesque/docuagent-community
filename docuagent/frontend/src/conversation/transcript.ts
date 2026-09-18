import { thinkingChipForText } from "./thinking";
import type { Message } from "../components/shell/TypewriterOutput";

export interface TranscriptState {
  history: Message[];
  message: Message | null;
}

export type TranscriptAction =
  | { type: "message"; message: Message }
  | { type: "updateMessage"; message: Message }
  | { type: "reset" }
  | { type: "setThinking"; thinking: string; fallback: string };

export const EMPTY_TRANSCRIPT: TranscriptState = {
  history: [],
  message: null,
};

export function transcriptReducer(
  state: TranscriptState,
  action: TranscriptAction,
): TranscriptState {
  if (action.type === "reset") return EMPTY_TRANSCRIPT;
  if (action.type === "updateMessage") {
    return { ...state, message: action.message };
  }
  if (action.type === "setThinking") {
    if (!state.message || state.message.role !== "agent") return state;
    return {
      ...state,
      message: {
        ...state.message,
        thinking: action.thinking,
        chips: [
          thinkingChipForText(
            state.message.text,
            action.thinking,
            action.fallback,
          ),
        ],
      },
    };
  }
  return {
    history: state.message ? [...state.history, state.message] : state.history,
    message: action.message,
  };
}
