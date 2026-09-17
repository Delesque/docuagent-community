// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DocIgnorePanel } from "./DocIgnorePanel";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const api = vi.hoisted(() => ({
  fetchDocIgnore: vi.fn(),
  setDocIgnore: vi.fn(),
}));

vi.mock("../api", async () => {
  const actual = await vi.importActual<typeof import("../api")>("../api");
  return { ...actual, ...api };
});

afterEach(() => vi.clearAllMocks());

/** Set a controlled input the way React listens for it: through the native
 *  value setter, not a bare property assignment. */
function typeInto(input: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  setter.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

async function renderPanel() {
  const container = document.createElement("div");
  const onClose = vi.fn();
  let root: Root;
  await act(async () => {
    root = createRoot(container);
    root.render(<DocIgnorePanel path="C:/p" onClose={onClose} />);
    await Promise.resolve();
  });
  const flush = () => act(async () => {});
  const byText = <T extends HTMLElement>(selector: string, text: string) =>
    Array.from(container.querySelectorAll<T>(selector)).find((el) => el.textContent === text)!;
  return {
    container,
    onClose,
    flush,
    byText,
    hasText: (text: string) => container.textContent!.includes(text),
    removeButton: (name: string) =>
      container.querySelector<HTMLButtonElement>(`button[aria-label='移除 ${name}']`)!,
    input: () => container.querySelector<HTMLInputElement>("input[aria-label='新目录名']")!,
    addButton: () => byText<HTMLButtonElement>("button", "添加"),
    closeButton: () => container.querySelector<HTMLButtonElement>("button[aria-label='关闭']")!,
    unmount() { act(() => root!.unmount()); },
  };
}

describe("DocIgnorePanel", () => {
  it("separates the built-in list from project additions", async () => {
    api.fetchDocIgnore.mockResolvedValue({
      defaults: ["node_modules"],
      ignored_dirs: ["node_modules", "vendor"],
    });
    const view = await renderPanel();
    expect(view.byText("h3", "系统内置")).toBeTruthy();
    expect(view.byText("h3", "本项目添加")).toBeTruthy();
    expect(view.hasText("node_modules")).toBe(true);
    expect(view.removeButton("vendor")).toBeTruthy();
    expect(view.removeButton("node_modules")).toBeFalsy();
  });

  it("adds a directory, clears the draft, and shows the merged reply", async () => {
    api.fetchDocIgnore.mockResolvedValue({ defaults: ["node_modules"], ignored_dirs: ["node_modules"] });
    api.setDocIgnore.mockResolvedValue({
      defaults: ["node_modules"],
      ignored_dirs: ["node_modules", "generated"],
    });
    const view = await renderPanel();
    act(() => {
      typeInto(view.input(), "generated");
    });
    act(() => {
      view.addButton().click();
    });
    await view.flush();
    expect(api.setDocIgnore).toHaveBeenCalledWith("C:/p", ["generated"]);
    expect(view.input().value).toBe("");
    expect(view.hasText("generated")).toBe(true);
  });

  it("removes a project directory through its delete button", async () => {
    api.fetchDocIgnore.mockResolvedValue({ defaults: [], ignored_dirs: ["vendor"] });
    api.setDocIgnore.mockResolvedValue({ defaults: [], ignored_dirs: [] });
    const view = await renderPanel();
    act(() => {
      view.removeButton("vendor").click();
    });
    await view.flush();
    expect(api.setDocIgnore).toHaveBeenCalledWith("C:/p", []);
  });

  it("shows a write failure as an alert", async () => {
    api.fetchDocIgnore.mockResolvedValue({ defaults: [], ignored_dirs: [] });
    api.setDocIgnore.mockRejectedValue(new Error("忽略目录必须是单级目录名"));
    const view = await renderPanel();
    act(() => {
      typeInto(view.input(), "a/b");
    });
    act(() => {
      view.addButton().click();
    });
    await view.flush();
    const alert = view.container.querySelector("[role='alert']");
    expect(alert?.textContent).toContain("单级目录名");
  });

  it("shows the read failure instead of the editor", async () => {
    api.fetchDocIgnore.mockRejectedValue(new Error("offline"));
    const view = await renderPanel();
    expect(view.container.textContent).toContain("读取失败");
    expect(view.container.querySelector("input")).toBeNull();
  });

  it("closes through the close button", async () => {
    api.fetchDocIgnore.mockResolvedValue({ defaults: [], ignored_dirs: [] });
    const view = await renderPanel();
    act(() => {
      view.closeButton().click();
    });
    expect(view.onClose).toHaveBeenCalled();
  });
});
