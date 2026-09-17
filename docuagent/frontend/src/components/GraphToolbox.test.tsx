import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { GraphToolbox, graphToolNeedsInput } from "./GraphToolbox";

describe("GraphToolbox", () => {
  it("treats action tools as click-to-run instead of text inputs", () => {
    for (const tool of ["focus", "verify", "archive"] as const) {
      expect(graphToolNeedsInput(tool)).toBe(false);
    }
  });

  it("keeps text input for the single note tool", () => {
    expect(graphToolNeedsInput("note")).toBe(true);
  });

  it("renders every action tool button", () => {
    const html = renderToStaticMarkup(
      <GraphToolbox activeTool={null} onSelect={() => undefined} />,
    );
    for (const label of ["聚焦", "验证", "归档", "备注"]) {
      expect(html).toContain(label);
    }
  });
});
