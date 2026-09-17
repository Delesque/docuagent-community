export interface ApiEnvelope<T> {
  ok: boolean;
  data?: T;
  error?: string;
}

export interface JsonRequestOptions extends Omit<RequestInit, "body"> {
  body?: unknown;
}

let sessionReady: Promise<void> | null = null;

function apiError(response: Response, payload: ApiEnvelope<unknown>, fallback: string): Error & { status: number } {
  const error = new Error(payload.error ?? fallback) as Error & { status: number };
  error.status = response.status;
  return error;
}

export async function ensureLocalSession(): Promise<void> {
  if (!sessionReady) {
    sessionReady = fetch("/api/session", {
      cache: "no-store",
      credentials: "same-origin",
    }).then((response) => {
      if (!response.ok) throw new Error(`会话初始化失败：${response.status}`);
    });
    sessionReady.catch(() => {
      sessionReady = null;
    });
  }
  return sessionReady;
}

export async function requestJson<T>(
  url: string,
  options: JsonRequestOptions = {},
): Promise<T> {
  await ensureLocalSession();
  const { body, headers, ...init } = options;
  const response = await fetch(url, {
    ...init,
    cache: init.cache ?? "no-store",
    credentials: "same-origin",
    headers: {
      ...(body !== undefined ? { "content-type": "application/json" } : {}),
      ...headers,
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const payload = (await response.json().catch(() => ({ ok: false }))) as ApiEnvelope<T>;
  if (!response.ok || !payload.ok || payload.data === undefined) {
    throw apiError(response, payload, `请求失败：${response.status}`);
  }
  return payload.data;
}

export async function requestNdjson<T>(
  url: string,
  body: unknown,
  onEvent: (event: T) => void,
  signal?: AbortSignal,
): Promise<void> {
  await ensureLocalSession();
  const response = await fetch(url, {
    method: "POST",
    cache: "no-store",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  if (!response.ok) {
    const payload = (await response.json().catch(() => ({ ok: false }))) as ApiEnvelope<never>;
    throw apiError(response, payload, `流式请求失败：${response.status}`);
  }

  const reader = response.body?.getReader();
  if (!reader) throw new Error("浏览器不支持流式响应。");
  const decoder = new TextDecoder();
  let buffer = "";

  const emit = (line: string): void => {
    if (!line.trim()) return;
    const event = JSON.parse(line) as Record<string, unknown>;
    if (event.type === "error" && typeof event.error === "string") {
      throw new Error(event.error);
    }
    onEvent(event as T);
  };

  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let newline = buffer.indexOf("\n");
    while (newline !== -1) {
      emit(buffer.slice(0, newline));
      buffer = buffer.slice(newline + 1);
      newline = buffer.indexOf("\n");
    }
  }
  buffer += decoder.decode();
  emit(buffer);
}
