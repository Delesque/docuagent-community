/** Conversation-as-interface shell.
 *
 *  The screen is one AI message at a time plus the bottom dialog. There is no sidebar,
 *  no toolbar, and no message list: stacking turns would turn this into the chat layout
 *  the design exists to replace. Older turns are reachable by wheeling up one page at
 *  a time (review mode), not by being permanently on screen.
 *
 *  Two contracts worth keeping in mind when editing:
 *  - Every backend route wants {path, provider: {...}}. See the note in api.ts.
 *  - Tab content is submitted as one structured turn, never as raw concatenated text.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import type { Chip, Message } from "./components/v2/TypewriterOutput";
import type { DialogTab } from "./components/v2/DialogBox";
import { ApiSettingsDialog } from "./components/v2/ApiSettingsDialog";
import { GraphCanvas } from "./components/GraphCanvas";
import { OutlineView } from "./components/OutlineView";
import { SnapshotPanel } from "./components/SnapshotPanel";
import { SystemPanel } from "./components/SystemPanel";
import { AgentsPanel } from "./components/AgentsPanel";
import { CodeIntelPanel } from "./codeintel/CodeIntelPanel";
import {
  codeIntelArchitectureProjection,
  codeIntelRegistryProjection,
  type ArchitectureProjection,
  type RegistryProjection,
  type RegistryType,
} from "./codeintel/api";
import { ConversationSurface } from "./components/ConversationSurface";
import { HandoffPanel } from "./components/HandoffPanel";
import { TriagePanel } from "./components/TriagePanel";
import { MicroTaskPanel } from "./components/MicroTaskPanel";
import { SuggestionsPanel } from "./components/SuggestionsPanel";
import { ProvenanceLedger } from "./components/ProvenanceLedger";
import { useGraphStore } from "./graph/store";
import { provenanceBadges } from "./graph/provenanceBadge";
import { withConversationNode } from "./graph/conversationNode";
import { latestNarration } from "./graph/narration";
import { usePrefersReducedMotion } from "./hooks/usePrefersReducedMotion";
import { fetchErrorNodes, type ErrorNode } from "./api/errorNodes";
import { useProviderConfig } from "./hooks/useProviderConfig";
import { useMicroTask } from "./hooks/useMicroTask";
import { useWorkspaceActions } from "./hooks/useWorkspaceActions";
import { useWorkspaceArtifacts } from "./hooks/useWorkspaceArtifacts";
import { useTriage } from "./hooks/useTriage";
import { useTaskFlow } from "./hooks/useTaskFlow";
import { useApplyMode } from "./hooks/useApplyMode";
import { ApplyModeSwitch } from "./components/ApplyModeSwitch";
import { DocIgnorePanel } from "./components/DocIgnorePanel";
import { ProjectDiscoveryPanel } from "./components/ProjectDiscoveryPanel";
import { useWorkspaceSession } from "./hooks/useWorkspaceSession";
import { useUiStatePersistence } from "./hooks/useUiStatePersistence";
import { useArchitectureWorkflow } from "./hooks/useArchitectureWorkflow";
import { useBootstrapSubmission } from "./hooks/useBootstrapSubmission";
import { useBootstrapTurn } from "./hooks/useBootstrapTurn";
import { useChipRouting } from "./hooks/useChipRouting";
import { useHandoff } from "./hooks/useHandoff";
import { useArchitectureEditor } from "./hooks/useArchitectureEditor";
import { useComposer } from "./hooks/useComposer";
import { useConversationNavigation } from "./hooks/useConversationNavigation";
import { useSuggestions } from "./hooks/useSuggestions";
import {
  applyTask,
  fetchContextCache,
  fetchTokenUsage,
  inspectWorkspace,
  rejectTask,
  retryTask,
  retryTaskDocs,
  saveConversation,
  setInterviewMode,
  updateArchitectureEdges,
  reopenArchitectureReview,
  updateProvenance,
  streamWorkStart,
  streamVerifyTask,
  applyTaskHunks,
  editTaskPatch,
  type BootstrapState,
  type InterviewMode,
  type ConversationMessage,
  type TaskPlan,
  type ProvenanceAction,
  type WorkspaceInfo,
} from "./api";
import {
  INITIAL_VIEW_STATE,
  reduceView,
  type ViewState,
} from "./conversation/viewMode";
import { delay } from "./conversation/delay";
import { loadReadingScale, saveReadingScale } from "./conversation/readingScale";
import { resolveInitialProjectPath } from "./conversation/projectRecovery";
import {
  EMPTY_TRANSCRIPT,
  transcriptReducer,
} from "./conversation/transcript";
import { SAVED_PATH_KEY } from "./conversation/workbenchConfig";
import type { Architecture } from "./graph/types";
import { deriveNodeStatuses } from "./conversation/appModel";

export default function App() {
  const [booted, setBooted] = useState(false);

  // `message` + `history` as one atomic state. Using a plain useState + updater
  // that calls setHistory inside setMessage is the root cause of the
  // removeChild/insertBefore crashes: React 18 concurrent mode may call updaters
  // more than once, and nested setState corrupts the fiber↔DOM mapping.
  const [transcript, dispatch] = useReducer(transcriptReducer, EMPTY_TRANSCRIPT);
  const message = transcript.message;
  const history = transcript.history;

  // Reading size is restored on the first render rather than in an effect, so the
  // opening turn is never typeset at the wrong size and then resized.
  const [view, setView] = useState<ViewState>(() => ({
    ...INITIAL_VIEW_STATE,
    scale: loadReadingScale(),
  }));

  const setReadingScale = useCallback((scale: number) => {
    setView((prev) => reduceView(prev, { type: "setScale", scale }));
    saveReadingScale(window.localStorage, scale);
  }, []);

  const [workspace, setWorkspace] = useState<WorkspaceInfo | null>(null);
  const [bootstrap, setBootstrap] = useState<BootstrapState | null>(null);
  const [busy, setBusy] = useState(false);

  const [tabs, setTabs] = useState<DialogTab[]>([]);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [dialogOpen, setDialogOpen] = useState(false);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [docIgnoreOpen, setDocIgnoreOpen] = useState(false);
  const { provider, configured, save: saveProvider } = useProviderConfig();
  const [undoAvailable, setUndoAvailable] = useState(false);
  const [tasks, setTasks] = useState<TaskPlan | null>(null);
  const [agentsOpen, setAgentsOpen] = useState(false);
  const [systemOpen, setSystemOpen] = useState(false);
  const [codeIntelOpen, setCodeIntelOpen] = useState(false);
  /** Which architecture module's public API the code-intel panel is showing. */
  const [codeIntelModuleId, setCodeIntelModuleId] = useState<string | null>(null);
  /** Per-module code-intel projection (public API + status) and global orphan count,
   *  fetched once when the graph is available. Drives the stale badges on nodes and
   *  the panel's module view. */
  const [codeIntelProjection, setCodeIntelProjection] = useState<ArchitectureProjection | null>(null);
  // §3 step3: typed Contract Registry projection, fetched alongside the architecture
  // projection. Default collapsed (count badges); the user expands + filters per type.
  const [registryProjection, setRegistryProjection] = useState<RegistryProjection | null>(null);
  const [registryExpanded, setRegistryExpanded] = useState(false);
  const [registryTypeFilter, setRegistryTypeFilter] = useState<Set<RegistryType>>(
    new Set<RegistryType>([
      "public_api",
      "data_schema",
      "commands_events",
      "config_policy",
      "shared_kernel",
      "vocabulary",
    ]),
  );
  // P1: unified error nodes. Fetched with the projections and refreshed after
  // any action that can create or resolve them (docs retry, work runs).
  const [errorNodes, setErrorNodes] = useState<ErrorNode[]>([]);
  const [revealOnOpen, setRevealOnOpen] = useState(false);
  const [snapshotsOpen, setSnapshotsOpen] = useState(false);
  const [discoveryOpen, setDiscoveryOpen] = useState(false);
  /** Per-claim provenance confirmation ledger (review stage). */
  const [provenanceLedgerOpen, setProvenanceLedgerOpen] = useState(false);
  const [provenanceBusyId, setProvenanceBusyId] = useState<string | null>(null);
  const handleStartWorkRef = useRef<() => void>(() => undefined);
  const [modelStreaming, setModelStreaming] = useState(false);
  const [retryStatus, setRetryStatus] = useState<{
    label: string;
    attempt: number;
    total: number;
  } | null>(null);
  const handleConfirmArchitectureRef = useRef<() => void>(() => undefined);
  const handleConfirmAllRef = useRef<() => void>(() => undefined);
  const handleOnboardRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    setUndoAvailable(workspace?.architecture_can_undo ?? false);
  }, [workspace]);

  const reducedMotion = usePrefersReducedMotion();

  /** An architecture that arrived by editing rather than by interviewing.
   *
   *  Needed because the graph used to be derived from `bootstrap` alone, so a finished
   *  project had no way to change it: `bootstrap` stops advancing at `initialized`, and
   *  a project opened from `architecture.json` never had a bootstrap state to begin with.
   *  When set this wins, because it is strictly newer than whatever produced it.
   */
  const [editedArchitecture, setEditedArchitecture] = useState<Architecture | null>(null);

  // The conversation is projected in as a node here rather than by the backend: the
  // architecture dict is echoed to the model every turn, and a synthetic module in it
  // would read as one the agent designed. See graph/conversationNode.ts.
  const activeBootstrap = bootstrap ?? workspace?.bootstrap ?? null;
  const architecture = useMemo(() => {
    // The graph is a reward, not a working surface: during the interview AND while the
    // draft waits at the confirmation gate (review), the conversation owns the screen.
    // It is only revealed after the user actually confirms (ready / initialized), so
    // the reveal animation is the payoff of that explicit action.
    if (
      activeBootstrap?.status === "interviewing" ||
      activeBootstrap?.status === "review"
    ) {
      return null;
    }
    return withConversationNode(
      editedArchitecture ?? activeBootstrap?.architecture ?? workspace?.architecture ?? null,
    );
  }, [activeBootstrap, editedArchitecture, workspace?.architecture]);

  const store = useGraphStore(architecture, workspace?.ui_state ?? null);
  const [moduleUsage, setModuleUsage] = useState<
    Record<
      string,
      {
        contextHitRate: number | null;
        serverHitRate: number | null;
        totalTokens: number;
      }
    >
  >({});


  // The first time a graph exists, open the conversation on it. Without this the user is
  // dropped onto a bare canvas mid-interview with the question they were answering now
  // behind a node they have not learned to open yet. Runs once per project: after that,
  // where the camera sits is the user's business.
  // Held in a ref rather than state: positions change on every layout pass, and
  // re-rendering the whole shell for them would fight the animation they exist to feed.
  const { handlePositions, setCamera, saveLayoutNow, restoreDefaultLayout } = useUiStatePersistence({ workspace, store });

  /** Canvas or Outline — two projections of one graph state, not two features.
   *
   *  The Outline is an accessibility requirement rather than a fallback: position on the
   *  canvas carries dependency meaning that a linear read cannot recover, and
   *  absolutely-positioned nodes are announced in model-output order. Both dispatch the
   *  same commands against the same store, which is what makes keyboard parity
   *  structural instead of a pile of extra shortcuts.
   */
  const [projection, setProjection] = useState<"canvas" | "outline">("canvas");

  /** True when the arrow keys belong to the canvas rather than to the transcript.
   *
   *  The canvas nudges a selected node with the arrow keys and this shell pages turns
   *  with them, both listening on `window`. Whoever has a node selected wins, because
   *  selecting a node is the more specific intent.
   */
  const nodeSelectedOnCanvas =
    projection === "canvas" &&
    store.selection.nodeId !== null &&
    store.focusedId === null;

  /** Commands that need the spatial surface pull the user back to it.
   *
   *  The focus panel and the camera both live inside the canvas, so entering a node or
   *  centering it from the Outline would otherwise appear to do nothing — `focusedId`
   *  would change with nothing mounted to render it. Parity means the Outline's Enter
   *  reaches the same place the canvas's does, not that it silently no-ops.
   */
  useEffect(() => {
    const unregister = store.registerEffect((command) => {
      if (
        command.type === "focus" ||
        command.type === "focusConversation" ||
        command.type === "centerNode" ||
        command.type === "fit"
      ) {
        setProjection("canvas");
      }
    });
    return unregister;
  }, [store]);

  /** Layer numbers, computed by the layout engine inside the canvas.
   *
   *  Kept here because the Outline needs them and the canvas unmounts when the Outline
   *  is showing — reading them from a live canvas would mean they vanish exactly when
   *  they are needed. Layers are derived from the architecture, so they stay correct
   *  while the canvas is absent.
   */
  const [layers, setLayers] = useState<Map<string, number>>(() => new Map());

  const graphIntroduced = useRef(false);
  const suppressGraphIntro = useRef(false);
  useEffect(() => {
    if (suppressGraphIntro.current) {
      suppressGraphIntro.current = false;
      graphIntroduced.current = true;
      return;
    }
    if (revealOnOpen) {
      graphIntroduced.current = true;
      return;
    }
    if (graphIntroduced.current || !architecture) return;
    graphIntroduced.current = true;
    store.dispatch({ type: "focusConversation" });
  }, [architecture, revealOnOpen, store]);

  // Held in a ref so the opening sequence reads the current value without the effect
  // depending on it — otherwise saving the API config replays the whole intro.
  const configuredRef = useRef(configured);
  configuredRef.current = configured;
  const autoOpeningRef = useRef(false);

  const say = useCallback((next: Message) => {
    dispatch({ type: "message", message: next });
  }, []);

  const artifacts = useWorkspaceArtifacts({ workspace, notify: (text) => say({ role: "agent", text }), setBusy });
  const { agents, snapshots, attachments, refreshAgents, refreshSnapshots, refreshAttachments, replaceAttachments, restoreSnapshot: handleRestoreSnapshot, sendAgentMessage: handleSendAgentMessage, addNodeAttachment: handleAddNodeAttachment, archiveModule: handleArchiveModule } = artifacts;
  const setThinking = useCallback((thinking: string, fallback: string) => {
    dispatch({ type: "setThinking", thinking, fallback });
  }, []);
  const nodeStatuses = useMemo(
    () => deriveNodeStatuses(store.statuses, tasks, workspace),
    [store.statuses, tasks, workspace],
  );

  const { handoffEntries, handoffTasks, handoffOpen, setHandoffOpen } = useHandoff({ tasks, architecture });

  const taskFlow = useTaskFlow({ tasks, setTasks, workspace, provider, architecture, setBusy, say, refreshSnapshots, refreshAgents, streamWorkStart, streamVerifyTask, applyTask, rejectTask, retryTask, editTaskPatch, applyTaskHunks });
  const applyMode = useApplyMode(workspace?.path ?? null);
  const { taskTails, taskStreaming, busyTaskId, taskProgressRef, progressTextRef, failedTasks, canStartWork, runTaskRequest, startWork: handleGenerateWave, stopTask, handleApplyTask, handleRejectTask, handleRetryTask, handleVerifyTask, handleSavePatch, handleApplyHunks } = taskFlow;

  useEffect(() => {
    const root = workspace?.path;
    if (!root) {
      setModuleUsage({});
      return;
    }
    let cancelled = false;
    const refresh = async (): Promise<void> => {
      const [context, tokens] = await Promise.all([
        fetchContextCache(root),
        fetchTokenUsage(root),
      ]);
      if (cancelled) return;
      const next: Record<
        string,
        {
          contextHitRate: number | null;
          serverHitRate: number | null;
          totalTokens: number;
        }
      > = {};
      for (const item of context.modules) {
        next[item.module_id] = {
          contextHitRate: item.overall_hit_rate,
          serverHitRate: null,
          totalTokens: 0,
        };
      }
      for (const item of tokens.modules) {
        const moduleId = item.module_id ?? "";
        if (!moduleId) continue;
        next[moduleId] = {
          ...(next[moduleId] ?? { contextHitRate: null, totalTokens: 0 }),
          serverHitRate: item.server_hit_rate,
          totalTokens: item.total_tokens,
        };
      }
      setModuleUsage(next);
    };
    void refresh().catch(() => {
      if (!cancelled) setModuleUsage({});
    });
    const timer = window.setInterval(
      () => void refresh().catch(() => {}),
      taskStreaming || busy ? 5_000 : 30_000,
    );
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [workspace?.path, taskStreaming, busy, tasks?.task_version]);

  const nodeUsageByModule = useMemo(() => {
    const result: Record<
      string,
      {
        contextHitRate: number | null;
        serverHitRate: number | null;
        totalTokens: number;
      }
    > = {};
    for (const module of architecture?.modules ?? []) {
      if (!module.path) continue;
      result[module.id] = moduleUsage[module.id] ?? {
        contextHitRate: null,
        serverHitRate: null,
        totalTokens: 0,
      };
    }
    return result;
  }, [architecture?.modules, moduleUsage]);
  const micro = useMicroTask({ workspace, provider, setTasks, setBusy, notify: (role, text) => say({ role, text }) });
  const { microTask, microOpen, setMicroOpen, reset: resetMicroTask, dispatch: handleMicroTaskDispatch, apply: handleMicroTaskApply, reject: handleMicroTaskReject } = micro;


  const interviewMode =
    provider.interview_mode ?? activeBootstrap?.interview_mode ?? "guided";

  /** 首次进入项目、且全局尚未选择过访谈模式时，先让用户选一次。 */
  const showInterviewModePicker = Boolean(
    workspace && !activeBootstrap && !provider.interview_mode,
  );

  const changeInterviewMode = useCallback(async (mode: InterviewMode) => {
    // 永久保存到 ~/.docuagent/config.json；之后只在设置中调整。
    saveProvider({ ...provider, interview_mode: mode });
    if (!workspace?.path || !activeBootstrap) return;
    try {
      setBootstrap(await setInterviewMode(workspace.path, mode));
    } catch (cause) {
      say({ role: "agent", text: `切换访谈模式失败：${(cause as Error).message}` });
    }
  }, [activeBootstrap, provider, saveProvider, say, workspace?.path]);

  // P3: project the derived symbol_index onto the architecture diagram. One
  // fetch per (graph, root) change; the backend caches the index, so it is cheap
  // after the first pass. Powers node stale badges + the panel's module/orphan view.
  useEffect(() => {
    if (!workspace?.path || !architecture) {
      setCodeIntelProjection(null);
      return;
    }
    let active = true;
    const modules = architecture.modules.map((m) => ({ id: m.id, path: m.path }));
    codeIntelArchitectureProjection(workspace.path, modules)
      .then((proj) => {
        if (active) setCodeIntelProjection(proj);
      })
      .catch(() => {
        if (active) setCodeIntelProjection(null);
      });
    // §3 step3: the typed Contract Registry projection (six-class nodes + aggregate
    // edges). Independent fetch; missing/empty contracts just yield no overlay.
    codeIntelRegistryProjection(workspace.path)
      .then((proj) => {
        if (active) setRegistryProjection(proj);
      })
      .catch(() => {
        if (active) setRegistryProjection(null);
      });
    // P1: unified error nodes. Missing/empty just means nothing is broken.
    fetchErrorNodes(workspace.path)
      .then((nodes) => {
        if (active) setErrorNodes(nodes);
      })
      .catch(() => {
        if (active) setErrorNodes([]);
      });
    return () => {
      active = false;
    };
  }, [workspace?.path, architecture]);

  // P1: re-fetch error nodes after anything that can create or resolve them.
  const refreshErrorNodes = useCallback(async () => {
    if (!workspace?.path) return;
    try {
      setErrorNodes(await fetchErrorNodes(workspace.path));
    } catch {
      setErrorNodes([]);
    }
  }, [workspace?.path]);

  const handleErrorNodeAction = useCallback(
    async (node: ErrorNode, actionId: string) => {
      if (actionId === "retry_documentation") {
        if (!workspace || !provider) return;
        try {
          const plan = await retryTaskDocs(workspace.path, provider);
          setTasks(plan);
          say({
            role: "agent",
            text: "文档维护已重试完成。如果错误节点已解决，它会从图上消失。",
          });
        } catch (cause) {
          say({
            role: "agent",
            text: `文档重试失败：${(cause as Error).message}`,
          });
        }
        void refreshErrorNodes();
        return;
      }
      if (actionId === "retry_task") {
        if (!node.code_task_id) return;
        taskFlow.retryTask(node.code_task_id);
        void refreshErrorNodes();
        return;
      }
      if (actionId === "verify_task") {
        if (!node.code_task_id) return;
        taskFlow.verifyTask(node.code_task_id);
        void refreshErrorNodes();
        return;
      }
      if (actionId === "view_code_change" || actionId === "open_related_docs") {
        const file = node.changed_files[0];
        if (file) {
          const url =
            "file:///" +
            file.replace(/\\/g, "/").replace(/^\/+/, "") +
            (actionId === "view_code_change" ? "" : "");
          window.open(url, "_blank");
        }
      }
      // view_error_detail: the expanded badge card already shows the detail.
    },
    [workspace, provider, refreshErrorNodes, say, taskFlow],
  );

  // Error nodes change whenever work runs, applies, verifies or docs sync.
  // tasks identity is the cheapest proxy for "something happened" — one cheap
  // GET per task-state update, no event stream needed yet.
  useEffect(() => {
    void refreshErrorNodes();
  }, [refreshErrorNodes, tasks]);

  const { loadWorkspace, openPathPicker } = useWorkspaceSession({
    workspace,
    say,
    setBooted,
    setWorkspace,
    setBootstrap,
    setTasks,
    setEditedArchitecture,
    setRevealOnOpen,
    setTabs,
    setDrafts,
    setActiveTab,
    dispatch,
    resetMicroTask,
    suppressGraphIntro,
  });

  const handleReopenReview = useCallback(async () => {
    if (!workspace) return;
    try {
      const next = await reopenArchitectureReview(workspace.path);
      setBootstrap(next);
      setEditedArchitecture(null);
      setView((prev) => reduceView(prev, { type: "turn" }));
      say({ role: "agent", text: "已重新打开架构确认页。你可以在这里接受、修改或标记来源与模块依赖，然后再次确认架构。" });
    } catch (cause) {
      say({ role: "agent", text: `重新进入确认页失败：${(cause as Error).message}` });
    }
  }, [say, setBootstrap, setEditedArchitecture, setView, workspace]);

  const suggestions = useSuggestions({ workspace, attachments, say, replaceAttachments });
  const { suggestionEntries, suggestionsOpen, setSuggestionsOpen, suggestionBusy, acceptSuggestion: handleAcceptSuggestion, rejectSuggestion: handleRejectSuggestion } = suggestions;

  // Auto-save conversation history whenever history or message changes
  useEffect(() => {
    if (!workspace?.path) return;

    // Build the full conversation from history + current message
    const allMessages: ConversationMessage[] = [];
    for (const msg of history) {
      allMessages.push({
        role: msg.role === "user" ? "user" : "assistant",
        content: msg.text,
        timestamp: new Date().toISOString(),
      });
    }
    if (message) {
      allMessages.push({
        role: message.role === "user" ? "user" : "assistant",
        content: message.text,
        timestamp: new Date().toISOString(),
      });
    }

    // Only save if we have messages
    if (allMessages.length === 0) return;

    // Debounce: save after 1 second of inactivity
    const timer = setTimeout(() => {
      saveConversation(workspace.path, allMessages).catch((err) => {
        console.error("Failed to save conversation:", err);
      });
    }, 1000);

    return () => clearTimeout(timer);
  }, [history, message, workspace?.path]);

  // Restore the last project on startup: a `?path=` deep link wins, otherwise the
  // saved path from the folder picker is used. This is what keeps rollback reloads
  // from dropping back to the directory-picker greeting.
  useEffect(() => {
    const savedPath = window.localStorage.getItem(SAVED_PATH_KEY);
    const initialPath = resolveInitialProjectPath(window.location.search, savedPath);
    if (!initialPath) return;
    window.localStorage.setItem(SAVED_PATH_KEY, initialPath);
    autoOpeningRef.current = true;
    suppressGraphIntro.current = true;
    inspectWorkspace(initialPath)
      .then((next) => {
        void loadWorkspace(next).catch(() => {
          autoOpeningRef.current = false;
          setBooted(true);
          say({
            role: "agent",
            text: "DocuAgent: 无法自动恢复上次项目，请重新选择项目目录。",
            chips: [{ text: "项目地址", type: "input" }],
          });
        });
      })
      .catch(() => {
        autoOpeningRef.current = false;
        setBooted(true);
        say({
          role: "agent",
          text: "DocuAgent: 无法自动恢复上次项目，请重新选择项目目录。",
          chips: [{ text: "项目地址", type: "input" }],
        });
      });
  }, [loadWorkspace, say]);

  // Opening sequence: blank → cursor → greeting. Runs exactly once.
  useEffect(() => {
    let cancelled = false;
    const run = async (): Promise<void> => {
      await delay(800);
      if (cancelled || autoOpeningRef.current) return;
      setBooted(true);
      await delay(600);
      if (cancelled) return;

      const chips: Chip[] = [{ text: "项目地址", type: "input" }];
      let text = "DocuAgent: 我们的项目地址是？";
      if (!configuredRef.current) {
        text += "当然，还要配置我的api。";
        chips.push({ text: "配置我的api", type: "input" });
      }
      say({ role: "agent", text, chips });
    };
    void run();
    return () => {
      cancelled = true;
    };
  }, []);


  const handleChip = useChipRouting({
    tabs,
    openPathPicker,
    setSuggestionsOpen,
    setProvenanceLedgerOpen,
    setSettingsOpen,
    setTabs,
    setDrafts,
    setActiveTab,
    setDialogOpen,
    handleStartWorkRef,
    handleConfirmArchitectureRef,
    handleConfirmAllRef,
    handleOnboardRef,
    storeDispatch: store.dispatch,
  });

  /** One deliberate provenance decision from the ledger. The route returns the
   *  refreshed public state, which keeps the review draft and this panel in step. */
  const handleProvenanceAction = useCallback(
    (claimId: string, action: ProvenanceAction, text?: string) => {
      if (!workspace || provenanceBusyId) return;
      setProvenanceBusyId(claimId);
      updateProvenance(workspace.path, claimId, action, text)
        .then((result) => {
          if (result.state) setBootstrap(result.state);
        })
        .catch((cause) => {
          say({ role: "agent", text: `来源确认失败：${(cause as Error).message}` });
        })
        .finally(() => setProvenanceBusyId(null));
    },
    [provenanceBusyId, say, setBootstrap, updateProvenance, workspace],
  );

  /** Accept every pending claim from the ledger without touching the architecture
   *  confirmation itself — narrower than the conversation's 全部确认, which also
   *  accepts edges and advances to generation. */
  const handleAcceptAllClaims = useCallback(() => {
    if (!workspace || !activeBootstrap || provenanceBusyId) return;
    const pending = (activeBootstrap.provenance ?? []).filter(
      (claim) => claim.source !== "confirmed" && claim.source !== "rejected",
    );
    if (pending.length === 0) return;
    setProvenanceBusyId("__all__");
    let chain: Promise<void> = Promise.resolve();
    for (const claim of pending) {
      chain = chain.then(() =>
        updateProvenance(workspace.path, claim.id, "accept").then((result) => {
          if (result.state) setBootstrap(result.state);
        }),
      );
    }
    chain
      .catch((cause) => {
        say({ role: "agent", text: `批量确认失败：${(cause as Error).message}` });
      })
      .finally(() => setProvenanceBusyId(null));
  }, [activeBootstrap, provenanceBusyId, say, setBootstrap, updateProvenance, workspace]);

  /** Render the result of one bootstrap turn. Shared by the dialog submit path and the
   *  action chip "确认架构", so a direct confirmation cannot drift from a typed one. */
  const applyBootstrapTurn = useBootstrapTurn({
    setBootstrap,
    setDialogOpen,
    setDrafts,
    setView,
    setTabs,
    setActiveTab,
    dispatch,
    say,
    refreshAttachments,
  });

  const architectureWorkflow = useArchitectureWorkflow({
    workspace,
    provider,
    configured,
    say,
    refreshSnapshots,
    refreshAttachments,
    applyBootstrapTurn,
    setTasks,
    setEditedArchitecture,
    setUndoAvailable,
    setRevealOnOpen,
    setBusy,
    setModelStreaming,
    setSettingsOpen,
    setThinking,
    setRetryStatus,
  });
  const { runModelCall, runBootstrapStream, confirm: handleConfirmArchitecture, onboard: handleOnboard, edit: runArchitectureEdit, stopRetry, stopStream } = architectureWorkflow;

  const handleConfirmAll = useCallback(async () => {
    if (!workspace || !activeBootstrap || activeBootstrap.status !== "review") return;
    try {
      for (const claim of activeBootstrap.provenance ?? []) {
        if (claim.source !== "confirmed") {
          const result = await updateProvenance(workspace.path, claim.id, "accept");
          if (result.state) setBootstrap(result.state);
        }
      }
      for (const edge of activeBootstrap.architecture.edges ?? []) {
        if (!edge.accepted) {
          const result = await updateArchitectureEdges(workspace.path, {
            action: "accept",
            from: edge.from,
            to: edge.to,
          });
          if (bootstrap) {
            setBootstrap((prev) =>
              prev
                ? {
                    ...prev,
                    architecture: result.architecture,
                    architecture_version: result.architecture_version,
                  }
                : prev,
            );
          }
        }
      }
      say({ role: "agent", text: "已全部确认来源与连线，正在确认架构并进入下一步…" });
      await handleConfirmArchitecture();
    } catch (cause) {
      say({ role: "agent", text: `全部确认失败：${(cause as Error).message}` });
    }
  }, [activeBootstrap, bootstrap, handleConfirmArchitecture, say, setBootstrap, updateArchitectureEdges, updateProvenance, workspace]);

  useEffect(() => {
    handleConfirmArchitectureRef.current = () => { void handleConfirmArchitecture(); };
    handleConfirmAllRef.current = () => { void handleConfirmAll(); };
  }, [handleConfirmAll, handleConfirmArchitecture]);
  useEffect(() => {
    handleOnboardRef.current = () => { void handleOnboard(); };
  }, [handleOnboard]);


  /** Route composer input to the active feature, then delegate bootstrap state transitions. */
  const submitBootstrap = useBootstrapSubmission({
    workspace,
    provider,
    bootstrap,
    tabs,
    say,
    setBusy,
    setRevealOnOpen,
    applyBootstrapTurn,
    confirmArchitecture: handleConfirmArchitecture,
    runModelCall,
    runBootstrapStream,
  });


  /** Ask the model to revise an architecture that already exists.
   *
   *  Distinct from the interview: no question is asked, no status advances, and the graph
   *  updates in place. This is the path that makes the canvas an editable artifact rather
   *  than a report — before it existed, `bootstrap` stopped at `initialized` and there was
   *  no route or control that could change the graph again.
   */
  const architectureEditor = useArchitectureEditor({
    workspace,
    say,
    runArchitectureEdit,
    setBusy,
    setDialogOpen,
    setDrafts,
    setView,
    setTabs,
    setActiveTab,
    setEditedArchitecture,
    setUndoAvailable,
    dispatch,
  });
  const { requestArchitectureEdit, undoArchitectureEdit } = architectureEditor;

  const handleSubmit = useComposer({
    workspace,
    bootstrap,
    tasks,
    tabs,
    activeTab,
    taskStreaming,
    taskProgressRef,
    progressTextRef,
    say,
    dispatch,
    setDialogOpen,
    setDrafts,
    submitBootstrap,
    requestArchitectureEdit,
    dispatchMicroTask: handleMicroTaskDispatch,
  });

  // history + current message as a flat array for review/overview modes.
  const allMessages = message ? [...history, message] : history;
  const total = allMessages.length;

  // One line for the conversation node, so zooming out mid-interview does not hide that
  // the agent is still working.
  const narration = useMemo(
    () =>
      latestNarration(
        allMessages.map((entry) => ({
          role: entry.role === "user" ? ("user" as const) : ("agent" as const),
          text: entry.text,
          pending: entry.pending,
        })),
      ),
    [allMessages],
  );

  useConversationNavigation({ total, nodeSelectedOnCanvas, setView, setProjection });

  const dialogVisible = dialogOpen && tabs.length > 0 && view.mode === "live";

  // Which message to show in live/review: review uses anchor, live uses the newest.
  const reviewIndex = view.mode === "review" ? (view.anchor ?? total - 1) : total - 1;
  const displayedMessage =
    view.mode === "review" ? (allMessages[reviewIndex] ?? null) : message;

  // Page-turn slide direction: -1 = older turn came from top, +1 = newer from bottom.
  // "bottom" means the page slides in from below (= a newer turn arriving from the
  // future end of the timeline).
  const enterFrom: "top" | "bottom" | null =
    view.direction === -1 ? "top" : view.direction === 1 ? "bottom" : null;

  /** Write layout state, debounced.
   *
   *  Reads positions from a ref rather than taking them as an argument, so a drag and a
   *  camera move can both trigger a save without either needing to know what the other
   *  changed. Debounced by replacing a pending timer: a drag ends with one pin, but a
   *  camera gesture fires dozens of times a second.
   */

  const triage = useTriage({
    workspace,
    provider,
    failedTasks,
    taskStreaming,
    runTaskRequest,
    setTasks,
    notify: (text) => say({ role: "agent", text }),
    focusModule: (moduleId) => store.dispatch({ type: "focus", nodeId: moduleId }),
  });
  const { failedTriageErrors, triageOpen, triageResult, triageBusy, triageBusyTaskId, requestTriage, handleTriageFocus, handleTriageRetryTask, handleTriageResumeTask, handleTriageRetryAll, setTriageOpen } = triage;



  const workspaceActions = useWorkspaceActions({
    workspace,
    say,
    setWorkspace,
    setBootstrap,
    setUndoAvailable,
  });
  const { handleTerminalExec, handleClearStale } = workspaceActions;

  // The conversation UI, rendered either full-screen (before a graph exists) or inside
  // the focus frame of its own node (once it does). Same elements either way, so the
  // interview does not restart or lose its scroll position when the graph appears.
  const conversationSurface = (
    <ConversationSurface
      view={view}
      booted={booted}
      dialogVisible={dialogVisible}
      tabs={tabs}
      activeTab={activeTab}
      drafts={drafts}
      busy={busy}
      taskStreaming={taskStreaming}
      retryStatus={retryStatus}
      modelStreaming={modelStreaming}
      displayedMessage={displayedMessage}
      messages={allMessages}
      reviewIndex={reviewIndex}
      enterFrom={enterFrom}
      interviewMode={interviewMode}
      showInterviewModePicker={showInterviewModePicker}
      onInterviewModeChange={(mode) => void changeInterviewMode(mode)}
      onPick={(index) =>
        setView((prev) => reduceView(prev, { type: "pick", index, total }))
      }
      onExit={() => setView((prev) => reduceView(prev, { type: "escape" }))}
      onChipClick={(chip) => {
        if (chip.text === "开源选型") setDiscoveryOpen(true);
        else handleChip(chip);
      }}
      onTabChange={setActiveTab}
      onDraftChange={(id, value) => setDrafts((prev) => ({ ...prev, [id]: value }))}
      onCloseDialog={() => setDialogOpen(false)}
      onSubmit={handleSubmit}
      onOpenChat={() => {
        setActiveTab(tabs[0]?.id ?? null);
        setDialogOpen(true);
      }}
      onStopRetry={stopRetry}
      onStopModelStream={stopStream}
      onStopTask={stopTask}
    />
  );

  return (
    <div className="relative h-screen w-screen overflow-hidden">
      {architecture && projection === "outline" ? (
        /* The linear projection. Same store, same commands — the canvas is its spatial
           counterpart, not its richer version. The conversation appears here too, with
           its latest line, because it is a node on the graph like any other. */
        <div className="absolute inset-0 overflow-y-auto bg-paper">
          <OutlineView
            architecture={architecture}
            statuses={nodeStatuses}
            selectedId={store.selection.nodeId}
            windowBar={store.windowBar}
            pinned={store.pinned}
            expanded={store.outlineExpanded}
            layers={layers}
            dispatch={store.dispatch}
            narration={narration}
          />
        </div>
      ) : architecture ? (
        /* Once an architecture exists the graph is the base surface, and the
           conversation is one node on it. There is no mode switch: `focusedId` decides
           what is open, and Home always flies back to the conversation. */
        <GraphCanvas
          architecture={architecture}
          camera={store.camera}
          statuses={nodeStatuses}
          selectedNodeId={store.selection.nodeId}
          selectedEdgeId={store.selection.edgeId}
          focusedId={store.focusedId}
          windowBar={store.windowBar}
          pinned={store.pinned}
          previousPositions={workspace?.ui_state?.nodes ?? {}}
          reducedMotion={reducedMotion}
          onCamera={setCamera}
          dispatch={store.dispatch}
          conversationContent={conversationSurface}
          onPositions={handlePositions}
          narration={narration}
          onLayout={setLayers}
          registerEffect={store.registerEffect}
          claimPendingCamera={store.claimPendingCamera}
          onSaveLayout={saveLayoutNow}
          onRestoreDefault={restoreDefaultLayout}
          canUndo={undoAvailable}
          onUndo={() => void undoArchitectureEdit()}
          revealOnOpen={revealOnOpen}
          onClearStale={(moduleId) => void handleClearStale(moduleId)}
          tasks={tasks}
          taskTails={taskTails.byModule}
          taskStreaming={taskStreaming}
          attachments={attachments}
          onAddAttachment={(moduleId, type, text) =>
            void handleAddNodeAttachment(moduleId, type, text)
          }
          onVerifyTask={handleVerifyTask}
          onApplyTask={handleApplyTask}
          onResumeTask={(taskId) => void handleTriageResumeTask(taskId)}
          onApplyHunks={handleApplyHunks}
          onSavePatch={handleSavePatch}
          onTerminalExec={handleTerminalExec}
          onArchiveModule={(moduleId) => void handleArchiveModule(moduleId)}
          onOpenModuleCode={(moduleId) => {
            setCodeIntelModuleId(moduleId);
            setCodeIntelOpen(true);
          }}
          codeStaleById={
            codeIntelProjection
              ? Object.fromEntries(
                  Object.entries(codeIntelProjection.modules)
                    .filter(([, m]) => m.status === "stale")
                    .map(([id]) => [id, true]),
                )
              : {}
          }
          adaptPendingById={
            registryProjection?.impact
              ? Object.fromEntries(
                  registryProjection.impact.affected.map((item) => [item.module_id, true]),
                )
              : {}
          }
          surfaceCountsById={
            codeIntelProjection || registryProjection
              ? (() => {
                  const ids = new Set<string>([
                    ...Object.keys(codeIntelProjection?.modules ?? {}),
                    ...(registryProjection?.nodes ?? [])
                      .map((n) => n.owner)
                      .filter(Boolean),
                  ]);
                  const entries: Array<[string, { api: number; contracts: number }]> = [];
                  for (const id of ids) {
                    const api = codeIntelProjection?.modules[id]?.public_api.length ?? 0;
                    const contracts =
                      registryProjection?.nodes.filter((n) => n.owner === id).length ?? 0;
                    if (api > 0 || contracts > 0) entries.push([id, { api, contracts }]);
                  }
                  return Object.fromEntries(entries);
                })()
              : undefined
          }
          usageByModule={nodeUsageByModule}
          provenanceBadgeById={provenanceBadges(activeBootstrap?.provenance)}
          registryNodes={registryProjection?.nodes ?? []}
          registryEdges={registryProjection?.edges ?? []}
          registryExpanded={registryExpanded}
          registryTypeFilter={registryTypeFilter}
          onToggleRegistry={() => setRegistryExpanded((v) => !v)}
          onSetRegistryFilter={(next) => setRegistryTypeFilter(next)}
          errorNodes={errorNodes}
          onErrorNodeAction={handleErrorNodeAction}
        />
      ) : (
        /* No graph yet: the conversation owns the whole screen. */
        conversationSurface
      )}

      {workspace && architecture ? (
        <div className="fixed bottom-8 right-8 z-50 flex items-center gap-3">
          {/* Sandbox-apply switch sits next to 开始工作: the choice matters
              exactly when work is about to start. */}
          <ApplyModeSwitch
            mode={applyMode.mode}
            busy={applyMode.busy}
            onChange={(next) => void applyMode.setMode(next)}
          />
          <button
            type="button"
            onClick={() => setDocIgnoreOpen((v) => !v)}
            aria-expanded={docIgnoreOpen}
            className="inline-flex h-11 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[11px] text-chalk-dim transition-colors hover:border-ink hover:text-chalk"
            title="配置哪些目录不生成导航文档"
          >
            忽略目录
          </button>
          {canStartWork ? (
            <button
              type="button"
              onClick={() => void handleGenerateWave()}
              className="inline-flex h-11 items-center gap-2 rounded-md border border-ink bg-paper-raise px-5 font-mono text-[13px] text-chalk shadow-2xl transition-colors hover:bg-paper-float"
              title="开始所有依赖已就绪且文件范围不重叠的模块"
            >
              <span className="h-2 w-2 rounded-full bg-emerald" />
              开始工作
            </button>
          ) : null}
        </div>
      ) : null}

      {/* Projection switch. A keyboard-only affordance would be undiscoverable, and this
          view exists precisely for people who cannot use the spatial one. Only shown
          once there is a graph to project. */}
      <div className="fixed right-4 top-4 z-40 flex max-w-[calc(100vw-2rem)] flex-wrap items-center justify-end gap-2">
        {workspace ? (
          <button
            type="button"
            onClick={() => void openPathPicker()}
            className="inline-flex h-8 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-paper-float"
            title={`切换项目\n当前：${workspace.path}`}
          >
            切换项目
          </button>
        ) : null}

        {architecture ? (
          <div
            role="tablist"
            aria-label="架构图视图"
            className="flex overflow-hidden rounded-full border border-ink-ghost bg-paper-raise/90 font-mono text-[10px]"
          >
            {(["canvas", "outline"] as const).map((name) => (
              <button
                key={name}
                type="button"
                role="tab"
                aria-selected={projection === name}
                onClick={() => setProjection(name)}
                className={`px-3 py-2 transition-colors duration-150 ${
                  projection === name
                    ? "bg-ink-ghost text-chalk"
                    : "text-chalk-dim hover:text-chalk"
                }`}
                title={name === "canvas" ? "画布（Alt+O 切换）" : "大纲（Alt+O 切换）"}
              >
                {name === "canvas" ? "画布" : "大纲"}
              </button>
            ))}
          </div>
        ) : null}

        {activeBootstrap && (activeBootstrap.status === "ready" || activeBootstrap.status === "initialized") ? (
          <button
            type="button"
            onClick={() => void handleReopenReview()}
            className="inline-flex h-8 items-center rounded-md border border-amber/60 bg-amber/5 px-3 font-mono text-[11px] text-amber transition-colors hover:bg-amber/10"
            title="回到架构确认页，重新处理来源与依赖"
          >
            重新审阅
          </button>
        ) : null}

        {failedTasks.length > 0 ? (
          <button
            type="button"
            onClick={() => void requestTriage()}
            disabled={triageBusy}
            className="inline-flex h-8 items-center rounded-md border border-vermilion/60 bg-vermilion/10 px-3 font-mono text-[11px] text-vermilion transition-colors hover:bg-vermilion/20 disabled:cursor-not-allowed disabled:opacity-40"
            title="汇总当前失败任务并生成处理建议"
          >
            错误汇总 {failedTasks.length}
          </button>
        ) : null}

        {handoffTasks.length > 0 ? (
          <button
            type="button"
            onClick={() => setHandoffOpen(true)}
            className="inline-flex h-8 items-center rounded-md border border-vermilion/60 bg-vermilion/10 px-3 font-mono text-[11px] text-vermilion transition-colors hover:bg-vermilion/20"
          >
            裁决 {handoffTasks.length}
          </button>
        ) : null}

        <button
          type="button"
          onClick={() => setSnapshotsOpen(true)}
          className="inline-flex h-8 items-center rounded-md border border-ink-dim/45 bg-paper-raise/90 px-3 font-mono text-[11px] text-chalk-dim transition-colors hover:border-ink hover:text-chalk"
        >
          回滚
        </button>

        {suggestionEntries.length > 0 ? (
          <button
            type="button"
            onClick={() => setSuggestionsOpen(true)}
            className="inline-flex h-8 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-paper-float"
          >
            建议 {suggestionEntries.length}
          </button>
        ) : null}

        {architecture ? (
          <button
            type="button"
            onClick={() => {
              void refreshAgents();
              setAgentsOpen(true);
            }}
            className="inline-flex h-8 items-center rounded-md border border-ink/60 bg-ink-ghost px-3 font-mono text-[11px] text-chalk transition-colors hover:bg-paper-float"
          >
            子 Agent
          </button>
        ) : null}

        {workspace && architecture ? (
          <button type="button" onClick={() => setDiscoveryOpen(true)}
            className="inline-flex h-8 items-center rounded-md border border-ink-dim/45 bg-paper-raise/90 px-3 font-mono text-[11px] text-chalk">
            开源选型
          </button>
        ) : null}

        {workspace ? (
          <button
            type="button"
            onClick={() => setSystemOpen(true)}
            className="inline-flex h-8 items-center gap-2 rounded-none border border-emerald/60 bg-emerald/5 px-3 font-mono text-[11px] text-emerald transition-colors hover:bg-emerald/10"
            title="扩展、记忆与缓存"
          >
            <span className="h-2 w-2 rounded-full border border-emerald bg-emerald/30" />
            系统
          </button>
        ) : null}

        {workspace ? (
          <button
            type="button"
            onClick={() => setCodeIntelOpen(true)}
            className="inline-flex h-8 items-center rounded-md border border-sky/60 bg-sky/5 px-3 font-mono text-[11px] text-sky transition-colors hover:bg-sky/10"
            title="代码智能：跳转/引用/悬停/符号"
          >
            代码智能
          </button>
        ) : null}

        <button
          type="button"
          onClick={() => setSettingsOpen(true)}
          aria-label="设置"
          title={configured ? `${provider.model}` : "未配置 API"}
          className="relative inline-flex h-8 w-8 items-center justify-center rounded-full text-chalk-dim transition-colors duration-200 hover:text-chalk"
        >
          <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6}>
            <circle cx="12" cy="12" r="3" />
            <path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1-2.8 2.8-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6v.2h-4V21a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1L4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9A1.7 1.7 0 0 0 3 14H2.8v-4H3a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9L4.2 7 7 4.2l.1.1A1.7 1.7 0 0 0 9 4.6 1.7 1.7 0 0 0 10 3V2.8h4V3a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1L19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9 1.7 1.7 0 0 0 1.6 1h.2v4H21a1.7 1.7 0 0 0-1.6 1z" />
          </svg>
          {!configured && booted ? (
            <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-vermilion" />
          ) : null}
        </button>
      </div>

      {discoveryOpen && workspace ? (
        <ProjectDiscoveryPanel key={workspace.path} path={workspace.path} provider={provider} onClose={() => setDiscoveryOpen(false)} />
      ) : null}

      {snapshotsOpen ? (
        <SnapshotPanel
          snapshots={snapshots}
          busy={busy}
          onClose={() => setSnapshotsOpen(false)}
          onRestore={(id) => void handleRestoreSnapshot(id)}
        />
      ) : null}

      {suggestionsOpen ? (
        <SuggestionsPanel
          entries={suggestionEntries}
          busyId={suggestionBusy}
          onAccept={(moduleId, attachmentId) =>
            void handleAcceptSuggestion(moduleId, attachmentId)
          }
          onReject={(moduleId, attachmentId) =>
            void handleRejectSuggestion(moduleId, attachmentId)
          }
          onClose={() => setSuggestionsOpen(false)}
        />
      ) : null}

      {provenanceLedgerOpen && activeBootstrap?.status === "review" ? (
        <ProvenanceLedger
          claims={activeBootstrap.provenance ?? []}
          moduleNames={Object.fromEntries(
            activeBootstrap.architecture.modules.map((module) => [module.id, module.name]),
          )}
          busyId={provenanceBusyId}
          onAction={handleProvenanceAction}
          onAcceptAll={handleAcceptAllClaims}
          onClose={() => setProvenanceLedgerOpen(false)}
        />
      ) : null}

      {handoffOpen && handoffEntries.length > 0 ? (
        <HandoffPanel
          entries={handoffEntries}
          busyTaskId={busyTaskId}
          onRetry={handleRetryTask}
          onReject={handleRejectTask}
          onSendMessage={handleSendAgentMessage}
          onClose={() => setHandoffOpen(false)}
        />
      ) : null}

      {triageOpen && triageResult ? (
        <TriagePanel
          result={triageResult}
          errors={failedTriageErrors}
          architecture={architecture}
          busy={triageBusy}
          busyTaskId={triageBusyTaskId}
          onRetryAll={() => void handleTriageRetryAll()}
          onRetryTask={(taskId) => void handleTriageRetryTask(taskId)}
          onResumeTask={(taskId) => void handleTriageResumeTask(taskId)}
          onFocus={handleTriageFocus}
          onClose={() => setTriageOpen(false)}
        />
      ) : null}

      {microOpen && microTask ? (
        <MicroTaskPanel
          task={microTask}
          busy={busy}
          onApply={() => void handleMicroTaskApply()}
          onReject={() => void handleMicroTaskReject()}
          onClose={() => setMicroOpen(false)}
        />
      ) : null}

      {agentsOpen ? (
        <AgentsPanel
          agents={agents}
          path={workspace?.path ?? ""}
          onClose={() => setAgentsOpen(false)}
        />
      ) : null}

      {systemOpen && workspace ? (
        <SystemPanel
          path={workspace.path}
          provider={provider}
          onClose={() => setSystemOpen(false)}
        />
      ) : null}

      {codeIntelOpen && workspace ? (
        <CodeIntelPanel
          path={workspace.path}
          module={
            codeIntelModuleId
              ? architecture?.modules.find((m) => m.id === codeIntelModuleId) ?? null
              : null
          }
          moduleProjection={codeIntelProjection?.modules ?? null}
          orphanCount={codeIntelProjection?.orphan.count ?? null}
          onClose={() => setCodeIntelOpen(false)}
        />
      ) : null}

      {docIgnoreOpen && workspace ? (
        <DocIgnorePanel path={workspace.path} onClose={() => setDocIgnoreOpen(false)} />
      ) : null}

      <ApiSettingsDialog
        isOpen={settingsOpen}
        config={provider}
        readingScale={view.scale}
        onReadingScaleChange={setReadingScale}
        onClose={() => setSettingsOpen(false)}
        onSave={(next) => {
          const modeChanged =
            (next.interview_mode ?? null) !== (provider.interview_mode ?? null);
          saveProvider(next);
          if (modeChanged && workspace?.path && activeBootstrap) {
            void setInterviewMode(
              workspace.path,
              next.interview_mode ?? "guided",
            )
              .then((state) => setBootstrap(state))
              .catch((cause) => {
                say({
                  role: "agent",
                  text: `切换访谈模式失败：${(cause as Error).message}`,
                });
              });
          }
          setSettingsOpen(false);
        }}
      />
    </div>
  );
}
