/** Per-claim provenance confirmation surface (review stage).
 *
 *  The review conversation offers bulk accept ("全部确认"); this ledger is the
 *  place for the deliberate path: read each claim, confirm it, correct its text,
 *  mark it unknown, or reject it. A claim anchored to a module shows that anchor
 *  so the user knows which part of the design the decision affects.
 *
 *  Actions call the same `/api/provenance/update` route the bulk path uses; the
 *  parent owns the updated bootstrap state this panel's props derive from.
 */

import type { ProvenanceAction, ProvenanceClaim } from "../api";

export interface ProvenanceLedgerProps {
  claims: ProvenanceClaim[];
  /** Module id → display name, so an anchored claim reads in design language. */
  moduleNames: Record<string, string>;
  busyId: string | null;
  onAction: (claimId: string, action: ProvenanceAction, text?: string) => void;
  /** Accept every pending claim at once. Omitting it hides the shortcut. */
  onAcceptAll?: () => void;
  onClose: () => void;
}

/** Pending first, then decided — the reading order matches the decision order. */
const PENDING_SOURCES: ProvenanceClaim["source"][] = ["inferred", "recommended", "unknown"];

export function ProvenanceLedger({
  claims,
  moduleNames,
  busyId,
  onAction,
  onAcceptAll,
  onClose,
}: ProvenanceLedgerProps) {
  const runModify = (claim: ProvenanceClaim) => {
    const value = window.prompt("修改这条来源说明：", claim.text);
    if (value === null) return;
    const text = value.trim();
    if (text.length < 2) return;
    onAction(claim.id, "modify", text);
  };

  const pending = claims.filter((claim) => PENDING_SOURCES.includes(claim.source));
  const decided = claims.filter((claim) => !PENDING_SOURCES.includes(claim.source));

  return (
    <div className="absolute inset-0 z-50 flex items-center justify-center bg-paper/88 p-6 backdrop-blur-sm">
      <section className="flex max-h-full w-full max-w-3xl flex-col overflow-hidden rounded-lg border border-ink-dim/50 bg-paper-raise shadow-2xl">
        <header className="flex items-center gap-3 border-b border-ink-ghost px-5 py-3">
          <div>
            <h2 className="font-display text-[16px] text-chalk">来源确认</h2>
            <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
              {pending.length} 条待处理 · 逐条决定后回到架构确认
            </p>
          </div>
          {onAcceptAll && pending.length > 0 ? (
            <button
              type="button"
              disabled={Boolean(busyId)}
              onClick={onAcceptAll}
              className="ml-auto inline-flex h-8 items-center rounded-md border border-ink/70 bg-ink/10 px-3 font-mono text-[11px] text-ink transition-colors hover:bg-ink/20 disabled:opacity-40"
            >
              全部确认（{pending.length}）
            </button>
          ) : null}
          <button
            type="button"
            onClick={onClose}
            className="inline-flex h-8 items-center rounded-md px-3 font-mono text-[11px] text-chalk-faint transition-colors hover:text-chalk"
          >
            关闭
          </button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {claims.length === 0 ? (
            <p className="font-body text-[12.5px] text-chalk-dim">
              这份架构没有需要确认的来源条目。
            </p>
          ) : null}

          {[
            { label: "待处理", items: pending },
            { label: "已决定", items: decided },
          ].map((section) =>
            section.items.length === 0 ? null : (
              <div key={section.label}>
                <h3 className="mb-2 font-mono text-[10px] uppercase tracking-[0.14em] text-chalk-faint">
                  {section.label} · {section.items.length}
                </h3>
                <div className="space-y-2">
                  {section.items.map((claim) => {
                    const busy = busyId === claim.id;
                    const anchor = claim.module_id
                      ? moduleNames[claim.module_id] ?? claim.module_id
                      : null;
                    return (
                      <article
                        key={claim.id}
                        className="rounded-lg border border-ink-ghost bg-paper/70 p-4"
                      >
                        <div className="flex items-start gap-2">
                          <p className="flex-1 font-body text-[12.5px] leading-relaxed text-chalk">
                            {claim.text}
                          </p>
                          <span className="shrink-0 rounded border border-ink-dim/40 px-1.5 py-0.5 font-mono text-[10px] text-chalk-dim">
                            {SOURCE_LABELS[claim.source]}
                          </span>
                        </div>
                        {anchor ? (
                          <p className="mt-1 font-mono text-[10.5px] text-chalk-faint">
                            挂载模块：{anchor}
                          </p>
                        ) : null}
                        <div className="mt-2 flex flex-wrap gap-2">
                          <button
                            type="button"
                            disabled={busy || Boolean(busyId)}
                            onClick={() => onAction(claim.id, "accept")}
                            className="inline-flex h-7 items-center rounded-md border border-ink/70 bg-ink/10 px-2.5 font-mono text-[11px] text-ink transition-colors hover:bg-ink/20 disabled:opacity-40"
                          >
                            确认
                          </button>
                          <button
                            type="button"
                            disabled={busy || Boolean(busyId)}
                            onClick={() => runModify(claim)}
                            className="inline-flex h-7 items-center rounded-md border border-ink-dim/45 px-2.5 font-mono text-[11px] text-chalk-dim transition-colors hover:border-ink hover:text-chalk disabled:opacity-40"
                          >
                            修改
                          </button>
                          <button
                            type="button"
                            disabled={busy || Boolean(busyId)}
                            onClick={() => onAction(claim.id, "unknown")}
                            className="inline-flex h-7 items-center rounded-md border border-ink-dim/45 px-2.5 font-mono text-[11px] text-chalk-dim transition-colors hover:border-ink hover:text-chalk disabled:opacity-40"
                          >
                            不确定
                          </button>
                          <button
                            type="button"
                            disabled={busy || Boolean(busyId)}
                            onClick={() => onAction(claim.id, "reject")}
                            className="inline-flex h-7 items-center rounded-md border border-vermilion/40 px-2.5 font-mono text-[11px] text-vermilion transition-colors hover:bg-vermilion/10 disabled:opacity-40"
                          >
                            拒绝
                          </button>
                        </div>
                      </article>
                    );
                  })}
                </div>
              </div>
            ),
          )}
        </div>
      </section>
    </div>
  );
}

const SOURCE_LABELS: Record<ProvenanceClaim["source"], string> = {
  confirmed: "用户事实",
  inferred: "AI 推测",
  recommended: "AI 建议",
  unknown: "未知",
  rejected: "已拒绝",
};
