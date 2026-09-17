"""Compatibility facade for the split task pipeline stages.

New code should import the owning task_* module directly. This module preserves the
original public surface and the legacy model-call patch targets used by callers/tests.
"""

from __future__ import annotations

from typing import Any

from agent_tools import AGENT_TOOL_LOOP_PROMPT
import task_runtime as _task_runtime
from task_jobs import (  # noqa: F401  (re-exported for diagnostics/tests)
    JobHandle,
    active_task_ids,
    any_task_cancelled,
    begin_job,
    cancel_jobs,
    finish_job,
    read_jobs,
    reconcile_jobs,
    task_cancelled,
)
from task_state import (  # noqa: F401  (re-exported for existing callers)
    APPLY_MODES,
    MAX_REPAIR_ATTEMPTS,
    TASK_DONE_STATUSES,
    TASK_SCHEMA_VERSION,
    TASK_STATE_FILE,
    _task_by_id,
    architecture_fingerprint,
    mark_architecture_docs_dirty,
    mark_stale_tasks,
    normalize_generated_files,
    read_apply_mode,
    read_task_state,
    write_apply_mode,
    write_task_state,
)
from task_docs import (  # noqa: F401  (re-exported for existing callers)
    DOC_SYNC_PROMPT,
    document_tree_gaps,
    generate_document_tree,
    maintain_document_tree,
    maintain_child_docs,
    retry_documentation,
    sync_docs,
)
import sandbox as _sandbox  # compatibility patch target

# Model calls go through task_runtime so the seam survives this file being split along
# the pipeline: a relocated function resolves these names in its own module namespace,
# where patch("tasks.call_model_json") would no longer reach it. Defining them here as
# thin forwarders keeps both the existing patch target and the post-split one working.


def call_model_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _task_runtime.call_model_json(*args, **kwargs)


def stream_json_model(*args: Any, **kwargs: Any):
    return _task_runtime.stream_json_model(*args, **kwargs)


def run_agent_tool_loop(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _task_runtime.run_agent_tool_loop(*args, **kwargs)


_task_runtime.install_legacy_providers(
    call_model_json=call_model_json,
    stream_json_model=stream_json_model,
    run_agent_tool_loop=run_agent_tool_loop,
)


# Kept as an import alias for callers/tests that still reference the task-level
# Implementation prompt. The live agent loop uses the same prompt directly.
IMPLEMENTATION_PROMPT = AGENT_TOOL_LOOP_PROMPT

from task_plan import (  # noqa: F401  (re-exported for existing callers)
    TASK_PLAN_PROMPT,
    TARGET_MODULE_PLAN_RULE,
    _focused_architecture,
    _merge_planned_module,
    _plan_prompt,
    confirm_tasks,
    merge_planned_modules,
    normalize_task_plan,
    plan_tasks,
    set_task_priority,
    stream_plan_tasks,
    sync_work_items_from_architecture,
)


from task_generate import (  # noqa: F401  (re-exported for existing callers)
    REPAIR_PROMPT,
    TaskCancelled,
    _apply_sandbox_to_main,
    _complete_sandbox_task,
    _deliver_upstream_change_notice_for_module,
    _prepare_wave_locks,
    _pyflakes_issues,
    _record_generation_failure,
    _record_generation_success,
    _record_verification_failure,
    _register_wave,
    _release_wave_locks,
    _repair_sandbox_once,
    _task_cancelled,
    cancel_tasks,
    generate_next_task,
    generate_one_task,
    generate_wave,
    make_patch_entries,
    next_generatable_task,
    ready_tasks,
    recover_stale_running,
    register_completed_documentation,
    stream_generate_one_task,
    stream_generate_wave,
    validate_generated_files,
)

from task_diff import apply_hunks, diff_hunks  # noqa: F401  (compat re-export)
from task_patch import (  # noqa: F401  (re-exported for existing callers)
    _patch_apply_payload,
    apply_task_hunks,
    apply_task_partial,
    apply_task_patch,
    edit_task_patch,
    ensure_file_unchanged,
    reject_task,
    task_hunks,
)
from task_verify import (  # noqa: F401  (re-exported for existing callers)
    _parse_pyflakes_issue,
    diagnose_file,
    repair_task,
    resume_task,
    retry_task,
    stream_verify_task,
    verify_task,
)
