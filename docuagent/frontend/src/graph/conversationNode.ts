/** The conversation as a node in the architecture graph.
 *
 *  One camera, one meaning for zoom: the whole surface is the graph, and the
 *  conversation is its topmost node. Zoom all the way out and you see the graph with
 *  the conversation at the top; zoom into the conversation and you get the full-screen
 *  typewriter; zoom into a module and you get its detail. Nothing switches cameras at
 *  a boundary, so "further out" always means the same thing.
 *
 *  ## Why this is a frontend concern
 *
 *  Deliberately NOT injected by `normalize_architecture`. The architecture dict is
 *  echoed back to the model as `current_architecture` on every turn, so a synthetic
 *  module there would read as one the agent had itself designed — it would try to
 *  assign the conversation a path, dependencies, and a build position. It would also
 *  land in `architecture.json`, count toward the readiness gate, and have to be
 *  filtered back out of `prune_ui_state` and task scheduling.
 *
 *  The conversation is a view concept: it has no source path and is never generated.
 *  It belongs to the projection, not to the model's design. Everything here operates
 *  on the already-validated architecture and adds nothing the backend must know about.
 */

import type { Architecture, GraphModule } from "./types";

/** Reserved id. Double underscores keep it clear of `slugify`, which strips leading
 *  and trailing separators — no model-supplied id can collide with it. */
export const CONVERSATION_NODE_ID = "__conversation__";

export function isConversationNode(id: string | null | undefined): boolean {
  return id === CONVERSATION_NODE_ID;
}

/** The synthetic module. `path` is empty on purpose: an empty path is the signal that
 *  a node has no file behind it, which is what suppresses the code-tail rendering that
 *  every real module gets. */
export function conversationModule(): GraphModule {
  return {
    id: CONVERSATION_NODE_ID,
    name: "对话流",
    brief: "与 AI 讨论架构",
    responsibility: "架构设计对话与需求追问",
    path: "",
    depends_on: [],
    needs_ui: true,
    group: null,
  };
}

/** Architecture plus the conversation node.
 *
 *  An empty or absent architecture returns null: before the model has produced any
 *  modules there is no graph to show, and the conversation must stay full-screen
 *  instead of landing on a blank canvas with no way back.
 */
export function withConversationNode(
  architecture: Architecture | null,
): Architecture | null {
  if (!architecture) return null;
  if (!Array.isArray(architecture.modules) || architecture.modules.length === 0) {
    return null;
  }
  // Idempotent: re-projecting an already-projected architecture is a no-op.
  if (architecture.modules.some((module) => isConversationNode(module.id))) {
    return architecture;
  }

  return {
    ...architecture,
    // Prepended so a linear read of `modules` — the Outline, screen readers — starts
    // at the conversation, matching its position on the canvas.
    modules: [conversationModule(), ...architecture.modules],
    edges: architecture.edges,
  };
}

/** Strip the projection before anything that must see only real modules: persistence,
 *  readiness counts, task scheduling. */
export function withoutConversationNode(architecture: Architecture): Architecture {
  return {
    ...architecture,
    modules: architecture.modules.filter((module) => !isConversationNode(module.id)),
    edges: architecture.edges.filter(
      (edge) =>
        !isConversationNode(edge.from) && !isConversationNode(edge.to),
    ),
  };
}
