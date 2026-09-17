import { describe, expect, it } from "vitest";
import {
  emptyTaskStream,
  pushTaskStream,
  taskStreamText,
} from "./taskStream";

describe("taskStream ring buffer", () => {
  it("splits chunks into complete lines and an open tail", () => {
    let stream = emptyTaskStream();
    stream = pushTaskStream(stream, "正在拆");
    stream = pushTaskStream(stream, "分依赖\n写入接口");
    stream = pushTaskStream(stream, "层");
    expect(taskStreamText(stream)).toEqual(["正在拆分依赖", "写入接口层"]);
  });

  it("bounds the number of retained lines", () => {
    let stream = emptyTaskStream();
    for (let index = 0; index < 30; index += 1) {
      stream = pushTaskStream(stream, `line ${index}\n`, 5, 400);
    }
    expect(taskStreamText(stream).length).toBeLessThanOrEqual(5);
    expect(taskStreamText(stream)).toContain("line 29");
  });

  it("bounds total characters even for one long line", () => {
    let stream = emptyTaskStream();
    stream = pushTaskStream(stream, "x".repeat(3000), 14, 2000);
    const text = taskStreamText(stream);
    expect(text.length).toBeGreaterThan(0);
    expect((text[0] ?? "").length).toBeLessThanOrEqual(2000);
  });
});
