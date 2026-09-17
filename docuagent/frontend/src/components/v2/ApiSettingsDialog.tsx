/** API configuration. Deliberately a modal rather than a settings page: the shell has
 *  no chrome to hang a settings route off, and configuration is a rare, focused act.
 *
 *  Both tests hit the local backend, never the vendor directly — a browser fetch to
 *  api.openai.com is CORS-blocked and would put the key on a cross-origin request.
 */

import { useCallback, useEffect, useState } from "react";
import type { InterviewMode, ProviderConfig } from "../../api";
import {
  isLocalBaseUrl,
  listModels,
  testProvider,
  testProviderReachable,
} from "../../api";
import {
  CONVERSATION_MAX_SCALE,
  CONVERSATION_MIN_SCALE,
} from "../../conversation/viewMode";
import { UsageDataSettings } from "./UsageDataSettings";

interface ApiSettingsDialogProps {
  isOpen: boolean;
  onClose: () => void;
  config: ProviderConfig;
  onSave: (config: ProviderConfig & { clear_api_key?: boolean }) => void;
  /** Reading size, applied live so the user can judge it against real text.
   *  It lives here because Ctrl+wheel now drives the graph camera instead. */
  readingScale: number;
  onReadingScaleChange: (scale: number) => void;
}

const PRESET_PROVIDERS: Array<{ name: string; base_url: string; description: string }> = [
  { name: "OpenAI", base_url: "https://api.openai.com/v1", description: "GPT 系列" },
  { name: "DeepSeek", base_url: "https://api.deepseek.com/v1", description: "deepseek-chat" },
  { name: "Moonshot", base_url: "https://api.moonshot.cn/v1", description: "Kimi 系列" },
  { name: "智谱 GLM", base_url: "https://open.bigmodel.cn/api/paas/v4", description: "GLM 系列" },
  { name: "阿里通义", base_url: "https://dashscope.aliyuncs.com/compatible-mode/v1", description: "Qwen 系列" },
  { name: "Ollama 本地", base_url: "http://localhost:11434/v1", description: "本地推理" },
];

const FORMAT_OPTIONS: Array<{
  id: "openai" | "anthropic" | "gemini";
  label: string;
  hint: string;
}> = [
  { id: "openai", label: "OpenAI 兼容", hint: "GPT / DeepSeek / 通义 / 智谱 / Ollama / 各类网关，走 chat/completions。" },
  { id: "anthropic", label: "Anthropic (Claude)", hint: "走 Messages API，用 x-api-key 鉴权，密钥不拼在 URL。" },
  { id: "gemini", label: "Google Gemini", hint: "走 Generative Language API，地址填到 /v1beta，密钥会拼在 URL 上。" },
];

const DEFAULT_BASE_URL: Record<"openai" | "anthropic" | "gemini", string> = {
  openai: "",
  anthropic: "https://api.anthropic.com/v1",
  gemini: "https://generativelanguage.googleapis.com/v1beta",
};

type Probe = { state: "idle" | "running" | "ok" | "fail"; message: string };

const IDLE: Probe = { state: "idle", message: "" };

const ROLE_LABELS = [
  { id: "main", label: "主 Agent" },
  { id: "subagent", label: "子 Agent" },
  { id: "vision", label: "视觉" },
];

export function ApiSettingsDialog({
  isOpen,
  onClose,
  config,
  onSave,
  readingScale,
  onReadingScaleChange,
}: ApiSettingsDialogProps) {
  const [enabled, setEnabled] = useState(config.enabled);
  const [baseUrl, setBaseUrl] = useState(config.base_url);
  const [format, setFormat] = useState<"openai" | "anthropic" | "gemini">(
    config.format ?? "openai",
  );
  const [model, setModel] = useState(config.model);
  const [apiKey, setApiKey] = useState(config.api_key);
  const [hasSavedKey, setHasSavedKey] = useState(Boolean(config.has_api_key));
  const [clearSavedKey, setClearSavedKey] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [roleModels, setRoleModels] = useState<Record<string, string>>({});
  const [interviewMode, setInterviewMode] = useState<InterviewMode>(
    config.interview_mode ?? "guided",
  );

  // Three independent probes. Sharing one status string made the model result
  // overwrite the list result and vice versa.
  const [listProbe, setListProbe] = useState<Probe>(IDLE);
  const [providerProbe, setProviderProbe] = useState<Probe>(IDLE);
  const [modelProbe, setModelProbe] = useState<Probe>(IDLE);

  // Snapshot the incoming config only when the dialog opens. Deliberately
  // excludes `config` from deps: if the parent updates config while the dialog
  // is already open (e.g. from an async boot-time load), we must not reset the
  // form mid-operation — the user's in-progress edits take priority.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!isOpen) return;
    setEnabled(config.enabled);
    setBaseUrl(config.base_url);
    setFormat(config.format ?? "openai");
    setModel(config.model);
    setApiKey(config.has_api_key ? "" : config.api_key);
    setHasSavedKey(Boolean(config.has_api_key));
    setClearSavedKey(false);
    setInterviewMode(config.interview_mode ?? "guided");
    setModels([]);
    setRoleModels(
      Object.fromEntries(
        Object.entries(config.roles ?? {}).map(([role, roleConfig]) => [
          role,
          roleConfig.model ?? "",
        ]),
      ),
    );
    setListProbe(IDLE);
    setProviderProbe(IDLE);
    setModelProbe(IDLE);
  }, [isOpen]); // intentional: snapshot once at open time

  useEffect(() => {
    if (!isOpen) return;
    const onKey = (event: KeyboardEvent): void => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [isOpen, onClose]);

  const draft = useCallback(
    (): ProviderConfig & { use_saved_key?: boolean } => ({
      enabled: true,
      base_url: baseUrl,
      model,
      api_key: hasSavedKey && !apiKey.trim() ? "" : apiKey.trim(),
      format,
      has_api_key: hasSavedKey || Boolean(apiKey.trim()),
      ...(hasSavedKey && !apiKey.trim() && !isLocalBaseUrl(baseUrl)
        ? { use_saved_key: true }
        : {}),
    }),
    [baseUrl, hasSavedKey, model, apiKey, format],
  );

  const handleGetModels = useCallback(async () => {
    setListProbe({ state: "running", message: "读取模型列表…" });
    try {
      const result = await listModels(draft());
      setModels(result.models);
      if (result.note) {
        setListProbe({ state: "ok", message: result.note });
        return;
      }
      if (result.models.length === 0) {
        setListProbe({ state: "fail", message: "服务商没有返回任何模型。" });
        return;
      }
      setListProbe({ state: "ok", message: `${result.models.length} 个模型可选` });
      if (!model && result.models[0]) setModel(result.models[0]);
    } catch (cause) {
      setModels([]);
      setListProbe({ state: "fail", message: (cause as Error).message });
    }
  }, [draft, model]);

  const handleTestProvider = useCallback(async () => {
    setProviderProbe({ state: "running", message: "连接服务商…" });
    try {
      const result = await testProviderReachable(draft());
      if (result.note) {
        setProviderProbe({ state: "fail", message: result.note });
        return;
      }
      setProviderProbe({
        state: "ok",
        message: `服务商可达，${result.model_count} 个模型`,
      });
    } catch (cause) {
      setProviderProbe({ state: "fail", message: (cause as Error).message });
    }
  }, [draft]);

  const handleTestModel = useCallback(async () => {
    if (!model.trim()) {
      setModelProbe({ state: "fail", message: "请先选择模型。" });
      return;
    }
    setModelProbe({ state: "running", message: "请求模型返回结构化 JSON…" });
    try {
      const result = await testProvider(draft());
      setModelProbe({ state: "ok", message: `${result.model} 可用（${result.capability}）` });
    } catch (cause) {
      setModelProbe({ state: "fail", message: (cause as Error).message });
    }
  }, [draft, model]);

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/70 px-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <form
        className="max-h-[88vh] w-full max-w-xl overflow-y-auto rounded-2xl border border-ink-ghost bg-paper-raise p-7 shadow-2xl"
        onClick={(event) => event.stopPropagation()}
        onSubmit={(event) => {
          event.preventDefault();
          onSave({
            enabled,
            base_url: baseUrl.trim(),
            model: model.trim(),
            api_key: apiKey.trim(),
            format,
            has_api_key: hasSavedKey || Boolean(apiKey.trim()),
            interview_mode: interviewMode,
            roles: Object.fromEntries(
              Object.entries(roleModels)
                .filter(([, model]) => model.trim())
                .map(([role, model]) => [role, { model: model.trim() }]),
            ),
            ...(clearSavedKey ? { clear_api_key: true } : {}),
          });
          onClose();
        }}
      >
        <div className="mb-6 flex items-baseline justify-between">
          <h2 className="font-display text-[19px] text-chalk">设置</h2>
          <span className="font-body text-[10.5px] text-chalk-faint">密钥只留在内存</span>
        </div>

        {/* Reading size, applied on change rather than on submit: this is the one
            setting whose effect is visible behind the modal, so deferring it to save
            would mean choosing a size blind. */}
        <div className="mb-6 border-b border-ink-ghost pb-5">
          <div className="mb-2 flex items-baseline justify-between">
            <label htmlFor="reading-scale" className="font-body text-[11px] text-chalk-dim">
              对话字号
            </label>
            <span className="font-mono text-[10px] text-chalk-faint">
              {Math.round(readingScale * 100)}%
            </span>
          </div>
          <input
            id="reading-scale"
            type="range"
            min={CONVERSATION_MIN_SCALE}
            max={CONVERSATION_MAX_SCALE}
            step={0.05}
            value={readingScale}
            onChange={(event) => onReadingScaleChange(Number(event.target.value))}
            className="w-full accent-vermilion"
            aria-label="对话字号"
            aria-valuetext={`${Math.round(readingScale * 100)}%`}
          />
          <p className="mt-1.5 font-body text-[10.5px] text-chalk-faint">
            Ctrl+滚轮用于架构图缩放，字号改在这里。Ctrl+O 打开对话总览。
          </p>
        </div>

        {/* 访谈模式：只在设置中调整。首次选择由对话区顶部的一次性选择器完成。 */}
        <div className="mb-6 border-b border-ink-ghost pb-5">
          <div className="mb-2 font-body text-[11px] text-chalk-dim">
            访谈模式
          </div>
          <div className="grid grid-cols-3 gap-2">
            {([["beginner", "陪伴学习", "白话 + 例子"], ["guided", "引导实践", "取舍 + 风险"], ["professional", "专业定制", "契约 + 波次"]] as const).map(([mode, label, detail]) => (
              <button
                key={mode}
                type="button"
                aria-pressed={interviewMode === mode}
                onClick={() => setInterviewMode(mode)}
                className={`min-h-14 rounded-lg border px-2 py-2 text-left transition-all duration-200 focus:outline-none focus:ring-2 focus:ring-vermilion/70 ${
                  interviewMode === mode
                    ? "border-vermilion bg-vermilion/10 text-chalk"
                    : "border-ink-ghost text-chalk-dim hover:border-ink-dim hover:text-chalk"
                }`}
              >
                <span className="block font-body text-[11px] font-medium">{label}</span>
                <span className="mt-1 block font-mono text-[9px] leading-tight opacity-70">{detail}</span>
              </button>
            ))}
          </div>
          <p className="mt-1.5 font-body text-[10.5px] text-chalk-faint">
            保存后永久记录在 ~/.docuagent/config.json，并在之后的访谈中默认使用。
          </p>
        </div>

        <UsageDataSettings isOpen={isOpen} />

        <label className="mb-5 flex items-center gap-3">
          <input
            type="checkbox"
            checked={enabled}
            onChange={(event) => setEnabled(event.target.checked)}
            className="h-4 w-4 accent-vermilion"
          />
          <span className="font-body text-[12.5px] text-chalk">启用模型调用</span>
          <span className="font-body text-[10.5px] text-chalk-faint">关闭后无法设计架构</span>
        </label>

        <div className="mb-5">
          <div className="mb-2 font-body text-[11px] text-chalk-dim">服务商</div>
          <div className="grid grid-cols-3 gap-2">
            {PRESET_PROVIDERS.map((preset) => (
              <button
                key={preset.name}
                type="button"
                onClick={() => setBaseUrl(preset.base_url)}
                className={`rounded-lg border px-3 py-2 text-left transition-colors duration-150 ${
                  baseUrl === preset.base_url
                    ? "border-ink bg-ink/10 text-chalk"
                    : "border-ink-ghost text-chalk-dim hover:border-ink-dim hover:text-chalk"
                }`}
              >
                <div className="font-body text-[11.5px]">{preset.name}</div>
                <div className="mt-0.5 font-body text-[9.5px] text-chalk-faint">
                  {preset.description}
                </div>
              </button>
            ))}
            <button
              key="custom"
              type="button"
              onClick={() => setBaseUrl("")}
              className={`rounded-lg border px-3 py-2 text-left transition-colors duration-150 ${
                !PRESET_PROVIDERS.some((p) => p.base_url === baseUrl)
                  ? "border-ink bg-ink/10 text-chalk"
                  : "border-ink-ghost text-chalk-dim hover:border-ink-dim hover:text-chalk"
              }`}
            >
              <div className="font-body text-[11.5px]">自定义</div>
              <div className="mt-0.5 font-body text-[9.5px] text-chalk-faint">
                任意填写地址
              </div>
            </button>
          </div>
        </div>

        <div className="mb-5">
          <div className="mb-2 font-body text-[11px] text-chalk-dim">协议格式</div>
          <select
            value={format}
            onChange={(event) => {
              const next = event.target.value as "openai" | "anthropic" | "gemini";
              setFormat(next);
              if (next !== "openai") setBaseUrl(DEFAULT_BASE_URL[next]);
            }}
            className="w-full rounded-lg border border-ink-ghost bg-paper px-3 py-2 font-mono text-[11.5px] text-chalk focus:border-ink focus:outline-none"
          >
            {FORMAT_OPTIONS.map((opt) => (
              <option key={opt.id} value={opt.id}>
                {opt.label}
              </option>
            ))}
          </select>
          <p className="mt-1.5 font-body text-[10.5px] text-chalk-faint">
            {FORMAT_OPTIONS.find((o) => o.id === format)?.hint}
          </p>
        </div>

        <label className="mb-4 block">
          <span className="mb-1.5 block font-body text-[11px] text-chalk-dim">
            API 地址
          </span>
          <input
            type="text"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder={
              format === "anthropic"
                ? "https://api.anthropic.com/v1"
                : format === "gemini"
                  ? "https://generativelanguage.googleapis.com/v1beta"
                  : "https://api.example.com/v1"
            }
            spellCheck={false}
            className="w-full rounded-lg border border-ink-ghost bg-paper px-3 py-2 font-mono text-[11.5px] text-chalk placeholder-chalk-faint focus:border-ink focus:outline-none"
          />
        </label>

        <label className="mb-5 block">
          <span className="mb-1.5 block font-body text-[11px] text-chalk-dim">API Key</span>
          {/* type="text" + visual masking avoids password-manager DOM injection
              (LastPass/Bitwarden/Chrome inject nodes into type="password" inputs,
              causing React reconciler crashes). The real key value never leaves
              the loopback origin anyway. */}
          <input
            type={showKey ? "text" : "password"}
            value={apiKey}
            onChange={(event) => {
              setApiKey(event.target.value);
              if (event.target.value.trim()) setClearSavedKey(false);
            }}
            placeholder={hasSavedKey && !apiKey ? "已保存密钥，留空保持不变" : "sk-…"}
            autoComplete="new-password"
            data-lpignore="true"
            data-1p-ignore
            spellCheck={false}
            className="w-full rounded-lg border border-ink-ghost bg-paper px-3 py-2 font-mono text-[11.5px] text-chalk placeholder-chalk-faint focus:border-ink focus:outline-none"
          />
          <button
            type="button"
            onClick={() => setShowKey((v) => !v)}
            className="mt-1 font-body text-[10px] text-chalk-faint hover:text-chalk-dim"
          >
            {showKey ? "隐藏" : "显示"}
          </button>
          {hasSavedKey && !apiKey ? (
            <button
              type="button"
              onClick={() => {
                setClearSavedKey(true);
                setHasSavedKey(false);
                setApiKey("");
              }}
              className="mt-1 ml-3 font-body text-[10px] text-vermilion/80 hover:text-vermilion"
            >
              清除已保存密钥
            </button>
          ) : null}
        </label>

        <div className="mb-2 flex items-center gap-3">
          <button
            type="button"
            onClick={handleGetModels}
            disabled={listProbe.state === "running"}
            className="rounded-lg border border-ink-ghost px-3 py-1.5 font-body text-[11.5px] text-chalk-dim transition-colors hover:border-ink-dim hover:text-chalk disabled:opacity-40"
          >
            {listProbe.state === "running" ? "读取中…" : "获取模型列表"}
          </button>
          <ProbeLine probe={listProbe} />
        </div>

        <label className="mb-6 block">
          <span className="mb-1.5 block font-body text-[11px] text-chalk-dim">模型</span>
          {models.length > 0 ? (
            <select
              key="model-select"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              className="w-full rounded-lg border border-ink-ghost bg-paper px-3 py-2 font-mono text-[11.5px] text-chalk focus:border-ink focus:outline-none"
            >
              <option value="">选择模型…</option>
              {models.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          ) : (
            <input
              key="model-input"
              type="text"
              value={model}
              onChange={(event) => setModel(event.target.value)}
              placeholder="先获取列表，或直接填写模型名"
              spellCheck={false}
              className="w-full rounded-lg border border-ink-ghost bg-paper px-3 py-2 font-mono text-[11.5px] text-chalk placeholder-chalk-faint focus:border-ink focus:outline-none"
            />
          )}
        </label>

        <div className="mb-6 space-y-3 rounded-lg border border-ink-ghost/60 p-3">
          <p className="font-body text-[11px] text-chalk-dim">
            角色模型（留空使用上方默认模型）
          </p>
          {ROLE_LABELS.map((role) => (
            <label key={role.id} className="flex items-center gap-3">
              <span className="w-20 shrink-0 font-body text-[11px] text-chalk-dim">
                {role.label}
              </span>
              <input
                type="text"
                value={roleModels[role.id] ?? ""}
                onChange={(event) =>
                  setRoleModels((prev) => ({
                    ...prev,
                    [role.id]: event.target.value,
                  }))
                }
                placeholder="模型名"
                spellCheck={false}
                className="min-w-0 flex-1 rounded-lg border border-ink-ghost bg-paper px-3 py-2 font-mono text-[11.5px] text-chalk placeholder-chalk-faint focus:border-ink focus:outline-none"
              />
            </label>
          ))}
        </div>

        <div className="mb-6 space-y-2.5 rounded-lg border border-ink-ghost/60 p-3">
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleTestProvider}
              disabled={providerProbe.state === "running"}
              className="w-44 shrink-0 rounded-lg border border-ink-ghost px-3 py-1.5 font-body text-[11.5px] text-chalk-dim transition-colors hover:border-ink-dim hover:text-chalk disabled:opacity-40"
            >
              测试服务商连通性
            </button>
            <ProbeLine probe={providerProbe} />
          </div>
          <div className="flex items-center gap-3">
            <button
              type="button"
              onClick={handleTestModel}
              disabled={modelProbe.state === "running"}
              className="w-44 shrink-0 rounded-lg border border-ink-ghost px-3 py-1.5 font-body text-[11.5px] text-chalk-dim transition-colors hover:border-ink-dim hover:text-chalk disabled:opacity-40"
            >
              测试模型连通性
            </button>
            <ProbeLine probe={modelProbe} />
          </div>
        </div>

        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-ink-ghost px-4 py-2 font-body text-[12px] text-chalk-dim transition-colors hover:text-chalk"
          >
            取消
          </button>
          <button
            type="submit"
            className="rounded-lg border border-vermilion/60 bg-vermilion/15 px-5 py-2 font-body text-[12px] text-chalk transition-colors hover:bg-vermilion/25"
          >
            保存
          </button>
        </div>
      </form>
    </div>
  );
}

function ProbeLine({ probe }: { probe: Probe }) {
  if (probe.state === "idle") return null;
  const tone =
    probe.state === "ok"
      ? "text-ink"
      : probe.state === "fail"
        ? "text-vermilion"
        : "text-chalk-dim";
  const mark = probe.state === "ok" ? "✓ " : probe.state === "fail" ? "✗ " : "";
  return (
    <span className={`font-body text-[10.5px] leading-snug ${tone}`} role="status">
      {mark}
      {probe.message}
    </span>
  );
}
