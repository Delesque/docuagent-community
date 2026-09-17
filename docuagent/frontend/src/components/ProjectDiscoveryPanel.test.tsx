// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import axe from "axe-core";
import { ProjectDiscoveryPanel } from "./ProjectDiscoveryPanel";
import type { ProviderConfig } from "../api";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
const api = vi.hoisted(() => ({ readDiscovery: vi.fn(), proposeDiscovery: vi.fn(), searchDiscovery: vi.fn(), decideDiscovery: vi.fn() }));
vi.mock("../api/discovery", () => api);
const provider = { enabled: false } as ProviderConfig;
const proposal = { id: "one", query: "booking application", status: "proposed", candidates: [], decisions: [] };
let root: Root;
let container: HTMLDivElement;

beforeEach(() => {
  vi.resetAllMocks();
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  container = document.createElement("div"); document.body.append(container);
  api.readDiscovery.mockResolvedValue({ status: "empty", candidates: [], decisions: [] });
});
afterEach(() => { if (root) act(() => root.unmount()); container.remove(); });

async function render() {
  const close = vi.fn();
  await act(async () => { root = createRoot(container); root.render(<ProjectDiscoveryPanel path="project" provider={provider} onClose={close} />); });
  return close;
}
const button = (label: string) => [...container.querySelectorAll("button")].find((item) => item.textContent === label)!;

describe("ProjectDiscoveryPanel", () => {
  it("does not search when opened or when a query is proposed", async () => {
    api.proposeDiscovery.mockResolvedValue(proposal);
    await render();
    expect(api.searchDiscovery).not.toHaveBeenCalled();
    await act(async () => button("AI 生成检索申请").click());
    expect(api.proposeDiscovery).toHaveBeenCalledWith("project", provider, undefined);
    expect(api.searchDiscovery).not.toHaveBeenCalled();
    api.searchDiscovery.mockResolvedValue({ ...proposal, status: "searched" });
    await act(async () => button("同意发送到 GitHub").click());
    expect(api.searchDiscovery).toHaveBeenCalledWith("project", "one");
    expect(container.textContent).toContain("未找到候选项目");
  });

  it("requires a fresh proposal after editing the public query", async () => {
    api.readDiscovery.mockResolvedValue(proposal);
    await render();
    const input = container.querySelector("input")!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, "other project");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    expect(button("同意发送到 GitHub")).toBeUndefined();
  });

  it("can skip the search without sending a network search request", async () => {
    api.readDiscovery.mockResolvedValue(proposal);
    api.decideDiscovery.mockResolvedValue({ ...proposal, status: "skipped" });
    await render();
    await act(async () => button("暂不检索").click());
    expect(api.decideDiscovery).toHaveBeenCalledWith("project", "one", "skip");
    expect(api.searchDiscovery).not.toHaveBeenCalled();
  });

  it("reports failures and keeps the proposal available for retry", async () => {
    api.readDiscovery.mockResolvedValue(proposal);
    api.searchDiscovery.mockRejectedValue(new Error("offline"));
    await render();
    await act(async () => button("同意发送到 GitHub").click());
    expect(container.querySelector('[role="alert"]')?.textContent).toBe("offline");
    expect(button("同意发送到 GitHub").disabled).toBe(false);
  });

  it("has named dialog and fields with no detected WCAG A/AA violations", async () => {
    api.readDiscovery.mockResolvedValue(proposal);
    await render();
    const result = await axe.run(container, { runOnly: { type: "tag", values: ["wcag2a", "wcag2aa"] }, rules: { "color-contrast": { enabled: false } } });
    expect(result.violations).toEqual([]);
  });
});
