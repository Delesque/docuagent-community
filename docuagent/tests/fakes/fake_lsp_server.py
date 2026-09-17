"""A tiny, self-contained fake Language Server for tests.

It speaks just enough of the LSP JSON-RPC protocol (over stdio, Content-Length
framed) to exercise docuagent.codeintel.lsp_bridge: initialize, didOpen
(publishes a diagnostic), definition, references, hover, documentSymbol, rename,
shutdown, exit. It returns canned results so tests need no real language server
and no third-party dependency.
"""

import json
import sys


def encode(obj: dict) -> bytes:
    body = json.dumps(obj).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body


def read_frames(buf: bytearray):
    """Yield complete LSP messages parsed from buf, consuming bytes."""
    while True:
        sep = buf.find(b"\r\n\r\n")
        if sep == -1:
            return
        header = buf[:sep].decode("ascii", "replace")
        length = None
        for line in header.split("\r\n"):
            if line.lower().startswith("content-length:"):
                try:
                    length = int(line.split(":", 1)[1].strip())
                except ValueError:
                    length = None
        if length is None:
            del buf[: sep + 4]
            continue
        total = sep + 4 + length
        if len(buf) < total:
            return
        body = buf[sep + 4 : total]
        del buf[:total]
        try:
            yield json.loads(body.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            continue


def main() -> None:
    buf = bytearray()
    out = sys.stdout.buffer
    while True:
        chunk = sys.stdin.buffer.read(4096)
        if not chunk:
            break
        buf.extend(chunk)
        for msg in read_frames(buf):
            if "id" not in msg:
                method = msg.get("method")
                if method == "textDocument/didOpen":
                    uri = msg["params"]["textDocument"]["uri"]
                    out.write(
                        encode(
                            {
                                "jsonrpc": "2.0",
                                "method": "textDocument/publishDiagnostics",
                                "params": {
                                    "uri": uri,
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
                    out.flush()
                continue

            method = msg.get("method")
            rid = msg["id"]
            result = None
            if method == "initialize":
                result = {"capabilities": {}}
            elif method == "textDocument/definition":
                pos = msg["params"]["position"]
                result = {
                    "uri": "file:///fake/target.py",
                    "range": {
                        "start": {"line": pos["line"], "character": pos["character"]},
                        "end": {
                            "line": pos["line"],
                            "character": pos["character"] + 3,
                        },
                    },
                }
            elif method == "textDocument/references":
                result = [
                    {
                        "uri": "file:///fake/ref.py",
                        "range": {
                            "start": {"line": 2, "character": 1},
                            "end": {"line": 2, "character": 4},
                        },
                    }
                ]
            elif method == "textDocument/hover":
                result = {"contents": {"kind": "plaintext", "value": "hover info"}}
            elif method == "textDocument/documentSymbol":
                result = [
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
            elif method == "textDocument/rename":
                result = {
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
            elif method == "shutdown":
                result = None
            elif method == "exit":
                return
            out.write(encode({"jsonrpc": "2.0", "id": rid, "result": result}))
            out.flush()


if __name__ == "__main__":
    main()
