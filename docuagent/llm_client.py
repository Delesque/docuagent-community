"""OpenAI-compatible LLM transport, shared by every model-calling module.

Split out of `bootstrap.py`, which was both the architecture-interview state machine
and the HTTP client for every model call. Seven modules (memory, orchestrate, skills,
agent_tools, ui_feedback, onboard, microtask) imported `bootstrap` only to reach
`ProviderConfig` / `call_model_json`, so the skill system depended on the interview
module for no reason. This module owns the transport; `bootstrap` keeps the interview.

Pure move: every function here is byte-identical to its previous definition. The
injection hooks below are still filled in by `main.py`.
"""

from __future__ import annotations

import json
import base64
import re
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from core import WorkspaceError
import token_usage


# Filled in by main.py so a frontend that only knows "a key exists" can ask the
# backend to reuse the saved key without ever receiving it.
SAVED_KEY_PROVIDER: Callable[[], str] | None = None
ROLE_PROVIDER: Callable[[str], dict[str, Any]] | None = None


def _is_local_base_url(base_url: str) -> bool:
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or host.startswith(
        "127."
    )


def urlopen_with_proxy_fallback(
    request: urllib.request.Request,
    timeout: int,
):
    """Open a URL, retrying once without the system proxy on Windows 10013.

    Windows can reject urllib's outbound socket when a system proxy is configured but
    not reachable. The provider may be perfectly reachable directly, so the second
    attempt bypasses proxies entirely instead of failing the connection test.
    """
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except (urllib.error.URLError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        if (
            getattr(reason, "winerror", None) != 10013
            and getattr(reason, "errno", None) != 10013
        ):
            raise
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)


# Transient provider failures worth another attempt. 524 is Cloudflare's
# "origin timed out" — aggregator endpoints in front of it regularly answer a
# long generation with one, then serve the identical request in seconds. 429 is
# rate limiting; 500/502/503/504 are upstream hiccups (one pilot run of a
# resale gateway answered `gpt-5.4-mini` with a permanent-looking 500 "pool has
# no resources", which is NOT retryable in spirit — but it arrives as 500, so a
# bounded retry costs one extra roundtrip and nothing else).
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504, 522, 524})
STATUS_RETRY_DELAYS = (2.0, 5.0)  # before attempts 2 and 3
# Connection failures (SSL handshake timeout, DNS, refused). The failed attempt
# itself often burns tens of seconds, so back off briefly but try more times:
# the resale gateway's connection quality oscillates on the scale of minutes.
CONNECTION_RETRY_DELAYS = (1.0, 2.0, 4.0, 8.0)  # before attempts 2..5


def open_with_retry(request: urllib.request.Request, timeout: int):
    """Open a URL with bounded retries for transient provider failures.

    Only connection-stage errors are retried, so streaming consumers never see
    duplicated events: by the time a response object exists, urllib has already
    raised HTTPError for bad statuses, and mid-stream timeouts happen later,
    inside the caller's iteration.
    """
    status_attempts = 0
    connection_attempts = 0
    while True:
        try:
            return urlopen_with_proxy_fallback(request, timeout)
        except urllib.error.HTTPError as exc:
            # HTTPError is an OSError, so this branch must come first.
            if exc.code not in RETRYABLE_STATUS:
                raise
            if status_attempts >= len(STATUS_RETRY_DELAYS):
                raise
            time.sleep(STATUS_RETRY_DELAYS[status_attempts])
            status_attempts += 1
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Covers ssl.SSLError ("handshake operation timed out") and plain
            # socket timeouts: connection-level failures, transient by nature.
            if connection_attempts >= len(CONNECTION_RETRY_DELAYS):
                raise
            time.sleep(CONNECTION_RETRY_DELAYS[connection_attempts])
            connection_attempts += 1


# Outbound requests must name themselves. Gateways fronted by Cloudflare (the
# common shape of resale/aggregator endpoints) answer urllib's default
# `Python-urllib/3.x` with HTTP 403 "error code: 1010", which reads like an
# invalid API key rather than a rejected client. Any other value passes.
USER_AGENT = "DocuAgent/1.0"


def request_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build request headers that always identify DocuAgent."""
    headers = {"User-Agent": USER_AGENT}
    if extra:
        headers.update(extra)
    return headers


@dataclass(frozen=True)
class ProviderConfig:
    base_url: str
    model: str
    api_key: str
    # Wire protocol the endpoint speaks. "openai" is the OpenAI-compatible
    # chat/completions shape (also covers most gateways, Ollama, DeepSeek,
    # Qwen, …). "anthropic" is the Claude Messages API; "gemini" is the
    # Generative Language generateContent REST shape.
    format: str = "openai"

    @classmethod
    def from_payload(
        cls,
        payload: dict[str, Any],
        role: str = "main",
    ) -> "ProviderConfig | None":
        raw = payload.get("provider")
        if not isinstance(raw, dict) or not raw.get("enabled"):
            return None
        base_url = str(raw.get("base_url") or "").strip().rstrip("/")
        model = str(raw.get("model") or "").strip()
        api_key = str(raw.get("api_key") or "").strip()
        raw_format = str(raw.get("format") or "openai").strip().lower()
        fmt = raw_format if raw_format in {"openai", "anthropic", "gemini"} else "openai"
        if role != "default" and ROLE_PROVIDER:
            role_config = ROLE_PROVIDER(role) or {}
            if str(role_config.get("base_url") or "").strip():
                base_url = str(role_config["base_url"]).strip().rstrip("/")
            if str(role_config.get("model") or "").strip():
                model = str(role_config["model"]).strip()
            if str(role_config.get("api_key") or "").strip():
                api_key = str(role_config["api_key"]).strip()
        use_saved_key = bool(raw.get("use_saved_key"))
        if (
            use_saved_key
            and not api_key
            and not _is_local_base_url(base_url)
            and SAVED_KEY_PROVIDER
        ):
            api_key = SAVED_KEY_PROVIDER().strip()
        if not base_url or not model:
            raise WorkspaceError("启用模型后必须填写 API 地址和模型名称。")
        if (
            not api_key
            and not _is_local_base_url(base_url)
            and not use_saved_key
        ):
            raise WorkspaceError("启用模型后必须填写 API 地址、模型名称和 API Key。")
        return cls(base_url=base_url, model=model, api_key=api_key, format=fmt)

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return f"{self.base_url}/chat/completions"


def strip_json_fence(content: str) -> str:
    value = content.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def model_error_message(exc: urllib.error.HTTPError) -> str:
    detail = ""
    try:
        payload = json.loads(exc.read().decode("utf-8", errors="replace"))
        error = payload.get("error", {})
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif error:
            detail = str(error)
    except (OSError, json.JSONDecodeError):
        detail = ""
    suffix = f"：{detail[:300]}" if detail else ""
    return f"模型服务返回 HTTP {exc.code}{suffix}"


PREFERRED_JSON_KEYS = frozenset({
    "thinking",
    "tool_calls",
    "files",
    "patch",
    "done",
    "needs_handoff",
    "summary",
    "architecture",
    "ready",
    "next_question",
    "tasks",
    "modules",
    "changes",
    "project",
    "answer",
})


class ToolCallingNotSupported(WorkspaceError):
    """The provider rejected a native `tools` request; callers should fall back."""


def openai_tool_schemas(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert DocuAgent's compact tool descriptions to OpenAI tool schemas.

    DocuAgent tools describe arguments as `{name: description}`. Every argument is
    treated as a string in the JSON schema; execute_tool performs the real type and
    security validation later.
    """
    schemas: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name") or "").strip()
        if not name:
            continue
        raw_args = tool.get("args")
        args = raw_args if isinstance(raw_args, dict) else {}
        properties = {
            str(arg_name): {
                "type": "string",
                "description": str(arg_description or ""),
            }
            for arg_name, arg_description in args.items()
            if str(arg_name).strip()
        }
        parameters: dict[str, Any] = {
            "type": "object",
            "properties": properties,
        }
        if properties:
            parameters["required"] = list(properties)
        schemas.append({
            "type": "function",
            "function": {
                "name": name,
                "description": str(tool.get("description") or ""),
                "parameters": parameters,
            },
        })
    return schemas


def parse_model_json_content(content: str) -> dict[str, Any] | None:
    """Return the most likely model JSON object from possibly-prose content.

    The agent loop deliberately allows natural language. A model may write a
    paragraph and then append one JSON block for tool calls or a final result.
    `json.loads` on the whole text would reject that, so fall back to scanning
    every `{` with a real JSON decoder. Candidates carrying known protocol keys
    win over arbitrary JSON examples embedded in the prose.
    """
    text = strip_json_fence(content)
    decoder = json.JSONDecoder()
    candidates: list[dict[str, Any]] = []
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            candidates.append(value)
    if not candidates:
        return None
    for candidate in candidates:
        if any(key in candidate for key in PREFERRED_JSON_KEYS):
            return candidate
    return candidates[0]


def _extract_json_result(
    raw_content: str,
    reasoning_text: str,
    usage_dict: dict[str, Any] | None,
    allow_prose: bool,
) -> dict[str, Any]:
    """Shared final step for every wire protocol: turn the model's text into the
    neutral result dict the rest of DocuAgent expects, attaching `__reasoning`
    when the provider exposed a reasoning channel and recording token usage.
    """
    result = parse_model_json_content(raw_content)
    if result is None:
        if allow_prose:
            prose_result: dict[str, Any] = {"__prose__": raw_content}
            if reasoning_text:
                prose_result["__reasoning"] = reasoning_text.strip()
            if usage_dict:
                token_usage.record_usage(usage_dict)
            return prose_result
        raise ValueError("未找到 JSON 对象")
    if reasoning_text:
        result["__reasoning"] = reasoning_text.strip()
    if usage_dict:
        token_usage.record_usage(usage_dict)
    if not isinstance(result, dict):
        raise WorkspaceError("模型返回值必须是 JSON 对象。")
    return result


def call_model_json(
    config: ProviderConfig,
    system_prompt: str,
    model_input: dict[str, Any],
    timeout: int = 180,
    json_mode: bool = False,
    temperature: float | None = None,
    json_retries: int = 2,
    allow_prose: bool = False,
) -> dict[str, Any]:
    """POST to the provider and parse the reply as JSON.

    No chat history is ever sent: `model_input` is the whole request. The wire
    protocol is selected by `config.format` (openai / anthropic / gemini); all
    three normalise back to the same neutral dict consumed upstream.

    The sub-agent tool loop sets `allow_prose=True`: there, natural language is a
    valid first-class reply and only tool calls / final structured results need to
    be JSON. Other callers remain strict and retry malformed replies a few times.
    Network and auth failures are NOT retried here: they are not transient in the
    same way and the HTTP layer handles them.
    """
    if config.format == "anthropic":
        return _call_anthropic_json(
            config, system_prompt, model_input, timeout, json_mode,
            temperature, json_retries, allow_prose,
        )
    if config.format == "gemini":
        return _call_gemini_json(
            config, system_prompt, model_input, timeout, json_mode,
            temperature, json_retries, allow_prose,
        )
    return _call_openai_json(
        config, system_prompt, model_input, timeout, json_mode,
        temperature, json_retries, allow_prose,
    )


def _call_openai_json(
    config: ProviderConfig,
    system_prompt: str,
    model_input: dict[str, Any],
    timeout: int,
    json_mode: bool,
    temperature: float | None,
    json_retries: int,
    allow_prose: bool,
) -> dict[str, Any]:
    request_payload: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(model_input, ensure_ascii=False),
            },
        ],
    }
    if json_mode:
        request_payload["response_format"] = {"type": "json_object"}
    if temperature is not None:
        request_payload["temperature"] = temperature
    request_body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    headers = request_headers({"Content-Type": "application/json"})
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        config.endpoint, data=request_body, method="POST", headers=headers,
    )

    last_parse_error: Exception | None = None
    raw_content = ""
    for attempt in range(json_retries + 1):
        try:
            with open_with_retry(request, timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if json_mode and exc.code == 400:
                return _call_openai_json(
                    config, system_prompt, model_input, timeout, False,
                    temperature, json_retries, allow_prose,
                )
            raise WorkspaceError(model_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise WorkspaceError(f"无法连接模型服务：{reason}") from exc
        except TimeoutError as exc:
            raise WorkspaceError("模型服务请求超时。") from exc

        message = body.get("choices", [{}])[0].get("message")
        if not isinstance(message, dict):
            last_parse_error = ValueError("响应中缺少 message")
            if attempt < json_retries:
                continue
            raise WorkspaceError("模型返回值不是有效的 JSON 对象。") from last_parse_error

        raw_content = str(message.get("content") or "")
        reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "")
        usage = body.get("usage")
        try:
            return _extract_json_result(
                raw_content,
                reasoning,
                usage if isinstance(usage, dict) else None,
                allow_prose,
            )
        except ValueError as exc:
            last_parse_error = exc
            if attempt < json_retries:
                continue
            tail = raw_content[-200:].replace("\n", " ")
            raise WorkspaceError(
                f"模型返回值不是有效的 JSON 对象：{tail}"
            ) from exc

    raise WorkspaceError("模型返回值不是有效的 JSON 对象。") from last_parse_error


def _call_anthropic_json(
    config: ProviderConfig,
    system_prompt: str,
    model_input: dict[str, Any],
    timeout: int,
    json_mode: bool,
    temperature: float | None,
    json_retries: int,
    allow_prose: bool,
) -> dict[str, Any]:
    """Claude Messages API. The system prompt is a top-level field (not a message),
    auth uses `x-api-key` + `anthropic-version`, and reasoning models emit `thinking`
    blocks alongside `text` blocks. JSON mode uses the native `response_format`.
    """
    endpoint = config.base_url if config.base_url.endswith("/messages") \
        else f"{config.base_url}/messages"
    payload: dict[str, Any] = {
        "model": config.model,
        "max_tokens": 16000,
        "system": system_prompt,
        "messages": [
            {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
        ],
    }
    if json_mode:
        payload["response_format"] = {"type": "json"}
    if temperature is not None:
        payload["temperature"] = temperature
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = request_headers({
        "Content-Type": "application/json",
        "x-api-key": config.api_key,
        "anthropic-version": "2023-06-01",
    })
    request = urllib.request.Request(
        endpoint, data=request_body, method="POST", headers=headers,
    )

    last_parse_error: Exception | None = None
    for attempt in range(json_retries + 1):
        try:
            with open_with_retry(request, timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise WorkspaceError(model_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise WorkspaceError(f"无法连接模型服务：{reason}") from exc
        except TimeoutError as exc:
            raise WorkspaceError("模型服务请求超时。") from exc

        blocks = body.get("content") if isinstance(body.get("content"), list) else []
        text_parts: list[str] = []
        thinking_parts: list[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(str(block.get("text") or ""))
            elif block_type == "thinking":
                thinking_parts.append(str(block.get("text") or ""))
        raw_content = "".join(text_parts)
        reasoning = "".join(thinking_parts)
        usage = body.get("usage")
        usage_dict = None
        if isinstance(usage, dict):
            usage_dict = {
                "prompt_tokens": int(usage.get("input_tokens") or 0),
                "completion_tokens": int(usage.get("output_tokens") or 0),
            }
        try:
            return _extract_json_result(raw_content, reasoning, usage_dict, allow_prose)
        except ValueError as exc:
            last_parse_error = exc
            if attempt < json_retries:
                continue
            tail = raw_content[-200:].replace("\n", " ")
            raise WorkspaceError(
                f"模型返回值不是有效的 JSON 对象：{tail}"
            ) from exc

    raise WorkspaceError("模型返回值不是有效的 JSON 对象。") from last_parse_error


def _call_gemini_json(
    config: ProviderConfig,
    system_prompt: str,
    model_input: dict[str, Any],
    timeout: int,
    json_mode: bool,
    temperature: float | None,
    json_retries: int,
    allow_prose: bool,
) -> dict[str, Any]:
    """Generative Language generateContent. The model id lives in the URL path,
    the API key is a query parameter, and `systemInstruction` is separate from
    `contents`. JSON mode is `generationConfig.responseMimeType`. Reasoning models
    (flash-thinking) emit `thought` parts that we surface as `__reasoning`.
    """
    endpoint = f"{config.base_url}/models/{config.model}:generateContent"
    if config.api_key:
        endpoint += f"?key={config.api_key}"
    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "contents": [
            {
                "role": "user",
                "parts": [{"text": json.dumps(model_input, ensure_ascii=False)}],
            },
        ],
        "generationConfig": {"maxOutputTokens": 16000},
    }
    if json_mode:
        payload["generationConfig"]["responseMimeType"] = "application/json"
    if temperature is not None:
        payload["generationConfig"]["temperature"] = temperature
    request_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = request_headers({"Content-Type": "application/json"})
    request = urllib.request.Request(
        endpoint, data=request_body, method="POST", headers=headers,
    )

    last_parse_error: Exception | None = None
    for attempt in range(json_retries + 1):
        try:
            with open_with_retry(request, timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise WorkspaceError(model_error_message(exc)) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            raise WorkspaceError(f"无法连接模型服务：{reason}") from exc
        except TimeoutError as exc:
            raise WorkspaceError("模型服务请求超时。") from exc

        candidates = body.get("candidates") if isinstance(body.get("candidates"), list) else []
        candidate = candidates[0] if candidates else {}
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        text_parts: list[str] = []
        thought_parts: list[str] = []
        if isinstance(parts, list):
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("thought"):
                    thought_parts.append(str(part.get("text") or ""))
                else:
                    text_parts.append(str(part.get("text") or ""))
        raw_content = "".join(text_parts)
        reasoning = "".join(thought_parts)
        usage = body.get("usageMetadata")
        usage_dict = None
        if isinstance(usage, dict):
            usage_dict = {
                "prompt_tokens": int(usage.get("promptTokenCount") or 0),
                "completion_tokens": int(usage.get("candidatesTokenCount") or 0),
            }
        try:
            return _extract_json_result(raw_content, reasoning, usage_dict, allow_prose)
        except ValueError as exc:
            last_parse_error = exc
            if attempt < json_retries:
                continue
            tail = raw_content[-200:].replace("\n", " ")
            raise WorkspaceError(
                f"模型返回值不是有效的 JSON 对象：{tail}"
            ) from exc

    raise WorkspaceError("模型返回值不是有效的 JSON 对象。") from last_parse_error


def _fallback_chat_json(
    config: ProviderConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    timeout: int,
) -> dict[str, Any]:
    """Non-OpenAI path for the sub-agent loop: there is no native `tools` API, so
    we ask the model for a JSON object carrying `tool_calls` and map that back into
    the same shape `call_model_chat` returns for OpenAI. Best-effort — the model
    must follow the JSON tool-call convention baked into its system prompt.
    """
    system_text = "\n".join(
        str(m.get("content") or "")
        for m in messages
        if m.get("role") == "system" and isinstance(m.get("content"), str)
    )
    convo = [
        {"role": m.get("role"), "content": m.get("content")}
        for m in messages
        if m.get("role") != "system"
    ]
    model_input: dict[str, Any] = {"messages": convo}
    if tools:
        model_input["tools"] = tools
    result = call_model_json(config, system_text, model_input, timeout=timeout, json_mode=True)
    raw_calls = result.get("tool_calls")
    tool_calls: list[dict[str, Any]] = []
    if isinstance(raw_calls, list):
        for index, call in enumerate(raw_calls):
            if not isinstance(call, dict):
                continue
            name = str(call.get("name") or "").strip()
            args = call.get("args") if isinstance(call.get("args"), dict) else {}
            if not name:
                continue
            tool_calls.append({
                "id": f"call_{index}",
                "name": name,
                "args": args,
                "raw": call,
            })
    content = result.get("__prose__") or json.dumps(result, ensure_ascii=False)
    return {
        "content": content,
        "tool_calls": tool_calls,
        "finish_reason": "tool_calls" if tool_calls else "stop",
        "reasoning": result.get("__reasoning", ""),
        "message": {},
    }


def call_model_chat(
    config: ProviderConfig,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    timeout: int = 300,
) -> dict[str, Any]:
    """OpenAI-compatible chat completion with native `tools` / `tool_calls`.

    This is the preferred path for the sub-agent loop. The model may write arbitrary
    natural-language `content` and, when it wants to act, returns structured
    `tool_calls` that are enforced by the provider instead of by prompt convention.

    Raises `ToolCallingNotSupported` for the HTTP statuses commonly returned when an
    OpenAI-compatible service or model does not support function calling.
    """
    if config.format != "openai":
        # No native tools API for Anthropic/Gemini here; fall back to a JSON
        # tool-call request so the sub-agent loop still works, just less reliably.
        return _fallback_chat_json(config, messages, tools, timeout)
    request_payload: dict[str, Any] = {
        "model": config.model,
        "messages": messages,
        # Stream the completion: the socket timeout then measures a pause
        # BETWEEN tokens (a stall), not the model's total thinking time. A
        # non-streaming request only answers after the FULL completion — long
        # reasoning turns silently exceeded the timeout and killed the task.
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    if tools:
        request_payload["tools"] = tools

    request_body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    headers = request_headers({"Content-Type": "application/json"})
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        config.endpoint,
        data=request_body,
        method="POST",
        headers=headers,
    )

    # Whole-request retries: a slow turn must not kill the whole sub-agent task.
    # Unlike a streamed event consumer, a regenerated request never duplicates
    # delivered events — we discard partial output and keep the final attempt.
    status_attempts = 0
    connection_attempts = 0
    while True:
        try:
            return _stream_chat_once(request, timeout)
        except ToolCallingNotSupported:
            raise
        except urllib.error.HTTPError as exc:
            if tools and exc.code in {400, 404, 422}:
                raise ToolCallingNotSupported(
                    f"当前模型服务不支持原生工具调用（HTTP {exc.code}）。"
                ) from exc
            if exc.code not in RETRYABLE_STATUS:
                raise WorkspaceError(model_error_message(exc)) from exc
            if status_attempts >= len(STATUS_RETRY_DELAYS):
                raise WorkspaceError(model_error_message(exc)) from exc
            time.sleep(STATUS_RETRY_DELAYS[status_attempts])
            status_attempts += 1
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # HTTPError is an OSError, so its branch must come first. Covers the
            # silent open (no bytes before the first chunk) and a mid-stream
            # drop alike: both regenerate the whole request, bounded.
            if connection_attempts >= len(CONNECTION_RETRY_DELAYS):
                raise WorkspaceError(
                    "无法连接模型服务。"
                    if isinstance(exc, urllib.error.URLError)
                    else "模型服务请求超时。"
                ) from exc
            time.sleep(CONNECTION_RETRY_DELAYS[connection_attempts])
            connection_attempts += 1


def _stream_chat_once(request: urllib.request.Request, timeout: int) -> dict[str, Any]:
    """Consume one streamed chat completion and normalise it like the JSON shape.

    SSE deltas for content / reasoning / tool_calls arrive split across chunks;
    they are accumulated per tool-call index and reassembled before parsing.
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_acc: dict[int, dict[str, str]] = {}
    finish_reason = ""
    usage: Any = None

    with urlopen_with_proxy_fallback(request, timeout) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line or not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            choice = (event.get("choices") or [{}])[0]
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = {}
            text = delta.get("content")
            if isinstance(text, str):
                content_parts.append(text)
            thought = delta.get("reasoning_content") or delta.get("reasoning")
            if isinstance(thought, str):
                reasoning_parts.append(thought)
            for raw_call in delta.get("tool_calls") or []:
                if not isinstance(raw_call, dict):
                    continue
                index = int(raw_call.get("index") or 0)
                slot = tool_acc.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if raw_call.get("id"):
                    slot["id"] = str(raw_call["id"])
                function = raw_call.get("function")
                if isinstance(function, dict):
                    if function.get("name"):
                        slot["name"] += str(function["name"])
                    fragment = function.get("arguments")
                    if isinstance(fragment, str):
                        slot["arguments"] += fragment
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
            streamed_usage = event.get("usage")
            if isinstance(streamed_usage, dict):
                usage = streamed_usage

    content = "".join(content_parts)
    reasoning = "".join(reasoning_parts)
    tool_calls: list[dict[str, Any]] = []
    raw_tool_calls: list[dict[str, Any]] = []
    for index in sorted(tool_acc):
        slot = tool_acc[index]
        raw_arguments = slot["arguments"]
        try:
            arguments = json.loads(raw_arguments or "{}")
        except json.JSONDecodeError:
            arguments = {"__unparseable__": raw_arguments}
        if not isinstance(arguments, dict):
            arguments = {"__unparseable__": raw_arguments}
        raw_call: dict[str, Any] = {
            "id": slot["id"] or f"call_{index}",
            "type": "function",
            "function": {"name": slot["name"], "arguments": raw_arguments},
        }
        raw_tool_calls.append(raw_call)
        tool_calls.append({
            "id": raw_call["id"],
            "name": slot["name"],
            "args": arguments,
            "raw": raw_call,
        })

    if isinstance(usage, dict):
        token_usage.record_usage(usage)

    message: dict[str, Any] = {
        "content": content,
        "tool_calls": raw_tool_calls,
    }
    if reasoning:
        message["reasoning_content"] = reasoning

    return {
        "content": content,
        "tool_calls": tool_calls,
        "finish_reason": finish_reason,
        "reasoning": reasoning,
        "message": message,
    }


def call_vision_json(
    config: ProviderConfig,
    system_prompt: str,
    text_input: dict[str, Any],
    image_bytes: bytes,
    timeout: int = 180,
) -> dict[str, Any]:
    """Call an OpenAI-compatible vision model with an inline base64 image."""
    if config.format != "openai":
        raise WorkspaceError("视觉模型当前仅支持 OpenAI 兼容协议（format=openai）。")
    image_url = "data:image/png;base64," + base64.b64encode(image_bytes).decode()
    request_payload = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(text_input, ensure_ascii=False),
                    },
                    {"type": "image_url", "image_url": {"url": image_url}},
                ],
            },
        ],
    }
    request_body = json.dumps(request_payload, ensure_ascii=False).encode("utf-8")
    headers = request_headers({"Content-Type": "application/json"})
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        config.endpoint,
        data=request_body,
        method="POST",
        headers=headers,
    )
    try:
        with open_with_retry(request, timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        message = body["choices"][0]["message"]
        result = json.loads(strip_json_fence(str(message["content"])))
        usage = body.get("usage")
        if isinstance(usage, dict):
            token_usage.record_usage(usage)
    except urllib.error.HTTPError as exc:
        raise WorkspaceError(model_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise WorkspaceError(f"无法连接模型服务：{reason}") from exc
    except TimeoutError as exc:
        raise WorkspaceError("模型服务请求超时。") from exc
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise WorkspaceError("视觉模型返回值不是有效的 JSON 对象。") from exc
    if not isinstance(result, dict):
        raise WorkspaceError("视觉模型返回值必须是 JSON 对象。")
    return result


def stream_json_model(
    config: ProviderConfig,
    system_prompt: str,
    model_input: dict[str, Any],
    timeout: int = 180,
    validate: Any = None,
):
    """Stream a chat/completions call and yield NDJSON `reasoning`/`content`/`done` events.

    `validate`, when given, transforms the parsed JSON result into the `done`
    payload; otherwise the raw result (with `__reasoning` attached when the
    provider exposed a reasoning channel) is used.
    """
    if config.format != "openai":
        # No streaming SSE shape for Anthropic/Gemini here; run it once and replay
        # the result as a single content burst followed by the done event.
        result = call_model_json(config, system_prompt, model_input, timeout=timeout)
        reasoning = result.get("__reasoning")
        if reasoning:
            yield {"type": "reasoning", "text": reasoning}
        yield {"type": "content", "text": json.dumps(result, ensure_ascii=False)}
        done = validate(result) if validate is not None else result
        yield {"type": "done", "result": done}
        return
    request_body = json.dumps(
        {
            "model": config.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(model_input, ensure_ascii=False),
                },
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
        }
    ).encode("utf-8")
    headers = request_headers({"Content-Type": "application/json"})
    if config.api_key:
        headers["Authorization"] = f"Bearer {config.api_key}"
    request = urllib.request.Request(
        config.endpoint,
        data=request_body,
        method="POST",
        headers=headers,
    )
    try:
        with urlopen_with_proxy_fallback(request, timeout) as response:
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            usage: Any = None
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                    if isinstance(chunk.get("usage"), dict):
                        usage = chunk["usage"]
                        continue
                    delta = chunk["choices"][0]["delta"]
                except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                    continue
                reasoning = delta.get("reasoning_content") or delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    reasoning_parts.append(reasoning)
                    yield {"type": "reasoning", "text": reasoning}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    content_parts.append(content)
                    yield {"type": "content", "text": content}
    except urllib.error.HTTPError as exc:
        raise WorkspaceError(model_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise WorkspaceError(f"无法连接模型服务：{reason}") from exc
    except TimeoutError as exc:
        raise WorkspaceError("模型服务请求超时。") from exc

    if isinstance(usage, dict):
        token_usage.record_usage(usage)
    content = "".join(content_parts)
    try:
        result = json.loads(strip_json_fence(content))
    except json.JSONDecodeError as exc:
        raise WorkspaceError("模型流式返回值不是有效的 JSON 对象。") from exc
    reasoning = "".join(reasoning_parts).strip()
    if reasoning:
        result["__reasoning"] = reasoning
    done = validate(result) if validate is not None else result
    yield {"type": "done", "result": done}
