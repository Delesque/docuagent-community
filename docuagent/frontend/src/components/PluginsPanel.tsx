import { useCallback, useEffect, useState } from "react";
import {
  fetchPluginMarketplace,
  fetchPlugins,
  installMarketPlugin,
  installPlugin,
  togglePlugin,
  uninstallPlugin,
  type PluginInfo,
} from "../api";

interface PluginsPanelProps {
  path: string;
  onClose: () => void;
}

export function PluginsPanel({ path, onClose, embedded = false }: PluginsPanelProps & { embedded?: boolean }) {
  const [plugins, setPlugins] = useState<PluginInfo[]>([]);
  const [source, setSource] = useState("");
  const [marketplace, setMarketplace] = useState("");
  const [entries, setEntries] = useState<PluginInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refresh = useCallback(async () => {
    setError("");
    setNotice("");
    try {
      const result = await fetchPlugins(path);
      setPlugins(result.plugins);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, [path]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleInstall = useCallback(async () => {
    if (!source.trim()) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await installPlugin(path, source.trim());
      setNotice(`插件已安装，默认未启用：${result.plugin.name}`);
      setSource("");
      await refresh();
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [path, refresh, source]);

  const handleBrowse = useCallback(async () => {
    if (!marketplace.trim()) return;
    setBusy(true);
    setError("");
    try {
      const result = await fetchPluginMarketplace(marketplace.trim());
      setEntries(result.entries);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }, [marketplace]);

  const handleInstallMarket = useCallback(
    async (name: string) => {
      if (!marketplace.trim()) return;
      setBusy(true);
      setError("");
      try {
        await installMarketPlugin(path, marketplace.trim(), name);
        await refresh();
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [marketplace, path, refresh],
  );

  const handleToggle = useCallback(
    async (plugin: PluginInfo) => {
      setBusy(true);
      setError("");
      try {
        await togglePlugin(path, plugin.name, !plugin.enabled);
        await refresh();
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [path, refresh],
  );

  const handleUninstall = useCallback(
    async (name: string) => {
      setBusy(true);
      setError("");
      try {
        await uninstallPlugin(path, name);
        await refresh();
      } catch (cause) {
        setError((cause as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [path, refresh],
  );

  return (
    <div
      className={embedded
        ? "h-full overflow-hidden"
        : "absolute inset-0 z-[60] flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm"}
      role="dialog"
      aria-modal="true"
      aria-label="插件面板"
    >
      <section className={`flex h-full w-full flex-col overflow-hidden border border-ink/60 bg-paper-raise shadow-2xl ${embedded ? "" : "max-h-full max-w-4xl rounded-lg"}`}>
        <header className="flex items-center gap-3 border-b border-ink/40 px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">插件</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              工具、提示词与市场
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto inline-flex h-8 items-center rounded-md px-3 font-mono text-[11px] text-chalk-faint transition-colors hover:text-chalk"
          >
            关闭
          </button>
        </header>

        {error ? (
          <p className="border-b border-vermilion/20 bg-vermilion/5 px-5 py-2 font-mono text-[10.5px] text-vermilion">
            {error}
          </p>
        ) : null}
        {notice ? (
          <p className="border-b border-[#FFC857]/30 bg-[#FFC857]/10 px-5 py-2 font-mono text-[10.5px] text-[#FFC857]">
            {notice}
          </p>
        ) : null}

        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          <section className="mb-4 rounded-lg border border-ink/40 bg-paper/70 p-4">
            <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              已安装（{plugins.length}）
            </p>
            <div className="space-y-2">
              {plugins.map((plugin) => (
                <div key={plugin.name} className="rounded-md border border-ink/30 bg-paper-raise/60 p-3">
                  <div className="flex items-center gap-3">
                    <span className="font-mono text-[11px] text-chalk">{plugin.name}</span>
                    <span className="font-mono text-[9px] text-chalk-faint">v{plugin.version}</span>
                    <span
                      className={
                        plugin.enabled
                          ? "font-mono text-[9px] text-emerald"
                          : "font-mono text-[9px] text-chalk-faint"
                      }
                    >
                      {plugin.enabled ? "已启用" : "未启用"}
                    </span>
                    <span className="min-w-0 flex-1 truncate font-body text-[11px] text-chalk-dim">
                      {plugin.description}
                    </span>
                    <button
                      type="button"
                      onClick={() => void handleToggle(plugin)}
                      disabled={busy}
                      className="inline-flex h-8 items-center rounded-md border border-ink/50 px-2.5 font-mono text-[10px] text-chalk transition-colors hover:bg-ink-ghost disabled:opacity-40"
                    >
                      {plugin.enabled ? "停用" : "启用"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void handleUninstall(plugin.name)}
                      disabled={busy}
                      className="inline-flex h-8 items-center rounded-md border border-vermilion/50 px-2.5 font-mono text-[10px] text-vermilion transition-colors hover:bg-vermilion/10 disabled:opacity-40"
                    >
                      卸载
                    </button>
                  </div>
                  {(plugin.tools?.length ?? 0) > 0 ? (
                    <p className="mt-1.5 font-mono text-[9px] text-chalk-faint">
                      工具：{plugin.tools?.map((tool) => tool.name).join("、")}
                    </p>
                  ) : null}
                </div>
              ))}
              {plugins.length === 0 ? (
                <p className="font-mono text-[10.5px] text-chalk-faint">还没有安装插件。</p>
              ) : null}
            </div>
          </section>

          <section className="mb-4 rounded-lg border border-ink/40 bg-paper/70 p-4">
            <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              本地安装
            </p>
            <div className="flex gap-2">
              <input
                value={source}
                onChange={(event) => setSource(event.target.value)}
                placeholder="插件目录或 zip 路径"
                className="min-w-0 flex-1 rounded-md border border-ink/50 bg-paper px-3 py-2 font-mono text-[11px] text-chalk outline-none"
              />
              <button
                type="button"
                onClick={() => void handleInstall()}
                disabled={busy || !source.trim()}
                className="inline-flex h-9 items-center rounded-md border border-ink/60 px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-ink-ghost disabled:opacity-40"
              >
                安装
              </button>
            </div>
          </section>

          <section className="rounded-lg border border-ink/40 bg-paper/70 p-4">
            <p className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              插件市场
            </p>
            <div className="flex gap-2">
              <input
                value={marketplace}
                onChange={(event) => setMarketplace(event.target.value)}
                placeholder="marketplace.json 路径或 URL"
                className="min-w-0 flex-1 rounded-md border border-ink/50 bg-paper px-3 py-2 font-mono text-[11px] text-chalk outline-none"
              />
              <button
                type="button"
                onClick={() => void handleBrowse()}
                disabled={busy || !marketplace.trim()}
                className="inline-flex h-9 items-center rounded-md border border-ink/60 px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-ink-ghost disabled:opacity-40"
              >
                查看市场
              </button>
            </div>
            <div className="mt-3 space-y-2">
              {entries.map((entry) => (
                <div
                  key={entry.name}
                  className="flex items-center gap-3 rounded-md border border-ink/30 bg-paper-raise/60 p-3"
                >
                  <div className="min-w-0 flex-1">
                    <span className="font-mono text-[11px] text-chalk">{entry.name}</span>
                    <span className="ml-2 font-body text-[11px] text-chalk-dim">
                      {entry.description}
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => void handleInstallMarket(entry.name)}
                    disabled={busy}
                    className="inline-flex h-8 items-center rounded-md border border-emerald/50 px-3 font-mono text-[10px] text-emerald transition-colors hover:bg-emerald/10 disabled:opacity-40"
                  >
                    安装
                  </button>
                </div>
              ))}
            </div>
          </section>
        </div>
      </section>
    </div>
  );
}
