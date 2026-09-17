import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { SnapshotPanel } from "./SnapshotPanel";

describe("SnapshotPanel", () => {
  it("marks incomplete snapshots and explains restore semantics", () => {
    const html = renderToStaticMarkup(
      <SnapshotPanel
        busy={false}
        onClose={() => undefined}
        onRestore={() => undefined}
        snapshots={[
          {
            id: "s1",
            created_at: "2026-08-16T00:00:00+00:00",
            reason: "应用任务",
            file_count: 10,
            total_files: 12,
            truncated: true,
            skipped_count: 2,
          },
        ]}
      />,
    );

    expect(html).toContain("跳过 2");
    expect(html).toContain("这个快照不完整");
    expect(html).toContain("不会自动删除该时刻之后新增的文件");
  });
});
