import { useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { TaskPlan } from "../api";
import { deriveHandoffEntries, type HandoffEntry } from "../conversation/appModel";
import type { Architecture } from "../graph/types";

export interface HandoffOptions {
  tasks: TaskPlan | null;
  architecture: Architecture | null;
}

export interface Handoff {
  handoffEntries: HandoffEntry[];
  handoffTasks: TaskPlan["tasks"];
  handoffOpen: boolean;
  setHandoffOpen: Dispatch<SetStateAction<boolean>>;
}

/** Handoff-derived tasks that need a human decision.
 *
 *  The shell auto-opens the panel once per fresh task id, and closes it when no
 *  handoff task remains (for example after a retry succeeds or a project switch).
 */
export function useHandoff({ tasks, architecture }: HandoffOptions): Handoff {
  const [handoffOpen, setHandoffOpen] = useState(false);
  const handoffSeen = useRef<Set<string>>(new Set());

  const handoffEntries = useMemo(
    () => deriveHandoffEntries(tasks, architecture),
    [architecture, tasks],
  );
  const handoffTasks = useMemo(
    () => handoffEntries.map((entry) => entry.task),
    [handoffEntries],
  );

  useEffect(() => {
    if (handoffTasks.length === 0) return;
    const fresh = handoffTasks.find((task) => !handoffSeen.current.has(task.id));
    if (!fresh) return;
    handoffSeen.current.add(fresh.id);
    setHandoffOpen(true);
  }, [handoffTasks]);

  useEffect(() => {
    if (handoffOpen && handoffTasks.length === 0) setHandoffOpen(false);
  }, [handoffOpen, handoffTasks.length]);

  return { handoffEntries, handoffTasks, handoffOpen, setHandoffOpen };
}
