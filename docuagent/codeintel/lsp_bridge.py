"""Minimal LSP client over stdio using only the Python standard library.

This module speaks the Language Server Protocol (JSON-RPC 2.0 with
Content-Length framing) to a language server process spawned by the user's
environment. It intentionally depends on nothing outside the stdlib so the
DocuAgent backend trunk remains zero-third-party-dependency.

Design note: the JSON-RPC *logic* (JsonRpcClient) is separated from the
*transport* (ProcessTransport) so it can be tested with an injected responder
instead of a live subprocess. A language server is optional: if the configured
command is not found, callers simply get empty results.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from concurrent.futures import Future
from typing import Any, Callable, Optional

from .ports import Diagnostic, Location, Symbol

_LSP_KIND = {
    1: "file",
    2: "module",
    3: "namespace",
    4: "package",
    5: "class",
    6: "method",
    7: "property",
    8: "field",
    9: "constructor",
    10: "enum",
    11: "interface",
    12: "function",
    13: "variable",
    14: "constant",
    15: "string",
    16: "number",
    17: "boolean",
    18: "array",
    19: "object",
    20: "key",
    21: "null",
    22: "enum_member",
    23: "struct",
    24: "event",
    25: "operator",
    26: "type_parameter",
}

# File extension -> LSP languageId
EXT_LANGUAGE = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".kt": "kotlin",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".scala": "scala",
    ".m": "objectivec",
}

# languageId -> ordered candidate commands (first found on PATH wins)
SERVER_CANDIDATES: dict[str, list[list[str]]] = {
    "python": [["pylsp"], ["pyright-langserver", "--stdio"], ["ruff", "server"]],
    "typescript": [["typescript-language-server", "--stdio"]],
    "javascript": [["typescript-language-server", "--stdio"]],
    "go": [["gopls"]],
    "rust": [["rust-analyzer"]],
    "java": [["jdtls"]],
    "kotlin": [["kotlin-language-server"]],
    "c": [["clangd"]],
    "cpp": [["clangd"]],
    "csharp": [["omnisharp"]],
    "ruby": [["solargraph", "--stdio"]],
    "php": [["phpactor", "language-server"]],
    "swift": [["sourcekit-lsp"]],
    "scala": [["metals"]],
    "objectivec": [["clangd"]],
}


def language_for_path(path: str) -> Optional[str]:
    from pathlib import Path

    return EXT_LANGUAGE.get(Path(path).suffix.lower())


def find_server_command(language: str) -> Optional[list[str]]:
    for candidate in SERVER_CANDIDATES.get(language, []):
        if shutil.which(candidate[0]):
            return candidate
    return None


def encode_message(obj: dict) -> bytes:
    """Frame a JSON-RPC message with LSP Content-Length headers."""
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


class _MessageBuffer:
    """Accumulates bytes from a stream and yields complete LSP frames."""

    def __init__(self) -> None:
        self._buf = b""

    def feed(self, chunk: bytes) -> list[dict]:
        self._buf += chunk
        out: list[dict] = []
        while True:
            sep = self._buf.find(b"\r\n\r\n")
            if sep == -1:
                break
            header = self._buf[:sep].decode("ascii", "replace")
            length = None
            for line in header.split("\r\n"):
                if line.lower().startswith("content-length:"):
                    try:
                        length = int(line.split(":", 1)[1].strip())
                    except ValueError:
                        length = None
            if length is None:
                self._buf = self._buf[sep + 4 :]
                continue
            total = sep + 4 + length
            if len(self._buf) < total:
                break
            body = self._buf[sep + 4 : total]
            self._buf = self._buf[total:]
            try:
                out.append(json.loads(body.decode("utf-8", "replace")))
            except json.JSONDecodeError:
                continue
        return out


class JsonRpcClient:
    """JSON-RPC 2.0 client logic, transport-agnostic.

    In production a ProcessTransport feeds bytes from a language server process.
    In tests a `responder` callback can supply canned results synchronously,
    avoiding any subprocess.
    """

    def __init__(self, responder: Optional[Callable[[str, dict], Any]] = None) -> None:
        self._buffer = _MessageBuffer()
        self._pending: dict[Any, Future] = {}
        self._next_id = 1
        self._diagnostics: dict[str, list[Diagnostic]] = {}
        self._responder = responder
        self._transport: Optional["ProcessTransport"] = None

    # ---- message ingestion ----------------------------------------------
    def feed_bytes(self, chunk: bytes) -> None:
        for msg in self._buffer.feed(chunk):
            self._on_message(msg)

    def _on_message(self, msg: dict) -> None:
        if "id" in msg and ("result" in msg or "error" in msg):
            rid = msg["id"]
            fut = self._pending.pop(rid, None)
            if fut:
                fut.set_result(msg)
        elif msg.get("method") == "textDocument/publishDiagnostics":
            params = msg.get("params", {})
            uri = params.get("uri")
            if uri is not None:
                self._diagnostics[uri] = _normalize_diagnostics(
                    params.get("diagnostics", [])
                )

    # ---- send primitives -------------------------------------------------
    def _send(self, obj: dict) -> None:
        if self._transport is not None:
            self._transport.write(encode_message(obj))

    def _notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict, timeout: float = 15.0):
        rid = self._next_id
        self._next_id += 1
        fut: Future = Future()
        self._pending[rid] = fut
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        if self._responder is not None:
            # Synchronous test path: resolve the future immediately.
            self._on_message(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": self._responder(method, params),
                }
            )
        try:
            resp = fut.result(timeout)
        except Exception:
            self._pending.pop(rid, None)
            return None
        if isinstance(resp, dict) and "error" in resp:
            return None
        return resp.get("result") if isinstance(resp, dict) else None

    # ---- document lifecycle ----------------------------------------------
    def open_document(self, uri: str, text: str, language_id: str) -> None:
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    # ---- features --------------------------------------------------------
    def definition(self, uri: str, line: int, col: int) -> list[Location]:
        res = self._request(
            "textDocument/definition",
            {"textDocument": {"uri": uri}, "position": _pos(line, col)},
        )
        return _normalize_locations(res)

    def references(self, uri: str, line: int, col: int) -> list[Location]:
        res = self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": _pos(line, col),
                "context": {"includeDeclaration": True},
            },
        )
        return _normalize_locations(res)

    def hover(self, uri: str, line: int, col: int) -> Optional[str]:
        res = self._request(
            "textDocument/hover",
            {"textDocument": {"uri": uri}, "position": _pos(line, col)},
        )
        if not res:
            return None
        return _stringify_hover(res.get("contents"))

    def document_symbols(self, uri: str) -> list[Symbol]:
        res = self._request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}
        )
        return _normalize_symbols(res)

    def rename(self, uri: str, line: int, col: int, new_name: str) -> dict:
        res = self._request(
            "textDocument/rename",
            {
                "textDocument": {"uri": uri},
                "position": _pos(line, col),
                "newName": new_name,
            },
        )
        return res or {}

    def latest_diagnostics(self, uri: str) -> list[Diagnostic]:
        return self._diagnostics.get(uri, [])


class ProcessTransport:
    """Spawns a language server and pumps its stdout into a JsonRpcClient."""

    def __init__(self, client: JsonRpcClient, command: list[str]) -> None:
        self.client = client
        self.proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._alive = True
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def write(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def _pump(self) -> None:
        assert self.proc.stdout is not None
        while self._alive:
            chunk = self.proc.stdout.read(4096)
            if not chunk:
                break
            self.client.feed_bytes(chunk)

    def close(self) -> None:
        self._alive = False
        try:
            self.client._request("shutdown", {}, timeout=3)
            self.write(encode_message({"jsonrpc": "2.0", "method": "exit"}))
        except Exception:
            pass
        try:
            if self.proc.stdin is not None:
                self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.kill()
        except Exception:
            pass


class LspBridge:
    """A single language-server process with a JSON-RPC client.

    Public API used by the service layer: initialize/notify happen on
    construction; feature methods delegate to the client.
    """

    def __init__(
        self, command: list[str], language_id: str, root_uri: str
    ) -> None:
        self.language_id = language_id
        self.client = JsonRpcClient(responder=None)
        self.transport = ProcessTransport(self.client, command)
        self.client._transport = self.transport
        self.client._request(
            "initialize",
            {
                "processId": None,
                "rootUri": root_uri,
                "capabilities": {},
                "workspaceFolders": None,
            },
        )
        self.client._notify("initialized", {})

    def open_document(self, uri: str, text: str, language_id: str) -> None:
        self.client.open_document(uri, text, language_id)

    def definition(self, uri: str, line: int, col: int) -> list[Location]:
        return self.client.definition(uri, line, col)

    def references(self, uri: str, line: int, col: int) -> list[Location]:
        return self.client.references(uri, line, col)

    def hover(self, uri: str, line: int, col: int) -> Optional[str]:
        return self.client.hover(uri, line, col)

    def document_symbols(self, uri: str) -> list[Symbol]:
        return self.client.document_symbols(uri)

    def rename(self, uri: str, line: int, col: int, new_name: str) -> dict:
        return self.client.rename(uri, line, col, new_name)

    def latest_diagnostics(self, uri: str) -> list[Diagnostic]:
        return self.client.latest_diagnostics(uri)

    def close(self) -> None:
        self.transport.close()


# ---- normalization helpers ------------------------------------------------
def _pos(line: int, col: int) -> dict:
    return {"line": line, "character": col}


def _normalize_locations(res: Any) -> list[Location]:
    if res is None:
        return []
    items = res if isinstance(res, list) else [res]
    out: list[Location] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rng = it.get("range") or {}
        start = rng.get("start") or {}
        end = rng.get("end") or {}
        out.append(
            Location(
                uri=it.get("uri", ""),
                line=start.get("line", 0),
                character=start.get("character", 0),
                end_line=end.get("line", 0),
                end_character=end.get("character", 0),
            )
        )
    return out


def _normalize_symbols(res: Any) -> list[Symbol]:
    if not isinstance(res, list):
        return []
    out: list[Symbol] = []

    def walk(items: list) -> None:
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name", "")
            kind = _LSP_KIND.get(it.get("kind", 0), str(it.get("kind", "")))
            rng = it.get("range") or it.get("location", {}).get("range") or {}
            start = rng.get("start") or {}
            out.append(
                Symbol(
                    name=name,
                    kind=kind,
                    line=start.get("line", 0),
                    character=start.get("character", 0),
                    detail=it.get("detail", "") or "",
                )
            )
            children = it.get("children")
            if isinstance(children, list):
                walk(children)

    walk(res)
    return out


def _normalize_diagnostics(items: Any) -> list[Diagnostic]:
    out: list[Diagnostic] = []
    for it in items or []:
        if not isinstance(it, dict):
            continue
        rng = it.get("range", {})
        start = rng.get("start") or {}
        end = rng.get("end") or {}
        out.append(
            Diagnostic(
                line=start.get("line", 0),
                character=start.get("character", 0),
                end_line=end.get("line", 0),
                end_character=end.get("character", 0),
                severity=int(it.get("severity", 1)),
                message=it.get("message", ""),
            )
        )
    return out


def _stringify_hover(contents: Any) -> Optional[str]:
    if contents is None:
        return None
    if isinstance(contents, str):
        return contents
    if isinstance(contents, list):
        parts = [_stringify_hover(c) for c in contents]
        return "\n".join(p for p in parts if p)
    if isinstance(contents, dict):
        value = contents.get("value")
        if value:
            return str(value)
        return _stringify_hover(contents.get("contents"))
    return None
