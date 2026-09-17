"""Tests for the code intelligence package.

These prove:
1. The JSON-RPC client logic (request/response, normalization, diagnostics)
   works end-to-end via an injected `responder` -- no live subprocess needed,
   so the tests run in any environment.
2. The wire framing (encode_message) is compatible with a real language-server
   process, validated by running the bundled fake server through
   subprocess.run (which the sandbox permits, unlike live bidirectional pipes).
3. The service degrades gracefully when no language server is available
   (NullCodeIntel and empty detection), preserving the zero-dependency trunk.

Run with:  PYTHONPATH=docuagent python -m pytest docuagent/tests/test_codeintel_lsp.py
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from codeintel.lsp_bridge import (
    JsonRpcClient,
    encode_message,
    find_server_command,
    language_for_path,
)
from codeintel.ports import NullCodeIntel
from codeintel.service import CodeIntelService

FAKE_SERVER = Path(__file__).parent / "fakes" / "fake_lsp_server.py"


def fake_lsp_respond(method: str, params: dict):
    """Canned language-server responses, mirroring tests/fakes/fake_lsp_server.py."""
    if method == "initialize":
        return {"capabilities": {}}
    if method == "textDocument/definition":
        pos = params["position"]
        return {
            "uri": "file:///fake/target.py",
            "range": {
                "start": {"line": pos["line"], "character": pos["character"]},
                "end": {"line": pos["line"], "character": pos["character"] + 3},
            },
        }
    if method == "textDocument/references":
        return [
            {
                "uri": "file:///fake/ref.py",
                "range": {
                    "start": {"line": 2, "character": 1},
                    "end": {"line": 2, "character": 4},
                },
            }
        ]
    if method == "textDocument/hover":
        return {"contents": {"kind": "plaintext", "value": "hover info"}}
    if method == "textDocument/documentSymbol":
        return [
            {
                "name": "my_func",
                "kind": 12,
                "range": {
                    "start": {"line": 1, "character": 0},
                    "end": {"line": 3, "character": 0},
                },
                "detail": "a function",
            }
        ]
    if method == "textDocument/rename":
        return {
            "changes": {
                "file:///fake/target.py": [
                    {
                        "range": {
                            "start": {"line": 0, "character": 0},
                            "end": {"line": 0, "character": 3},
                        },
                        "newText": "renamed",
                    }
                ]
            }
        }
    return None


@pytest.fixture
def client():
    return JsonRpcClient(responder=fake_lsp_respond)


def test_definition(client):
    locs = client.definition("file:///fake/source.py", 5, 2)
    assert len(locs) == 1
    assert locs[0].uri == "file:///fake/target.py"
    assert locs[0].line == 5
    assert locs[0].character == 2


def test_references(client):
    refs = client.references("file:///fake/source.py", 0, 0)
    assert len(refs) == 1
    assert refs[0].uri == "file:///fake/ref.py"
    assert refs[0].line == 2


def test_hover(client):
    assert client.hover("file:///fake/source.py", 0, 0) == "hover info"


def test_document_symbols(client):
    syms = client.document_symbols("file:///fake/source.py")
    assert len(syms) == 1
    assert syms[0].name == "my_func"
    assert syms[0].kind == "function"
    assert syms[0].line == 1


def test_rename(client):
    edit = client.rename("file:///fake/source.py", 0, 0, "renamed")
    assert "changes" in edit


def test_diagnostics_published(client):
    client.feed_bytes(
        encode_message(
            {
                "jsonrpc": "2.0",
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": "file:///fake/source.py",
                    "diagnostics": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 5},
                            },
                            "severity": 1,
                            "message": "fake error",
                        }
                    ],
                },
            }
        )
    )
    diags = client.latest_diagnostics("file:///fake/source.py")
    assert any(d.message == "fake error" for d in diags)


def test_wire_framing_against_fake_server():
    """encode_message must be byte-compatible with a real LSP process."""
    frames = encode_message(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"rootUri": "file:///x", "capabilities": {}}}
    )
    frames += encode_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "textDocument/definition",
            "params": {"textDocument": {"uri": "file:///f.py"}, "position": {"line": 5, "character": 2}},
        }
    )
    result = subprocess.run(
        [sys.executable, str(FAKE_SERVER)],
        input=frames,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=15,
    )
    assert result.returncode == 0
    text = result.stdout.decode("utf-8", "replace")
    assert '"id": 1' in text and '"capabilities"' in text
    assert '"file:///fake/target.py"' in text


def test_language_for_path_and_detection():
    assert language_for_path("/a/b.py") == "python"
    assert language_for_path("/a/b.ts") == "typescript"
    assert language_for_path("/a/b.go") == "go"
    assert language_for_path("/a/b.unknown") is None
    assert isinstance(find_server_command("python"), (list, type(None)))


def test_null_code_intel_degrades():
    n = NullCodeIntel()
    assert n.available() is False
    assert n.goto_definition("/r", "/r/f.py", 0, 0) == []
    assert n.find_references("/r", "/r/f.py", 0, 0) == []
    assert n.hover("/r", "/r/f.py", 0, 0) is None
    assert n.document_symbols("/r", "/r/f.py") == []
    assert n.rename("/r", "/r/f.py", 0, 0, "x") == {}
    assert n.diagnostics("/r", "/r/f.py") == []
    assert n.capabilities().available is False


def test_service_degrades_without_servers(monkeypatch):
    monkeypatch.setattr(
        "codeintel.lsp_bridge.SERVER_CANDIDATES",
        {"python": [["definitely-not-a-real-lsp-binary-xyz"]]},
    )
    svc = CodeIntelService()
    assert svc.available() is False
    assert svc.capabilities().available is False
    assert svc.goto_definition("/tmp", "/tmp/example.py", 0, 0) == []
    assert svc.hover("/tmp", "/tmp/example.py", 0, 0) is None
