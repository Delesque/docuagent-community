import type { Chip, Message } from "../components/v2/TypewriterOutput";
import type { DialogTab } from "../components/v2/DialogBox";
import type { BootstrapQuestionTradeoff, BootstrapState } from "../api";
import { CONVERSATION_TAB } from "./workbenchConfig";
import { draftStatus, formatDraftStatus } from "./draftStatus";

function formatTradeoffs(tradeoffs: BootstrapQuestionTradeoff[]): string {
  return tradeoffs
    .map((item) => `${item.option}\n  利：${item.pros || "（未给出）"}\n  弊：${item.cons || "（未给出）"}`)
    .join("\n");
}

export interface BootstrapTurnProjection {
  thinking: string;
  thinkingFallback: string;
  message: Message;
  tabs: DialogTab[];
  activeTab: string | null;
  refreshAttachments: boolean;
}

const initializedTabs: DialogTab[] = [CONVERSATION_TAB];

export function projectBootstrapTurn(next: BootstrapState): BootstrapTurnProjection {
  const thinking = next.thinking || "";
  const question = next.current_question;
  if (question) {
    const tab: DialogTab = { id: question.id, label: question.title, promptKey: question.id, placeholder: question.placeholder };
    const chips: Chip[] = question.options?.length ? question.options.map((text) => ({ text, type: "input" as const })) : [{ text: question.title, type: "input" }];
    // Trade-offs render as a trailing view chip: clicking "权衡" expands the model's
    // pros/cons list without submitting anything — the option chips stay the
    // answer-affordance, the trade-off chip is read-only context beside them.
    if (question.tradeoffs?.length) {
      chips.push({ text: "权衡", type: "view", detail: formatTradeoffs(question.tradeoffs) });
    }
    // The status line rides after the question as metadata-only text: the draft's
    // shape stays private, the user just sees the counts and what still blocks it.
    const status = draftStatus(next);
    const statusText = status ? `\n\n${formatDraftStatus(status)}` : "";
    return {
      thinking,
      thinkingFallback: `问题类型：${question.id}\n预期回答：${question.placeholder}`,
      message: { role: "agent", text: `DocuAgent: ${question.prompt} →「${question.title}」${statusText}`, chips },
      tabs: [tab],
      activeTab: question.id,
      refreshAttachments: false,
    };
  }
  if (next.status === "review") {
    const count = next.suggestions?.length ?? 0;
    const moduleList = next.architecture.modules
      .map((module) => `${module.name}（${module.brief || module.responsibility}）`)
      .join("、");
    const edgeList = next.architecture.edges
      .map((edge) => `${edge.from} → ${edge.to}（${edge.kind}${edge.accepted ? "，已接受" : "，待接受"}）`)
      .join("、");
    // After a revision the computed delta rides ahead of the full draft: the user
    // sees what changed before re-reading every module. Counts line, then entries.
    const delta = next.architecture_delta;
    const deltaText = delta
      ? Object.values(delta.counts).some((n) => n > 0)
        ? `\n\n较上一版：新增 ${delta.counts.added} · 移除 ${delta.counts.removed} · 更新 ${delta.counts.changed} · 仅位置调整 ${delta.counts.moved}\n${delta.changes.map((change) => `· ${change.message}`).join("\n")}\n\n`
        : "\n\n（与上一版相同。）\n\n"
      : "";
    const pendingClaims = (next.provenance ?? []).filter((claim) => claim.source !== "confirmed").length;
    const text = next.onboard
      ? (next.onboard_summary || "已完成项目接入分析。") + (count > 0 ? `\n\n同时给出了 ${count} 条基于现有实现的修改建议，确认架构后它们会挂载在对应模块上。` : "")
      : `架构草案已完整，共 ${next.architecture.modules.length} 个模块：\n${moduleList || "（暂无模块）"}\n\n连线关系：\n${edgeList || "（暂无显式连线）"}${deltaText}\n确认后会生成项目框架。也可以直接告诉我需要修改哪里。`;
    const chips: Chip[] = [
      { text: "开源选型", type: "action" },
      { text: "全部确认", type: "action" },
      { text: "确认架构", type: "action" },
    ];
    // Per-claim confirmation lives in a dedicated ledger; the chip only appears
    // when something actually awaits a decision.
    if (pendingClaims > 0) {
      chips.push({ text: "确认推测", type: "action" });
    }
    chips.push({ text: "修改架构", type: "input" });
    return {
      thinking,
      thinkingFallback: "架构已完成初步设计，包含模块划分、依赖关系和技术栈选择。",
      message: { role: "agent", text, chips },
      tabs: [{ id: "revise", label: "修改架构", promptKey: "revise", placeholder: "描述需要修改的地方…" }],
      activeTab: "revise",
      refreshAttachments: false,
    };
  }
  if (next.status === "ready" || next.status === "initialized") {
    const moduleList = next.architecture.modules.map((module) => `${module.name}（${module.brief}）`).join("、");
    const count = next.suggestions?.length ?? 0;
    const message = next.onboard
      ? `项目已接入：架构图（${next.architecture.modules.length} 个模块）和逐文件夹文档树已写入。` + (count > 0 ? `${count} 条修改建议已挂载在对应模块上。` : "")
      : `项目框架已生成！共 ${next.architecture.modules.length} 个模块：${moduleList}。文档和脚手架已写入项目目录。`;
    const chips: Chip[] = [{ text: "查看架构图", type: "view", detail: "打开图形视图查看模块关系" }];
    if (next.onboard && count > 0) chips.push({ text: "查看修改建议", type: "action" });
    chips.push({ text: "开始实现", type: "action" }, { text: "继续修改架构", type: "input" });
    return { thinking, thinkingFallback: `已生成 ${next.architecture.modules.length} 个模块的文档与脚手架。`, message: { role: "agent", text: message, chips }, tabs: initializedTabs, activeTab: "conversation", refreshAttachments: Boolean(next.onboard) };
  }
  return { thinking, thinkingFallback: `状态机停在 ${next.status}，没有待回答的问题。`, message: { role: "agent", text: `当前状态：${next.status}。`, chips: [] }, tabs: [], activeTab: null, refreshAttachments: false };
}
