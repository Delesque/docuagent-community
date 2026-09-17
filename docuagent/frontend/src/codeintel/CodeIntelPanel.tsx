// Code-intelligence panel. Proves the end-to-end loop (backend
// /api/codeintel/* -> service -> built-in Python index or optional LSP) from the
// UI. Works out of the box on Python projects via the zero-dependency indexer;
// an installed language server upgrades it to rename + live diagnostics.
//
// P3: when an architecture module is focused (double-click its node), the panel
// projects that module's public API from the derived symbol_index. Clicking a
// symbol opens the file in the user's OWN editor (their VSCode) via a file://
// deep link — DocuAgent deliberately does NOT embed an editor for human writing.

import { useCallback, useEffect, useState } from "react";
import {
  codeIntelCapabilities,
  codeIntelGoto,
  codeIntelReferences,
  codeIntelSearch,
  codeIntelSymbols,
  type CodeIntelCapabilities,
  type CodeIntelLocation,
  type CodeIntelSymbol,
  type CodeIntelSymbolRecord,
  type ModuleProjection,
} from "./api";
import type { GraphModule } from "../graph/types";

interface CodeIntelPanelProps {
  path: string;
  onClose: () => void;
  /** The architecture module focused from the graph (double-click). Null = plain
   *  diagnostic mode. */
  module?: GraphModule | null;
  /** Pre-fetched per-module projection (public API + status), keyed by module id. */
  moduleProjection?: Record<string, ModuleProjection> | null;
  /** Count of public symbols in source that belong to no architecture module. */
  orphanCount?: number | null;
}

const panelStyle: React.CSSProperties = {
  position: "fixed",
  top: "4rem",
  right: "1rem",
  width: "26rem",
  maxHeight: "calc(100vh - 6rem)",
  overflow: "auto",
  background: "#11161d",
  color: "#e8eef5",
  border: "1px solid #2a3340",
  borderRadius: "0.5rem",
  padding: "0.75rem",
  zIndex: 50,
  fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace",
  fontSize: "12px",
};

function providerLabel(provider: string): string {
  if (provider.includes("python-ast")) return "内置 Python 索引（零依赖，开箱即用）";
  if (provider.includes("lsp")) return "LSP 语言服务器";
  return "无";
}

const kindColor = (kind: string) =>
  kind === "class" ? "#e0c07e" : kind === "constant" ? "#c0a0ff" : "#9fd0ff";

/** Build a file:// deep link so the user can open the symbol in their own
 *  editor (their VSCode). DocuAgent never renders an editor for writing. */
function toFileUrl(root: string, file: string, line1: number): string {
  const base = "file://" + root.replace(/\\/g, "/").replace(/\/+$/, "");
  return `${base}/${file.replace(/^\/+/, "")}#L${line1}`;
}

export function CodeIntelPanel({
  path,
  onClose,
  module = null,
  moduleProjection = null,
  orphanCount = null,
}: CodeIntelPanelProps) {
  const [caps, setCaps] = useState<CodeIntelCapabilities | null>(null);
  const [file, setFile] = useState("docuagent/codeintel/service.py");
  const [line, setLine] = useState(1);
  const [character, setCharacter] = useState(1);
  const [symbols, setSymbols] = useState<CodeIntelSymbol[]>([]);
  const [locations, setLocations] = useState<CodeIntelLocation[]>([]);
  const [mode, setMode] = useState<"symbols" | "goto" | "references">("symbols");
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<CodeIntelSymbolRecord[]>([]);

  useEffect(() => {
    let active = true;
    codeIntelCapabilities(path)
      .then((c) => {
        if (active) setCaps(c);
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      active = false;
    };
  }, [path]);

  // Auto-load the symbol list for the prefilled file so the panel is useful
  // the moment it opens — no external dependency required.
  useEffect(() => {
    let active = true;
    codeIntelSymbols(path, file)
      .then((s) => {
        if (active) {
          setSymbols(s);
          setMode("symbols");
        }
      })
      .catch((e: unknown) => {
        if (active) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      active = false;
    };
  }, [path, file]);

  const runGoto = useCallback(() => {
    setError(null);
    setMode("goto");
    codeIntelGoto(path, file, line - 1, character - 1)
      .then(setLocations)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [path, file, line, character]);

  const runReferences = useCallback(() => {
    setError(null);
    setMode("references");
    codeIntelReferences(path, file, line - 1, character - 1)
      .then(setLocations)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [path, file, line, character]);

  // symbol_index search — demonstrates the derived single source of truth.
  const runSearch = useCallback(() => {
    setError(null);
    const q = searchQuery.trim();
    if (!q) return;
    codeIntelSearch(path, q, 30)
      .then(setSearchResults)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, [path, searchQuery]);

  const moduleProj: ModuleProjection | null = module
    ? moduleProjection?.[module.id] ?? null
    : null;
  const publicApi = moduleProj?.public_api ?? [];

  return (
    <div style={panelStyle}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "0.5rem",
        }}
      >
        <strong>代码智能</strong>
        <button
          type="button"
          onClick={onClose}
          style={{ background: "transparent", color: "#9fb0c0", border: "none", cursor: "pointer" }}
        >
          ✕
        </button>
      </div>

      {caps ? (
        caps.available ? (
          <p style={{ color: "#7ee0a8" }}>
            已就绪：{caps.languages.join(", ")}
            <br />
            <span style={{ color: "#9fb0c0" }}>提供方：{providerLabel(caps.provider)}</span>
          </p>
        ) : (
          <p style={{ color: "#e0c07e" }}>未检测到可用提供方。</p>
        )
      ) : (
        <p style={{ color: "#9fb0c0" }}>加载中…</p>
      )}

      {error ? <p style={{ color: "#ff9b9b" }}>错误：{error}</p> : null}

      {/* ---- P3: focused module public API -------------------------------- */}
      {module ? (
        <div style={{ marginTop: "0.5rem", borderTop: "1px solid #2a3340", paddingTop: "0.5rem" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.4rem" }}>
            <strong style={{ color: "#cfe3f2" }}>模块公开 API · {module.name}</strong>
            {moduleProj ? (
              <span
                title={moduleProj.status === "stale" ? "声明的路径已无法解析到索引代码（路径漂移）" : "路径已解析到索引代码"}
                style={{
                  fontSize: "10px",
                  padding: "1px 6px",
                  borderRadius: "999px",
                  color: moduleProj.status === "stale" ? "#ffb3ff" : "#7ee0a8",
                  border: `1px solid ${moduleProj.status === "stale" ? "#7a4d7a" : "#2f6b46"}`,
                }}
              >
                {moduleProj.status === "stale" ? "代码未解析" : "已索引"}
              </span>
            ) : (
              <span style={{ color: "#9fb0c0", fontSize: "10px" }}>加载中…</span>
            )}
          </div>
          <p style={{ color: "#6b7785", margin: "0.25rem 0" }}>
            双击架构图中的模块节点可切换此视图。点符号在<strong style={{ color: "#9fb0c0" }}>你的编辑器（如 VSCode）</strong>中打开。
          </p>
          <ul style={{ paddingLeft: "1rem", margin: "0.25rem 0 0", maxHeight: "12rem", overflow: "auto" }}>
            {publicApi.length === 0 ? (
              <li style={{ color: "#6b7785" }}>
                {moduleProj ? "该模块未索引到公开符号（可能路径漂移或非 Python 文件）。" : "加载中…"}
              </li>
            ) : (
              publicApi.map((r, i) => (
                <li key={i} style={{ marginBottom: "2px" }}>
                  <a
                    href={toFileUrl(path, r.file, r.line + 1)}
                    title={`${r.signature || r.name}\n点开在默认编辑器（如 VSCode）中打开`}
                    style={{ color: kindColor(r.kind), textDecoration: "none" }}
                  >
                    {r.kind} · {r.name}{" "}
                    <span style={{ color: "#6b7785" }}>({r.line + 1})</span>
                  </a>
                </li>
              ))
            )}
          </ul>
        </div>
      ) : null}

      {/* ---- symbol_index search (always available) ----------------------- */}
      <div style={{ marginTop: "0.5rem" }}>
        <p style={{ color: "#9fb0c0", margin: "0.25rem 0" }}>符号索引搜索（symbol_index）：</p>
        <div style={{ display: "flex", gap: "0.35rem" }}>
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") runSearch();
            }}
            placeholder="函数 / 类 / 常量名…"
            style={{ flex: 1, background: "#0c1117", color: "#e8eef5", border: "1px solid #2a3340", borderRadius: "4px", padding: "4px 6px" }}
          />
          <button
            type="button"
            onClick={runSearch}
            style={{ background: "#3a7a4f", color: "#fff", border: "none", borderRadius: "4px", padding: "6px", cursor: "pointer" }}
          >
            搜索
          </button>
        </div>
        <ul style={{ paddingLeft: "1rem", margin: "0.35rem 0 0" }}>
          {searchResults.slice(0, 20).map((r, i) => (
            <li key={i}>
              <a
                href={toFileUrl(path, r.file, r.line + 1)}
                title="点开在默认编辑器（如 VSCode）中打开"
                style={{ color: kindColor(r.kind), textDecoration: "none" }}
              >
                {r.kind} · {r.name} <span style={{ color: "#6b7785" }}>@ {r.file}:{r.line + 1}</span>
                {r.is_public ? <span style={{ color: "#7ee0a8" }}> ★</span> : null}
              </a>
            </li>
          ))}
        </ul>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: "0.35rem", marginTop: "0.5rem" }}>
        <input
          value={file}
          onChange={(e) => setFile(e.target.value)}
          placeholder="文件路径（相对项目根）"
          style={{ background: "#0c1117", color: "#e8eef5", border: "1px solid #2a3340", borderRadius: "4px", padding: "4px 6px" }}
        />
        <div style={{ display: "flex", gap: "0.35rem" }}>
          <label style={{ flex: 1 }}>
            行
            <input
              type="number"
              value={line}
              onChange={(e) => setLine(Number(e.target.value))}
              style={{ width: "100%", background: "#0c1117", color: "#e8eef5", border: "1px solid #2a3340", borderRadius: "4px", padding: "4px 6px" }}
            />
          </label>
          <label style={{ flex: 1 }}>
            列
            <input
              type="number"
              value={character}
              onChange={(e) => setCharacter(Number(e.target.value))}
              style={{ width: "100%", background: "#0c1117", color: "#e8eef5", border: "1px solid #2a3340", borderRadius: "4px", padding: "4px 6px" }}
            />
          </label>
        </div>
        <div style={{ display: "flex", gap: "0.35rem" }}>
          <button
            type="button"
            onClick={runGoto}
            style={{ flex: 1, background: "#2a6df0", color: "#fff", border: "none", borderRadius: "4px", padding: "6px", cursor: "pointer" }}
          >
            跳转到定义
          </button>
          <button
            type="button"
            onClick={runReferences}
            style={{ flex: 1, background: "#2a6df0", color: "#fff", border: "none", borderRadius: "4px", padding: "6px", cursor: "pointer" }}
          >
            查找引用
          </button>
        </div>
      </div>

      <div style={{ marginTop: "0.5rem" }}>
        {mode === "symbols" ? (
          <>
            <p style={{ color: "#9fb0c0", margin: "0.25rem 0" }}>符号（{symbols.length}）：</p>
            <ul style={{ paddingLeft: "1rem", margin: 0 }}>
              {symbols.map((s, i) => (
                <li key={i} style={{ color: kindColor(s.kind) }}>
                  {s.kind} · {s.name} <span style={{ color: "#6b7785" }}>({s.line + 1}:{s.character + 1})</span>
                </li>
              ))}
            </ul>
          </>
        ) : (
          <>
            <p style={{ color: "#9fb0c0", margin: "0.25rem 0" }}>
              {mode === "goto" ? "跳转到定义" : "查找引用"}（{locations.length}）：
            </p>
            <ul style={{ paddingLeft: "1rem", margin: 0 }}>
              {locations.map((loc, i) => (
                <li key={i}>
                  {loc.uri.split("/").pop()}:{loc.line + 1}:{loc.character + 1}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>

      {orphanCount != null ? (
        <p style={{ color: "#9fb0c0", marginTop: "0.5rem" }}>
          代码索引中有 <span style={{ color: "#ffb3ff" }}>{orphanCount}</span> 个公开符号未归档到任何架构模块（orphan）。
        </p>
      ) : null}

      <p style={{ color: "#6b7785", marginTop: "0.5rem" }}>
        默认用零依赖 Python 索引；安装 pylsp/pyright 后自动升级为 LSP（含重命名与实时诊断）。
      </p>
    </div>
  );
}
