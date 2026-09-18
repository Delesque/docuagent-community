/** The AI output surface. One message owns the whole page — this is not a chat flow.
 *
 *  Used for both `live` and `review`: the same component, the same typography, the
 *  same measure. Review is not a second layout, it is this layout showing an older
 *  turn, which is what lets zoom stay a single font-size dial across the whole
 *  transcript. Only `typing` and the enter animation differ.
 *
 *  Two things that were wrong before and are load-bearing now:
 *  - Only one message renders. Stacking every message turned the screen into a
 *    conventional message list, which is the layout this design exists to avoid.
 *  - Width is a share of the viewport, not the dialog's max-width. The text block is
 *    the page; it must not inherit the input box's measure.
 *
 *  Font size steps down as the text grows and bottoms out, after which the block
 *  scrolls. Nothing about the dialog being open dims or blurs this layer.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { conversationFontSize } from "../../conversation/viewMode";

export interface Chip {
  text: string;
  type: "input" | "view" | "action";
  /** Shown inline under the message when a view chip is expanded. For a "看看" chip
   *  on an agent turn: the model's reasoning from `message.thinking` when present,
   *  otherwise a locally composed summary explicitly labelled as local. */
  detail?: string;
  /** True when `detail` is a local summary rather than the model's own reasoning.
   *  The UI labels it accordingly so a user never mistakes it for thinking. */
  localSummary?: boolean;
}

export interface Message {
  role: "agent" | "user";
  text: string;
  chips?: Chip[];
  /** Renders an animated 1→3 dot cycle at the end while the turn is in flight. */
  pending?: boolean;
  /** The model's own reasoning: native `reasoning_content` or the `thinking` field
   *  the prompt requests. Empty when the model exposed neither, in which case the
   *  UI composes a local summary explicitly labelled as such. */
  thinking?: string;
  /** Render the message as already written instead of replaying the typing effect.
   *  Used for live progress streams that rewrite their own text many times. */
  instant?: boolean;
}

interface TypewriterOutputProps {
  visible: boolean;
  message: Message | null;
  dialogOpen: boolean;
  onChipClick: (chip: Chip) => void;
  /** Conversation zoom. Multiplies the length-derived type size. */
  scale?: number;
  /** False for review pages: an older turn is already written, so replaying the
   *  typing would misrepresent it as arriving now. */
  typing?: boolean;
  /** Slide-in edge for the page-turn animation. Null in `live`, where a new turn
   *  types itself in and does not need to travel. */
  enterFrom?: "top" | "bottom" | null;
  /** Fires once a message has finished typing, so the shell can render it instantly
   *  if the user pages away and back instead of replaying it as if it were new. */
  onTyped?: (text: string) => void;
}

const TYPE_INTERVAL = 42;
const PUNCTUATION_PAUSE = 220;
const PAUSE_AFTER = new Set(["，", "。", "？", "！", "、", "：", ",", ".", "?", "!"]);

export function TypewriterOutput({
  visible,
  message,
  dialogOpen,
  onChipClick,
  scale = 1,
  typing = true,
  enterFrom = null,
  onTyped,
}: TypewriterOutputProps) {
  const [typed, setTyped] = useState("");
  const [done, setDone] = useState(false);
  const [cursorOn, setCursorOn] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const text = message?.text ?? "";

  // Held in a ref so the typing effect does not restart when the parent passes a
  // fresh closure, which would rewind the animation on every render.
  const onTypedRef = useRef(onTyped);
  onTypedRef.current = onTyped;

  // Retype whenever the message text changes. A self-scheduling timeout (not an
  // interval) is what allows the pause after punctuation.
  useEffect(() => {
    setExpanded(null);
    if (message?.instant && text) {
      setTyped(text);
      setDone(true);
      return;
    }
    if (!text) {
      setTyped("");
      setDone(false);
      return;
    }
    if (!typing) {
      setTyped(text);
      setDone(true);
      return;
    }
    let index = 0;
    let timer = 0;
    setTyped("");
    setDone(false);

    const step = (): void => {
      index += 1;
      setTyped(text.slice(0, index));
      if (index >= text.length) {
        setDone(true);
        onTypedRef.current?.(text);
        return;
      }
      const justTyped = text[index - 1] ?? "";
      const delay = PAUSE_AFTER.has(justTyped) ? PUNCTUATION_PAUSE : TYPE_INTERVAL;
      timer = window.setTimeout(step, delay);
    };
    timer = window.setTimeout(step, TYPE_INTERVAL);
    return () => window.clearTimeout(timer);
  }, [message?.instant, text, typing]);

  useEffect(() => {
    const id = window.setInterval(() => setCursorOn((on) => !on), 530);
    return () => window.clearInterval(id);
  }, []);

  // 1 → 2 → 3 dots, for both "让我看看..." and any in-flight turn.
  const [dots, setDots] = useState(1);
  useEffect(() => {
    if (!message?.pending) return;
    const id = window.setInterval(() => setDots((n) => (n % 3) + 1), 420);
    return () => window.clearInterval(id);
  }, [message?.pending]);

  // Longer text gets a smaller measure; zoom scales that result, so the dial means
  // the same thing on a one-line greeting and on a wall of prose.
  const fontSize = useMemo(() => conversationFontSize(text.length, scale), [text.length, scale]);

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [typed]);

  if (!visible) return <div className="h-full w-full" />;

  const segments = message && done ? splitByChips(message.text, message.chips ?? []) : null;
  // Chips not found in the message text (e.g. AI-generated question titles that use
  // different wording) render as a trailing button row rather than being silently dropped.
  //
  // This deliberately includes `view` chips. Filtering to `type === "input"` meant a
  // "看看" chip whose word does not appear in the message was dropped, and since only
  // one of the five thinking phrases contains that word, the model's reasoning was
  // unreachable on most turns. A chip is a promise that something is inspectable; if it
  // cannot be placed inline it still has to be reachable somewhere.
  const inlineChipTexts = new Set(
    segments?.filter((s) => s.chip).map((s) => s.chip!.text) ?? [],
  );
  const trailingChips = done
    ? (message?.chips ?? []).filter((chip) => !inlineChipTexts.has(chip.text))
    : [];

  // The chip whose reasoning box is open. Resolved here so the fixed-position box
  // can live outside the text flow (below) without the inline expansion pushing the
  // page's characters around.
  const expandedChip =
    done && expanded
      ? ((message?.chips ?? []).find((chip) => chip.text === expanded) ?? null)
      : null;

  const enterClass =
    enterFrom === "top" ? "page-enter-down" : enterFrom === "bottom" ? "page-enter-up" : "";

  return (
    <div
      className="absolute inset-x-0 top-0 flex justify-center overflow-hidden px-[7vw]"
      style={{
        // Yields the lower third to the dialog by shrinking the region, not by
        // fading or blurring the text.
        bottom: dialogOpen ? "34vh" : 0,
        transition: "bottom 380ms cubic-bezier(0.22, 1, 0.36, 1)",
      }}
    >
      <div ref={scrollRef} className="flex w-full max-w-[1400px] flex-col justify-center overflow-y-auto py-10">
        {!message ? (
          <div className="text-center">
            <span className="mr-1 font-body text-ink">&gt;</span>
            <span
              className="inline-block h-[1.1em] w-[3px] bg-ink align-middle"
              style={{ opacity: cursorOn ? 1 : 0, fontSize }}
            />
          </div>
        ) : (
          <div
            className={`text-left font-body ${enterClass} ${message.role === "agent" ? "text-chalk" : "text-chalk-dim"}`}
            style={{ fontSize, lineHeight: 1.55 }}
          >
            {/* Wrapped rather than interpolated bare: swapping a raw string child for
                an element array in the same slot is what desynchronises React's fiber
                tree from the DOM and surfaces as removeChild/insertBefore errors. */}
            <span className="mr-2 select-none text-ink">
              {message.role === "agent" ? ">" : "$"}
            </span>
            <span>
              {segments
                ? segments.map((segment, index) =>
                    segment.chip ? (
                      <ChipWithDetail
                        key={index}
                        chip={segment.chip}
                        expanded={expanded === segment.chip.text}
                        onClick={() => {
                          if (segment.chip!.type === "view") {
                            setExpanded((current) => (current === segment.chip!.text ? null : segment.chip!.text));
                          }
                          onChipClick(segment.chip!);
                        }}
                      />
                    ) : (
                      <span key={index}>{segment.text}</span>
                    ),
                  )
                : typed}
            </span>

            {message.pending ? <span>{".".repeat(dots)}</span> : null}

            {!done ? (
              <span
                className="ml-0.5 inline-block h-[1em] w-[3px] translate-y-[0.08em] bg-ink align-middle"
                style={{ opacity: cursorOn ? 1 : 0 }}
              />
            ) : null}

            {trailingChips.length > 0 ? (
              <div className="mt-5 flex flex-wrap gap-2">
                {trailingChips.map((chip) => (
                  <ChipWithDetail
                    key={chip.text}
                    chip={chip}
                    expanded={expanded === chip.text}
                    onClick={() => {
                      // A view chip out here toggles the same expansion an inline one
                      // does. Hardcoding `expanded={false}` and skipping the toggle made
                      // a trailing "看看" render as a button that visibly did nothing.
                      if (chip.type === "view") {
                        setExpanded((current) => (current === chip.text ? null : chip.text));
                      }
                      onChipClick(chip);
                    }}
                  />
                ))}
              </div>
            ) : null}

          </div>
        )}
      </div>

      {expandedChip?.detail ? (
        <div className="pointer-events-auto absolute inset-x-[7vw] bottom-4 z-10 mx-auto max-h-[36vh] w-[calc(100%-14vw)] max-w-[1400px] overflow-y-auto rounded-md border border-vermilion/30 bg-paper-raise/95 p-4 text-left shadow-lg">
          {expandedChip.localSummary ? (
            <span className="mb-1 block font-mono text-[9px] uppercase tracking-[0.12em] text-chalk-faint/70">
              本地摘要（非模型推理）
            </span>
          ) : null}
          <span className="block whitespace-pre-wrap font-body text-[0.72em] leading-relaxed text-chalk-dim">
            {expandedChip.detail}
          </span>
        </div>
      ) : null}
    </div>
  );
}

/** A view chip plus its collapsible detail box, rendered as one inline-block so the
 *  chip stays on top and the box opens directly beneath it. */
function ChipWithDetail({
  chip,
  expanded,
  onClick,
}: {
  chip: Chip;
  expanded: boolean;
  onClick: () => void;
}) {
  return <ChipButton chip={chip} expanded={expanded} onClick={onClick} />;
}

function ChipButton({
  chip,
  expanded,
  onClick,
}: {
  chip: Chip;
  expanded: boolean;
  onClick: () => void;
}) {
  const input = chip.type === "input";
  const action = chip.type === "action";
  return (
    <button
      type="button"
      onClick={onClick}
      aria-expanded={chip.type === "view" ? expanded : undefined}
      className={`mx-[0.18em] inline-block border px-[0.34em] py-[0.1em] align-baseline font-mono transition-colors duration-150 ${
        input
          ? "border-ink/70 bg-ink/10 text-ink hover:border-ink hover:bg-ink/20"
          : action
            ? "border-ink bg-ink/20 text-ink hover:bg-ink/30"
            : "border-[#FFC857]/60 bg-[#FFC857]/10 text-[#FFC857] hover:bg-[#FFC857]/20"
      }`}
    >
      <span aria-hidden className="select-none opacity-70">[</span>
      <span className="px-1">{chip.text}</span>
      <span aria-hidden className="select-none opacity-70">]</span>
    </button>
  );
}

/** Splits the message so each chip's first occurrence becomes a button and the rest
 *  stays plain text. Scanning forward from the previous match keeps repeated words
 *  from collapsing onto the same span. */
function splitByChips(
  text: string,
  chips: Chip[],
): Array<{ text: string; chip?: Chip }> {
  const hits: Array<{ start: number; end: number; chip: Chip }> = [];
  let cursor = 0;
  for (const chip of chips) {
    const start = text.indexOf(chip.text, cursor);
    if (start === -1) continue;
    hits.push({ start, end: start + chip.text.length, chip });
    cursor = start + chip.text.length;
  }
  if (hits.length === 0) return [{ text }];

  const segments: Array<{ text: string; chip?: Chip }> = [];
  let index = 0;
  for (const hit of hits) {
    if (hit.start > index) segments.push({ text: text.slice(index, hit.start) });
    segments.push({ text: hit.chip.text, chip: hit.chip });
    index = hit.end;
  }
  if (index < text.length) segments.push({ text: text.slice(index) });
  return segments;
}
