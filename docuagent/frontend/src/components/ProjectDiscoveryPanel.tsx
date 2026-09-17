import { useEffect, useRef, useState } from "react";
import { ExternalLink, Search, X } from "lucide-react";
import { decideDiscovery, proposeDiscovery, readDiscovery, searchDiscovery, type DiscoveryDecision, type DiscoveryRecord } from "../api/discovery";
import type { ProviderConfig } from "../api";

const button = "inline-flex min-h-9 items-center justify-center gap-2 rounded border border-ink-dim/60 px-3 text-chalk hover:bg-paper-float disabled:opacity-40";
const field = "min-h-9 w-full min-w-0 rounded border border-ink-dim/60 bg-paper px-2 text-chalk";
const choices: Record<DiscoveryDecision, string> = {
  evaluate_adoption: "评估基于此项目开发", differentiate: "规划差异化",
  independent: "独立实现", reject: "不采用",
};

export function ProjectDiscoveryPanel({ path, provider, onClose }: {
  path: string; provider: ProviderConfig; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [record, setRecord] = useState<DiscoveryRecord | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [candidate, setCandidate] = useState("");
  const [decision, setDecision] = useState<DiscoveryDecision>("evaluate_adoption");
  const [reason, setReason] = useState("");

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    element.showModal();
    return () => { element.close(); previous?.focus(); };
  }, []);
  useEffect(() => {
    let cancelled = false;
    void readDiscovery(path).then((next) => {
      if (!cancelled) { setRecord(next); setQuery(next.query ?? ""); }
    }).catch((cause: Error) => { if (!cancelled) setError(cause.message); });
    return () => { cancelled = true; };
  }, [path]);

  const run = async (action: () => Promise<DiscoveryRecord>) => {
    setBusy(true); setError("");
    try {
      const next = await action();
      if (next.id !== record?.id) { setCandidate(""); setReason(""); }
      setRecord(next); setQuery(next.query ?? "");
    }
    catch (cause) { setError((cause as Error).message); }
    finally { setBusy(false); }
  };
  const approvedQueryUnchanged = record?.status === "proposed" && query === record.query;

  return <dialog ref={dialog} onCancel={onClose} aria-labelledby="discovery-title"
    className="m-auto max-h-[85vh] w-[min(46rem,calc(100vw-2rem))] overflow-y-auto rounded-lg border border-ink-dim bg-paper-raise p-5 font-body text-sm text-chalk shadow-xl backdrop:bg-black/60">
    <header className="flex items-center justify-between gap-3">
      <h2 id="discovery-title" className="text-base font-semibold">项目整体开源选型</h2>
      <button type="button" aria-label="关闭开源选型" title="关闭" onClick={onClose} className="flex h-9 w-9 shrink-0 items-center justify-center"><X size={18} /></button>
    </header>
    {error && <p role="alert" className="mt-3 break-words text-vermilion">{error}</p>}
    <form className="mt-4 space-y-3" onSubmit={(event) => { event.preventDefault(); void run(() => proposeDiscovery(path, provider, query.trim() || undefined)); }}>
      <label className="block" htmlFor="discovery-query">公开查询词</label>
      <input id="discovery-query" className={field} value={query} maxLength={200} disabled={busy}
        onChange={(event) => setQuery(event.target.value)} autoComplete="off" />
      <div className="flex flex-wrap gap-2">
        <button type="submit" disabled={busy} className={button}>{query.trim() ? "准备检索申请" : "AI 生成检索申请"}</button>
        {approvedQueryUnchanged && <>
          <button type="button" disabled={busy} className={button} onClick={() => void run(() => searchDiscovery(path, record.id!))}><Search size={16} />同意发送到 GitHub</button>
          <button type="button" disabled={busy} className={button} onClick={() => void run(() => decideDiscovery(path, record.id!, "skip"))}>暂不检索</button>
        </>}
      </div>
    </form>
    {busy && <p role="status" className="mt-3">处理中...</p>}
    {record?.status === "skipped" && <p role="status" className="mt-3">本次检索已跳过。</p>}
    {record?.status === "searched" && <section className="mt-5 border-t border-ink-dim/40 pt-4" aria-label="候选项目">
      {record.incomplete && <p className="mb-3 text-amber">GitHub 返回了部分搜索结果。</p>}
      {record.candidates.length === 0 && <p>未找到候选项目，可以修改查询词或继续独立实现。</p>}
      <ul className="divide-y divide-ink-dim/40">
        {record.candidates.map((item) => {
          const saved = record.decisions.find((entry) => entry.candidate === item.name);
          return <li key={item.name} className="py-3">
            <a href={item.url} target="_blank" rel="noopener noreferrer" className="inline-flex max-w-full items-center gap-2 break-all text-sky underline"><span>{item.name}</span><ExternalLink size={14} className="shrink-0" /></a>
            <p className="mt-1 break-words">{item.description || "暂无项目说明"}</p>
            <p className="mt-2 text-xs text-chalk-dim">许可：{item.license === "unknown" ? "未确认" : item.license} · 功能覆盖待核验{item.archived ? " · 已归档" : ""}</p>
            {saved && <p className="mt-2 break-words text-xs">{choices[saved.decision]}：{saved.reason}</p>}
          </li>;
        })}
      </ul>
      {record.candidates.length > 0 && <form className="mt-4 space-y-3 border-t border-ink-dim/40 pt-4" onSubmit={(event) => {
        event.preventDefault(); void run(() => decideDiscovery(path, record.id!, decision, candidate, reason));
      }}>
        <label className="block" htmlFor="discovery-candidate">候选项目</label>
        <select id="discovery-candidate" className={field} value={candidate} onChange={(event) => setCandidate(event.target.value)} required disabled={busy}>
          <option value="">选择项目</option>{record.candidates.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
        </select>
        <label className="block" htmlFor="discovery-decision">下一步</label>
        <select id="discovery-decision" className={field} value={decision} onChange={(event) => setDecision(event.target.value as DiscoveryDecision)} disabled={busy}>
          {Object.entries(choices).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
        <label className="block" htmlFor="discovery-reason">选择理由与需求差异</label>
        <textarea id="discovery-reason" className={`${field} min-h-20 py-2`} value={reason} onChange={(event) => setReason(event.target.value)} required maxLength={2000} disabled={busy} />
        <button type="submit" className={button} disabled={busy || !candidate || !reason.trim()}>保存决定</button>
      </form>}
    </section>}
  </dialog>;
}
