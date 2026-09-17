"""Wire-protocol dispatch for call_model_json / call_model_chat / call_vision_json.

The request/response shapes for OpenAI, Anthropic and Gemini differ; these tests
pin the construction and parsing for each without hitting a real vendor. We
replace `urlopen_with_proxy_fallback` with a recorder that returns a canned
payload, then assert the outgoing URL/headers/body and the normalised result.
"""

import io
import json
import ssl
import unittest
import urllib.error
from unittest.mock import patch

import llm_client
from core import WorkspaceError
from llm_client import ProviderConfig


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _sse_lines(events: list[dict]) -> list[str]:
    """Encode a list of SSE event dicts into wire lines (with [DONE] terminator)."""
    lines: list[str] = []
    for event in events:
        lines.append(f"data: {json.dumps(event)}")
    lines.append("data: [DONE]")
    return lines


class _SSEResponse:
    """Minimal SSE stream: context-managed, iterable of raw `data:` lines."""

    def __init__(self, events: list[dict]) -> None:
        self._lines = [f"{line}\n".encode("utf-8") for line in _sse_lines(events)]

    def __enter__(self) -> "_SSEResponse":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def __iter__(self):
        return iter(self._lines)


def _recording_open(captured: dict, payload: dict):
    def _open(request, timeout):  # noqa: ANN001
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeResponse(payload)

    return _open


OPENAI_RESP = {
    "choices": [
        {"message": {"content": json.dumps({"ok": True}), "reasoning_content": "r"}},
    ],
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
}
ANTHROPIC_RESP = {
    "content": [
        {"type": "thinking", "text": "athink"},
        {"type": "text", "text": json.dumps({"ok": True})},
    ],
    "usage": {"input_tokens": 10, "output_tokens": 5},
}
GEMINI_RESP = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {"thought": True, "text": "gthink"},
                    {"text": json.dumps({"ok": True})},
                ],
            },
        },
    ],
    "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
}


class LlmFormatTest(unittest.TestCase):
    def test_from_payload_defaults_to_openai(self) -> None:
        cfg = ProviderConfig.from_payload(
            {"provider": {"enabled": True, "base_url": "x", "model": "m", "api_key": "k"}}
        )
        self.assertEqual(cfg.format, "openai")

    def test_from_payload_accepts_known_formats(self) -> None:
        for fmt in ("openai", "anthropic", "gemini"):
            cfg = ProviderConfig.from_payload(
                {
                    "provider": {
                        "enabled": True,
                        "base_url": "x",
                        "model": "m",
                        "api_key": "k",
                        "format": fmt,
                    }
                }
            )
            self.assertEqual(cfg.format, fmt)

    def test_from_payload_rejects_unknown_format(self) -> None:
        cfg = ProviderConfig.from_payload(
            {
                "provider": {
                    "enabled": True,
                    "base_url": "x",
                    "model": "m",
                    "api_key": "k",
                    "format": "bogus",
                }
            }
        )
        self.assertEqual(cfg.format, "openai")

    def test_openai_request_shape(self) -> None:
        captured: dict = {}
        cfg = ProviderConfig(
            base_url="https://api.openai.com/v1", model="gpt", api_key="k", format="openai"
        )
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", _recording_open(captured, OPENAI_RESP)
        ):
            result = llm_client.call_model_json(cfg, "sys", {"q": 1}, json_mode=True)
        self.assertTrue(captured["url"].endswith("/chat/completions"))
        self.assertEqual(captured["headers"]["Authorization"], "Bearer k")
        self.assertEqual(captured["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["__reasoning"], "r")

    def test_anthropic_request_shape(self) -> None:
        captured: dict = {}
        cfg = ProviderConfig(
            base_url="https://api.anthropic.com/v1",
            model="claude-x",
            api_key="k",
            format="anthropic",
        )
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", _recording_open(captured, ANTHROPIC_RESP)
        ):
            result = llm_client.call_model_json(cfg, "sys", {"q": 1}, json_mode=True)
        self.assertTrue(captured["url"].endswith("/messages"))
        header_map = {k.lower(): v for k, v in captured["headers"].items()}
        self.assertEqual(header_map["x-api-key"], "k")
        self.assertEqual(header_map["anthropic-version"], "2023-06-01")
        self.assertEqual(captured["body"]["system"], "sys")
        self.assertEqual(captured["body"]["max_tokens"], 16000)
        self.assertEqual(captured["body"]["response_format"], {"type": "json"})
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["__reasoning"], "athink")

    def test_gemini_request_shape(self) -> None:
        captured: dict = {}
        cfg = ProviderConfig(
            base_url="https://generativelanguage.googleapis.com/v1beta",
            model="gem-x",
            api_key="k",
            format="gemini",
        )
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", _recording_open(captured, GEMINI_RESP)
        ):
            result = llm_client.call_model_json(cfg, "sys", {"q": 1}, json_mode=True)
        self.assertIn("/models/gem-x:generateContent?key=k", captured["url"])
        self.assertEqual(captured["body"]["systemInstruction"]["parts"][0]["text"], "sys")
        self.assertEqual(
            captured["body"]["generationConfig"]["responseMimeType"], "application/json"
        )
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["__reasoning"], "gthink")

    def test_non_openai_vision_rejected(self) -> None:
        cfg = ProviderConfig(base_url="x", model="m", api_key="k", format="gemini")
        with self.assertRaises(WorkspaceError):
            llm_client.call_vision_json(cfg, "sys", {"q": 1}, b"img")

    def test_non_openai_chat_falls_back_to_json_tool_calls(self) -> None:
        captured: dict = {}
        fallback_resp = {
            "content": [
                {
                    "type": "text",
                    "text": json.dumps(
                        {"tool_calls": [{"name": "read", "args": {"p": "x"}}]}
                    ),
                },
            ],
            "usage": {},
        }
        cfg = ProviderConfig(
            base_url="https://api.anthropic.com/v1", model="m", api_key="k", format="anthropic"
        )
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", _recording_open(captured, fallback_resp)
        ):
            out = llm_client.call_model_chat(
                cfg, [{"role": "user", "content": "go"}], tools=[{"name": "read", "args": "p"}]
            )
        self.assertEqual(captured["url"].endswith("/messages"), True)
        self.assertEqual(len(out["tool_calls"]), 1)
        self.assertEqual(out["tool_calls"][0]["name"], "read")
        self.assertEqual(out["tool_calls"][0]["args"], {"p": "x"})


class OpenWithRetryTest(unittest.TestCase):
    """Bounded retry for transient gateway/upstream failures (524, 429, 5xx)."""

    @staticmethod
    def _error_opener(calls: dict, code: int, fail_times: int):
        def _open(request, timeout):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise urllib.error.HTTPError(
                    request.full_url, code, "err", {}, io.BytesIO(b"")
                )
            return _FakeResponse(OPENAI_RESP)

        return _open

    def test_524_is_retried_and_then_succeeds(self) -> None:
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._error_opener(calls, 524, 1)
        ), patch.object(llm_client.time, "sleep"):
            result = llm_client.call_model_json(cfg, "sys", {"q": 1})
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["ok"], True)

    def test_auth_error_is_not_retried(self) -> None:
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._error_opener(calls, 401, 99)
        ), patch.object(llm_client.time, "sleep"):
            with self.assertRaises(WorkspaceError):
                llm_client.call_model_json(cfg, "sys", {"q": 1})
        self.assertEqual(calls["n"], 1)

    def test_retries_are_exhausted_after_three_attempts(self) -> None:
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._error_opener(calls, 524, 99)
        ), patch.object(llm_client.time, "sleep"):
            with self.assertRaises(WorkspaceError):
                llm_client.call_model_json(cfg, "sys", {"q": 1})
        self.assertEqual(calls["n"], 3)

    @staticmethod
    def _handshake_opener(calls: dict, fail_times: int):
        def _open(request, timeout):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise urllib.error.URLError(
                    ssl.SSLError("_ssl.c:983: The handshake operation timed out")
                )
            return _FakeResponse(OPENAI_RESP)

        return _open

    def test_ssl_handshake_timeout_is_retried_and_succeeds(self) -> None:
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._handshake_opener(calls, 2)
        ), patch.object(llm_client.time, "sleep"):
            result = llm_client.call_model_json(cfg, "sys", {"q": 1})
        self.assertEqual(calls["n"], 3)
        self.assertEqual(result["ok"], True)

    def test_connection_failures_get_five_attempts(self) -> None:
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._handshake_opener(calls, 99)
        ), patch.object(llm_client.time, "sleep"):
            with self.assertRaises(WorkspaceError):
                llm_client.call_model_json(cfg, "sys", {"q": 1})
        self.assertEqual(calls["n"], 5)

    # --- call_model_chat (streamed SSE) and call_vision_json (non-streaming).

    @staticmethod
    def _chat_timeout_opener(calls: dict, fail_times: int):
        def _open(request, timeout):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] <= fail_times:
                raise TimeoutError()
            return _SSEResponse([{"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}])

        return _open

    def test_chat_read_timeout_is_retried_and_succeeds(self) -> None:
        """Pilot-observed failure: one slow turn killed the whole sub-agent task."""
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._chat_timeout_opener(calls, 2)
        ), patch.object(llm_client.time, "sleep"):
            result = llm_client.call_model_chat(
                cfg, [{"role": "user", "content": "go"}], tools=[{"type": "function", "function": {"name": "read"}}]
            )
        self.assertEqual(calls["n"], 3)
        self.assertEqual(result["content"], "ok")

    def test_chat_read_timeout_exhausts_five_attempts(self) -> None:
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._chat_timeout_opener(calls, 99)
        ), patch.object(llm_client.time, "sleep"):
            with self.assertRaises(WorkspaceError):
                llm_client.call_model_chat(cfg, [{"role": "user", "content": "go"}])
        self.assertEqual(calls["n"], 5)

    def test_vision_read_timeout_is_retried_and_succeeds(self) -> None:
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")

        def _vision_opener(request, timeout):  # noqa: ANN001
            calls["n"] += 1
            if calls["n"] <= 1:
                raise TimeoutError()
            return _FakeResponse(OPENAI_RESP)

        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", _vision_opener
        ), patch.object(llm_client.time, "sleep"):
            result = llm_client.call_vision_json(cfg, "sys", {"q": 1}, b"img")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(result["ok"], True)

    def test_chat_tool_unsupported_status_still_not_retried(self) -> None:
        """400/404/422 must surface as ToolCallingNotSupported, not burn retries."""
        calls: dict = {"n": 0}
        cfg = ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")
        with patch.object(
            llm_client, "urlopen_with_proxy_fallback", self._error_opener(calls, 404, 99)
        ), patch.object(llm_client.time, "sleep"):
            with self.assertRaises(llm_client.ToolCallingNotSupported):
                llm_client.call_model_chat(
                    cfg, [{"role": "user", "content": "go"}], tools=[{"type": "function", "function": {"name": "read"}}]
                )
        self.assertEqual(calls["n"], 1)


class ChatStreamParseTest(unittest.TestCase):
    """The streamed chat path must reassemble split SSE deltas exactly."""

    @staticmethod
    def _config() -> ProviderConfig:
        return ProviderConfig(base_url="https://api.openai.com/v1", model="gpt", api_key="k")

    def test_content_split_across_chunks_is_reassembled(self) -> None:
        response = _SSEResponse([
            {"choices": [{"delta": {"role": "assistant", "content": "hel"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "lo"}, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
        ])
        with patch.object(llm_client, "urlopen_with_proxy_fallback", lambda _req, _t: response):
            result = llm_client.call_model_chat(self._config(), [{"role": "user", "content": "go"}])
        self.assertEqual(result["content"], "hello")
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(result["tool_calls"], [])

    def test_tool_call_delta_fragments_are_merged_and_parsed(self) -> None:
        response = _SSEResponse([
            {"choices": [{"delta": {
                "tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_file", "arguments": '{"path"'}}]
            }, "finish_reason": None}]},
            {"choices": [{"delta": {
                "tool_calls": [{"index": 0, "function": {"arguments": ':"src/a.js"}'}}]
            }, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ])
        with patch.object(llm_client, "urlopen_with_proxy_fallback", lambda _req, _t: response):
            result = llm_client.call_model_chat(self._config(), [{"role": "user", "content": "go"}])
        self.assertEqual(len(result["tool_calls"]), 1)
        call = result["tool_calls"][0]
        self.assertEqual(call["id"], "call_1")
        self.assertEqual(call["name"], "read_file")
        self.assertEqual(call["args"], {"path": "src/a.js"})
        self.assertEqual(result["finish_reason"], "tool_calls")

    def test_two_parallel_tool_calls_keep_their_indices(self) -> None:
        response = _SSEResponse([
            {"choices": [{"delta": {
                "tool_calls": [
                    {"index": 0, "id": "a", "function": {"name": "read", "arguments": '{"p":"1"}'}},
                    {"index": 1, "id": "b", "function": {"name": "write", "arguments": '{"p":"2"}'}},
                ]
            }, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ])
        with patch.object(llm_client, "urlopen_with_proxy_fallback", lambda _req, _t: response):
            result = llm_client.call_model_chat(self._config(), [{"role": "user", "content": "go"}])
        names = [call["name"] for call in result["tool_calls"]]
        args = [call["args"] for call in result["tool_calls"]]
        self.assertEqual(names, ["read", "write"])
        self.assertEqual(args, [{"p": "1"}, {"p": "2"}])

    def test_reasoning_deltas_are_accumulated(self) -> None:
        response = _SSEResponse([
            {"choices": [{"delta": {"reasoning_content": "think"}, "finish_reason": None}]},
            {"choices": [{"delta": {"reasoning_content": "ing"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "done"}, "finish_reason": "stop"}]},
        ])
        with patch.object(llm_client, "urlopen_with_proxy_fallback", lambda _req, _t: response):
            result = llm_client.call_model_chat(self._config(), [{"role": "user", "content": "go"}])
        self.assertEqual(result["reasoning"], "thinking")
        self.assertEqual(result["content"], "done")

    def test_unparseable_arguments_are_tagged_not_dropped(self) -> None:
        response = _SSEResponse([
            {"choices": [{"delta": {
                "tool_calls": [{"index": 0, "function": {"name": "read", "arguments": "{broken"}}]
            }, "finish_reason": None}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ])
        with patch.object(llm_client, "urlopen_with_proxy_fallback", lambda _req, _t: response):
            result = llm_client.call_model_chat(self._config(), [{"role": "user", "content": "go"}])
        self.assertIn("__unparseable__", result["tool_calls"][0]["args"])
