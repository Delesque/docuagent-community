import { useCallback, useEffect, useState } from "react";
import {
  clearTelemetry,
  fetchTelemetry,
  setTelemetryConsent,
  uploadTelemetry,
  type TelemetrySummary,
} from "../../api";

function percent(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

function integer(value: number): string {
  return value.toLocaleString();
}

export function UsageDataSettings({ isOpen }: { isOpen: boolean }) {
  const [summary, setSummary] = useState<TelemetrySummary | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");

  const refresh = useCallback(async () => {
    try {
      setSummary(await fetchTelemetry());
      setMessage("");
    } catch (cause) {
      setMessage((cause as Error).message);
    }
  }, []);

  useEffect(() => {
    if (!isOpen) return;
    void refresh();
  }, [isOpen, refresh]);

  const toggleConsent = async (consent: boolean): Promise<void> => {
    setBusy(true);
    try {
      setSummary(await setTelemetryConsent(consent));
      setMessage(consent ? "已允许匿名统计上传。" : "已停止匿名统计上传。");
    } catch (cause) {
      setMessage((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const uploadNow = async (): Promise<void> => {
    setBusy(true);
    try {
      const result = await uploadTelemetry();
      setMessage(
        result.uploaded
          ? "匿名统计已上传，本地待上传记录已清空。"
          : "当前没有可上传的统计。",
      );
      await refresh();
    } catch (cause) {
      setMessage((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const clearLocal = async (): Promise<void> => {
    if (!window.confirm("清除本机保存的匿名使用统计？此操作不会关闭已同意的上传设置。")) {
      return;
    }
    setBusy(true);
    try {
      setSummary(await clearTelemetry());
      setMessage("本地匿名统计已清除。");
    } catch (cause) {
      setMessage((cause as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!isOpen) return null;
  const lifetime = summary?.lifetime;

  return (
    <div className="mb-6 border-b border-ink-ghost pb-5">
      <div className="mb-2 flex items-baseline justify-between">
        <span className="font-body text-[11px] text-chalk-dim">匿名使用数据</span>
        <span className="font-mono text-[9px] text-chalk-faint">
          本地默认开启
        </span>
      </div>

      {lifetime ? (
        <div className="mb-3 grid grid-cols-4 gap-2 font-mono">
          <div className="border border-ink-ghost p-2">
            <p className="text-[8.5px] text-chalk-faint">任务失败率</p>
            <p className="mt-1 text-[14px] text-chalk">{percent(summary.failure_rate)}</p>
          </div>
          <div className="border border-ink-ghost p-2">
            <p className="text-[8.5px] text-chalk-faint">缓存命中率</p>
            <p className="mt-1 text-[14px] text-emerald">{percent(summary.cache_hit_rate)}</p>
          </div>
          <div className="border border-ink-ghost p-2">
            <p className="text-[8.5px] text-chalk-faint">启动次数</p>
            <p className="mt-1 text-[14px] text-chalk">{integer(lifetime.app_starts)}</p>
          </div>
          <div className="border border-ink-ghost p-2">
            <p className="text-[8.5px] text-chalk-faint">模型 tokens</p>
            <p className="mt-1 text-[14px] text-chalk">
              {integer(lifetime.provider_prompt_tokens + lifetime.provider_completion_tokens)}
            </p>
          </div>
        </div>
      ) : null}

      <label className="flex items-start gap-3">
        <input
          type="checkbox"
          checked={summary?.consent ?? false}
          disabled={busy || !summary}
          onChange={(event) => void toggleConsent(event.target.checked)}
          className="mt-0.5 h-4 w-4 shrink-0 accent-vermilion"
        />
        <span>
          <span className="block font-body text-[12px] text-chalk">
            允许匿名统计上传
          </span>
          <span className="mt-1 block font-body text-[10px] leading-relaxed text-chalk-faint">
            同意后最多每 24 小时批量上传一次。只包含启动次数、任务成功/失败/取消/中断、
            耗时聚合、缓存命中和 token 计数，以及平台、CPU 架构和应用版本。
          </span>
        </span>
      </label>

      <p className="mt-2 font-body text-[10px] leading-relaxed text-chalk-faint">
        不上传项目路径、项目名、代码、提示词、模型回复、模型名称、API 地址或密钥。
        本机统计可随时清除，上传失败时待上传数据继续保留在本地。
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={() => void uploadNow()}
          disabled={
            busy ||
            !summary?.consent ||
            !summary?.upload_available ||
            (summary?.pending_events ?? 0) === 0
          }
          className="rounded border border-ink-dim/50 px-3 py-1.5 font-mono text-[10px] text-chalk-dim hover:bg-ink/10 hover:text-chalk disabled:cursor-not-allowed disabled:opacity-40"
        >
          立即上传
        </button>
        <button
          type="button"
          onClick={() => void clearLocal()}
          disabled={busy || !summary}
          className="rounded border border-ink-dim/50 px-3 py-1.5 font-mono text-[10px] text-chalk-faint hover:bg-ink/10 hover:text-chalk disabled:opacity-40"
        >
          清除本地统计
        </button>
        {summary ? (
          <span className="font-mono text-[9px] text-chalk-faint">
            待上传 {summary.pending_events} 项 ·{" "}
            {summary.upload_available ? "上传服务已配置" : "上传服务尚未配置"}
          </span>
        ) : null}
      </div>

      {message ? (
        <p className="mt-2 font-body text-[10px] text-chalk-faint">{message}</p>
      ) : null}
      {summary?.last_upload_error ? (
        <p className="mt-1 font-body text-[10px] text-vermilion">
          最近上传失败：{summary.last_upload_error}
        </p>
      ) : null}
    </div>
  );
}
