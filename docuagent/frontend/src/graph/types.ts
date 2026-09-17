/** Mirrors the architecture schema in docuagent.py. See
 *  .docuagent/features/graph-workbench.md for the authoritative definition. */

export const EDGE_KINDS = ["uses", "data", "event", "extends", "blocks"] as const;
export type EdgeKind = (typeof EDGE_KINDS)[number];

/** `event` is excluded on purpose: publish/subscribe in both directions is valid
 *  design, so those edges neither constrain build order nor fail cycle checks. */
export const BUILD_ORDER_EDGE_KINDS: ReadonlySet<EdgeKind> = new Set<EdgeKind>([
  "uses",
  "data",
  "extends",
  "blocks",
]);

export type NodeStatus =
  | "pending"
  | "queued"
  | "running"
  | "review"
  | "applied"
  | "verified"
  | "done"
  | "failed"
  | "blocked"
  | "stopped"
  | "skipped"
  | "stale";

export interface GraphModule {
  id: string;
  name: string;
  brief: string;
  responsibility: string;
  path: string;
  depends_on: string[];
  needs_ui: boolean;
  group: string | null;
  uncertain?: boolean;
  uncertain_reason?: string;
}

export interface GraphGroup {
  id: string;
  label: string;
  kind: "framework" | "layer";
  members: string[];
}

export interface GraphEdge {
  from: string;
  to: string;
  kind: EdgeKind;
  label: string;
  reason: string;
  accepted?: boolean;
}

export interface ProvenanceClaim {
  id: string;
  text: string;
  source: "confirmed" | "inferred" | "recommended" | "unknown" | "rejected";
  /** The module this claim describes, when the model anchored it. */
  module_id?: string;
}

export interface Architecture {
  summary: string;
  platform: string;
  language: string;
  runtime: string;
  frameworks: string[];
  stack: string[];
  modules: GraphModule[];
  groups: GraphGroup[];
  edges: GraphEdge[];
  data: string[];
  integrations: string[];
  constraints: string[];
  verification: string[];
  risks: string[];
  unresolved: string[];
  provenance?: ProvenanceClaim[];
}

export interface Camera {
  x: number;
  y: number;
  scale: number;
}

export interface NodeLayoutState {
  x: number;
  y: number;
  pinned: boolean;
}

export interface UiState {
  ui_state_version: number;
  camera: Camera;
  nodes: Record<string, NodeLayoutState>;
  window_bar: string[];
  outline_expanded: string[];
}

/** Visual encoding is redundant by design: color, stroke pattern, and a hover label
 *  each identify the type on their own, so color-blind users lose nothing. */
export interface EdgeStyle {
  label: string;
  color: string;
  dash: string | undefined;
  doubled: boolean;
  buildOrder: boolean;
}

export const EDGE_STYLES: Record<EdgeKind, EdgeStyle> = {
  uses: {
    label: "调用",
    color: "#6CFFA8",
    dash: undefined,
    doubled: false,
    buildOrder: true,
  },
  data: {
    label: "共享数据",
    color: "#53E3C7",
    dash: "10 5",
    doubled: false,
    buildOrder: true,
  },
  event: {
    label: "异步事件",
    color: "#FFC857",
    dash: "2 4",
    doubled: false,
    buildOrder: false,
  },
  extends: {
    label: "框架契约",
    color: "#B18CFF",
    dash: undefined,
    doubled: true,
    buildOrder: true,
  },
  blocks: {
    label: "构建顺序",
    color: "#FF5C63",
    dash: "4 4",
    doubled: false,
    buildOrder: true,
  },
};

export const NODE_STATUS_LABELS: Record<NodeStatus, string> = {
  pending: "待生成",
  queued: "排队中",
  running: "生成中",
  review: "待审阅",
  applied: "已应用",
  verified: "已验证",
  done: "已完成",
  failed: "失败",
  blocked: "需人工处理",
  stopped: "已停止",
  skipped: "已跳过",
  stale: "已过期",
};
