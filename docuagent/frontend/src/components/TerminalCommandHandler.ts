import type { TaskItem } from "../api";
import { clearTerminal, writelnToTerminal } from "./terminalBus";

export interface CommandContext {
  task: TaskItem | null;
  moduleId: string | null;
  onVerify?: () => void;
  onApplyAll?: () => void;
}

const COMMANDS = {
  help: {
    description: "显示所有可用命令",
    usage: "help",
  },
  info: {
    description: "显示当前任务信息",
    usage: "info",
  },
  verify: {
    description: "运行任务验证",
    usage: "verify",
  },
  apply: {
    description: "应用所有文件修改",
    usage: "apply",
  },
  clear: {
    description: "清空终端",
    usage: "clear",
  },
  files: {
    description: "列出任务生成的文件",
    usage: "files",
  },
  status: {
    description: "显示任务状态",
    usage: "status",
  },
};

export function handleCommand(command: string, context: CommandContext): void {
  const [cmd] = command.split(/\s+/);

  switch (cmd) {
    case "help":
      showHelp();
      break;

    case "info":
      showTaskInfo(context);
      break;

    case "verify":
      if (context.onVerify) {
        writelnToTerminal("\x1b[1;33m⏳ 正在运行验证...\x1b[0m");
        context.onVerify();
      } else {
        writelnToTerminal("\x1b[1;31m✗ 验证功能不可用\x1b[0m");
      }
      break;

    case "apply":
      if (context.onApplyAll) {
        writelnToTerminal("\x1b[1;33m⏳ 正在应用所有文件...\x1b[0m");
        context.onApplyAll();
      } else {
        writelnToTerminal("\x1b[1;31m✗ 应用功能不可用\x1b[0m");
      }
      break;

    case "clear":
      clearTerminal();
      break;

    case "files":
      showFiles(context);
      break;

    case "status":
      showStatus(context);
      break;

    case "":
      // 空命令，忽略
      break;

    default:
      writelnToTerminal(`\x1b[1;31m✗ 未知命令: ${cmd}\x1b[0m`);
      writelnToTerminal(`\x1b[90m输入 'help' 查看可用命令\x1b[0m`);
  }
}

function showHelp(): void {
  writelnToTerminal("\x1b[1;36m可用命令:\x1b[0m");
  for (const info of Object.values(COMMANDS)) {
    writelnToTerminal(
      `  \x1b[1;32m${info.usage.padEnd(12)}\x1b[0m ${info.description}`
    );
  }
}

function showTaskInfo(context: CommandContext): void {
  if (!context.task) {
    writelnToTerminal("\x1b[1;31m✗ 没有选中的任务\x1b[0m");
    return;
  }

  const task = context.task;
  writelnToTerminal("\x1b[1;36m任务信息:\x1b[0m");
  writelnToTerminal(`  ID:      ${task.id}`);
  writelnToTerminal(`  模块:    ${task.module_id}`);
  writelnToTerminal(`  摘要:    ${task.summary}`);
  writelnToTerminal(`  状态:    ${getStatusLabel(task.status)}`);

  if (task.target_files?.length) {
    writelnToTerminal(`  目标文件:`);
    task.target_files.forEach((file) => {
      writelnToTerminal(`    - ${file}`);
    });
  }

  if (task.depends_on?.length) {
    writelnToTerminal(`  依赖任务: ${task.depends_on.join(", ")}`);
  }
}

function showFiles(context: CommandContext): void {
  if (!context.task?.patch?.length) {
    writelnToTerminal("\x1b[1;31m✗ 没有生成的文件\x1b[0m");
    return;
  }

  writelnToTerminal("\x1b[1;36m生成的文件:\x1b[0m");
  context.task.patch.forEach((file, index) => {
    const status = getFileStatus(file.path, context.task);
    writelnToTerminal(`  ${index + 1}. ${file.path} ${status}`);
  });
}

function showStatus(context: CommandContext): void {
  if (!context.task) {
    writelnToTerminal("\x1b[1;31m✗ 没有选中的任务\x1b[0m");
    return;
  }

  const task = context.task;
  const statusLabel = getStatusLabel(task.status);
  writelnToTerminal(`\x1b[1;36m任务状态:\x1b[0m ${statusLabel}`);

  if (task.verification_output) {
    const success = task.verification_output.returncode === 0;
    const icon = success ? "\x1b[1;32m✓\x1b[0m" : "\x1b[1;31m✗\x1b[0m";
    writelnToTerminal(`  验证:    ${icon} ${success ? "通过" : "失败"}`);
  }

  if (task.patch?.length) {
    const accepted = task.applied_files?.length ?? 0;
    const rejected = task.rejected_files?.length ?? 0;
    const pending = task.pending_files?.length ?? task.patch.length - accepted - rejected;
    writelnToTerminal(`  文件决策: ${accepted} 接受, ${rejected} 拒绝, ${pending} 待定`);
  }
}

function getStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    pending: "\x1b[1;33m⏳ 待处理\x1b[0m",
    in_progress: "\x1b[1;34m🔄 进行中\x1b[0m",
    generated: "\x1b[1;36m📝 已生成\x1b[0m",
    applied: "\x1b[1;32m✓ 已应用\x1b[0m",
    partially_applied: "\x1b[1;35m◐ 部分应用\x1b[0m",
    verified: "\x1b[1;32m✓ 已验证\x1b[0m",
    rejected: "\x1b[1;31m✗ 已拒绝\x1b[0m",
  };
  return labels[status] || status;
}

function getFileStatus(path: string, task: TaskItem | null): string {
  if (task?.applied_files?.includes(path)) {
    return "\x1b[1;32m✓ accepted\x1b[0m";
  }
  if (task?.rejected_files?.includes(path)) {
    return "\x1b[1;31m✗ rejected\x1b[0m";
  }
  return "\x1b[90m○ pending\x1b[0m";
}
