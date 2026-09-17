"""Capture a DOM layout tree from a real page using a local headless browser.

The capture path talks to the browser through the Chrome DevTools Protocol over a
minimal WebSocket client, so the runtime keeps the project's zero-third-party
dependency baseline. The resulting ``dom_layout_snapshot`` is a compact tree of
elements with stable selectors, geometry, computed layout properties, and frame
state, stored next to the existing screenshot previews.
"""

from __future__ import annotations

import base64
import contextlib
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Iterator

from core import WorkspaceError, slugify
from workspace import atomic_write_json, managed_path, read_json, utc_now

LAYOUT_TIMEOUT = 90
WINDOW_SIZE = "1280,800"
DEFAULT_MAX_NODES = 800
DEFAULT_MAX_TEXT = 120

LAYOUT_SCHEMA_VERSION = 1

# Keep the injected script readable while still passing it through CDP as one
# expression. Chrome's headless DevTools endpoint supports async IIFEs.
_LAYOUT_CAPTURE_SCRIPT = r"""
(async () => {
  const MAX_NODES = Number(%(max_nodes)d) || 800;
  const MAX_TEXT = Number(%(max_text)d) || 120;
  const visited = new Set();
  let nextId = 0;
  let truncated = false;
  let pruned = 0;

  const round2 = (value) => Math.round(value * 100) / 100;
  const num = (value) => {
    const parsed = parseFloat(value);
    return Number.isFinite(parsed) ? parsed : 0;
  };
  const cleanText = (value) => (value || "").replace(/\s+/g, " ").trim().slice(0, MAX_TEXT);
  const directText = (el) => {
    let text = "";
    for (const child of el.childNodes) {
      if (child.nodeType === Node.TEXT_NODE) text += " " + (child.textContent || "");
    }
    return cleanText(text);
  };

  const cssEscape = (value) => {
    if (typeof CSS !== "undefined" && CSS.escape) return CSS.escape(String(value));
    return String(value).replace(/["\\#.\[\]:]/g, "\\$&");
  };

  const uniqueSelector = (el) => {
    if (el.id) {
      const idSelector = "#" + cssEscape(el.id);
      try {
        if (document.querySelectorAll(idSelector).length === 1) return idSelector;
      } catch (_) {}
    }
    const attributeCandidates = [
      ["data-testid", "data-testid"],
      ["data-test-id", "data-test-id"],
      ["data-cy", "data-cy"],
      ["data-component", "data-component"],
      ["name", "name"],
      ["aria-label", "aria-label"],
    ];
    for (const [attr, selector] of attributeCandidates) {
      const value = el.getAttribute(attr);
      if (!value) continue;
      const candidate = `[${selector}=${JSON.stringify(value)}]`;
      try {
        if (document.querySelectorAll(candidate).length === 1) return candidate;
      } catch (_) {}
    }
    const parts = [];
    let node = el;
    while (node && node.nodeType === Node.ELEMENT_NODE && node !== document.documentElement) {
      let part = node.tagName.toLowerCase();
      if (node.id) {
        part += "#" + cssEscape(node.id);
        parts.unshift(part);
        break;
      }
      if (node.classList && node.classList.length) {
        const classes = Array.from(node.classList).slice(0, 3).map(cssEscape).join(".");
        if (classes) part += "." + classes;
      }
      const parent = node.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter(
          (sibling) => sibling.tagName === node.tagName
        );
        if (siblings.length > 1) {
          part += `:nth-of-type(${siblings.indexOf(node) + 1})`;
        }
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(" > ");
  };

  const computedLayout = (el) => {
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const gridColumn = style.gridColumnStart && style.gridColumnEnd
      ? `${style.gridColumnStart} / ${style.gridColumnEnd}`
      : style.gridColumnStart || "";
    const gridRow = style.gridRowStart && style.gridRowEnd
      ? `${style.gridRowStart} / ${style.gridRowEnd}`
      : style.gridRowStart || "";
    return {
      display: style.display,
      position: style.position,
      direction: style.direction,
      visibility: style.visibility,
      opacity: num(style.opacity),
      box_sizing: style.boxSizing,
      min_width: style.minWidth,
      max_width: style.maxWidth,
      min_height: style.minHeight,
      max_height: style.maxHeight,
      flex_direction: style.flexDirection,
      flex_wrap: style.flexWrap,
      align_items: style.alignItems,
      align_content: style.alignContent,
      justify_content: style.justifyContent,
      gap: style.gap,
      row_gap: style.rowGap,
      column_gap: style.columnGap,
      padding: [
        num(style.paddingTop),
        num(style.paddingRight),
        num(style.paddingBottom),
        num(style.paddingLeft),
      ],
      margin: [
        num(style.marginTop),
        num(style.marginRight),
        num(style.marginBottom),
        num(style.marginLeft),
      ],
      grid_template_columns: style.gridTemplateColumns,
      grid_template_rows: style.gridTemplateRows,
      grid_auto_flow: style.gridAutoFlow,
      grid_column: gridColumn,
      grid_row: gridRow,
      flex_grow: style.flexGrow,
      flex_shrink: style.flexShrink,
      flex_basis: style.flexBasis,
      order: style.order,
      align_self: style.alignSelf,
      overflow: style.overflowX === "visible" && style.overflowY === "visible"
        ? ""
        : [style.overflowX, style.overflowY].filter(Boolean).join(" "),
      z_index: style.zIndex === "auto" ? null : parseInt(style.zIndex, 10) || 0,
      width: round2(rect.width),
      height: round2(rect.height),
    };
  };

  const elementRole = (el) => {
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    switch (el.tagName) {
      case "A": return "link";
      case "BUTTON": return "button";
      case "INPUT": return "input";
      case "TEXTAREA": return "textbox";
      case "SELECT": return "combobox";
      case "IMG": return "img";
      case "NAV": return "navigation";
      case "MAIN": return "main";
      case "HEADER": return "banner";
      case "FOOTER": return "contentinfo";
      default: return "";
    }
  };

  const sourceHint = (el) => {
    for (const attr of ["data-source", "data-file", "data-component", "data-module"]) {
      const value = el.getAttribute(attr);
      if (value) return value;
    }
    if (el.classList && el.classList.length) {
      return Array.from(el.classList).slice(0, 3).join(".");
    }
    return "";
  };

  const frameState = (el) => {
    if (el.tagName !== "IFRAME") return null;
    let accessible = false;
    try {
      accessible = Boolean(el.contentDocument);
    } catch (_) {}
    return {
      state: accessible ? "same-origin" : "opaque",
      src: el.getAttribute("src") || "",
      accessible,
    };
  };

  const visibleElement = (el) => {
    if (el.tagName === "HTML" || el.tagName === "BODY") return true;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.display !== "none"
      && style.visibility !== "hidden"
      && num(style.opacity) > 0
      && rect.width > 0
      && rect.height > 0;
  };

  const skipTag = (el) => {
    const skipped = new Set(["SCRIPT", "STYLE", "LINK", "META", "NOSCRIPT", "TEMPLATE"]);
    return skipped.has(el.tagName);
  };

  const visit = (el) => {
    if (!el || el.nodeType !== Node.ELEMENT_NODE || visited.has(el)) return null;
    if (skipTag(el)) {
      pruned += 1;
      return null;
    }
    if (!visibleElement(el)) {
      pruned += 1;
      return null;
    }
    if (nextId >= MAX_NODES) {
      truncated = true;
      return null;
    }
    visited.add(el);
    const id = "n" + nextId;
    nextId += 1;
    const rect = el.getBoundingClientRect();
    const layout = computedLayout(el);
    const frame = frameState(el);
    const children = [];
    if (frame && frame.state === "same-origin") {
      const inner = el.contentDocument && el.contentDocument.documentElement;
      if (inner) {
        const child = visit(inner);
        if (child) children.push(child);
      }
    } else {
      for (const child of el.children) {
        const result = visit(child);
        if (result) children.push(result);
      }
    }
    const node = {
      id,
      tag: el.tagName.toLowerCase(),
      selector: uniqueSelector(el),
      role: elementRole(el),
      text: directText(el),
      value: el.value !== undefined && typeof el.value === "string" ? el.value.slice(0, MAX_TEXT) : "",
      aria_label: el.getAttribute("aria-label") || "",
      source_hint: sourceHint(el),
      rect: {
        x: round2(rect.x),
        y: round2(rect.y),
        width: round2(rect.width),
        height: round2(rect.height),
      },
      layout,
      frame,
      children,
    };
    return node;
  };

  if (document.readyState !== "complete" || document.URL === "about:blank") {
    await new Promise((resolve) => {
      const timer = setInterval(() => {
        if (document.readyState === "complete" && document.URL !== "about:blank") {
          clearInterval(timer);
          resolve();
        }
      }, 50);
    });
  }
  await new Promise((resolve) => requestAnimationFrame(resolve));

  const root = visit(document.documentElement);
  return {
    schema_version: %(schema_version)d,
    captured_at: new Date().toISOString(),
    url: document.URL,
    title: document.title || "",
    viewport: {
      width: window.innerWidth,
      height: window.innerHeight,
    },
    root,
    meta: {
      node_count: nextId,
      pruned,
      truncated,
    },
  };
})()
"""


def layout_path(project_root: Path, module_id: str) -> Path:
    safe = slugify(module_id) or "layout"
    return managed_path(project_root, "layouts", f"{safe}.json")


def _browser_candidates() -> list[str]:
    known = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    candidates = []
    for name in ("msedge", "chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for path in known:
        if Path(path).exists():
            candidates.append(path)
    return candidates


def _wait_for_devtools_port(user_data_dir: Path, process: subprocess.Popen[bytes], timeout: int = 30) -> int:
    deadline = time.monotonic() + timeout
    port_file = user_data_dir / "DevToolsActivePort"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise WorkspaceError("浏览器提前退出，无法采集布局。")
        if port_file.exists():
            try:
                lines = port_file.read_text(encoding="utf-8").splitlines()
                if lines:
                    return int(lines[0].strip())
            except (OSError, ValueError):
                pass
        time.sleep(0.1)
    raise WorkspaceError("浏览器 DevTools 端口未就绪。")


def _http_json(port: int, path: str) -> Any:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url, timeout=3) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise WorkspaceError(f"无法连接浏览器 DevTools：{exc}") from exc


def _page_websocket_url(port: int, timeout: int = 30) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        targets = _http_json(port, "/json/list")
        if isinstance(targets, list):
            pages = [target for target in targets if target.get("type") == "page"]
            if pages:
                ws = pages[0].get("webSocketDebuggerUrl")
                if ws:
                    return str(ws)
        time.sleep(0.2)
    raise WorkspaceError("浏览器没有可用的页面目标。")


def _ws_connect(ws_url: str) -> socket.socket:
    parsed = urllib.parse.urlsplit(ws_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    sock = socket.create_connection((host, port), timeout=15)
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {parsed.path or '/'}{'?' + parsed.query if parsed.query else ''} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = bytearray()
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            raise WorkspaceError("WebSocket 握手失败：连接被关闭。")
        response.extend(chunk)
    if not response.startswith(b"HTTP/1.1 101"):
        raise WorkspaceError("WebSocket 握手失败：浏览器未返回 101。")
    return sock


def _ws_send_frame(sock: socket.socket, opcode: int, payload: bytes = b"") -> None:
    first = 0x80 | opcode
    length = len(payload)
    header = bytearray([first])
    if length < 126:
        header.append(0x80 | length)
    elif length < 65536:
        header.append(0x80 | 126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack(">Q", length))
    mask = os.urandom(4)
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(header + masked)


def _ws_recv_frame(sock: socket.socket) -> tuple[bool, int, bytes]:
    head = sock.recv(2)
    if len(head) != 2:
        raise WorkspaceError("WebSocket 连接被关闭。")
    fin = bool(head[0] & 0x80)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        raw = sock.recv(2)
        length = struct.unpack(">H", raw)[0]
    elif length == 127:
        raw = sock.recv(8)
        length = struct.unpack(">Q", raw)[0]
    mask = b""
    if masked:
        mask = sock.recv(4)
    payload = bytearray()
    while len(payload) < length:
        chunk = sock.recv(min(65536, length - len(payload)))
        if not chunk:
            raise WorkspaceError("WebSocket 数据不完整。")
        payload.extend(chunk)
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return fin, opcode, bytes(payload)


def _ws_send_text(sock: socket.socket, text: str) -> None:
    _ws_send_frame(sock, 0x1, text.encode("utf-8"))


def _ws_recv_message(sock: socket.socket) -> bytes | None:
    chunks: list[bytes] = []
    first_opcode: int | None = None
    while True:
        fin, opcode, payload = _ws_recv_frame(sock)
        if opcode == 0x8:
            return None
        if opcode == 0x9:
            _ws_send_frame(sock, 0xA, payload)
            continue
        if opcode == 0xA:
            continue
        if opcode in (0x1, 0x2):
            first_opcode = opcode
            chunks = [payload]
        elif opcode == 0x0 and chunks:
            chunks.append(payload)
        if fin and first_opcode is not None:
            return b"".join(chunks)


def _cdp_call(
    sock: socket.socket,
    method: str,
    params: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    message_id = int(time.time() * 1000) % 1000000
    payload = json.dumps({
        "id": message_id,
        "method": method,
        "params": params or {},
    })
    _ws_send_text(sock, payload)
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = _ws_recv_message(sock)
        if raw is None:
            raise WorkspaceError("浏览器 DevTools 连接已关闭。")
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if message.get("id") != message_id:
            continue
        if message.get("error"):
            raise WorkspaceError(f"浏览器 DevTools 调用失败：{message['error']}")
        return message.get("result", {})
    raise WorkspaceError("浏览器布局采集超时。")


def _cdp_wait_for_event(
    sock: socket.socket,
    event_name: str,
    timeout: int = 30,
) -> dict[str, Any]:
    sock.settimeout(timeout)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = _ws_recv_message(sock)
        if raw is None:
            raise WorkspaceError("浏览器 DevTools 连接已关闭。")
        try:
            message = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if message.get("method") == event_name:
            return message.get("params", {})
    raise WorkspaceError(f"浏览器未触发 {event_name} 事件。")


def _cdp_evaluate(sock: socket.socket, expression: str, timeout: int = 60) -> Any:
    result = _cdp_call(
        sock,
        "Runtime.evaluate",
        {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout=timeout,
    )
    if result.get("exceptionDetails"):
        details = result["exceptionDetails"].get("exception", {}).get("description", "")
        raise WorkspaceError(f"页面脚本执行失败：{details[:500]}")
    return result.get("result", {}).get("value")


@contextlib.contextmanager
def _browser_session() -> Iterator[int]:
    browser = next(iter(_browser_candidates()), None)
    if not browser:
        raise WorkspaceError("未找到可用的 Edge/Chrome，无法采集布局。")
    temp_dir = Path(tempfile.mkdtemp(prefix="docuagent-layout-"))
    process: subprocess.Popen[bytes] | None = None
    try:
        command = [
            browser,
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "--remote-debugging-port=0",
            "--remote-debugging-address=127.0.0.1",
            f"--user-data-dir={temp_dir}",
            f"--window-size={WINDOW_SIZE}",
            "about:blank",
        ]
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        port = _wait_for_devtools_port(temp_dir, process)
        yield port
    finally:
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        shutil.rmtree(temp_dir, ignore_errors=True)


def _run_cdp_capture(url: str, max_nodes: int, max_text: int) -> Any:
    with _browser_session() as port:
        ws_url = _page_websocket_url(port)
        with _ws_connect(ws_url) as sock:
            _cdp_call(sock, "Page.enable")
            _cdp_call(sock, "Page.navigate", {"url": url})
            _cdp_wait_for_event(sock, "Page.loadEventFired")
            expression = _LAYOUT_CAPTURE_SCRIPT % {
                "max_nodes": int(max_nodes),
                "max_text": int(max_text),
                "schema_version": LAYOUT_SCHEMA_VERSION,
            }
            return _cdp_evaluate(sock, expression)


def validate_layout_snapshot(payload: Any) -> dict[str, Any]:
    """Validate the common shape of a dom_layout_snapshot and return it."""
    if not isinstance(payload, dict):
        raise WorkspaceError("布局快照必须是 JSON 对象。")
    if payload.get("schema_version") != LAYOUT_SCHEMA_VERSION:
        raise WorkspaceError(f"不支持的布局快照版本：{payload.get('schema_version')!r}")
    if not isinstance(payload.get("root"), dict):
        raise WorkspaceError("布局快照缺少 root 节点。")
    errors: list[str] = []
    seen_ids: set[str] = set()

    def walk(node: Any, depth: int = 0) -> None:
        if depth > 80:
            errors.append("布局树超过 80 层。")
            return
        if not isinstance(node, dict):
            errors.append("布局节点必须是对象。")
            return
        node_id = str(node.get("id") or "")
        if not node_id:
            errors.append("布局节点缺少 id。")
        elif node_id in seen_ids:
            errors.append(f"布局节点 id 重复：{node_id}")
        else:
            seen_ids.add(node_id)
        rect = node.get("rect")
        if not isinstance(rect, dict):
            errors.append(f"布局节点 {node_id} 缺少 rect。")
        layout = node.get("layout")
        if not isinstance(layout, dict):
            errors.append(f"布局节点 {node_id} 缺少 layout。")
        children = node.get("children")
        if children is None:
            children = []
            node["children"] = children
        if not isinstance(children, list):
            errors.append(f"布局节点 {node_id} 的 children 不是数组。")
            return
        for child in children:
            walk(child, depth + 1)

    walk(payload.get("root"))
    if errors:
        raise WorkspaceError("布局快照无效：" + "；".join(errors[:10]))
    return payload


def normalize_layout_payload(raw: Any, module_id: str, url: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise WorkspaceError("浏览器没有返回布局快照。")
    payload = validate_layout_snapshot(raw)
    payload["schema_version"] = LAYOUT_SCHEMA_VERSION
    payload["captured_at"] = utc_now()
    payload["url"] = str(raw.get("url") or url)
    payload["module_id"] = module_id
    meta = payload.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        payload["meta"] = meta
    meta["node_count"] = int(meta.get("node_count") or 0)
    meta["pruned"] = int(meta.get("pruned") or 0)
    meta["truncated"] = bool(meta.get("truncated"))
    return payload


def element_signature(element: dict[str, Any]) -> dict[str, Any]:
    """A stable fingerprint of an element that survives selector churn."""
    rect = element.get("rect")
    if not isinstance(rect, dict):
        rect = {}

    def num(value: Any) -> int | None:
        try:
            return round(float(value))
        except (TypeError, ValueError):
            return None

    return {
        "tag": str(element.get("tag") or "").lower(),
        "role": str(element.get("role") or ""),
        "text": (str(element.get("text") or "").strip())[:80],
        "source_hint": str(element.get("source_hint") or ""),
        "width": num(rect.get("width")),
        "height": num(rect.get("height")),
    }


def _signature_score(node: dict[str, Any], signature: dict[str, Any]) -> int:
    score = 0
    if str(node.get("tag") or "").lower() == signature.get("tag"):
        score += 2
    if signature.get("role") and str(node.get("role") or "") == signature.get("role"):
        score += 1
    node_text = (str(node.get("text") or "").strip())[:80]
    if signature.get("text") and node_text == signature.get("text"):
        score += 3
    if signature.get("source_hint") and str(node.get("source_hint") or "") == signature.get("source_hint"):
        score += 3
    rect = node.get("rect")
    if isinstance(rect, dict):
        def num(value: Any) -> int | None:
            try:
                return round(float(value))
            except (TypeError, ValueError):
                return None
        width = num(rect.get("width"))
        height = num(rect.get("height"))
        if width is not None and signature.get("width") is not None and abs(width - signature["width"]) <= 2:
            score += 1
        if height is not None and signature.get("height") is not None and abs(height - signature["height"]) <= 2:
            score += 1
    return score


def find_element_by_signature(
    snapshot: dict[str, Any],
    signature: dict[str, Any],
) -> dict[str, Any] | None:
    """Re-locate an element in a snapshot by signature, ignoring its selector."""
    root = snapshot.get("root")
    if not isinstance(root, dict):
        return None
    best: dict[str, Any] | None = None
    best_score = 0

    def walk(node: dict[str, Any]) -> None:
        nonlocal best, best_score
        score = _signature_score(node, signature)
        if score > best_score:
            best_score = score
            best = node
        children = node.get("children")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    walk(child)

    walk(root)
    return best if best_score > 0 else None


def capture_layout_snapshot(
    project_root: Path,
    url: str,
    module_id: str,
    max_nodes: int = DEFAULT_MAX_NODES,
    max_text: int = DEFAULT_MAX_TEXT,
) -> dict[str, Any]:
    """Capture a DOM layout snapshot and persist it under `.docuagent/layouts/`."""
    url = str(url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise WorkspaceError("布局采集地址必须是 http/https URL。")
    if not module_id.strip():
        raise WorkspaceError("缺少模块 ID。")
    max_nodes = max(1, min(int(max_nodes), 5000))
    max_text = max(1, min(int(max_text), 500))
    target = layout_path(project_root, module_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    raw = _run_cdp_capture(url, max_nodes, max_text)
    payload = normalize_layout_payload(raw, module_id.strip(), url)
    atomic_write_json(target, payload)
    return {
        "module_id": module_id.strip(),
        "path": str(target),
        "node_count": int(payload["meta"]["node_count"]),
        "truncated": bool(payload["meta"]["truncated"]),
        "size": target.stat().st_size,
    }


def read_layout_snapshot(project_root: Path, module_id: str) -> dict[str, Any]:
    target = layout_path(project_root, module_id)
    payload = read_json(target)
    if not isinstance(payload, dict):
        raise WorkspaceError("还没有该模块的布局快照。")
    return validate_layout_snapshot(payload)


def layout_snapshot_schema() -> dict[str, Any]:
    """Return a short, human-readable schema for documentation and tests."""
    return {
        "schema_version": LAYOUT_SCHEMA_VERSION,
        "captured_at": "ISO 8601 UTC string",
        "url": "captured page URL",
        "module_id": "architecture module id",
        "viewport": {"width": "number", "height": "number"},
        "root": {
            "id": "stable node id within this snapshot",
            "tag": "lowercase tag name",
            "selector": "stable CSS selector when possible",
            "role": "ARIA/role summary",
            "text": "direct text, truncated",
            "value": "form control value, truncated",
            "source_hint": "data attribute or class hint",
            "rect": {"x": "number", "y": "number", "width": "number", "height": "number"},
            "layout": {
                "display": "computed display",
                "position": "computed position",
                "min_width": "computed min-width",
                "max_width": "computed max-width",
                "min_height": "computed min-height",
                "max_height": "computed max-height",
                "flex_direction": "row/column/...",
                "flex_wrap": "wrap/nowrap/...",
                "align_items": "computed align-items",
                "justify_content": "computed justify-content",
                "gap": "computed gap",
                "padding": "[top, right, bottom, left]",
                "margin": "[top, right, bottom, left]",
                "grid_template_columns": "computed grid template",
                "grid_template_rows": "computed grid template",
                "grid_column": "computed grid column span",
                "grid_row": "computed grid row span",
                "flex_grow": "number",
                "flex_shrink": "number",
                "order": "number",
                "align_self": "computed align-self",
                "z_index": "number or null",
                "width": "number",
                "height": "number",
            },
            "frame": "null or {state: same-origin|opaque, src}",
            "children": ["nested layout nodes"],
        },
        "meta": {
            "node_count": "number",
            "pruned": "number",
            "truncated": "boolean",
        },
    }
