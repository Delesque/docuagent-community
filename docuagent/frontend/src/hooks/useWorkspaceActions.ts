import { useCallback } from "react";
import type { Dispatch, SetStateAction } from "react";
import { clearStale, inspectWorkspace, terminalExec, type BootstrapState, type WorkspaceInfo } from "../api";
import { writelnToTerminal } from "../components/terminalBus";
import type { Message } from "../components/v2/TypewriterOutput";

export interface WorkspaceActionsOptions {
  workspace: WorkspaceInfo | null;
  say: (message: Message) => void;
  setWorkspace: Dispatch<SetStateAction<WorkspaceInfo | null>>;
  setBootstrap: Dispatch<SetStateAction<BootstrapState | null>>;
  setUndoAvailable: Dispatch<SetStateAction<boolean>>;
}

export interface WorkspaceActions {
  handleTerminalExec: (command: string) => void;
  handleClearStale: (moduleId: string) => Promise<void>;
}

/** Graph-canvas actions that mutate workspace state outside the task flow.

 *
 *  Terminal output writes to the terminal bus; clearing stale marks refreshes the
 *  workspace snapshot so the graph statuses and undo button stay current.
 */
export function useWorkspaceActions({ workspace, say, setWorkspace, setBootstrap, setUndoAvailable }: WorkspaceActionsOptions): WorkspaceActions {
  const handleTerminalExec = useCallback(
    (command: string) => {
      if (!workspace) {
        writelnToTerminal("\x1b[1;31m✗ 还没有选择项目地址。\x1b[0m");
        return;
      }
      void (async () => {
        try {
          const result = await terminalExec(workspace.path, command);
          if (result.stdout) writelnToTerminal(result.stdout.replace(/\n$/, ""));
          if (result.stderr) writelnToTerminal(`\x1b[1;31m${result.stderr.replace(/\n$/, "")}\x1b[0m`);
          writelnToTerminal(result.returncode === 0 ? "\x1b[90m退出码 0\x1b[0m" : `\x1b[1;31m退出码 ${result.returncode}\x1b[0m`);
        } catch (cause) {
          writelnToTerminal(`\x1b[1;31m✗ ${(cause as Error).message}\x1b[0m`);
        }
      })();
    },
    [workspace],
  );

  const handleClearStale = useCallback(
    async (moduleId: string) => {
      if (!workspace) return;
      try {
        await clearStale(workspace.path, [moduleId]);
        const refreshed = await inspectWorkspace(workspace.path);
        setWorkspace(refreshed);
        setBootstrap(refreshed.bootstrap);
        setUndoAvailable(refreshed.architecture_can_undo);
      } catch (cause) {
        say({ role: "agent", text: `清除过期标记失败：${(cause as Error).message}` });
      }
    },
    [say, setBootstrap, setUndoAvailable, setWorkspace, workspace],
  );

  return { handleTerminalExec, handleClearStale };
}
