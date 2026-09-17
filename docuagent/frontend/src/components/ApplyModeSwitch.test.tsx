// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";
import { createRoot, type Root } from "react-dom/client";
import { act } from "react";
import { ApplyModeSwitch } from "./ApplyModeSwitch";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function renderSwitch(mode: "review" | "auto", onChange: (next: "review" | "auto") => void, busy = false) {
  const container = document.createElement("div");
  const root: Root = createRoot(container);
  act(() => {
    root.render(<ApplyModeSwitch mode={mode} busy={busy} onChange={onChange} />);
  });
  const buttons = Array.from(container.querySelectorAll<HTMLButtonElement>("[role='tab']"));
  const byLabel = (label: string) => buttons.find((button) => button.textContent === label)!;
  return {
    container,
    buttons,
    byLabel,
    unmount() { act(() => root.unmount()); },
  };
}

describe("ApplyModeSwitch", () => {
  it("marks the active mode and leaves the other selectable", () => {
    const view = renderSwitch("review", () => undefined);
    expect(view.byLabel("人工审阅").getAttribute("aria-selected")).toBe("true");
    expect(view.byLabel("自动应用").getAttribute("aria-selected")).toBe("false");
  });

  it("reports the clicked mode through onChange", () => {
    const onChange = vi.fn();
    const view = renderSwitch("review", onChange);
    act(() => {
      view.byLabel("自动应用").click();
    });
    expect(onChange).toHaveBeenCalledWith("auto");
  });

  it("disables both options while a switch is in flight", () => {
    const view = renderSwitch("review", () => undefined, true);
    for (const button of view.buttons) {
      expect(button.disabled).toBe(true);
    }
  });
});
