import { describe, expect, it } from "vitest";
import {
  clampConversationScale,
  CONVERSATION_DEFAULT_SCALE,
  CONVERSATION_MAX_SCALE,
  CONVERSATION_MIN_SCALE,
  INITIAL_VIEW_STATE,
  INTENT_WINDOW_MS,
  overviewDensity,
  reduceView,
  REVIEW_INTENT_DELTA,
  summarize,
  type ViewState,
} from "./viewMode";

function wheel(
  deltaY: number,
  options: { deltaMode?: number; now?: number; total?: number } = {},
) {
  return {
    type: "wheel" as const,
    deltaY,
    deltaMode: options.deltaMode ?? 0,
    now: options.now ?? 1000,
    total: options.total ?? 10,
  };
}

/** Feeds a burst of wheel events inside one intent window. */
function burst(state: ViewState, deltaY: number, count: number, total = 10): ViewState {
  let next = state;
  for (let i = 0; i < count; i += 1) {
    next = reduceView(next, wheel(deltaY, { now: 1000 + i * 20, total }));
  }
  return next;
}

describe("conversation scale clamping", () => {
  it("bounds scale to the conversation band", () => {
    expect(clampConversationScale(0.01)).toBe(CONVERSATION_MIN_SCALE);
    expect(clampConversationScale(9)).toBe(CONVERSATION_MAX_SCALE);
    expect(clampConversationScale(0.7)).toBe(0.7);
  });
});

describe("reading size is a setting, not a gesture", () => {
  it("setScale changes size without moving the user off their turn", () => {
    const reviewing: ViewState = { ...INITIAL_VIEW_STATE, mode: "review", anchor: 2 };
    const next = reduceView(reviewing, { type: "setScale", scale: 1.4 });
    expect(next.scale).toBe(1.4);
    expect(next.mode).toBe("review");
    expect(next.anchor).toBe(2);
  });

  it("clamps a setting from outside the band", () => {
    expect(reduceView(INITIAL_VIEW_STATE, { type: "setScale", scale: 9 }).scale)
      .toBe(CONVERSATION_MAX_SCALE);
    expect(reduceView(INITIAL_VIEW_STATE, { type: "setScale", scale: 0 }).scale)
      .toBe(CONVERSATION_MIN_SCALE);
  });

  it("no scale, however small, can select a layout", () => {
    // The whole point of moving this to settings: Ctrl+wheel now belongs to the
    // graph camera, so scale must never imply a mode.
    for (const scale of [CONVERSATION_MIN_SCALE, 0.8, 1.0, CONVERSATION_MAX_SCALE]) {
      const next = reduceView(INITIAL_VIEW_STATE, { type: "setScale", scale });
      expect(next.mode).toBe("live");
    }
  });

  it("survives escape, paging, and a new turn", () => {
    let state = reduceView(INITIAL_VIEW_STATE, { type: "setScale", scale: 1.5 });
    state = reduceView(state, { type: "page", delta: -1, total: 5 });
    expect(state.scale).toBe(1.5);
    state = reduceView(state, { type: "escape" });
    expect(state.scale).toBe(1.5);
    state = reduceView(state, { type: "turn" });
    expect(state.scale).toBe(1.5);
  });
});

describe("review entry by plain wheel", () => {
  it("ignores a single flick", () => {
    const next = reduceView(INITIAL_VIEW_STATE, wheel(-40, { total: 5 }));
    expect(next.mode).toBe("live");
  });

  it("enters review once accumulated upward travel passes the threshold", () => {
    const next = burst(INITIAL_VIEW_STATE, -40, 3, 5);
    expect(next.mode).toBe("review");
    expect(next.intent).toBe(0);
  });

  it("does not enter on travel that accumulates across separate windows", () => {
    let state = reduceView(INITIAL_VIEW_STATE, wheel(-60, { now: 1000, total: 5 }));
    expect(state.mode).toBe("live");
    // Second burst arrives after the window expired: intent restarts rather than
    // adding to the stale total, so slow drift never flips the mode.
    state = reduceView(state, wheel(-60, { now: 1000 + INTENT_WINDOW_MS + 1, total: 5 }));
    expect(state.mode).toBe("live");
  });

  it("returns to live on accumulated downward travel", () => {
    const reviewing = burst(INITIAL_VIEW_STATE, -40, 3, 5);
    const back = burst(reviewing, 40, 3, 5);
    expect(back.mode).toBe("live");
  });

  it("restarts accumulation when the wheel direction flips", () => {
    let state = reduceView(INITIAL_VIEW_STATE, wheel(-80, { now: 1000, total: 5 }));
    state = reduceView(state, wheel(80, { now: 1020, total: 5 }));
    expect(state.intent).toBe(80);
    expect(state.mode).toBe("live");
  });

  it("crosses on one decisive notch", () => {
    const next = reduceView(INITIAL_VIEW_STATE, wheel(-REVIEW_INTENT_DELTA, { total: 5 }));
    expect(next.mode).toBe("review");
  });
});

describe("overview is an explicit toggle", () => {
  it("enters from live", () => {
    const next = reduceView(INITIAL_VIEW_STATE, { type: "toggleOverview", total: 10 });
    expect(next.mode).toBe("overview");
  });

  it("round-trips back to live when nothing was anchored", () => {
    const over = reduceView(INITIAL_VIEW_STATE, { type: "toggleOverview", total: 10 });
    const back = reduceView(over, { type: "toggleOverview", total: 10 });
    expect(back.mode).toBe("live");
    expect(back.anchor).toBeNull();
  });

  it("leaves on the highlighted row, so the timeline acts as a jump table", () => {
    const over: ViewState = {
      ...INITIAL_VIEW_STATE,
      mode: "overview",
      anchor: 3,
    };
    const back = reduceView(over, { type: "toggleOverview", total: 10 });
    expect(back.mode).toBe("review");
    expect(back.anchor).toBe(3);
  });

  it("does not change reading size in either direction", () => {
    const sized = reduceView(INITIAL_VIEW_STATE, { type: "setScale", scale: 1.3 });
    const over = reduceView(sized, { type: "toggleOverview", total: 10 });
    expect(over.scale).toBe(1.3);
    expect(reduceView(over, { type: "toggleOverview", total: 10 }).scale).toBe(1.3);
  });

  it("keeps a plain wheel inside overview as scrolling, not a mode change", () => {
    const overview: ViewState = { ...INITIAL_VIEW_STATE, mode: "overview" };
    const next = burst(overview, -120, 5, 10);
    expect(next.mode).toBe("overview");
  });
});

describe("escape and new turns", () => {
  it("escape returns to live from either mode, keeping reading size", () => {
    for (const mode of ["review", "overview"] as const) {
      const next = reduceView(
        { ...INITIAL_VIEW_STATE, mode, scale: 1.35, anchor: 2 },
        { type: "escape" },
      );
      expect(next.mode).toBe("live");
      expect(next.anchor).toBeNull();
      // Size is a setting; "put everything back" must not silently reset it.
      expect(next.scale).toBe(1.35);
    }
  });

  it("a new turn pulls the user back to live from overview", () => {
    const next = reduceView(
      { ...INITIAL_VIEW_STATE, mode: "overview", anchor: 3 },
      { type: "turn" },
    );
    expect(next.mode).toBe("live");
    expect(next.anchor).toBeNull();
  });

  it("a new turn arrives at whatever size the user set", () => {
    const next = reduceView(
      { ...INITIAL_VIEW_STATE, mode: "review", anchor: 1, scale: 0.9 },
      { type: "turn" },
    );
    expect(next.mode).toBe("live");
    expect(next.scale).toBe(0.9);
  });
});

describe("picking a row in overview", () => {
  it("lands in review anchored on the chosen turn", () => {
    const overview: ViewState = { ...INITIAL_VIEW_STATE, mode: "overview" };
    const next = reduceView(overview, { type: "pick", index: 2, total: 10 });
    expect(next.mode).toBe("review");
    expect(next.anchor).toBe(2);
  });

  it("picking the newest row lands in live, not a duplicate review page", () => {
    const overview: ViewState = { ...INITIAL_VIEW_STATE, mode: "overview" };
    const next = reduceView(overview, { type: "pick", index: 9, total: 10 });
    expect(next.mode).toBe("live");
    expect(next.anchor).toBeNull();
  });

  it("preserves the reading size the user chose", () => {
    const overview: ViewState = { ...INITIAL_VIEW_STATE, mode: "overview", scale: 1.2 };
    expect(reduceView(overview, { type: "pick", index: 2, total: 10 }).scale).toBe(1.2);
  });
});

describe("overview density", () => {
  it("is constant now that no gesture drives it", () => {
    expect(overviewDensity()).toBe(1);
  });
});

describe("initial state", () => {
  it("starts live at the default reading size", () => {
    expect(INITIAL_VIEW_STATE.mode).toBe("live");
    expect(INITIAL_VIEW_STATE.scale).toBe(CONVERSATION_DEFAULT_SCALE);
  });
});

describe("row summaries", () => {
  it("takes the first non-empty line", () => {
    expect(summarize("\n\n第一行\n第二行")).toBe("第一行");
  });

  it("drops the speaker prefix the role band already carries", () => {
    expect(summarize("DocuAgent: 让我想想")).toBe("让我想想");
    expect(summarize("DocuAgent：让我想想")).toBe("让我想想");
  });

  it("truncates long lines with an ellipsis", () => {
    const result = summarize("x".repeat(200), 20);
    expect(result).toHaveLength(20);
    expect(result.endsWith("…")).toBe(true);
  });
});
