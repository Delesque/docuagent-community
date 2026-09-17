"""Bootstrap-phase HTTP route handlers that do not call the model seam directly.

`public_state` is the frontend contract for interview state, so it lives with the
bootstrap routes that return it. Model-calling handlers stay in `main.py` because
tests patch `docuagent.call_architecture_model` and friends through `main`'s namespace.
"""

from __future__ import annotations

from typing import Any
import copy

import importscan as _importscan
import onboard as _onboard
import snapshots as _snapshots
import tasks as _tasks
import workspace as _workspace
from architecture_preflight import require_architecture_preflight
from bootstrap import (
    INTERVIEW_TOPICS,
    ASPECT_MAX_QUESTIONS,
    aspect_question_count,
    normalize_interview_mode,
    refresh_progress,
)
from core import (
    MAX_FIRST_VERSION_MODULES,
    MIN_FIRST_VERSION_MODULES,
    SCHEMA_VERSION,
    WorkspaceError,
    architecture_readiness_issues,
    diagnostics_receipt,
    normalize_architecture,
    normalize_edge_type,
    slugify,
)
from llm_client import ProviderConfig
from scaffold import (
    create_scaffold,
    render_initial_feature,
    render_project_overview,
    write_if_missing,
)
from standards import DEFAULT_STANDARDS_MD
from workspace import (
    atomic_write_json,
    atomic_write_text,
    inspect_workspace as _inspect_workspace,
    managed_path,
    prune_ui_state,
    read_json,
    resolve_project_path,
    utc_now,
    write_ui_state,
)


def public_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    """The subset of interview state the frontend is allowed to see."""
    if not state:
        return None
    current = state.get("current_question")
    raw_architecture = state.get("architecture", {})
    architecture = raw_architecture if isinstance(raw_architecture, dict) else {}
    # `created_files` and `documents` stay server-side: they are persisted in
    # bootstrap.json but are not part of the frontend contract.
    result = {
        "schema_version": state.get("schema_version"),
        "status": state.get("status"),
        "project": state.get("project"),
        "graph_diagnostics": state.get("graph_diagnostics") or [],
        "answers": state.get("answers", {}),
        "architecture": architecture,
        "provenance": state.get("architecture", {}).get("provenance", []),
        "graph_quality_issues": state.get("graph_quality_issues") or [],
        "architecture_version": int(state.get("architecture_version") or 0),
        "current_question": current,
        "progress": state.get("progress", 0),
        "updated_at": state.get("updated_at"),
        "model_notice": state.get("model_notice"),
        "thinking": state.get("thinking") or "",
        "agent_mode": state.get("agent_mode", "fallback"),
        "model_name": state.get("model_name"),
        "user_profile": state.get("user_profile", {}),
        "interview_mode": normalize_interview_mode(state.get("interview_mode")),
        "profile_progress": int(state.get("profile_progress", 0)),
        "mode_offer": state.get("mode_offer"),
        "confirmed_at": state.get("confirmed_at"),
        "architecture_delta": state.get("architecture_delta"),
    }
    # Interview progress metadata: how many planned aspects the stateless agent
    # has not started yet. When the model maintains its own `interview_plan`,
    # progress follows that plan (counts only — the draft itself stays private
    # until ready=true); otherwise it falls back to the fixed topic list so a
    # legacy model or an in-flight old session still gets a progress display.
    if state.get("status") == "interviewing":
        plan = state.get("interview_plan")
        answers = state.get("answers", {})
        question_aspects = state.get("question_aspects")
        if plan:
            remaining: list[str] = []
            enriched = []
            for aspect in plan:
                asked = aspect_question_count(
                    plan, aspect["id"], answers, question_aspects
                )
                enriched.append(
                    {
                        "id": aspect["id"],
                        "title": aspect["title"],
                        "asked": asked,
                        "max": ASPECT_MAX_QUESTIONS,
                    }
                )
                if asked == 0:
                    remaining.append(aspect["title"])
            result["topics_remaining"] = remaining
            result["topics_total"] = len(plan)
            result["interview_plan"] = enriched
        else:
            result["topics_remaining"] = [
                topic["title"] for topic in INTERVIEW_TOPICS
                if topic["id"] not in (state.get("answers") or {})
            ]
            result["topics_total"] = len(INTERVIEW_TOPICS)
    # Onboarding extras ride the same public-state envelope so the frontend's
    # existing review/confirm flow can carry them through without a new contract.
    if state.get("onboard"):
        result["onboard"] = True
        result["onboard_summary"] = state.get("onboard_summary") or ""
        result["doc_tree"] = state.get("doc_tree") or []
        result["suggestions"] = state.get("suggestions") or []
    return result

def inspect_workspace(raw_path: str) -> dict[str, Any]:
    """Bind `public_state` into the workspace snapshot."""
    project_root = resolve_project_path(raw_path)
    result = _inspect_workspace(raw_path, public_state)
    result["capabilities"] = {
        "project_reconstruction": _onboard.available(),
    }
    result["work_state"] = _tasks.read_task_state(project_root)
    document = _workspace.architecture_document(project_root)
    result["architecture_version"] = int(
        (result.get("bootstrap") or {}).get("architecture_version")
        or (document or {}).get("architecture_version")
        or 0
    )
    return result


PROVENANCE_ACTIONS = frozenset({"accept", "modify", "unknown", "reject"})


def update_provenance(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one user decision to a provenance claim.

    Accept and modify turn the claim into a confirmed fact; unknown and reject keep the
    text but relabel the source. During review the architecture lives in
    `bootstrap.json`; after finalize it lives in `architecture.json`, so both sources
    are supported.
    """
    project_root = resolve_project_path(str(payload.get("path", "")))
    claim_id = str(payload.get("claim_id") or "").strip()
    if not claim_id:
        raise WorkspaceError("缺少来源条目 ID。")
    action = str(payload.get("action") or "").strip().lower()
    if action not in PROVENANCE_ACTIONS:
        raise WorkspaceError("不支持的操作。")

    state = read_json(managed_path(project_root, "bootstrap.json"))
    state_architecture = state.get("architecture") if isinstance(state, dict) else None
    document = _workspace.architecture_document(project_root)

    in_state = (
        isinstance(state_architecture, dict)
        and bool(state_architecture.get("modules"))
    )
    architecture = state_architecture if in_state else document
    if not isinstance(architecture, dict):
        raise WorkspaceError("这个项目还没有架构可以修改。")

    claims = architecture.get("provenance")
    if not isinstance(claims, list):
        raise WorkspaceError("这个架构还没有来源条目。")
    claim = next(
        (item for item in claims if isinstance(item, dict) and item.get("id") == claim_id),
        None,
    )
    if claim is None:
        raise WorkspaceError(f"找不到来源条目：{claim_id}")

    if action == "accept":
        claim["source"] = "confirmed"
    elif action == "modify":
        text_value = str(payload.get("text") or "").strip()[:500]
        if len(text_value) < 2:
            raise WorkspaceError("请填写修改后的来源说明。")
        claim["text"] = text_value
        claim["source"] = "confirmed"
    elif action == "unknown":
        claim["source"] = "unknown"
    else:
        claim["source"] = "rejected"

    if in_state:
        state["architecture"] = architecture
        state["updated_at"] = utc_now()
        atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
        _snapshots.create_snapshot(project_root, f"更新来源：{claim_id}")
        return {"state": public_state(state)}
    else:
        atomic_write_json(managed_path(project_root, "architecture.json"), document)
        _snapshots.create_snapshot(project_root, f"更新来源：{claim_id}")
        return {"architecture": document}


def set_interview_mode(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    state_path = managed_path(project_root, "bootstrap.json")
    state = read_json(state_path)
    if not state:
        raise WorkspaceError("项目尚未开始初始化。")
    state["interview_mode"] = normalize_interview_mode(payload.get("interview_mode"))
    state["mode_offer"] = None
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)
    return public_state(state) or {}

def confirm_bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    """The human confirmation gate. Nothing is generated until this passes."""
    project_root = resolve_project_path(str(payload.get("path", "")))
    state_path = managed_path(project_root, "bootstrap.json")
    state = read_json(state_path)
    if not state:
        raise WorkspaceError("项目尚未开始初始化。")
    if state.get("status") == "ready":
        return public_state(state) or {}
    if state.get("status") != "review":
        raise WorkspaceError("架构访谈尚未进入确认阶段。")

    issues = architecture_readiness_issues(state.get("architecture", {}))
    if issues:
        raise WorkspaceError("架构仍缺少：" + "、".join(issues[:6]))
    require_architecture_preflight(project_root, state.get("architecture", {}))
    # The inference gate lives here, not in the architecture model's validation:
    # blocking the *answer* path surfaced a product rule as "AI 模型调用失败，请重试",
    # which points the user at their API key for something retrying cannot fix. At the
    # confirmation gate the same refusal is actionable — the review stage has the
    # provenance ledger (with accept-all) sitting right there.
    pending = [
        claim
        for claim in (state.get("architecture", {}).get("provenance") or [])
        if isinstance(claim, dict) and claim.get("source") == "inferred"
    ]
    if pending:
        raise WorkspaceError(
            f"还有 {len(pending)} 条 AI 推测未确认，请先在「来源确认」里逐条处理"
            "（或直接全部确认），再确认架构。",
            diagnostics_receipt(
                [
                    {
                        "rule": "provenance/inferred-remaining",
                        "severity": "error",
                        "subject": {
                            "surface": "bootstrap.architecture",
                            "type": "provenance",
                        },
                        "evidence": {
                            "count": len(pending),
                            "ids": [
                                str(claim.get("id") or "")
                                for claim in pending[:6]
                            ],
                        },
                        "message": "仍有未确认的 AI 推测。",
                    }
                ]
            ),
        )
    state["status"] = "ready"
    state["architecture_version"] = 1
    state["confirmed_at"] = utc_now()
    refresh_progress(state)
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)
    _snapshots.create_snapshot(project_root, "确认架构")
    return public_state(state) or {}

def finalize_bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    """Write memory, scaffold, then ask the Doc Maintainer for the document tree.

    Managed documents under `.docuagent/` are rewritten; business files go through
    `write_if_missing`, so importing an existing project never clobbers its code.
    """
    project_root = resolve_project_path(str(payload.get("path", "")))
    state_path = managed_path(project_root, "bootstrap.json")
    state = read_json(state_path)
    if not state:
        raise WorkspaceError("项目尚未开始初始化。")
    if state.get("status") == "initialized":
        return public_state(state) or {}
    if state.get("status") != "ready":
        raise WorkspaceError("架构访谈尚未完成。")
    provider = ProviderConfig.from_payload(payload, role="documentation")
    if provider is None:
        raise WorkspaceError("生成目录文档需要启用模型。")

    architecture = state.get("architecture", {})
    require_architecture_preflight(project_root, architecture)

    # Scaffold first, so the document tree below lists the files that now exist.
    created_files = create_scaffold(project_root, state)

    # Long-term memory: the stable project overview.
    atomic_write_text(
        managed_path(project_root, "project.md"), render_project_overview(state)
    )

    write_if_missing(
        managed_path(project_root, "standards.md"),
        DEFAULT_STANDARDS_MD,
    )

    # Short-term memory: the current feature requirement.
    atomic_write_text(
        managed_path(project_root, "features", "initial-build.md"),
        render_initial_feature(state),
    )

    # `architecture.json` is a first-class architecture source, not a bootstrap
    # appendix: finished and hand-authored projects have no bootstrap.json but the
    # canvas must still render.
    architecture_document = {
        "schema_version": SCHEMA_VERSION,
        "architecture_version": 1,
        "generated_at": utc_now(),
        "project": state["project"],
        **architecture,
    }
    atomic_write_json(
        managed_path(project_root, "architecture.json"), architecture_document
    )

    # Project Contract Registry: new projects already have the minimal scaffold
    # version written by create_scaffold; imported projects get an initial draft
    # from architecture + importscan. In both cases the file is the machine source,
    # AI_ARCH.md remains the human-readable projection.
    if state["project"].get("mode") == "imported":
        _importscan.write_contracts_draft(
            project_root,
            architecture,
            state["project"],
        )

    # Drop layout entries for modules that no longer exist, then persist. Written
    # defensively because a hand-edited architecture.json can contain junk entries.
    module_ids = {
        module["id"]
        for module in architecture.get("modules", [])
        if isinstance(module, dict) and module.get("id")
    }
    write_ui_state(project_root, prune_ui_state(project_root, module_ids))

    _tasks.generate_document_tree(
        project_root,
        provider,
        architecture,
        state.get("project") or {},
        all_directories=True,
    )

    atomic_write_json(
        managed_path(project_root, "state.json"),
        {
            "schema_version": SCHEMA_VERSION,
            "architecture_version": 1,
            "phase": "architecture_ready",
            "next_phase": "task_dag",
            "docs_in_sync": True,
            "updated_at": utc_now(),
        },
    )

    # Onboarding suggestions are mounted as node attachments only after the
    # architecture is confirmed and written, so "accept -> task" always targets a
    # real module in a real architecture.
    suggestions = state.get("suggestions")
    if isinstance(suggestions, list) and suggestions:
        _onboard.write_suggestion_attachments(project_root, suggestions)

    _tasks.sync_work_items_from_architecture(
        project_root,
        architecture,
        state.get("project") or {},
    )

    state["status"] = "initialized"
    state["architecture_version"] = max(
        1, int(state.get("architecture_version") or 0)
    )
    state["created_files"] = created_files
    refresh_progress(state)
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)

    _snapshots.create_snapshot(project_root, "生成项目框架")

    # `created_files` 与 `documents` 只出现在本次响应里，不写入 bootstrap.json：
    # 它们描述的是"这次生成了什么"，不是需要长期持有的项目状态。
    return {
        **(public_state(state) or {}),
        "created_files": created_files,
        "work_state": _tasks.read_task_state(project_root),
        "documents": [
            ".docuagent/project.md",
            ".docuagent/standards.md",
            ".docuagent/features/initial-build.md",
            ".docuagent/architecture.json",
            ".docuagent/state.json",
            ".docuagent/ui-state.json",
            "AI_ARCH.md",
        ],
    }

def undo_architecture(payload: dict[str, Any]) -> dict[str, Any]:
    """Restore the most recent pre-edit architecture document.

    Every accepted edit pushes the document it replaced onto
    `.docuagent/architecture-history.json`; undo pops that entry, writes it back as the
    authoritative `architecture.json`, and keeps `bootstrap.json` and UI state in step.
    """
    project_root = resolve_project_path(str(payload.get("path", "")))
    previous = _workspace.pop_architecture_history(project_root)
    if previous is None:
        raise WorkspaceError("没有可以撤销的架构修改。")

    migrated, _ = _workspace.migrate_architecture_shape(previous)
    atomic_write_json(managed_path(project_root, "architecture.json"), migrated)

    state = read_json(managed_path(project_root, "bootstrap.json"))
    if state:
        state["architecture"] = migrated
        state["architecture_version"] = int(migrated.get("architecture_version") or 0)
        state["thinking"] = ""
        state["updated_at"] = utc_now()
        atomic_write_json(managed_path(project_root, "bootstrap.json"), state)

    module_ids = {module["id"] for module in migrated.get("modules", [])}
    write_ui_state(project_root, prune_ui_state(project_root, module_ids))
    _workspace.write_stale_modules(project_root, set())
    _tasks.mark_architecture_docs_dirty(project_root, migrated)
    remaining = len(_workspace.read_architecture_history(project_root))

    _snapshots.create_snapshot(project_root, "撤销架构")

    return {
        "architecture": migrated,
        "architecture_version": int(migrated.get("architecture_version") or 0),
        "history_remaining": remaining,
    }

def _stale_after_edit(
    old_architecture: dict[str, Any],
    new_architecture: dict[str, Any],
) -> set[str]:
    old_modules = {
        module["id"]: module
        for module in old_architecture.get("modules", [])
        if isinstance(module, dict) and module.get("id")
    }
    new_modules = {
        module["id"]: module
        for module in new_architecture.get("modules", [])
        if isinstance(module, dict) and module.get("id")
    }
    changed: set[str] = set()
    for module_id in set(old_modules) | set(new_modules):
        old = old_modules.get(module_id)
        new = new_modules.get(module_id)
        if old is None or new is None:
            changed.add(module_id)
            continue
        for field in ("responsibility", "brief", "path", "depends_on", "needs_ui"):
            if old.get(field) != new.get(field):
                changed.add(module_id)
                break

    stale = set(changed)
    edges = [
        edge
        for edge in new_architecture.get("edges", [])
        if isinstance(edge, dict) and edge.get("from") and edge.get("to")
    ]
    queue = list(changed)
    while queue:
        current = queue.pop()
        for edge in edges:
            if edge["to"] == current and edge["from"] not in stale:
                stale.add(edge["from"])
                queue.append(edge["from"])
    return stale

def _stale_after_module_edit(
    architecture: dict[str, Any],
    module_id: str,
) -> set[str]:
    """Mark a module and every build-order dependent stale after a requirement edit."""
    stale = {module_id}
    edges = [
        edge
        for edge in architecture.get("edges", [])
        if isinstance(edge, dict) and edge.get("from") and edge.get("to")
    ]
    queue = list(stale)
    while queue:
        current = queue.pop()
        for edge in edges:
            if edge["to"] == current and edge["from"] not in stale:
                stale.add(edge["from"])
                queue.append(edge["from"])
    return stale

def update_module_requirement(payload: dict[str, Any]) -> dict[str, Any]:
    """Edit one node's requirement without a model call.

    This is the "改需求" half of Phase 2: the requirement is a first-class node edit,
    so it must not depend on an AI turn. It increments `architecture_version`, pushes
    the previous document onto the undo history, and marks the edited module plus every
    build-order dependent stale, which blocks their tasks until re-confirmed.
    """
    project_root = resolve_project_path(str(payload.get("path", "")))
    module_id = str(payload.get("module_id") or "").strip()
    requirement = str(payload.get("requirement") or "").strip()
    if not module_id:
        raise WorkspaceError("请选择要修改的节点。")
    if len(requirement) < 2:
        raise WorkspaceError("请填写至少 2 个字符的需求说明。")

    document = _workspace.architecture_document(project_root)
    state = read_json(managed_path(project_root, "bootstrap.json"))
    if state and state.get("architecture", {}).get("modules"):
        current = state["architecture"]
    elif document and document.get("modules"):
        current = document
    else:
        raise WorkspaceError("这个项目还没有架构可以修改。")

    modules = current.get("modules", [])
    target = next(
        (
            module
            for module in modules
            if isinstance(module, dict) and module.get("id") == module_id
        ),
        None,
    )
    if target is None:
        raise WorkspaceError(f"找不到模块：{module_id}")

    target["brief"] = requirement[:500]
    target["responsibility"] = requirement[:1200]
    stale = _stale_after_module_edit(current, module_id)
    _workspace.write_stale_modules(project_root, stale)

    project = (document or {}).get("project") or (state or {}).get("project") or {}
    previous_version = int((document or {}).get("architecture_version") or 0)
    previous_doc = document
    if not previous_doc and state and state.get("architecture", {}).get("modules"):
        previous_doc = {
            "schema_version": SCHEMA_VERSION,
            "architecture_version": 0,
            "generated_at": state.get("updated_at") or utc_now(),
            "project": project,
            **state["architecture"],
        }
    architecture_doc = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": utc_now(),
        "project": project,
        **current,
    }
    architecture_doc["architecture_version"] = previous_version + 1
    if previous_doc:
        _workspace.push_architecture_history(project_root, previous_doc)
    atomic_write_json(
        managed_path(project_root, "architecture.json"), architecture_doc
    )
    if state:
        state["architecture"] = current
        state["architecture_version"] = architecture_doc["architecture_version"]
        state["thinking"] = ""
        state["updated_at"] = utc_now()
        atomic_write_json(managed_path(project_root, "bootstrap.json"), state)

    _tasks.mark_architecture_docs_dirty(project_root, current)
    _snapshots.create_snapshot(project_root, f"编辑节点需求：{module_id}")
    return {
        "architecture": current,
        "architecture_version": architecture_doc["architecture_version"],
        "stale_modules": sorted(stale),
        "history_remaining": len(_workspace.read_architecture_history(project_root)),
    }





def _commit_direct_architecture_edit(
    project_root: Any,
    state: dict[str, Any] | None,
    document: dict[str, Any] | None,
    old_architecture: dict[str, Any],
    current: dict[str, Any],
    action_label: str,
) -> dict[str, Any]:
    """Persist a deterministic architecture edit with the same rules as node ops."""
    stale = _stale_after_edit(old_architecture, current)
    _workspace.write_stale_modules(project_root, stale)

    architecture_version = 0
    history_remaining = len(_workspace.read_architecture_history(project_root))
    is_review = bool(state and state.get("status") == "review")
    if is_review:
        architecture_version = int(state.get("architecture_version") or 0)
        if state:
            state["architecture"] = current
            state["updated_at"] = utc_now()
            atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
    else:
        project = (document or {}).get("project") or (state or {}).get("project") or {}
        previous_version = int((document or {}).get("architecture_version") or 0)
        previous_doc = copy.deepcopy(document)
        if not previous_doc and state and state.get("architecture", {}).get("modules"):
            previous_doc = {
                "schema_version": SCHEMA_VERSION,
                "architecture_version": 0,
                "generated_at": state.get("updated_at") or utc_now(),
                "project": project,
                **state["architecture"],
            }
        architecture_doc = {
            "schema_version": SCHEMA_VERSION,
            "architecture_version": previous_version + 1,
            "generated_at": utc_now(),
            "project": project,
            **current,
        }
        architecture_version = architecture_doc["architecture_version"]
        if previous_doc:
            _workspace.push_architecture_history(project_root, previous_doc)
        atomic_write_json(managed_path(project_root, "architecture.json"), architecture_doc)
        if state:
            state["architecture"] = current
            state["architecture_version"] = architecture_version
            state["thinking"] = ""
            state["updated_at"] = utc_now()
            atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
        _tasks.sync_work_items_from_architecture(project_root, current, project)
        module_ids = {module["id"] for module in current.get("modules", [])}
        write_ui_state(project_root, prune_ui_state(project_root, module_ids))
        history_remaining = len(_workspace.read_architecture_history(project_root))

    _snapshots.create_snapshot(project_root, action_label)
    return {
        "architecture": current,
        "architecture_version": architecture_version,
        "stale_modules": sorted(stale),
        "history_remaining": history_remaining,
        "work_state": _tasks.read_task_state(project_root),
    }

NODE_OPERATIONS = frozenset({"rename", "responsibility", "uncertain", "delete", "merge", "split"})


def _unique_module_id(base: str, existing: set[str]) -> str:
    candidate = slugify(base) or "module"
    index = 2
    while candidate in existing:
        candidate = f"{slugify(base) or 'module'}-{index}"
        index += 1
    return candidate


def _module_by_id(modules: list[dict[str, Any]], module_id: str) -> dict[str, Any] | None:
    return next(
        (module for module in modules if module.get("id") == module_id),
        None,
    )


def update_architecture_nodes(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one deterministic node-level edit without a model call.

    Supported actions are rename, responsibility, uncertain, delete, merge, and split.
    During review these edit the private draft; after finalize they bump
    `architecture_version` and push the previous document onto undo history exactly
    like an AI revision.
    """
    project_root = resolve_project_path(str(payload.get("path", "")))
    action = str(payload.get("action") or "").strip().lower()
    if action not in NODE_OPERATIONS:
        raise WorkspaceError("不支持的节点操作。")

    state = read_json(managed_path(project_root, "bootstrap.json"))
    document = _workspace.architecture_document(project_root)
    if state and state.get("architecture", {}).get("modules"):
        current = state["architecture"]
    elif document and document.get("modules"):
        current = document
    else:
        raise WorkspaceError("这个项目还没有架构可以修改。")
    current = copy.deepcopy(current)
    old_architecture = copy.deepcopy(current)
    modules = current.setdefault("modules", [])
    edges = current.setdefault("edges", [])
    groups = current.setdefault("groups", [])
    module_id = str(payload.get("module_id") or payload.get("target_id") or "").strip()
    target = _module_by_id(modules, module_id)

    if action == "rename":
        if target is None:
            raise WorkspaceError(f"找不到模块：{module_id}")
        name = str(payload.get("name") or "").strip()[:120]
        if len(name) < 2:
            raise WorkspaceError("请填写至少 2 个字符的模块名称。")
        target["name"] = name

    elif action == "responsibility":
        if target is None:
            raise WorkspaceError(f"找不到模块：{module_id}")
        text = str(payload.get("text") or "").strip()[:1200]
        if len(text) < 2:
            raise WorkspaceError("请填写至少 2 个字符的职责说明。")
        target["responsibility"] = text
        target["brief"] = text[:140]

    elif action == "uncertain":
        if target is None:
            raise WorkspaceError(f"找不到模块：{module_id}")
        target["uncertain"] = True
        target["uncertain_reason"] = str(
            payload.get("reason") or "用户标记为不确定。"
        ).strip()[:200]

    elif action == "delete":
        if target is None:
            raise WorkspaceError(f"找不到模块：{module_id}")
        modules[:] = [module for module in modules if module.get("id") != module_id]
        edges[:] = [
            edge for edge in edges
            if edge.get("from") != module_id and edge.get("to") != module_id
        ]
        for module in modules:
            module["depends_on"] = [
                dep for dep in module.get("depends_on", [])
                if dep != module_id
            ]
        for group in groups:
            group["members"] = [
                member for member in group.get("members", [])
                if member != module_id
            ]

    elif action == "merge":
        if target is None:
            raise WorkspaceError(f"找不到模块：{module_id}")
        target_id = target["id"]
        source_ids = [
            str(item).strip()
            for item in payload.get("source_ids", [])
            if str(item or "").strip()
        ]
        if not source_ids:
            raise WorkspaceError("请选择要合并的模块。")
        sources = [
            module for module in modules
            if module.get("id") in source_ids
        ]
        missing = set(source_ids) - {module.get("id") for module in sources}
        if missing:
            raise WorkspaceError("找不到模块：" + "、".join(sorted(missing)))
        target_id = target["id"]
        for source in sources:
            target["responsibility"] = (
                f"{target.get('responsibility', '')}；"
                f"{source.get('responsibility', '')}"
            ).strip("；")[:1200]
            target["brief"] = target["responsibility"][:140]
            if source.get("uncertain"):
                target["uncertain"] = True
                target["uncertain_reason"] = (
                    f"{target.get('uncertain_reason', '')}；"
                    f"{source.get('uncertain_reason', '')}"
                ).strip("；")[:200]
        modules[:] = [
            module for module in modules
            if module.get("id") == target_id or module.get("id") not in source_ids
        ]
        source_set = set(source_ids)
        for edge in edges:
            if edge.get("from") in source_set:
                edge["from"] = target_id
                edge["reason"] = f"合并自 {edge.get('reason', '') or '源模块'}。".strip()[:200]
            if edge.get("to") in source_set:
                edge["to"] = target_id
                edge["reason"] = f"合并至 {edge.get('reason', '') or '目标模块'}。".strip()[:200]
        edges[:] = [
            edge for edge in edges
            if edge.get("from") != edge.get("to")
        ]
        for module in modules:
            deps = []
            for dep in module.get("depends_on", []):
                if dep in source_set:
                    dep = target_id
                if dep != module.get("id") and dep not in deps:
                    deps.append(dep)
            module["depends_on"] = deps
        if target_id not in target.get("depends_on", []):
            target["depends_on"] = [
                dep for source in sources for dep in source.get("depends_on", [])
                if dep != target_id
            ] + target.get("depends_on", [])
            target["depends_on"] = list(dict.fromkeys(target["depends_on"]))
        for group in groups:
            members = group.get("members", [])
            if target_id in members:
                members = [m for m in members if m not in source_set]
            else:
                members = [m for m in members if m not in source_set]
            group["members"] = members

    elif action == "split":
        if target is None:
            raise WorkspaceError(f"找不到模块：{module_id}")
        if len(modules) + 1 > MAX_FIRST_VERSION_MODULES:
            raise WorkspaceError(
                f"拆分后模块数会超过 {MAX_FIRST_VERSION_MODULES} 个，请先合并其他模块。"
            )
        name_a = str(payload.get("name_a") or "").strip()[:120]
        name_b = str(payload.get("name_b") or "").strip()[:120]
        if len(name_a) < 2 or len(name_b) < 2:
            raise WorkspaceError("拆分需要两个至少 2 个字符的模块名称。")
        existing = {module.get("id") for module in modules}
        id_a = _unique_module_id(name_a, existing)
        existing.add(id_a)
        id_b = _unique_module_id(name_b, existing)
        old_id = target["id"]
        old_path = str(target.get("path") or "src")
        deps = list(target.get("depends_on", []))
        group = target.get("group")
        module_a = {
            **copy.deepcopy(target),
            "id": id_a,
            "name": name_a,
            "responsibility": str(payload.get("responsibility_a") or name_a).strip()[:1200],
            "brief": str(payload.get("brief_a") or payload.get("responsibility_a") or name_a).strip()[:140],
            "path": str(payload.get("path_a") or f"{old_path}-a").strip()[:200],
            "depends_on": list(deps),
            "group": group,
            "uncertain": False,
            "uncertain_reason": "",
        }
        module_b = {
            **copy.deepcopy(target),
            "id": id_b,
            "name": name_b,
            "responsibility": str(payload.get("responsibility_b") or name_b).strip()[:1200],
            "brief": str(payload.get("brief_b") or payload.get("responsibility_b") or name_b).strip()[:140],
            "path": str(payload.get("path_b") or f"{old_path}-b").strip()[:200],
            "depends_on": list(deps),
            "group": group,
            "uncertain": False,
            "uncertain_reason": "",
        }
        for module in modules:
            if old_id in module.get("depends_on", []):
                module["depends_on"] = [
                    dep for dep in module.get("depends_on", []) if dep != old_id
                ] + [id_a, id_b]
        modules[:] = [
            module for module in modules if module.get("id") != old_id
        ] + [module_a, module_b]
        out_edges = [
            edge for edge in edges
            if edge.get("from") == old_id and edge.get("to") != old_id
        ]
        in_edges = [
            edge for edge in edges
            if edge.get("to") == old_id and edge.get("from") != old_id
        ]
        edges[:] = [
            edge for edge in edges
            if edge.get("from") != old_id and edge.get("to") != old_id
        ]
        for edge in out_edges:
            edges.append({
                "from": id_a,
                "to": edge.get("to"),
                "kind": edge.get("kind", "uses"),
                "label": edge.get("label", ""),
                "reason": f"拆分自 {old_id}：{edge.get('reason', '')}".strip()[:200],
            })
            edges.append({
                "from": id_b,
                "to": edge.get("to"),
                "kind": edge.get("kind", "uses"),
                "label": edge.get("label", ""),
                "reason": f"拆分自 {old_id}：{edge.get('reason', '')}".strip()[:200],
            })
        for edge in in_edges:
            edges.append({
                "from": edge.get("from"),
                "to": id_a,
                "kind": edge.get("kind", "uses"),
                "label": edge.get("label", ""),
                "reason": f"拆分自 {old_id}：{edge.get('reason', '')}".strip()[:200],
            })
            edges.append({
                "from": edge.get("from"),
                "to": id_b,
                "kind": edge.get("kind", "uses"),
                "label": edge.get("label", ""),
                "reason": f"拆分自 {old_id}：{edge.get('reason', '')}".strip()[:200],
            })

    provenance = current.get("provenance", [])
    current = normalize_architecture(current)
    current["provenance"] = provenance
    if len(current["modules"]) > MAX_FIRST_VERSION_MODULES:
        raise WorkspaceError(
            f"架构最多 {MAX_FIRST_VERSION_MODULES} 个模块，当前 {len(current['modules'])} 个。"
        )
    is_review = bool(state and state.get("status") == "review")
    if is_review and len(current["modules"]) < MIN_FIRST_VERSION_MODULES:
        raise WorkspaceError(
            f"首版架构至少需要 {MIN_FIRST_VERSION_MODULES} 个模块。"
        )

    return _commit_direct_architecture_edit(
        project_root,
        state,
        document,
        old_architecture,
        current,
        f"节点操作：{action} {module_id or ''}",
    )




EDGE_OPERATIONS = frozenset({"accept", "type", "reason", "delete"})


def update_architecture_edges(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one deterministic edge review decision without a model call."""
    project_root = resolve_project_path(str(payload.get("path", "")))
    action = str(payload.get("action") or "").strip().lower()
    if action not in EDGE_OPERATIONS:
        raise WorkspaceError("不支持的连线操作。")
    source = str(payload.get("from") or "").strip()
    target = str(payload.get("to") or "").strip()
    if not source or not target:
        raise WorkspaceError("缺少连线端点。")

    state = read_json(managed_path(project_root, "bootstrap.json"))
    document = _workspace.architecture_document(project_root)
    if state and state.get("architecture", {}).get("modules"):
        current = state["architecture"]
    elif document and document.get("modules"):
        current = document
    else:
        raise WorkspaceError("这个项目还没有架构可以修改。")
    current = copy.deepcopy(current)
    old_architecture = copy.deepcopy(current)
    edges = current.setdefault("edges", [])
    edge = next(
        (item for item in edges if item.get("from") == source and item.get("to") == target),
        None,
    )
    if edge is None:
        raise WorkspaceError(f"找不到连线：{source} → {target}")

    if action == "accept":
        edge["accepted"] = True
    elif action == "type":
        kind = normalize_edge_type(payload.get("kind"))
        edge["kind"] = kind
    elif action == "reason":
        reason = str(payload.get("reason") or "").strip()[:200]
        if len(reason) < 2:
            raise WorkspaceError("请填写至少 2 个字符的连线原因。")
        edge["reason"] = reason
    else:
        edges[:] = [
            item for item in edges
            if not (item.get("from") == source and item.get("to") == target)
        ]
        source_module = _module_by_id(current.get("modules", []), source)
        if source_module is not None:
            source_module["depends_on"] = [
                dep for dep in source_module.get("depends_on", []) if dep != target
            ]

    provenance = current.get("provenance", [])
    current = normalize_architecture(current)
    current["provenance"] = provenance
    return _commit_direct_architecture_edit(
        project_root,
        state,
        document,
        old_architecture,
        current,
        f"连线操作：{action} {source} → {target}",
    )


def reopen_architecture_review(payload: dict[str, Any]) -> dict[str, Any]:
    """Move a confirmed or generated architecture back into the review gate."""
    project_root = resolve_project_path(str(payload.get("path", "")))
    state_path = managed_path(project_root, "bootstrap.json")
    state = read_json(state_path)
    if not state:
        raise WorkspaceError("这个项目没有访谈状态，不能重新进入确认页。")
    if state.get("status") not in {"ready", "initialized"}:
        raise WorkspaceError("只有已确认或已生成框架的项目可以重新进入确认页。")

    document = _workspace.architecture_document(project_root)
    if document and document.get("modules"):
        state["architecture"] = document
    state["status"] = "review"
    state["current_question"] = None
    state["confirmed_at"] = None
    state["progress"] = 95
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)
    _snapshots.create_snapshot(project_root, "重新进入架构确认")
    return public_state(state) or {}


def clear_stale(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    current = _workspace.read_stale_modules(project_root)
    requested = payload.get("module_ids")
    remaining = (
        set() if requested is None else current - {str(module_id) for module_id in requested}
    )
    _workspace.write_stale_modules(project_root, remaining)
    return {"stale_modules": sorted(remaining)}
