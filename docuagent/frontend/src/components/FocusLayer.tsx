/** What a node shows when you zoom into it.
 *
 *  The conversation and a module are the same kind of thing here: both are nodes you
 *  enter by zooming, both are left the same way. That is the point of folding the
 *  conversation into the graph — there is no "chat mode" versus "graph mode", only how
 *  close the camera is to one node.
 *
 *  This layer is presentation only. It owns no camera state and no focus decision:
 *  `decideFocus` in graph/zoom.ts remains the single place focus is decided, and the
 *  store holds `focusedId`. Rendering follows.
 */

import { lazy, Suspense, useEffect, useRef, useState } from "react";
import type { GraphModule } from "../graph/types";
import { isConversationNode } from "../graph/conversationNode";
import type { NodeAttachment, TaskItem } from "../api";
import { TaskInfoCard } from "./TaskInfoCard";
import { VerificationBadge } from "./VerificationBadge";
import { FileDiffBlocks } from "./FileDiffBlocks";
import { handleCommand } from "./TerminalCommandHandler";

/** Shown when a lazy chunk fails to load — typically the frontend was rebuilt while
 *  this tab was still open, so the cached entry references a chunk hash the server
 *  no longer serves. Refreshing re-fetches the entry and the current chunk hashes. */
function ChunkLoadError({ name }: { name: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-2 p-4 text-center">
      <span className="font-mono text-[11px] text-chalk-faint">
        {name}资源版本已更新，当前页面缓存过期。
      </span>
      <button
        type="button"
        onClick={() => window.location.reload()}
        className="inline-flex h-7 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[10.5px] text-chalk transition-colors hover:bg-paper-float"
      >
        刷新页面
      </button>
    </div>
  );
}

// Lazy chunks can 404 after a rebuild swaps their content hash out from under a tab
// that stayed open. Catch that and render a recoverable notice instead of a blank
// panel with only a console error.
const CodeViewer = lazy(() =>
  import("./CodeViewer")
    .then((module) => ({ default: module.CodeViewer }))
    .catch(() => ({ default: () => <ChunkLoadError name="代码查看器" /> })),
);
const Terminal = lazy(() =>
  import("./Terminal")
    .then((module) => ({ default: module.Terminal }))
    .catch(() => ({ default: () => <ChunkLoadError name="终端" /> })),
);

const TRACEBACK_LINK_RE =
  /File "([^"]+)", line ([0-9]+)|([A-Za-z0-9_./-]+[.](?:py|ts|tsx|js|jsx|json|md|yaml|yml|html|css|sql|sh)):([0-9]+)(?::[0-9]+)?/g;

function linkifyTraceback(
  text: string,
  onJump: (path: string, line: number) => void,
): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let key = 0;
  TRACEBACK_LINK_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = TRACEBACK_LINK_RE.exec(text)) !== null) {
    const full = match[0];
    const path = match[1] ?? match[3];
    const line = match[2] ?? match[4];
    if (!path || !line) continue;
    if (match.index > last) nodes.push(text.slice(last, match.index));
    nodes.push(
      <button
        key={key++}
        type="button"
        onClick={() => onJump(path, parseInt(line, 10))}
        className="border-0 bg-transparent p-0 font-mono text-emerald underline underline-offset-2 hover:text-chalk"
      >
        {full}
      </button>
    );
    last = match.index + full.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

interface FocusLayerProps {
  module: GraphModule;
  /** Rendered inside the frame when the conversation is focused. */
  children?: React.ReactNode;
  reducedMotion: boolean;
  /** Screen-space rect of the node the camera came from, so entry can scale from it. */
  source?: { x: number; y: number; width: number; height: number } | null;
  /** Viewport size the panel grows into, in the same coordinate space as `source`. */
  target?: { width: number; height: number };
  /** True while the exit animation runs before the layer unmounts. */
  leaving?: boolean;
  /** Whether this module's task/requirement is stale after an architecture edit. */
  stale?: boolean;
  /** Confirm the stale module is fixed and clear its stale marker. */
  onClearStale?: () => void;
  /** The task bound to this module, if one exists. */
  task?: TaskItem | null;
  /** Live ring-buffer lines while the task is being generated. */
  taskStream?: string[];
  /** Whether a generation wave is running for this module's task. */
  taskStreaming?: boolean;
  /** Notes attached to this module. */
  attachments?: NodeAttachment[];
  /** Apply selected diff hunks for one patch file. */
  onApplyHunks?: (
    file: string,
    hunkIds: number[],
  ) => boolean | Promise<boolean>;
  /** Persist a user edit to one proposed file before applying. */
  onSavePatch?: (
    file: string,
    content: string,
    baseAfter: string,
  ) => boolean | Promise<boolean>;
  /** Run the backend verification for this module's task. */
  onVerifyTask?: (taskId: string) => void;
  /** Apply this module's full task patch. */
  onApplyTask?: (taskId: string) => void;
  onResumeTask?: (taskId: string) => void;
  /** Run a whitelisted shell command and write its output to the terminal. */
  onTerminalExec?: (command: string) => void;
  onExit: () => void;
}

export function FocusLayer({
  module,
  children,
  reducedMotion,
  source,
  target,
  leaving = false,
  stale = false,
  onClearStale,
  task = null,
  taskStream = [],
  taskStreaming = false,
  attachments = [],
  onApplyHunks,
  onSavePatch,
  onVerifyTask,
  onApplyTask,
  onResumeTask,
  onTerminalExec,
  onExit,
}: FocusLayerProps) {
  const frameRef = useRef<HTMLDivElement>(null);
  const conversation = isConversationNode(module.id);
  const sharedTransition =
    source && target && target.width > 0 && target.height > 0 && !reducedMotion;

  useEffect(() => {
    frameRef.current?.focus({ preventScroll: true });
  }, [module.id]);

  return (
    <div
      className="absolute inset-0 z-40"
      style={{
        transformOrigin: sharedTransition ? "0 0" : undefined,
        ...(sharedTransition
          ? {
              ["--fx" as string]: `${source.x}px`,
              ["--fy" as string]: `${source.y}px`,
              ["--fsx" as string]: source.width / target.width,
              ["--fsy" as string]: source.height / target.height,
              animation: leaving
                ? "focus-to-node 170ms cubic-bezier(0.2,0.8,0.2,1) both"
                : "focus-from-node 220ms cubic-bezier(0.2,0.8,0.2,1) both",
            }
          : {
              animation: reducedMotion ? "none" : "focus-in 180ms cubic-bezier(0.2,0.8,0.2,1)",
            }),
      }}
    >
      <div className="absolute inset-0 bg-paper/82" onPointerDown={onExit} />

      <div
        ref={frameRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${module.name} · CLI`}
        tabIndex={-1}
        className="absolute inset-0 overflow-hidden border-0 bg-paper-raise pt-12 outline-none"
        onPointerDown={(event) => event.stopPropagation()}
      >
        <header className="flex items-baseline gap-3 border-b border-ink-ghost px-6 py-3">
          <h2 className="font-display text-[16px] text-chalk">{module.name}</h2>
          {module.path ? (
            <span className="truncate font-mono text-[10px] text-chalk-faint">
              {module.path}
            </span>
          ) : null}
          <button
            type="button"
            onClick={onExit}
            className="ml-auto rounded-md border border-ink-dim/45 bg-paper px-3 py-1.5 font-mono text-[11px] text-chalk-dim transition-colors duration-150 hover:border-ink hover:text-chalk focus-visible:ring-1 focus-visible:ring-ink"
          >
            Esc 返回图
          </button>
        </header>

        <div className="h-[calc(100%-49px)] overflow-y-auto" data-scrollable>
          {conversation ? (
            children
          ) : (
            <ModuleCli
              module={module}
              stale={stale}
              onClearStale={onClearStale}
              task={task}
              taskStream={taskStream}
              taskStreaming={taskStreaming}
              attachments={attachments}
              onApplyHunks={onApplyHunks}
              onSavePatch={onSavePatch}
              onVerifyTask={onVerifyTask}
              onApplyTask={onApplyTask}
              onResumeTask={onResumeTask}
              onTerminalExec={onTerminalExec}
            />
          )}
        </div>
      </div>
    </div>
  );
}

function ModuleCli({
  module,
  stale,
  onClearStale,
  task,
  taskStream,
  taskStreaming,
  attachments,
  onApplyHunks,
  onSavePatch,
  onVerifyTask,
  onApplyTask,
  onResumeTask,
  onTerminalExec,
}: {
  module: GraphModule;
  stale: boolean;
  onClearStale?: () => void;
  task?: TaskItem | null;
  taskStream?: string[];
  taskStreaming?: boolean;
  attachments?: NodeAttachment[];
  onApplyHunks?: (
    file: string,
    hunkIds: number[],
  ) => boolean | Promise<boolean>;
  onSavePatch?: (
    file: string,
    content: string,
    baseAfter: string,
  ) => boolean | Promise<boolean>;
  onVerifyTask?: (taskId: string) => void;
  onApplyTask?: (taskId: string) => void;
  onResumeTask?: (taskId: string) => void;
  onTerminalExec?: (command: string) => void;
}) {
  const streamRef = useRef<HTMLPreElement>(null);
  const notes = (attachments ?? []).filter((attachment) => !attachment.resolved);
  const [viewerOpen, setViewerOpen] = useState(false);
  const [jump, setJump] = useState<{ path: string; line: number; seq: number } | null>(null);
  const jumpSeq = useRef(0);
  const handleJump = (path: string, line: number) => {
    const norm = path.split(String.fromCharCode(92)).join("/");
    if (!task?.patch?.some((entry) => entry.path === norm)) return;
    jumpSeq.current += 1;
    setJump({ path: norm, line, seq: jumpSeq.current });
  };
  const builtinCommands = new Set([
    "help",
    "info",
    "verify",
    "apply",
    "clear",
    "files",
    "status",
  ]);

  useEffect(() => {
    const element = streamRef.current;
    if (element && taskStreaming) element.scrollTop = element.scrollHeight;
  }, [taskStream, taskStreaming]);

  useEffect(() => {
    setViewerOpen(false);
  }, [module.id]);

  const outputLines =
    taskStreaming || (task?.status === "applied" && (taskStream?.length ?? 0) > 0)
    ? taskStream && taskStream.length > 0
      ? taskStream
      : ["正在等待子 Agent 输出…"]
    : task?.thinking
      ? task.thinking.split("\n")
      : task?.patch && task.patch.length > 0
        ? task.patch.map((entry) => `$ file: ${entry.path}`)
        : ["等待子 Agent 输出。"];

  const hasCode = task?.patch && task.patch.length > 0;
  const hasVerification = task?.verification_output;
  const hasContractLint = Boolean(
    task?.contract_lint_violations?.length || task?.contract_delta?.length,
  );

  const commandContext = {
    task: task ?? null,
    moduleId: module.id,
    onVerify: task && onVerifyTask ? () => onVerifyTask(task.id) : undefined,
    onApplyAll: task && onApplyTask ? () => onApplyTask(task.id) : undefined,
  };

  const handleTerminalCommand = (command: string): void => {
    const cmd = command.split(/\s+/)[0] ?? "";
    if (builtinCommands.has(cmd) || !onTerminalExec) {
      handleCommand(command, commandContext);
      return;
    }
    onTerminalExec(command);
  };

  return (
    <div className="relative mx-auto max-w-5xl px-6 py-5">
      {/* Verification Badge */}
      <VerificationBadge status={task?.verification_output} />

      {stale ? (
        <div className="mb-4 flex flex-wrap items-center gap-3 rounded-none border border-vermilion/40 bg-vermilion/5 px-4 py-3">
          <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-vermilion">
            模块已过期
          </span>
          <span className="min-w-0 flex-1 font-body text-[12px] leading-relaxed text-chalk-dim">
            架构编辑后此模块或其上游需求发生变化，关联任务已被阻止。
          </span>
          {onClearStale ? (
            <button
              type="button"
              onClick={onClearStale}
              className="inline-flex h-7 items-center rounded-none border border-vermilion/50 px-2.5 font-mono text-[10.5px] text-vermilion transition-colors hover:bg-vermilion/10"
            >
              确认，清除过期
            </button>
          ) : null}
        </div>
      ) : null}

      {/* Task Info Card - always on top */}
      <TaskInfoCard module={module} task={task} />
      {task && ["pending", "failed"].includes(task.status) && task.checkpoint_available && onResumeTask ? (
        <button type="button" disabled={taskStreaming} onClick={() => onResumeTask(task.id)}
          className="mb-3 border border-ink px-3 py-1 font-mono text-[11px] text-ink">
          从检查点恢复
        </button>
      ) : null}

      {/* Interactive terminal */}
      <section className="mb-4">
        <div className="mb-1.5 flex items-center gap-3">
          <p className="text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
            &gt; 终端
          </p>
          <button
            type="button"
            onClick={() => setViewerOpen((open) => !open)}
            disabled={!hasCode}
            className="ml-auto inline-flex h-7 items-center rounded-md border border-ink/50 px-2.5 font-mono text-[10px] text-chalk-dim transition-colors hover:border-ink hover:text-chalk disabled:cursor-not-allowed disabled:opacity-40"
          >
            {viewerOpen ? "收起代码" : "查看代码"}
          </button>
        </div>
        <div className="h-56 overflow-hidden rounded-none border border-ink/60 bg-[#0A0D12] shadow-xl">
          <Suspense
            fallback={
              <div className="flex h-full items-center justify-center font-mono text-[11px] text-chalk-faint">
                正在加载终端...
              </div>
            }
          >
            <Terminal onCommand={handleTerminalCommand} />
          </Suspense>
        </div>
      </section>

      {viewerOpen && hasCode ? (
        <section className="mb-4">
          <p className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
            &gt; 代码查看
          </p>
          <div className="h-[420px] overflow-hidden rounded-none border border-ink/60 bg-[#0A0D12] shadow-xl">
            <Suspense
              fallback={
                <div className="flex h-full items-center justify-center font-mono text-[11px] text-chalk-faint">
                  正在加载代码查看器...
                </div>
              }
            >
              <CodeViewer
                files={task.patch.map((entry) => ({
                  path: entry.path,
                  content: entry.after,
                }))}
                onSave={onSavePatch}
                jumpRequest={jump}
              />
            </Suspense>
          </div>
        </section>
      ) : null}

      {/* Terminal-style continuous view */}
      <div className="overflow-hidden rounded-none border border-ink/60 bg-paper/95 font-mono shadow-2xl">
        <div className="flex items-center gap-3 border-b border-ink/40 bg-paper-raise px-4 py-2.5">
          <span className="text-ink">&gt;</span>
          <span className="truncate text-[12px] text-chalk">{module.id}</span>
          {module.path ? (
            <span className="truncate text-[10px] text-chalk-faint">{module.path}</span>
          ) : null}
        </div>

        <div className="space-y-4 p-4">
          <section>
            <p className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              &gt; 子 Agent 输出
            </p>
            <pre
              ref={streamRef}
              data-scrollable
              className="max-h-[46vh] overflow-auto whitespace-pre-wrap rounded-none border border-ink/40 bg-paper/80 p-3 text-[11px] leading-[1.55] text-chalk-dim"
            >
              {outputLines.join("\n")}
            </pre>
          </section>

          {/* File Diff Blocks - inline in terminal flow */}
          {hasCode ? (
            <FileDiffBlocks
              files={task.patch}
              appliedFiles={task.applied_files}
              rejectedFiles={task.rejected_files}
              onApplyHunks={onApplyHunks}
            />
          ) : null}

          {/* Contract lint results - inline in terminal flow */}
          {hasContractLint && task ? (
            <section>
              <p className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
                &gt; 契约检查
              </p>
              <div className="space-y-2">
                {task.contract_lint_violations?.length ? (
                  <div className="rounded-none border border-vermilion/40 bg-vermilion/5 p-3">
                    <p className="mb-1.5 text-[10px] text-vermilion">契约违规（应用前必须处理）</p>
                    <ul className="max-h-[32vh] space-y-1 overflow-auto text-[10.5px] leading-[1.5] text-vermilion">
                      {task.contract_lint_violations.map((item, index) => (
                        <li key={index}>- {item}</li>
                      ))}
                    </ul>
                  </div>
                ) : null}
                {task.contract_delta?.length ? (
                  <div className="rounded-none border border-amber/40 bg-amber/5 p-3">
                    <p className="mb-1.5 text-[10px] text-amber">契约变更待审阅（contract_delta）</p>
                    <ul className="max-h-[32vh] space-y-1 overflow-auto text-[10.5px] leading-[1.5] text-amber">
                      {task.contract_delta.map((item, index) => (
                        <li key={index}>
                          - {item.module_id}:{item.symbol}（{item.kind}） — {item.file}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </div>
            </section>
          ) : null}

          {/* Verification Output - inline in terminal flow */}
          {hasVerification && task.verification_output ? (
            <section>
              <p className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
                &gt; 验证输出
              </p>
              <div className="space-y-2">
                {task.verification_output.command ? (
                  <p className="text-[10px] text-chalk-faint">
                    $ {task.verification_output.command}
                  </p>
                ) : null}
                {task.verification_output.stdout ? (
                  <pre
                    data-scrollable
                    className="max-h-[32vh] overflow-auto whitespace-pre-wrap rounded-none border border-ink/40 bg-paper/80 p-3 text-[11px] leading-[1.55] text-chalk-dim"
                  >
                    {linkifyTraceback(task.verification_output.stdout, handleJump)}
                  </pre>
                ) : null}
                {task.verification_output.stderr ? (
                  <pre
                    data-scrollable
                    className="max-h-[32vh] overflow-auto whitespace-pre-wrap rounded-none border border-vermilion/40 bg-vermilion/5 p-3 text-[11px] leading-[1.55] text-vermilion"
                  >
                    {linkifyTraceback(task.verification_output.stderr, handleJump)}
                  </pre>
                ) : null}
                <div className="flex items-center gap-2 text-[10px]">
                  <span className="text-chalk-faint">返回码：</span>
                  <span
                    className={
                      task.verification_output.returncode === 0
                        ? "text-emerald"
                        : "text-vermilion"
                    }
                  >
                    {task.verification_output.returncode}
                  </span>
                </div>
              </div>
            </section>
          ) : null}

          {notes.length > 0 ? (
            <section>
              <p className="mb-1.5 text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
                &gt; 备注
              </p>
              <div className="space-y-2">
                {notes.map((note) => (
                  <p
                    key={note.id}
                    className="whitespace-pre-wrap border border-ink/30 bg-paper-raise/70 px-3 py-2 text-[11px] leading-relaxed text-chalk-dim"
                  >
                    # {note.text}
                  </p>
                ))}
              </div>
            </section>
          ) : null}
        </div>
      </div>
    </div>
  );
}
