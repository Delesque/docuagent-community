import { useCallback, useEffect, useState } from "react";
import {
  approveMcpServer,
  fetchMcpServers,
  saveMcpServers,
  testMcpServer,
  type McpServerConfig,
} from "../api";

interface McpPanelProps {
  path: string;
  onClose: () => void;
}

interface Row extends McpServerConfig {
  argsText: string;
  testResult: string;
}

function toRow(server: McpServerConfig): Row {
  return {
    ...server,
    argsText: server.args.join(" "),
    testResult: "",
  };
}

export function McpPanel({ path, onClose, embedded = false }: McpPanelProps & { embedded?: boolean }) {
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    try {
      const result = await fetchMcpServers(path);
      setRows(result.servers.map(toRow));
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [path]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const updateRow = (index: number, patch: Partial<Row>) => {
    setRows((prev) => prev.map((row, i) => (i === index ? { ...row, ...patch } : row)));
  };

  const handleSave = useCallback(async () => {
    setBusy(true);
    setError("");
    try {
      const servers = rows.map((row) => ({
        name: row.name.trim(),
        command: row.command.trim(),
        args: row.argsText.trim() ? row.argsText.trim().split(/\s+/) : [],
      }));
      const result = await saveMcpServers(path, servers);
      setRows(result.servers.map(toRow));
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [path, rows]);

  const handleApprove = useCallback(
    async (index: number) => {
      const row = rows[index];
      if (!row) return;
      setBusy(true);
      setError("");
      try {
        const result = await approveMcpServer(path, row.name.trim());
        setRows(result.servers.map(toRow));
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [path, rows],
  );

  const handleTest = useCallback(
    async (index: number) => {
      const row = rows[index];
      if (!row) return;
      setBusy(true);
      setError("");
      try {
        const args = row.argsText.trim() ? row.argsText.trim().split(/\s+/) : [];
        const result = await testMcpServer(row.command.trim(), args);
        updateRow(index, { testResult: `可用工具：${result.tools.length}` });
      } catch (cause) {
        updateRow(index, { testResult: `测试失败：${(cause as Error).message}` });
      } finally {
        setBusy(false);
      }
    },
    [rows],
  );

  return (
    <div
      className={embedded
        ? "h-full overflow-hidden"
        : "absolute inset-0 z-[60] flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm"}
      role="dialog"
      aria-modal="true"
      aria-label="MCP 面板"
    >
      <section className={`flex h-full w-full flex-col overflow-hidden border border-ink/60 bg-paper-raise shadow-2xl ${embedded ? "" : "max-h-full max-w-4xl rounded-lg"}`}>
        <header className="flex items-center gap-3 border-b border-ink/40 px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">MCP</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              配置外部工具服务器
            </p>
          </div>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => void refresh()}
              disabled={busy}
              className="inline-flex h-8 items-center rounded-md border border-ink/60 px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-ink-ghost disabled:opacity-40"
            >
              刷新
            </button>
            <button
              type="button"
              onClick={onClose}
              className="inline-flex h-8 items-center rounded-md px-3 font-mono text-[11px] text-chalk-faint transition-colors hover:text-chalk"
            >
              关闭
            </button>
          </div>
        </header>

        {error ? (
          <p className="border-b border-vermilion/20 bg-vermilion/5 px-5 py-2 font-mono text-[10.5px] text-vermilion">
            {error}
          </p>
        ) : null}

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
          {rows.map((row, index) => (
            <div
              key={index}
              className="grid grid-cols-1 gap-2 rounded-lg border border-ink/40 bg-paper/70 p-3 md:grid-cols-[1fr_1.5fr_1fr_auto]"
            >
              <input
                value={row.name}
                onChange={(event) => updateRow(index, { name: event.target.value })}
                placeholder="名称"
                className="rounded-md border border-ink/50 bg-paper px-2 py-1.5 font-mono text-[11px] text-chalk outline-none"
              />
              <input
                value={row.command}
                onChange={(event) => updateRow(index, { command: event.target.value })}
                placeholder="命令，如 python"
                className="rounded-md border border-ink/50 bg-paper px-2 py-1.5 font-mono text-[11px] text-chalk outline-none"
              />
              <input
                value={row.argsText}
                onChange={(event) => updateRow(index, { argsText: event.target.value })}
                placeholder="参数，空格分隔"
                className="rounded-md border border-ink/50 bg-paper px-2 py-1.5 font-mono text-[11px] text-chalk outline-none"
              />
              <div className="flex items-center gap-2">
                {row.approved ? (
                  <span className="rounded border border-emerald/50 px-1.5 py-0.5 font-mono text-[9px] text-emerald">已确认</span>
                ) : (
                  <button
                    type="button"
                    onClick={() => void handleApprove(index)}
                    disabled={busy || !row.name.trim()}
                    className="inline-flex h-8 items-center rounded-md border border-amber/50 px-2.5 font-mono text-[10px] text-amber transition-colors hover:bg-amber/10 disabled:opacity-40"
                    title={`确认启动命令：${row.command} ${row.argsText}`}
                  >
                    确认启动
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => void handleTest(index)}
                  disabled={busy}
                  className="inline-flex h-8 items-center rounded-md border border-ink/50 px-2.5 font-mono text-[10px] text-chalk transition-colors hover:bg-ink-ghost disabled:opacity-40"
                >
                  测试
                </button>
                <button
                  type="button"
                  onClick={() => setRows((prev) => prev.filter((_, i) => i !== index))}
                  className="inline-flex h-8 items-center rounded-md border border-vermilion/50 px-2.5 font-mono text-[10px] text-vermilion transition-colors hover:bg-vermilion/10"
                >
                  移除
                </button>
              </div>
              {row.testResult ? (
                <p className="font-mono text-[10px] text-chalk-faint md:col-span-4">
                  {row.testResult}
                </p>
              ) : null}
            </div>
          ))}
          <button
            type="button"
            onClick={() =>
              setRows((prev) => [
                ...prev,
                { name: "", command: "", args: [], argsText: "", testResult: "" },
              ])
            }
            className="inline-flex h-8 items-center rounded-md border border-ink/50 px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-ink-ghost"
          >
            添加 Server
          </button>
        </div>

        <footer className="flex items-center gap-2 border-t border-ink/40 px-5 py-3">
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={busy}
            className="inline-flex h-8 items-center rounded-md border border-emerald/50 px-3 font-mono text-[11px] text-emerald transition-colors hover:bg-emerald/10 disabled:opacity-40"
          >
            保存到项目
          </button>
        </footer>
      </section>
    </div>
  );
}
