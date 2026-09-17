import { useEffect, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import type { editor } from "monaco-editor";
import {
  clearPatchDraft,
  isPatchDraftStale,
  upsertPatchDraft,
  type PatchDraftMap,
} from "./codeViewerDraft";

interface CodeViewerProps {
  files: Array<{ path: string; content: string }>;
  activeFile?: string;
  onFileSelect?: (path: string) => void;
  onSave?: (
    path: string,
    content: string,
    baseAfter: string,
  ) => boolean | Promise<boolean>;
  /** When set, select this file and reveal its line (seq forces re-fire). */
  jumpRequest?: { path: string; line: number; seq: number } | null;
}

const LANGUAGE_MAP: Record<string, string> = {
  py: "python",
  ts: "typescript",
  tsx: "typescript",
  js: "javascript",
  jsx: "javascript",
  json: "json",
  md: "markdown",
  yaml: "yaml",
  yml: "yaml",
  toml: "toml",
  html: "html",
  css: "css",
  sql: "sql",
  sh: "shell",
  txt: "plaintext",
};

function detectLanguage(filePath: string): string {
  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  return LANGUAGE_MAP[ext] || "plaintext";
}

export function CodeViewer({
  files,
  activeFile,
  onFileSelect,
  onSave,
  jumpRequest,
}: CodeViewerProps) {
  const [selectedPath, setSelectedPath] = useState<string>(
    activeFile || files[0]?.path || ""
  );
  const [drafts, setDrafts] = useState<PatchDraftMap>({});
  const [saving, setSaving] = useState(false);
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null);

  useEffect(() => {
    if (activeFile && activeFile !== selectedPath) {
      setSelectedPath(activeFile);
    }
  }, [activeFile, selectedPath]);

  const currentFile = files.find((f) => f.path === selectedPath);
  const language = currentFile ? detectLanguage(currentFile.path) : "plaintext";
  const currentDraft = currentFile ? drafts[currentFile.path] : undefined;
  const currentContent = currentFile
    ? (currentDraft?.content ?? currentFile.content)
    : "";
  const isDirty =
    currentFile !== undefined &&
    currentDraft !== undefined &&
    currentDraft.content !== currentFile.content;
  const isStaleDraft =
    currentFile !== undefined &&
    isPatchDraftStale(currentFile.content, currentDraft);

  const handleFileClick = (path: string) => {
    setSelectedPath(path);
    onFileSelect?.(path);
  };

  const handleEditorDidMount = (
    editor: editor.IStandaloneCodeEditor,
  ) => {
    editorRef.current = editor;

    // Define custom theme matching terminal colors
    editor.updateOptions({
      theme: "docuagent-dark",
    });

  };

  const handleSave = async () => {
    if (!onSave || !currentFile || isStaleDraft) return;
    const baseAfter = currentDraft?.base ?? currentFile.content;
    setSaving(true);
    try {
      const saved = await onSave(currentFile.path, currentContent, baseAfter);
      if (saved !== false) {
        setDrafts((prev) => clearPatchDraft(prev, currentFile.path));
      }
    } finally {
      setSaving(false);
    }
  };

  const discardDraft = () => {
    if (!currentFile) return;
    setDrafts((prev) => clearPatchDraft(prev, currentFile.path));
  };

  const handleSaveRef = useRef(handleSave);
  handleSaveRef.current = handleSave;

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void handleSaveRef.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useEffect(() => {
    if (!jumpRequest) return;
    setSelectedPath(jumpRequest.path);
    const timer = window.setTimeout(() => {
      const editor = editorRef.current;
      if (editor) {
        const line = Math.max(1, jumpRequest.line);
        editor.setPosition({ lineNumber: line, column: 1 });
        editor.revealLineInCenter(line);
        editor.focus();
      }
    }, 60);
    return () => window.clearTimeout(timer);
  }, [jumpRequest]);

  useEffect(() => {
    // Register custom theme on mount. The monaco chunk can also 404 after a rebuild
    // swaps its hash; a missing theme is cosmetic, so degrade silently rather than
    // leaving an unhandled rejection.
    import("monaco-editor")
      .then((monaco) => {
        monaco.editor.defineTheme("docuagent-dark", {
          base: "vs-dark",
          inherit: true,
          rules: [
            { token: "comment", foreground: "65A67F" },
            { token: "string", foreground: "6CFFA8" },
            { token: "keyword", foreground: "38BDF8" },
            { token: "number", foreground: "E9A568" },
          ],
          colors: {
            "editor.background": "#0A0D12",
            "editor.foreground": "#D4D9E0",
            "editor.lineHighlightBackground": "#161D2B",
            "editorLineNumber.foreground": "#4A5568",
            "editorCursor.foreground": "#6CFFA8",
            "editor.selectionBackground": "#1E2636",
          },
        });
      })
      .catch(() => undefined);
  }, []);

  if (files.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="font-mono text-[11px] text-chalk-faint">无可查看的文件</p>
      </div>
    );
  }

  return (
    <div className="flex h-full overflow-hidden rounded-none border border-ink/60 bg-paper/95 font-mono">
      {/* File list sidebar */}
      <div className="w-64 shrink-0 overflow-y-auto border-r border-ink/40 bg-paper-raise/50">
        <div className="border-b border-ink/40 px-3 py-2">
          <p className="text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
            文件列表
          </p>
        </div>
        <div className="space-y-0.5 p-2">
          {files.map((file) => {
            const isActive = file.path === selectedPath;
            const hasDraft = drafts[file.path] !== undefined;
            return (
              <button
                key={file.path}
                type="button"
                onClick={() => handleFileClick(file.path)}
                className={`w-full truncate rounded-none px-2 py-1.5 text-left text-[11px] transition-colors ${
                  isActive
                    ? "bg-ink/20 text-chalk"
                    : "text-chalk-dim hover:bg-ink/10 hover:text-chalk"
                }`}
                title={file.path}
              >
                {hasDraft ? "● " : ""}
                {file.path}
              </button>
            );
          })}
        </div>
      </div>

      {/* Monaco Editor */}
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-3 border-b border-ink/40 bg-paper-raise px-4 py-2.5">
          <span className="text-ink">&gt;</span>
          <span className="truncate text-[12px] text-chalk">{selectedPath}</span>
          {isDirty ? (
            <span className="text-[10px] text-amber">未保存</span>
          ) : null}
          {isStaleDraft ? (
            <span className="text-[10px] text-vermilion">草稿已过期</span>
          ) : null}
          <span className="ml-auto text-[10px] text-chalk-faint">{language}</span>
          {isStaleDraft ? (
            <button
              type="button"
              onClick={discardDraft}
              className="rounded-none border border-vermilion/50 px-2 py-0.5 text-[10px] text-vermilion transition-colors hover:bg-vermilion/10"
            >
              放弃草稿
            </button>
          ) : null}
          {onSave ? (
            <button
              type="button"
              onClick={handleSave}
              disabled={saving || !isDirty || isStaleDraft}
              className="rounded-none border border-emerald/50 px-2 py-0.5 text-[10px] text-emerald transition-colors hover:bg-emerald/10 disabled:cursor-not-allowed disabled:opacity-40"
            >
              {saving ? "保存中…" : "保存 (Ctrl+S)"}
            </button>
          ) : null}
        </div>
        {isStaleDraft ? (
          <div className="border-b border-vermilion/40 bg-vermilion/10 px-4 py-2 text-[10px] text-vermilion">
            补丁已在别处更新，当前草稿基于旧版本。为避免覆盖新内容，保存已禁用；可放弃草稿查看最新内容。
          </div>
        ) : null}
        <div className="flex-1 overflow-hidden">
          <Editor
            height="100%"
            language={language}
            value={currentContent}
            theme="docuagent-dark"
            onMount={handleEditorDidMount}
            onChange={(value) =>
              currentFile &&
              setDrafts((prev) =>
                upsertPatchDraft(
                  prev,
                  currentFile.path,
                  currentFile.content,
                  value ?? "",
                ),
              )
            }
            options={{
              readOnly: isStaleDraft,
              minimap: { enabled: false },
              scrollBeyondLastLine: false,
              fontSize: 13,
              lineNumbers: "on",
              wordWrap: "on",
              automaticLayout: true,
              scrollbar: {
                vertical: "visible",
                horizontal: "visible",
              },
            }}
          />
        </div>
      </div>
    </div>
  );
}
