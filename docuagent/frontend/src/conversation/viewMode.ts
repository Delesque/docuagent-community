/** Conversation view state: three layouts over one transcript.
 *
 *  - `live`     — the newest turn owns the page, dialog docked at the bottom.
 *  - `review`   — wheel up; older turns, one per page, same full-screen typography.
 *  - `overview` — every turn compresses to one row with a role band, so a long
 *                 interview is scannable at a glance.
 *
 *  Kept as pure functions for the same reason `graph/zoom.ts` is: mode transitions
 *  and intent gating are exactly the behaviors that need assertions, and neither
 *  needs a DOM to verify. The component layer only feeds events in and renders.
 *
 *  ## Ctrl+wheel belongs to the graph camera, not to type size
 *
 *  Conversation zoom used to be a font-size dial on Ctrl+wheel, and dropping below
 *  0.53 switched to the timeline. That cannot coexist with the graph workbench: the
 *  conversation is a node in the architecture graph, so Ctrl+wheel has to mean
 *  "move the camera" everywhere, and 0.53 sits inside the range the graph needs for
 *  its own map band. Two cameras on one gesture meant the same scale mapped to two
 *  different layouts depending on which surface had focus.
 *
 *  So `scale` here is no longer gesture-driven. It is a user preference, set in
 *  settings and persisted, and `overview` is entered by an explicit command
 *  (Ctrl+O, or the mode control) rather than by crossing a threshold. Plain-wheel
 *  paging is unchanged: it keys off accumulated travel, never off scale.
 */

/** Reading-size preference, applied as a type-size multiplier. Set in settings;
 *  never changed by a gesture. Unrelated to the graph camera in `graph/zoom.ts`. */
export const CONVERSATION_MAX_SCALE = 1.6;
export const CONVERSATION_MIN_SCALE = 0.7;
export const CONVERSATION_DEFAULT_SCALE = 1.0;

/** Overview row density is a fixed layout choice now that scale cannot drive it. */
export const OVERVIEW_DEFAULT_DENSITY = 1;

/** Accumulated plain-wheel travel required to turn one page. */
export const REVIEW_INTENT_DELTA = 90;

/** Travel older than this is discarded, so slow drift never adds up to a page turn. */
export const INTENT_WINDOW_MS = 300;

export type ViewMode = "live" | "review" | "overview";

export interface ViewState {
  mode: ViewMode;
  /** Reading-size preference from settings, in
   *  `[CONVERSATION_MIN_SCALE, CONVERSATION_MAX_SCALE]`. Never gesture-driven, so
   *  mode transitions must not read it. */
  scale: number;
  /** Signed plain-wheel travel accumulated inside the intent window. */
  intent: number;
  /** When `intent` was last added to. */
  intentAt: number;
  /** Index into the flat transcript that review is paging on, and that overview
   *  highlights. Null means "the newest turn", which is what `live` shows. */
  anchor: number | null;
  /** Which way the last page turn went: -1 toward older, +1 toward newer, 0 for
   *  anything that was not a page turn. The view layer reads this to pick the slide
   *  direction, so the animation is a property of the transition rather than
   *  something the renderer has to infer by diffing its own previous props. */
  direction: -1 | 0 | 1;
}

export const INITIAL_VIEW_STATE: ViewState = {
  mode: "live",
  scale: CONVERSATION_DEFAULT_SCALE,
  intent: 0,
  intentAt: 0,
  anchor: null,
  direction: 0,
};

export type ViewEvent =
  /** Plain-wheel paging. `total` is the transcript length: paging needs to know
   *  where the ends are. A ctrlKey wheel is not handled here at all — it belongs to
   *  the graph camera. */
  | { type: "wheel"; deltaY: number; deltaMode: number; now: number; total: number }
  /** Keyboard paging. -1 is older, +1 is newer. */
  | { type: "page"; delta: number; total: number }
  | { type: "escape" }
  /** Explicit overview toggle (Ctrl+O or the mode control), replacing the old
   *  zoom-out-past-a-threshold entry. */
  | { type: "toggleOverview"; total: number }
  /** A row chosen in overview. `total` lets picking the newest row land in `live`
   *  instead of a review page that would duplicate it. */
  | { type: "pick"; index: number; total: number }
  /** A new turn arrived: the newest message is the point of the screen again. */
  | { type: "turn" }
  /** Reading size changed in settings. Layout must not shift as a side effect. */
  | { type: "setScale"; scale: number };

export function clampConversationScale(scale: number): number {
  return Math.min(CONVERSATION_MAX_SCALE, Math.max(CONVERSATION_MIN_SCALE, scale));
}

/** Row scale for the overview timeline.
 *
 *  Constant now: density used to interpolate on zoom-out depth, which no longer
 *  exists as a gesture. Kept as a function so the component keeps one call site if
 *  density becomes a preference later.
 */
export function overviewDensity(): number {
  return OVERVIEW_DEFAULT_DENSITY;
}

/** Type size for a full-screen turn: longer text gets a smaller measure, then the
 *  block scrolls. Multiplied by the reading-size preference so the same text reads
 *  the same in `live` and `review` alike, which is what makes paging feel like one
 *  document. */
export function conversationFontSize(length: number, scale = 1): string {
  const base =
    length < 90
      ? "clamp(26px, 3.4vw, 42px)"
      : length < 220
        ? "clamp(22px, 2.6vw, 32px)"
        : length < 480
          ? "clamp(18px, 2vw, 25px)"
          : "clamp(15px, 1.5vw, 19px)";
  if (Math.abs(scale - 1) < 0.001) return base;
  return `calc(${base} * ${scale.toFixed(3)})`;
}

function accumulate(state: ViewState, deltaY: number, now: number): number {
  const expired = now - state.intentAt > INTENT_WINDOW_MS;
  const flipped = state.intent !== 0 && Math.sign(deltaY) !== Math.sign(state.intent);
  if (expired || flipped) return deltaY;
  return state.intent + deltaY;
}

/** One page turn. `delta` is -1 for older, +1 for newer. Walking past the newest
 *  turn lands back in `live` — the newest turn is `live`, so there is no separate
 *  review page for it. */
function pageBy(state: ViewState, delta: number, total: number): ViewState {
  const step = (delta < 0 ? -1 : 1) as -1 | 1;
  const cleared = { ...state, intent: 0, direction: step };
  if (total <= 0) return { ...cleared, direction: 0 as const };

  const newest = total - 1;
  const from = state.mode === "review" ? (state.anchor ?? newest) : newest;
  const target = from + delta;

  // Already at an end: hold the page and report no movement, so the view layer
  // does not replay a slide for a turn that did not happen.
  if (target >= newest) {
    if (state.mode === "live") return { ...cleared, direction: 0 as const };
    return { ...cleared, mode: "live" as const, anchor: null };
  }
  if (target < 0) {
    const stuck = state.mode === "review" && state.anchor === 0;
    if (state.mode !== "review") return { ...cleared, direction: 0 as const };
    return { ...cleared, anchor: 0, direction: stuck ? (0 as const) : step };
  }
  return { ...cleared, mode: "review" as const, anchor: target };
}

export function reduceView(state: ViewState, event: ViewEvent): ViewState {
  switch (event.type) {
    case "escape":
      // "Put everything back" — but not the reading size, which is a setting now and
      // must survive. Only mode, anchor, and accumulated intent reset.
      return {
        ...INITIAL_VIEW_STATE,
        scale: state.scale,
        intentAt: state.intentAt,
      };

    case "turn":
      // A reply pulls the user back to the newest turn, whatever they were reading.
      // Direction resets so the arriving turn types itself in rather than sliding:
      // it is new text, not a page the user turned to.
      return {
        ...state,
        mode: "live",
        intent: 0,
        anchor: null,
        direction: 0,
      };

    case "setScale":
      // Reading size only. Changing it must never move the user off the turn they
      // are reading, which is exactly what the old zoom-driven mode switch did.
      return { ...state, scale: clampConversationScale(event.scale) };

    case "toggleOverview": {
      if (state.mode === "overview") {
        // Leaving lands on the row that was highlighted, so the timeline acts as a
        // jump table rather than a detour.
        const newest = event.total - 1;
        const target = state.anchor ?? newest;
        if (target >= newest) {
          return { ...state, mode: "live", anchor: null, intent: 0, direction: 0 };
        }
        return { ...state, mode: "review", anchor: target, intent: 0, direction: 0 };
      }
      return { ...state, mode: "overview", intent: 0, direction: 0 };
    }

    case "pick": {
      // Picking the newest row means "back to now", which is `live` — a review page
      // anchored on the newest turn would be the same screen under a different mode.
      const newest = event.total - 1;
      const from = state.mode === "review" ? (state.anchor ?? newest) : newest;
      const direction = (event.index === from ? 0 : event.index < from ? -1 : 1) as -1 | 0 | 1;
      const base = { ...state, intent: 0, direction };
      if (event.index >= newest) return { ...base, mode: "live" as const, anchor: null };
      return { ...base, mode: "review" as const, anchor: event.index };
    }

    case "page":
      return pageBy(state, Math.sign(event.delta), event.total);

    case "wheel": {
      // In overview the timeline scrolls itself; a plain wheel is the scroll, not a
      // mode change. Esc or the toggle is how you leave.
      if (state.mode === "overview") {
        return { ...state, intent: 0, intentAt: event.now };
      }

      const intent = accumulate(state, event.deltaY, event.now);
      const next = { ...state, intent, intentAt: event.now };
      if (intent <= -REVIEW_INTENT_DELTA) return pageBy(next, -1, event.total);
      if (intent >= REVIEW_INTENT_DELTA) return pageBy(next, 1, event.total);
      return next;
    }

    default:
      return state;
  }
}

/** First line of a turn, trimmed to a scannable length for one timeline row.
 *  The speaker prefix is dropped because the row already carries a role band. */
export function summarize(text: string, limit = 88): string {
  const line = text.split("\n").find((part) => part.trim())?.trim() ?? "";
  const stripped = line.replace(/^DocuAgent[:：]\s*/, "");
  if (stripped.length <= limit) return stripped;
  return `${stripped.slice(0, limit - 1).trimEnd()}…`;
}
