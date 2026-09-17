import { useState } from "react";
import type { ProviderConfig } from "../api";
import { ContextCachePanel } from "./ContextCachePanel";
import { McpPanel } from "./McpPanel";
import { MemoryPanel } from "./MemoryPanel";
import { PluginsPanel } from "./PluginsPanel";
import { SkillsPanel } from "./SkillsPanel";

type SystemTab = "extensions" | "memory" | "cache";
type ExtensionTab = "mcp" | "skills" | "plugins";

const SYSTEM_TABS: Array<{ id: SystemTab; label: string; hint: string }> = [
  { id: "extensions", label: "扩展", hint: "MCP / 技能 / 插件" },
  { id: "memory", label: "记忆", hint: "用户画像、项目规范、recipe 候选" },
  { id: "cache", label: "用量", hint: "项目、模块和功能缓存命中率与 token" },
];

const EXTENSION_TABS: Array<{ id: ExtensionTab; label: string; hint: string }> = [
  { id: "mcp", label: "MCP", hint: "外部工具服务器" },
  { id: "skills", label: "技能", hint: "本地导入与市场安装" },
  { id: "plugins", label: "插件", hint: "工具、提示词与市场" },
];

export function SystemPanel({
  path,
  provider,
  onClose,
}: {
  path: string;
  provider: ProviderConfig;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<SystemTab>("extensions");
  const [extensionTab, setExtensionTab] = useState<ExtensionTab>("mcp");

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm">
      <section className="flex h-full max-h-[860px] w-full max-w-5xl flex-col overflow-hidden rounded-none border border-ink/60 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink/40 px-5 py-3">
          <div className="flex items-center gap-3">
            <span className="h-3 w-3 rounded-full border border-emerald bg-emerald/30" />
            <div>
              <h2 className="font-display text-[16px] text-chalk">系统</h2>
              <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
                扩展 · 记忆 · 用量
              </p>
            </div>
          </div>
          <nav className="ml-6 flex items-center gap-1">
            {SYSTEM_TABS.map((item) => (
              <button
                key={item.id}
                type="button"
                title={item.hint}
                onClick={() => setTab(item.id)}
                className={`rounded-none border px-3 py-1.5 font-mono text-[11px] transition-colors ${
                  tab === item.id
                    ? "border-ink bg-ink/20 text-ink"
                    : "border-ink-dim/45 text-chalk-dim hover:border-ink hover:text-chalk"
                }`}
              >
                {item.label}
              </button>
            ))}
          </nav>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded-none border border-ink-dim/45 px-3 py-1.5 font-mono text-[11px] text-chalk-faint transition-colors hover:border-ink hover:text-chalk"
          >
            关闭
          </button>
        </header>

        {tab === "extensions" ? (
          <>
            <div className="flex items-center gap-1 border-b border-ink/30 px-5 py-2">
              {EXTENSION_TABS.map((item) => (
                <button
                  key={item.id}
                  type="button"
                  title={item.hint}
                  onClick={() => setExtensionTab(item.id)}
                  className={`rounded-none border px-3 py-1 font-mono text-[10px] transition-colors ${
                    extensionTab === item.id
                      ? "border-ink bg-ink/20 text-ink"
                      : "border-ink-dim/45 text-chalk-dim hover:border-ink hover:text-chalk"
                  }`}
                >
                  {item.label}
                </button>
              ))}
            </div>
            <div className="min-h-0 flex-1 overflow-hidden">
              {extensionTab === "mcp" ? <McpPanel path={path} onClose={onClose} embedded /> : null}
              {extensionTab === "skills" ? <SkillsPanel path={path} onClose={onClose} embedded /> : null}
              {extensionTab === "plugins" ? <PluginsPanel path={path} onClose={onClose} embedded /> : null}
            </div>
          </>
        ) : null}

        {tab === "memory" ? (
          <div className="min-h-0 flex-1 overflow-hidden">
            <MemoryPanel path={path} provider={provider} onClose={onClose} embedded />
          </div>
        ) : null}

        {tab === "cache" ? (
          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
            <ContextCachePanel path={path} onClose={onClose} />
          </div>
        ) : null}
      </section>
    </div>
  );
}
