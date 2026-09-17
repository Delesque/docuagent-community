# Purpose

`frontend/` is the React and Vite workbench for architecture interviews, graph review, task execution, patch review, verification, recovery, and project tools. It builds to `../web-next/`, which the Python service serves locally.

# Entry Points

- `src/main.tsx`: mounts the application.
- `src/App.v2.tsx`: owns workspace loading, top-level dialogs, graph/conversation coordination, and persisted UI state.
- `src/api.ts` and `src/api/`: typed clients for the backend `{ok, data}` response envelope.
- `src/graph/store.ts`: shared selection and command state for canvas and outline views.

# User Flows

## Architecture And Conversation

- `src/components/ConversationSurface.tsx`: interview, streaming, retry, review, and overview messages.
- `src/conversation/bootstrapProjection.ts`: maps backend interview state into user-visible conversation actions.
- `src/components/ArchitectureReviewPanel.tsx`: architecture confirmation and revision.
- `src/components/ProvenanceLedger.tsx`: review of confirmed, inferred, recommended, unknown, and rejected claims.
- `src/hooks/useArchitectureWorkflow.ts`: initialization, optional extension capability checks, and architecture lifecycle actions.

## Graph Workbench

- `src/components/GraphCanvas.tsx`: pan, zoom, selection, and graph rendering.
- `src/components/GraphNode.tsx`: architecture/work-item node content, status, cache rates and token totals.
- `src/components/EdgeLayer.tsx`: dependency and contract edges.
- `src/components/OutlineView.tsx`: keyboard and screen-reader alternative to the canvas.
- `src/components/FocusLayer.tsx`: focused module view, task progress, review actions, terminal, and code viewer.
- `src/components/Minimap.tsx` and `src/components/GraphToolbox.tsx`: navigation and graph-level commands.
- `src/graph/layout.ts`, `src/graph/zoom.ts`, and `src/graph/snap.ts`: deterministic layout, camera thresholds, and alignment behavior.

Canvas and outline interactions must dispatch the same commands. Fixed-format nodes and controls must keep stable dimensions so status changes do not shift the workspace.

## Task Delivery And Recovery

- `src/hooks/useTaskFlow.ts`: generation waves, verification, patch review, apply/reject, repair, and checkpoint recovery.
- `src/components/TaskInfoCard.tsx`: task status and task-level commands.
- `src/components/FileDiffBlocks.tsx`: per-file and hunk review.
- `src/components/AgentsPanel.tsx`: agent registry, work log, and error memory.
- `src/components/HandoffPanel.tsx`: blocked-task retry, rejection, and user instructions.
- `src/components/ErrorNodeOverlay.tsx` and `src/components/ErrorEdgeIndicators.tsx`: unresolved workflow error projection.

A stopped or failed task shows checkpoint recovery only when the backend reports `checkpoint_available`. Verified tasks must not retain recovery actions or active error markers.

## Project Tools

- `src/components/ProjectDiscoveryPanel.tsx` and `src/api/discovery.ts`: whole-project query proposal, explicit approval, repository metadata, and selection records.
- `src/components/MemoryPanel.tsx`: review and application of durable memory candidates.
- `src/components/SnapshotPanel.tsx`, `src/components/GitPanel.tsx`, and `src/components/Terminal.tsx`: project snapshots, local Git operations, and approved commands.
- `src/components/McpPanel.tsx`, `src/components/SkillsPanel.tsx`, and `src/components/PluginsPanel.tsx`: optional tools and extensions.
- `src/components/SystemPanel.tsx` and `src/components/v2/ApiSettingsDialog.tsx`: local system and provider settings.
- `src/components/ContextCachePanel.tsx` and `src/components/v2/UsageDataSettings.tsx`: project/module/feature cache and token queries, local anonymous metrics, explicit upload consent and local clearing.

# State And API Rules

- Workspace changes must go through typed API helpers rather than direct fetch calls in view components.
- Conversation state is reduced through `src/conversation/transcript.ts`; startup recovery is handled by `src/conversation/projectRecovery.ts`.
- Project state comes from the backend. Browser storage is limited to user-interface preferences such as reading scale and the last project path.
- Provider keys are never rendered from unmasked API values.
- Anonymous telemetry is locally recorded by default; the UI must not upload until the user explicitly enables consent, and clear/disable actions must remain visible.
- Public GitHub search requires an approved query; selecting a candidate records intent and does not install code.

# Verification

Run from the repository root:

```bash
npm --prefix docuagent/frontend test
npm --prefix docuagent/frontend run build
```

Tests live beside the component, hook, API, graph, or conversation module they cover. Keep behavior in pure helpers when it can be verified without a DOM.
