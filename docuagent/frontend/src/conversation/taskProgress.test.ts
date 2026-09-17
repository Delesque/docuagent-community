import { describe, expect, it } from "vitest";
import {
  composeTaskProgress,
  composeTaskStatusAnswer,
} from "./taskProgress";

describe("task progress lines", () => {
  it("shows running tasks with the latest reasoning tail", () => {
    const text = composeTaskProgress([
      { id: "core", progress: { status: "running", latest: "拆\n拆分认证边界" } },
      { id: "auth", progress: { status: "done", latest: "" } },
    ]);
    expect(text).toContain("「core」生成中：拆分认证边界");
    expect(text).toContain("「auth」已生成");
  });

  it("waits for the first token instead of showing a local summary", () => {
    const text = composeTaskProgress([
      { id: "core", progress: { status: "running", latest: "" } },
    ]);
    expect(text).toContain("等待模型输出");
    expect(text).not.toContain("本地摘要");
  });

  it("answers status questions from the same source", () => {
    const answer = composeTaskStatusAnswer([
      { id: "core", progress: { status: "running", latest: "正在写接口" } },
    ]);
    expect(answer).toContain("「core」生成中：正在写接口");
  });
});
