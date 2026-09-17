import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { ProvenanceLedger } from "./ProvenanceLedger";
import type { ProvenanceClaim } from "../api";

const claims: ProvenanceClaim[] = [
  { id: "claim-1", text: "用户配置存在本地文件。", source: "inferred", module_id: "config" },
  { id: "claim-2", text: "第一版不引入数据库。", source: "confirmed" },
  { id: "claim-3", text: "导出格式选 WebP。", source: "rejected" },
];

describe("ProvenanceLedger", () => {
  it("shows pending claims with anchors and decided claims separately", () => {
    const html = renderToStaticMarkup(
      <ProvenanceLedger
        claims={claims}
        moduleNames={{ config: "配置中心" }}
        busyId={null}
        onAction={() => undefined}
        onClose={() => undefined}
      />,
    );

    expect(html).toContain("用户配置存在本地文件。");
    expect(html).toContain("挂载模块：配置中心");
    expect(html).toContain("待处理 · 1");
    expect(html).toContain("已决定 · 2");
    // Pending claims offer all four decisions; disabled logic is props-driven.
    expect(html).toContain("确认");
    expect(html).toContain("修改");
    expect(html).toContain("不确定");
    expect(html).toContain("拒绝");
    // Decided claims render without the four-button row: the confirmed one has
    // no buttons left, the rejected one neither.
    const decidedPart = html.split("已决定")[1] ?? "";
    expect(decidedPart).not.toContain("挂载模块");
  });

  it("disables the busy claim's buttons while a decision is in flight", () => {
    const html = renderToStaticMarkup(
      <ProvenanceLedger
        claims={claims}
        moduleNames={{}}
        busyId="claim-1"
        onAction={() => undefined}
        onClose={() => undefined}
      />,
    );

    const busyCard = html.split("用户配置存在本地文件。")[1]?.split("</article>")[0] ?? "";
    expect(busyCard).toContain("disabled");
  });

  it("renders an empty state when there is nothing to confirm", () => {
    const html = renderToStaticMarkup(
      <ProvenanceLedger
        claims={[]}
        moduleNames={{}}
        busyId={null}
        onAction={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(html).toContain("这份架构没有需要确认的来源条目。");
  });

  it("offers accept-all when there are pending claims and hides it otherwise", () => {
    const withPending = renderToStaticMarkup(
      <ProvenanceLedger
        claims={claims}
        moduleNames={{}}
        busyId={null}
        onAction={() => undefined}
        onAcceptAll={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(withPending).toContain("全部确认（1）");

    const decidedOnly = renderToStaticMarkup(
      <ProvenanceLedger
        claims={[{ id: "c9", text: "已确认的事实。", source: "confirmed" }]}
        moduleNames={{}}
        busyId={null}
        onAction={() => undefined}
        onAcceptAll={() => undefined}
        onClose={() => undefined}
      />,
    );
    expect(decidedOnly).not.toContain("全部确认");
  });
});
