import { useEffect, useRef } from "react";
import { Terminal as XTerm } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";
import { setTerminalHandle } from "./terminalBus";

interface TerminalProps {
  onCommand?: (command: string) => void;
  onResize?: (cols: number, rows: number) => void;
}

const PROMPT = "\x1b[1;32m$\x1b[0m ";

export function Terminal({ onCommand, onResize }: TerminalProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const terminalRef = useRef<XTerm | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const currentLineRef = useRef<string>("");
  const historyRef = useRef<string[]>([]);
  const historyIndexRef = useRef(0);
  const onCommandRef = useRef(onCommand);
  const onResizeRef = useRef(onResize);
  onCommandRef.current = onCommand;
  onResizeRef.current = onResize;

  useEffect(() => {
    if (!containerRef.current) return;

    const terminal = new XTerm({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: "'Cascadia Code', 'Fira Code', 'Consolas', monospace",
      theme: {
        background: "#0A0D12",
        foreground: "#E5E7EB",
        cursor: "#38BDF8",
        black: "#0F131C",
        red: "#F87171",
        green: "#6EE7B7",
        yellow: "#FCD34D",
        blue: "#38BDF8",
        magenta: "#C084FC",
        cyan: "#22D3EE",
        white: "#E5E7EB",
        brightBlack: "#6B7280",
        brightRed: "#FCA5A5",
        brightGreen: "#86EFAC",
        brightYellow: "#FDE68A",
        brightBlue: "#7DD3FC",
        brightMagenta: "#D8B4FE",
        brightCyan: "#67E8F9",
        brightWhite: "#F9FAFB",
      },
    });

    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();
    terminal.loadAddon(fitAddon);
    terminal.loadAddon(webLinksAddon);
    terminal.open(containerRef.current);
    fitAddon.fit();

    terminal.writeln("\x1b[1;36mDocuAgent Terminal\x1b[0m");
    terminal.writeln("\x1b[90m输入 help 查看任务命令，或直接运行白名单命令\x1b[0m");
    terminal.write(PROMPT);

    terminalRef.current = terminal;
    fitAddonRef.current = fitAddon;
    setTerminalHandle({
      write: (text) => terminal.write(text),
      writeln: (text) => terminal.writeln(text),
      clear: () => terminal.clear(),
    });

    terminal.onData((data) => {
      const code = data.charCodeAt(0);

      if (code === 13) {
        const command = currentLineRef.current.trim();
        terminal.write("\r\n");
        if (command) {
          historyRef.current.push(command);
          historyIndexRef.current = historyRef.current.length;
          onCommandRef.current?.(command);
        }
        currentLineRef.current = "";
        terminal.write(PROMPT);
        return;
      }

      if (code === 127) {
        if (currentLineRef.current.length > 0) {
          currentLineRef.current = currentLineRef.current.slice(0, -1);
          terminal.write("\b \b");
        }
        return;
      }

      if (code === 3 || (code === 12 && data.toLowerCase() === "\f")) {
        terminal.clear();
        terminal.write(PROMPT);
        currentLineRef.current = "";
        return;
      }

      if (code === 38 || code === 40) {
        const history = historyRef.current;
        if (history.length === 0) return;
        if (code === 38 && historyIndexRef.current > 0) {
          historyIndexRef.current -= 1;
        } else if (code === 40 && historyIndexRef.current < history.length) {
          historyIndexRef.current += 1;
        } else {
          return;
        }
        const next =
          historyIndexRef.current < history.length
            ? (history[historyIndexRef.current] ?? "")
            : "";
        currentLineRef.current = next;
        terminal.write("\r\x1b[2K");
        terminal.write(PROMPT);
        terminal.write(next);
        return;
      }

      if (code >= 32 && code < 127) {
        currentLineRef.current += data;
        terminal.write(data);
      }
    });

    const resizeObserver = new ResizeObserver(() => {
      fitAddon.fit();
      onResizeRef.current?.(terminal.cols, terminal.rows);
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      setTerminalHandle(null);
      terminalRef.current = null;
      fitAddonRef.current = null;
      terminal.dispose();
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="h-full w-full"
      style={{ padding: "8px" }}
    />
  );
}
