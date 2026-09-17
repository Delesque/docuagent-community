import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { FileDiffBlocks } from "./FileDiffBlocks";
import type { TaskPatchEntry } from "../api";

const file: TaskPatchEntry = {
  path: "src/core.py",
  before: "print('old')\n",
  after: "print('new')\n",
  diff: "+print('new')",
  hunks: [
    {
      id: 0,
      tag: "replace",
      before_start: 1,
      before_count: 1,
      after_start: 1,
      after_count: 1,
      before: "print('old')\n",
      after: "print('new')\n",
    },
  ],
};

describe("FileDiffBlocks", () => {
  it("summarizes generated files and applied state", () => {
    const html = renderToStaticMarkup(
      <FileDiffBlocks files={[file]} appliedFiles={["src/core.py"]} />,
    );

    expect(html).toContain("生成的文件");
    expect(html).toContain("1 已应用 / 1 个文件");
    expect(html).toContain("src/core.py");
    expect(html).toContain("✓ 已应用");
  });

  it("keeps hunk review actions available for unapplied files", () => {
    const html = renderToStaticMarkup(
      <FileDiffBlocks files={[file]} appliedFiles={[]} />,
    );

    expect(html).toContain("▼ 改动块");
    expect(html).not.toContain("✓ 已应用");
  });
});
