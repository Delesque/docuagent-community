/** Per-task stream ring buffer.
 *
 *  Reasoning arrives as a token stream, not as lines, so the buffer keeps a bounded
 *  list of complete lines plus one open tail line. Graph nodes and the task panel
 *  slice the last N lines from this same structure, which is what keeps a 60+ node
 *  graph from retaining the full transcript of every task.
 */

export interface TaskStream {
  lines: string[];
  tail: string;
}

export function emptyTaskStream(): TaskStream {
  return { lines: [], tail: "" };
}

export function pushTaskStream(
  stream: TaskStream | undefined,
  text: string,
  maxLines = 14,
  maxChars = 2400,
): TaskStream {
  const current = stream ?? emptyTaskStream();
  let tail = current.tail + text;
  if (tail.length > maxChars) tail = tail.slice(-maxChars);
  const lines = current.lines.slice();
  const parts = tail.split("\n");
  tail = parts.pop() ?? "";
  for (const part of parts) {
    if (part) lines.push(part);
  }
  while (lines.length > maxLines) lines.shift();
  while (lines.join("\n").length > maxChars) lines.shift();
  return { lines, tail };
}

/** Complete lines plus the open tail, newest last. */
export function taskStreamText(stream: TaskStream | undefined): string[] {
  if (!stream) return [];
  return stream.tail ? [...stream.lines, stream.tail] : stream.lines;
}
