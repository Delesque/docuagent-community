import { ensureLocalSession, requestJson, requestNdjson } from "../transport";
import type { ModelCallOptions, ProviderConfig } from "./types";

export { ensureLocalSession } from "../transport";

export async function post<T>(url: string, body: unknown): Promise<T> {
  return requestJson<T>(url, { method: "POST", body: withSavedProvider(body) });
}

export async function get<T>(url: string): Promise<T> {
  return requestJson<T>(url);
}

function abortError(): Error {
  return new DOMException("请求已中止", "AbortError");
}

async function postOnce<T>(
  url: string,
  body: unknown,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<T> {
  const controller = new AbortController();
  await ensureLocalSession();
  const timer = window.setTimeout(
    () => controller.abort(new DOMException("请求超时", "TimeoutError")),
    timeoutMs,
  );
  const onExternal = (): void => controller.abort(signal?.reason);

  if (signal) {
    if (signal.aborted) {
      window.clearTimeout(timer);
      throw abortError();
    }
    signal.addEventListener("abort", onExternal, { once: true });
  }

  try {
    return await requestJson<T>(url, {
      method: "POST",
      body: withSavedProvider(body),
      signal: controller.signal,
    });
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener("abort", onExternal);
  }
}

function sleepWithSignal(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const cleanup = (): void => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    };
    const timer = window.setTimeout(() => {
      cleanup();
      resolve();
    }, ms);
    const onAbort = (): void => {
      cleanup();
      reject(abortError());
    };

    if (!signal) return;
    if (signal.aborted) {
      cleanup();
      reject(abortError());
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function postWithRetry<T>(
  url: string,
  body: unknown,
  options: ModelCallOptions = {},
): Promise<T> {
  const retries = options.retries ?? 10;
  const timeoutMs = options.timeoutMs ?? 180_000;
  let lastError: unknown;

  for (let attempt = 1; attempt <= retries; attempt += 1) {
    if (options.signal?.aborted) throw abortError();
    try {
      return await postOnce(url, body, timeoutMs, options.signal);
    } catch (error) {
      lastError = error;
      if (options.signal?.aborted) throw error;
      const status = (error as { status?: number }).status;
      if (status !== undefined && status < 500) throw error;
      if (attempt < retries) {
        options.onRetry?.(attempt, retries);
        await sleepWithSignal(Math.min(1000 * 2 ** (attempt - 1), 8000), options.signal);
      }
    }
  }
  throw lastError;
}

export function isLocalBaseUrl(baseUrl: string): boolean {
  try {
    const host = new URL(baseUrl).hostname.toLowerCase();
    return (
      host === "localhost" ||
      host === "127.0.0.1" ||
      host === "0.0.0.0" ||
      host === "::1" ||
      host.startsWith("127.")
    );
  } catch {
    return false;
  }
}

function providerPayload(
  provider: ProviderConfig,
): ProviderConfig & { use_saved_key?: boolean } {
  return {
    ...provider,
    api_key: provider.api_key || "",
    ...(provider.has_api_key &&
    !provider.api_key &&
    !isLocalBaseUrl(provider.base_url)
      ? { use_saved_key: true }
      : {}),
  };
}

export function withSavedProvider(body: unknown): unknown {
  if (body && typeof body === "object" && !Array.isArray(body)) {
    const record = body as Record<string, unknown>;
    if (record.provider && typeof record.provider === "object") {
      return {
        ...record,
        provider: providerPayload(record.provider as ProviderConfig),
      };
    }
  }
  return body;
}

export async function readNdjsonEvents<T>(
  url: string,
  body: unknown,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  await requestNdjson<T>(url, withSavedProvider(body), onEvent, signal);
}

export { requestNdjson };
