"""HTTP server, route handlers, and CLI entry point.

Every response is wrapped as `{ok: true, data: ...}` or `{ok: false, error: "..."}`.
The frontend's `api.ts` unwraps that envelope — it was once omitted here, and every
request failed silently at runtime while TypeScript stayed happy. Keep new routes
consistent.

Binds loopback only. Provider credentials live in `~/.docuagent/config.json`, outside
any project directory, so one key config serves every project and never lands in a
migration bundle.
"""

from __future__ import annotations

import argparse
import json
import secrets
import shlex
import socket
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import bootstrap as _bootstrap
import agents as _agents
import architecture_delta as _delta
import error_nodes as _error_nodes
import importscan as _importscan
import memory as _memory
import orchestrate as _orchestrate
import plugins as _plugins
import snapshots as _snapshots
import skills as _skills
import tasks as _tasks
import telemetry as _telemetry
import ui_layout as _ui_layout
import layout_delta as _layout_delta
import onboard as _onboard
import project_discovery as _project_discovery
import workspace as _workspace
import command_policy as _command_policy
import llm_client as _llm_client
from main_routes_tasks import (
    accept_suggestion_route,
    apply_micro_task,
    apply_mode_route,
    apply_task,
    apply_task_hunks_route,
    apply_task_partial,
    cancel_tasks_route,
    context_cache_route,
    debug_file_route,
    diagnose_file_route,
    dispatch_micro_task,
    doc_ignore_route,
    edit_task_patch_route,
    generate_next_task,
    generate_wave,
    get_task_hunks_route,
    latest_triage_route,
    orchestrate_tasks,
    reject_suggestion_route,
    reject_task,
    repair_task,
    resume_task,
    retry_documentation,
    retry_task,
    scan_import_route,
    start_onboard_route,
    stream_architecture_edit_events,
    stream_bootstrap_answer_events,
    stream_bootstrap_start_events,
    stream_onboard_events,
    stream_orchestrate_tasks_events,
    stream_task_wave_events,
    stream_verify_task_events,
    sync_docs,
    task_context,
    terminal_exec,
    token_usage_route,
    triage_errors_route,
    verify_task,
)

from main_routes_bootstrap import (
    _stale_after_edit,
    _stale_after_module_edit,
    clear_stale,
    confirm_bootstrap,
    finalize_bootstrap,
    inspect_workspace,
    public_state,
    set_interview_mode,
    undo_architecture,
    update_module_requirement,
    update_provenance,
    update_architecture_nodes,
    update_architecture_edges,
    reopen_architecture_review,
)

from main_routes_workspace import (
    add_attachment_route,
    approve_mcp_server_route,
    archive_attachment_route,
    build_layout_delta_route,
    build_layout_delta_target_route,
    call_mcp_tool_route,
    capture_ui_layout_route,
    clear_agent_errors_route,
    git_commit_route,
    git_diff_route,
    git_status_route,
    import_skill_route,
    install_market_plugin_route,
    install_plugin_route,
    install_skill_route,
    list_attachments_route,
    list_mcp_servers_route,
    list_mcp_tools_route,
    list_plugins_route,
    list_skills_route,
    list_snapshots_route,
    marketplace_entries_route,
    plugin_marketplace_route,
    resolve_attachment_route,
    restore_snapshot_route,
    save_mcp_servers_route,
    send_agent_message_route,
    test_mcp_server_route,
    toggle_plugin_route,
    uninstall_plugin_route,
)

from main_routes_codeintel import (
    CODEINTEL_GET_ROUTES,
    CODEINTEL_POST_ROUTES,
)
from llm_client import ProviderConfig, model_error_message
from architecture_preflight import require_architecture_preflight
from bootstrap import (
    apply_architecture_model_result,
    refresh_progress,
    normalize_interview_mode,
    profile_complete,
    profile_question,
)

# Indirection so tests can patch these.
#
# The suite patches `docuagent.call_model_json` / `docuagent.ask_directory` etc. Before
# the split everything lived in one module, so patching the module attribute was enough.
# Now these functions live elsewhere, and a plain `from x import f` would bind the
# original at import time — the patch would apply to a name nothing calls. Routing
# through the module object means the lookup happens per call, so patching either the
# owning module or this one takes effect.
#
# `docuagent.py` re-exports these wrappers, which is what keeps
# `patch("docuagent.call_model_json")` working.


def call_model_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _bootstrap.call_model_json(*args, **kwargs)


def call_architecture_model(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _bootstrap.call_architecture_model(*args, **kwargs)


def call_architecture_edit_model(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _bootstrap.call_architecture_edit_model(*args, **kwargs)


def ask_directory(*args: Any, **kwargs: Any) -> str:
    return _workspace.ask_directory(*args, **kwargs)
from core import (
    APP_VERSION,
    MANAGED_DIR,
    SCHEMA_VERSION,
    WorkspaceError,
    architecture_readiness_issues,
    slugify,
)
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
    detect_mode,
    inspect_workspace as _inspect_workspace,
    managed_path,
    prune_ui_state,
    read_json,
    resolve_project_path,
    utc_now,
    visible_entries,
    write_conversation_history,
    write_ui_state,
)


APP_ROOT = Path(__file__).resolve().parent
# The Vite build lands in web-next/. The old web/ workbench is archived and is no
# longer served.
WEB_NEXT_ROOT = APP_ROOT / "web-next"
MAX_BODY_BYTES = 1_000_000
SESSION_COOKIE_NAME = "docuagent_session"
SESSION_TOKEN = secrets.token_urlsafe(32)
SESSION_EXEMPT_PATHS = frozenset({"/api/health", "/api/session"})
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})
DEV_ORIGIN_PORTS = frozenset({"3100"})
# The terminal and task verification share one policy so they cannot drift apart.
# Kept as module-level aliases because existing callers and tests reference these names.
TERMINAL_ALLOWED_COMMANDS = _command_policy.ALLOWED_COMMANDS
TERMINAL_FORBIDDEN_MARKERS = _command_policy.FORBIDDEN_MARKERS


def split_host_port(value: str) -> tuple[str, str]:
    """Normalize a Host/Origin netloc into (hostname, port)."""
    text = value.strip()
    if "://" in text:
        text = text.split("://", 1)[1]
    if text.startswith("["):
        end = text.find("]")
        if end == -1:
            return text.lower(), ""
        host = text[1:end]
        port = text[end + 2 :] if text[end + 1 : end + 2] == ":" else ""
        return host.lower(), port
    if text.count(":") == 1:
        host, port = text.rsplit(":", 1)
        return host.lower(), port
    return text.lower(), ""


def origin_allowed(origin: str, bound_host: str, bound_port: int) -> bool:
    """Loopback origins (any port) plus the exact bound host are trusted."""
    if not origin:
        return True
    if origin == "null":
        return False
    try:
        parsed = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host, port = split_host_port(parsed.netloc)
    if not port:
        port = "443" if parsed.scheme == "https" else "80"
    return (
        host == bound_host.lower() and str(port) == str(bound_port)
    ) or (
        host in LOOPBACK_HOSTNAMES
        and (str(port) == str(bound_port) or str(port) in DEV_ORIGIN_PORTS)
    )


def host_allowed(host_header: str, bound_host: str) -> bool:
    """Block DNS-rebinding Host headers; loopback aliases are always allowed."""
    if not host_header:
        return True
    host, _ = split_host_port(host_header)
    if host in LOOPBACK_HOSTNAMES:
        return True
    bound = bound_host.lower()
    if bound in {"0.0.0.0", "::"}:
        return False
    return host == bound


def has_session_cookie(headers) -> bool:
    cookie = headers.get("Cookie") or ""
    for part in cookie.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE_NAME and value == SESSION_TOKEN:
            return True
    return False


def mask_api_key(value: str) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) <= 8:
        return "****"
    return f"{key[:4]}****{key[-4:]}"

# Global (cross-project) config — provider credentials. Stored outside any
# project directory so a single key config survives across multiple projects.
# The server runs on loopback so writing the key here is safe for a desktop app.
GLOBAL_CONFIG_PATH = Path.home() / ".docuagent" / "config.json"

# Whitelist, so a stray key in the config file can never reach the provider payload.
_ALLOWED_CONFIG_KEYS = frozenset({"enabled", "base_url", "model", "api_key", "roles", "interview_mode", "format"})


def resolve_web_root() -> Path:
    return WEB_NEXT_ROOT


WEB_ROOT = resolve_web_root()


def choose_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    selected = ask_directory(str(payload.get("initial_path") or ""))
    if not selected:
        return {"cancelled": True}
    return {
        "cancelled": False,
        "workspace": inspect_workspace(selected),
    }


def start_bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    project_root.mkdir(parents=True, exist_ok=True)
    project_name = str(
        payload.get("name") or project_root.name or "Untitled Project"
    ).strip()
    description = str(payload.get("description") or "").strip()

    existing = read_json(managed_path(project_root, "bootstrap.json"))
    if existing and not payload.get("restart"):
        existing_architecture = existing.get("architecture")
        has_modules = (
            isinstance(existing_architecture, dict)
            and bool(existing_architecture.get("modules"))
        )
        failed_first_turn = (
            existing.get("agent_mode") == "ai_failed"
            or (
                existing.get("status") == "interviewing"
                and existing.get("current_question") is None
                and not has_modules
            )
        )
        # A failed first turn leaves an empty draft behind. Returning it as "already
        # started" would show a broken state with no question; resubmitting must
        # restart the interview instead.
        if not failed_first_turn:
            return public_state(existing) or {}

    if detect_mode(project_root) == "imported" and not (
        existing and (existing.get("project") or {}).get("mode") == "new"
    ):
        _onboard.require_available()

    # Checked before any state is built: the Architecture Agent is the product, not an
    # enhancement, so there is nothing meaningful to construct without it.
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("需要配置 AI 模型才能初始化项目。请先在设置中配置 API。")

    answers: dict[str, str] = {}
    if description:
        answers["goal"] = description

    # `architecture` starts empty and `current_question` null: the first model call
    # below fills both. Seeding them with locally invented content would only be
    # visible if that call failed, which is exactly when a fabricated architecture is
    # most misleading.
    state = {
        "schema_version": SCHEMA_VERSION,
        "status": "interviewing",
        "project": {
            "name": project_name,
            "slug": slugify(project_name),
            "root": str(project_root),
            "mode": detect_mode(project_root),
            # The architecture agent must see the real file system before
            # designing; otherwise it fabricates module files and verification
            # commands that have no counterpart in the project.
            "scan_summary": _importscan.architect_summary(
                _importscan.ensure_scan(project_root)
            ),
        },
        "answers": answers,
        "user_profile": {},
        "interview_mode": normalize_interview_mode(
            payload.get("interview_mode")
            or read_global_config().get("interview_mode")
        ),
        "architecture": {},
        "current_question": None,
        "progress": 0,
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "model_notice": "",
        "agent_mode": "ai",
        "model_name": provider.model,
        "agent_turns": 0,
    }

    try:
        state["current_question"] = profile_question(0)
        state["profile_progress"] = 0
        state["progress"] = 5
        state["thinking"] = "先了解你希望怎样被帮助，再开始项目架构访谈。"
    except WorkspaceError as exc:
        # Persist the failure so the UI can explain it, then surface it. Falling back
        # to canned questions here would hide a broken API config.
        state["model_notice"] = f"AI 调用失败：{exc}"
        state["agent_mode"] = "ai_failed"
        state["thinking"] = f"初始化时 AI 调用失败：{exc}"
        atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
        raise WorkspaceError(
            f"AI 模型调用失败：{exc}。请重试；若反复失败请检查 API 配置。",
            payload=getattr(exc, "payload", None),
        )

    atomic_write_json(managed_path(project_root, "bootstrap.json"), state)
    _snapshots.create_snapshot(project_root, "开始项目访谈")
    return public_state(state) or {}


def answer_bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    state_path = managed_path(project_root, "bootstrap.json")
    state = read_json(state_path)
    if not state:
        raise WorkspaceError("项目尚未开始初始化。")
    if state.get("status") == "initialized":
        return public_state(state) or {}
    if state.get("status") == "review":
        raise WorkspaceError("架构正在等待确认；需要修改时请提交架构修订。")
    if state.get("status") == "ready":
        raise WorkspaceError("架构已经确认，可以生成项目框架。")

    current = state.get("current_question")
    if not isinstance(current, dict):
        raise WorkspaceError("当前没有待回答的问题。")
    answer = str(payload.get("answer") or "").strip()
    if len(answer) < 2:
        raise WorkspaceError("请提供更完整的回答。")

    # `normalize_question` guarantees an id on every question the model returns, so a
    # turn counter is only needed for state written by an older version.
    question_id = str(current.get("id") or f"turn-{len(state.get('answers', {})) + 1}")
    if not profile_complete(state.get("user_profile", {})):
        state.setdefault("user_profile", {})[question_id] = answer
        state["profile_progress"] = len(state["user_profile"])
        state["interview_mode"] = normalize_interview_mode(payload.get("interview_mode", state.get("interview_mode")))
        state["current_question"] = profile_question(state["profile_progress"])
        state["progress"] = min(25, 5 + state["profile_progress"] * 4)
        state["updated_at"] = utc_now()
        if not profile_complete(state["user_profile"]):
            atomic_write_json(state_path, state)
            return public_state(state) or {}
    state.setdefault("answers", {})[question_id] = answer
    latest = {"question_id": question_id, "answer": answer}

    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("需要配置 AI 模型才能进行架构设计。请先在设置中配置 API。")

    try:
        model_result = call_architecture_model(state, latest, provider)
        apply_architecture_model_result(state, model_result, provider)
    except WorkspaceError as exc:
        # Hold position rather than advancing: a failed call must not consume the turn.
        # The answer stays recorded so retrying does not make the user retype it.
        state["model_notice"] = f"AI 调用失败：{exc}。请检查配置或重试。"
        state["agent_mode"] = "ai_failed"
        state["thinking"] = f"尝试调用 AI 模型时失败：{exc}"
        refresh_progress(state)
        state["updated_at"] = utc_now()
        atomic_write_json(state_path, state)
        raise WorkspaceError(
            f"AI 模型调用失败：{exc}。请重试；若反复失败请检查 API 配置。",
            payload=getattr(exc, "payload", None),
        )

    refresh_progress(state)
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)
    _snapshots.create_snapshot(project_root, f"回答：{question_id}")
    return public_state(state) or {}


def revise_bootstrap(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    state_path = managed_path(project_root, "bootstrap.json")
    state = read_json(state_path)
    if not state:
        raise WorkspaceError("项目尚未开始初始化。")
    if state.get("status") != "review":
        raise WorkspaceError("只有等待确认的架构可以提交修订。")

    feedback = str(payload.get("feedback") or "").strip()
    if len(feedback) < 2:
        raise WorkspaceError("请描述需要修改的架构决策。")
    provider = ProviderConfig.from_payload(payload)
    if provider is None:
        raise WorkspaceError("架构修订需要启用 Architecture Agent 模型。")

    revision_id = f"architecture-review-{len(state.get('answers', {})) + 1}"
    latest = {"question_id": revision_id, "answer": feedback}
    model_result = call_architecture_model(state, latest, provider)
    previous_architecture = state.get("architecture") or {}
    state.setdefault("answers", {})[revision_id] = feedback
    apply_architecture_model_result(state, model_result, provider)
    state["architecture_delta"] = _delta.compare_architectures(
        previous_architecture, state.get("architecture") or {}
    )
    state["confirmed_at"] = None
    refresh_progress(state)
    state["updated_at"] = utc_now()
    atomic_write_json(state_path, state)
    _snapshots.create_snapshot(project_root, "修订架构草案")
    return public_state(state) or {}


def edit_architecture(payload: dict[str, Any]) -> dict[str, Any]:
    """Revise the architecture of a project that already has one.

    This is the "the graph can be changed after generation" path, and it is deliberately
    separate from the interview:

    - `answer_bootstrap` returns early once `status == "initialized"`, and
      `revise_bootstrap` only accepts `status == "review"`. Neither can touch a finished
      project, which is why editing needed its own route rather than a relaxed guard on
      an existing one — loosening those would let a finished project fall back into
      interviewing and re-run the readiness gate against a design already on disk.
    - The source of truth here is `architecture.json`, not `bootstrap.json`. Hand-authored
      and finished projects have no interview state at all, and they must be editable too.

    `architecture_version` increments on every accepted revision. UI state is pruned but
    not reset: a module that survives the edit keeps the position the user gave it.
    """
    project_root = resolve_project_path(str(payload.get("path", "")))
    request = str(payload.get("request") or "").strip()
    if len(request) < 2:
        raise WorkspaceError("请描述需要修改的地方。")

    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("修改架构需要配置 AI 模型。请先在设置中配置 API。")

    document = _workspace.architecture_document(project_root)
    state = read_json(managed_path(project_root, "bootstrap.json"))
    # Same resolution order as `inspect_workspace`: a live interview's draft wins, then
    # the generated document. Editing must work in both, and for hand-written projects.
    if state and state.get("architecture", {}).get("modules"):
        current = state["architecture"]
    elif document and document.get("modules"):
        current = document
    else:
        raise WorkspaceError("这个项目还没有架构可以修改。")

    project = (document or {}).get("project") or (state or {}).get("project") or {}
    result = call_architecture_edit_model(current, request, project, provider)
    architecture = result["architecture"]
    require_architecture_preflight(project_root, architecture)
    # Computed truth about what this edit did — the model's own `changes` prose is
    # not trusted to be complete or accurate. `moved` entries are presentation-only.
    delta = _delta.compare_architectures(current, architecture)
    _workspace.write_stale_modules(
        project_root,
        _stale_after_edit(current, architecture),
    )

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
        "architecture_version": previous_version + 1,
        "generated_at": utc_now(),
        "project": project,
        **architecture,
    }
    if previous_doc:
        _workspace.push_architecture_history(project_root, previous_doc)
    atomic_write_json(
        managed_path(project_root, "architecture.json"), architecture_doc
    )

    # Keep bootstrap.json in step when it exists, so reopening the project does not show
    # the pre-edit architecture — `inspect_workspace` prefers the interview draft.
    if state:
        state["architecture"] = architecture
        state["architecture_version"] = architecture_doc["architecture_version"]
        state["thinking"] = result["thinking"]
        state["updated_at"] = utc_now()
        atomic_write_json(managed_path(project_root, "bootstrap.json"), state)

    _tasks.sync_work_items_from_architecture(project_root, architecture, project)
    _tasks.mark_architecture_docs_dirty(project_root, architecture)

    # Drop layout entries for modules the edit removed. Survivors keep their positions:
    # re-laying out the whole graph would discard placements the user chose by hand.
    module_ids = {module["id"] for module in architecture.get("modules", [])}
    write_ui_state(project_root, prune_ui_state(project_root, module_ids))

    _snapshots.create_snapshot(project_root, "修改架构")

    return {
        "architecture": architecture,
        "architecture_version": architecture_doc["architecture_version"],
        "thinking": result["thinking"],
        "changes": result["changes"],
        "delta": delta,
        "delta_lines": _delta.format_delta(delta),
        "delta_summary": _delta.format_delta_summary(delta),
        "history_remaining": len(_workspace.read_architecture_history(project_root)),
        "work_state": _tasks.read_task_state(project_root),
    }


def provider_endpoint_parts(payload: dict[str, Any]) -> tuple[str, str, str]:
    """Base URL, key and wire `format` without requiring a model name.

    ProviderConfig.from_payload demands a model, but listing models and probing
    provider reachability both happen before the user has picked one.
    """
    raw = payload.get("provider")
    if not isinstance(raw, dict):
        raise WorkspaceError("缺少模型连接信息。")
    base_url = str(raw.get("base_url") or "").strip().rstrip("/")
    api_key = str(raw.get("api_key") or "").strip()
    # The browser never receives the saved secret. Provider probes opt in to
    # reusing the server-side value with `use_saved_key`.
    if not api_key and raw.get("use_saved_key") is True:
        api_key = str(read_global_config().get("api_key") or "").strip()
    if not base_url:
        raise WorkspaceError("请先填写 API 地址。")
    # A full chat endpoint is valid for model calls, but model listing has to hit
    # `{base_url}/models`; strip the suffix so both probes agree.
    if base_url.endswith("/chat/completions"):
        base_url = base_url[: -len("/chat/completions")]
    raw_format = str(raw.get("format") or "openai").strip().lower()
    fmt = raw_format if raw_format in {"openai", "anthropic", "gemini"} else "openai"
    return base_url, api_key, fmt


def fetch_provider_models(base_url: str, api_key: str, timeout: int = 40) -> list[str]:
    """GET {base_url}/models. Proxied through the backend so the key never leaves
    the loopback origin and the browser never hits provider CORS."""
    headers = _llm_client.request_headers({})
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        f"{base_url}/models",
        method="GET",
        headers=headers,
    )
    try:
        with _llm_client.open_with_retry(request, timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WorkspaceError(model_error_message(exc)) from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise WorkspaceError(f"无法连接模型服务：{reason}") from exc
    except TimeoutError as exc:
        raise WorkspaceError("模型服务请求超时。") from exc
    except json.JSONDecodeError as exc:
        raise WorkspaceError("模型服务返回值不是有效 JSON。") from exc

    entries = body.get("data") if isinstance(body, dict) else None
    if not isinstance(entries, list):
        raise WorkspaceError("模型服务未返回模型列表。")
    models: list[str] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("id"):
            models.append(str(entry["id"]))
        elif isinstance(entry, str):
            models.append(entry)
    return sorted(set(models))


def list_provider_models(payload: dict[str, Any]) -> dict[str, Any]:
    base_url, api_key, fmt = provider_endpoint_parts(payload)
    if fmt != "openai":
        # Anthropic/Gemini expose models through a different API surface; the
        # user fills the model name by hand instead of picking from a list.
        return {
            "models": [],
            "count": 0,
            "note": "该协议暂不支持自动列出模型，请在「模型」处手动填写名称。",
        }
    models = fetch_provider_models(base_url, api_key)
    return {"models": models, "count": len(models)}


def test_provider_reachable(payload: dict[str, Any]) -> dict[str, Any]:
    """Provider-level probe: the endpoint answers and the key is accepted.
    Says nothing about whether a specific model works."""
    base_url, api_key, fmt = provider_endpoint_parts(payload)
    if fmt != "openai":
        # No OpenAI-style /models probe for these protocols; the model-level
        # connection test (call_model_json) still works through the dispatcher.
        return {
            "reachable": False,
            "base_url": base_url,
            "model_count": 0,
            "note": "该协议暂不支持自动连通性探测，请用「测试模型连通性」验证。",
        }
    models = fetch_provider_models(base_url, api_key)
    return {"reachable": True, "base_url": base_url, "model_count": len(models)}


def test_provider_connection(payload: dict[str, Any]) -> dict[str, Any]:
    """Model-level probe: it answers and can return a JSON object."""
    provider = ProviderConfig.from_payload(payload)
    if provider is None:
        raise WorkspaceError("请先启用并填写模型连接信息。")
    result = call_model_json(
        provider,
        (
            "You are a connection test. Return JSON only: "
            '{"ok":true,"capability":"stateless structured architecture reasoning"}.'
        ),
        {"instruction": "Confirm that this model can return a JSON object."},
        timeout=90,
        json_mode=True,
        temperature=0,
    )
    return {
        "connected": True,
        "model": provider.model,
        "base_url": provider.base_url,
        "capability": str(result.get("capability") or "structured JSON"),
    }


def save_ui_state(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    # A camera position for a directory DocuAgent has never managed is meaningless,
    # and writing one would silently create `.docuagent/` in an arbitrary folder.
    if not (project_root / MANAGED_DIR).exists():
        raise WorkspaceError("项目尚未开始初始化。")
    return write_ui_state(project_root, payload.get("ui_state"))


def save_conversation(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise WorkspaceError("`messages` 必须是数组。")
    write_conversation_history(project_root, messages)
    return {"saved": True, "count": len(messages)}


def read_global_config() -> dict[str, Any]:
    """Return the saved provider config, or safe defaults when absent.

    The provider dict itself, not wrapped in a `{"provider": ...}` envelope.
    """
    defaults = {
        "enabled": False,
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "api_key": "",
        "roles": {},
    }
    try:
        raw = read_json(GLOBAL_CONFIG_PATH)
    except WorkspaceError:
        return defaults
    if not raw:
        return defaults
    merged = dict(defaults)
    for k in _ALLOWED_CONFIG_KEYS:
        if k in raw:
            merged[k] = raw[k]
    return merged


_llm_client.SAVED_KEY_PROVIDER = lambda: str(
    read_global_config().get("api_key") or ""
)
_llm_client.ROLE_PROVIDER = lambda role: (
    read_global_config().get("roles", {}).get(role, {})
)


def read_public_global_config() -> dict[str, Any]:
    """Config for the frontend: the real key never leaves the server."""
    config = dict(read_global_config())
    key = str(config.get("api_key") or "").strip()
    config["has_api_key"] = bool(key)
    config["api_key"] = mask_api_key(key)
    roles = config.get("roles") or {}
    config["interview_mode"] = config.get("interview_mode")
    config["roles"] = {
        str(name): {
            "base_url": str(role.get("base_url") or ""),
            "model": str(role.get("model") or ""),
            "api_key": mask_api_key(str(role.get("api_key") or "")),
        }
        for name, role in roles.items()
        if isinstance(role, dict)
    }
    return config


def save_global_config(payload: dict[str, Any]) -> dict[str, Any]:
    # Merge onto what is already saved, so a partial update keeps the other fields.
    # A blank or masked api_key preserves the saved key; clear_api_key removes it.
    config = read_global_config()
    saved_key = str(config.get("api_key") or "").strip()
    for key in _ALLOWED_CONFIG_KEYS:
        if key in payload:
            value = payload[key]
            if key == "api_key":
                incoming = str(value or "").strip()
                masked = mask_api_key(saved_key)
                if incoming and incoming != masked:
                    config[key] = incoming
            elif key == "interview_mode":
                config[key] = _bootstrap.normalize_interview_mode(value)
            else:
                config[key] = value
    if isinstance(payload.get("roles"), dict):
        saved_roles = config.get("roles") or {}
        merged_roles: dict[str, Any] = {}
        for name, incoming in payload["roles"].items():
            if not isinstance(incoming, dict):
                continue
            saved = saved_roles.get(name) or {}
            role = dict(saved)
            for field in ("base_url", "model"):
                if str(incoming.get(field) or "").strip():
                    role[field] = incoming[field]
            incoming_key = str(incoming.get("api_key") or "").strip()
            saved_key = str(saved.get("api_key") or "").strip()
            if incoming_key and incoming_key != mask_api_key(saved_key):
                role["api_key"] = incoming_key
            elif not incoming_key and saved_key:
                role["api_key"] = saved_key
            merged_roles[str(name)] = role
        config["roles"] = merged_roles
    if payload.get("clear_api_key") is True:
        config["api_key"] = ""
    elif not str(config.get("api_key") or "").strip() and saved_key:
        config["api_key"] = saved_key
    atomic_write_json(GLOBAL_CONFIG_PATH, config)
    return read_public_global_config()


def get_config(_payload: dict[str, Any]) -> dict[str, Any]:
    return read_public_global_config()


def save_config(payload: dict[str, Any]) -> dict[str, Any]:
    return save_global_config(payload)


def list_memory_candidates_route(raw_path: str) -> dict[str, Any]:
    project_root = resolve_project_path(raw_path)
    return {"candidates": _memory.read_memory_candidates(project_root)}


def suggest_memory_updates(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    provider = ProviderConfig.from_payload(payload)
    if not provider:
        raise WorkspaceError("生成记忆候选需要配置 AI 模型。")
    return _memory.suggest_memory_updates(project_root, provider)


def apply_memory_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        raise WorkspaceError("缺少记忆候选 ID。")
    result = _memory.apply_memory_candidate(project_root, candidate_id)
    _snapshots.create_snapshot(project_root, "应用记忆候选")
    return result


def reject_memory_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        raise WorkspaceError("缺少记忆候选 ID。")
    return _memory.reject_memory_candidate(project_root, candidate_id)


def revert_memory_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = resolve_project_path(str(payload.get("path", "")))
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        raise WorkspaceError("缺少记忆候选 ID。")
    result = _memory.revert_memory_candidate(project_root, candidate_id)
    _snapshots.create_snapshot(project_root, "回滚记忆候选")
    return result


STREAM_ROUTE_PATHS = frozenset({
    "/api/orchestrate/plan-stream",
    "/api/tasks/generate-wave-stream",
    "/api/work/start-stream",
    "/api/tasks/verify-stream",
    "/api/bootstrap/stream-start",
    "/api/bootstrap/stream-answer",
    "/api/architecture/stream-edit",
    "/api/onboard/stream",
})

POST_ROUTES = {
    "/api/discovery/propose": _project_discovery.propose,
    "/api/discovery/search": _project_discovery.search,
    "/api/discovery/decision": _project_discovery.decide,
    "/api/discovery/read": _project_discovery.read,
    "/api/dialog/folder": choose_workspace,
    "/api/provider/test": test_provider_connection,
    "/api/provider/reachable": test_provider_reachable,
    "/api/provider/models": list_provider_models,
    "/api/bootstrap/start": start_bootstrap,
    "/api/bootstrap/answer": answer_bootstrap,
    "/api/bootstrap/mode": set_interview_mode,
    "/api/bootstrap/revise": revise_bootstrap,
    "/api/provenance/update": update_provenance,
    "/api/architecture/nodes": update_architecture_nodes,
    "/api/architecture/edges": update_architecture_edges,
    "/api/architecture/reopen-review": reopen_architecture_review,
    "/api/bootstrap/confirm": confirm_bootstrap,
    "/api/bootstrap/finalize": finalize_bootstrap,
    "/api/architecture/edit": edit_architecture,
    "/api/architecture/update-module-requirement": update_module_requirement,
    "/api/architecture/undo": undo_architecture,
    "/api/architecture/clear-stale": clear_stale,
    "/api/orchestrate/plan": orchestrate_tasks,
    "/api/tasks/cancel": cancel_tasks_route,
    "/api/tasks/generate-next": generate_next_task,
    "/api/tasks/generate-wave": generate_wave,
    "/api/work/start": generate_wave,
    "/api/tasks/apply": apply_task,
    "/api/tasks/apply-mode": apply_mode_route,
    "/api/doc-ignore": doc_ignore_route,
    "/api/tasks/apply-partial": apply_task_partial,
    "/api/tasks/patch-edit": edit_task_patch_route,
    "/api/tasks/apply-hunks": apply_task_hunks_route,
    "/api/tasks/hunks": get_task_hunks_route,
    "/api/file/diagnose": diagnose_file_route,
    "/api/debug/file": debug_file_route,
    "/api/context-cache": context_cache_route,
    "/api/token-usage": token_usage_route,
    "/api/terminal/exec": terminal_exec,
    "/api/orchestrate/triage": triage_errors_route,
    "/api/orchestrate/triage-latest": latest_triage_route,
    "/api/tasks/reject": reject_task,
    "/api/tasks/verify": verify_task,
    "/api/tasks/retry": retry_task,
    "/api/tasks/repair": repair_task,
    "/api/tasks/resume": resume_task,
    "/api/git/commit": git_commit_route,
    "/api/micro-task/dispatch": dispatch_micro_task,
    "/api/micro-task/apply": apply_micro_task,
    "/api/mcp/tools": list_mcp_tools_route,
    "/api/mcp/approve": approve_mcp_server_route,
    "/api/mcp/call": call_mcp_tool_route,
    "/api/mcp/servers": save_mcp_servers_route,
    "/api/mcp/test": test_mcp_server_route,
    "/api/skills/import": import_skill_route,
    "/api/skills/marketplace": marketplace_entries_route,
    "/api/skills/install": install_skill_route,
    "/api/plugins/install": install_plugin_route,
    "/api/plugins/uninstall": uninstall_plugin_route,
    "/api/plugins/toggle": toggle_plugin_route,
    "/api/plugins/marketplace": plugin_marketplace_route,
    "/api/plugins/install-market": install_market_plugin_route,
    "/api/ui-layout/delta": build_layout_delta_route,
    "/api/ui-layout/delta-target": build_layout_delta_target_route,
    "/api/ui-layout/capture": capture_ui_layout_route,
    "/api/tasks/sync-docs": sync_docs,
    "/api/tasks/retry-documentation": retry_documentation,
    "/api/snapshots/restore": restore_snapshot_route,
    "/api/attachments/add": add_attachment_route,
    "/api/attachments/resolve": resolve_attachment_route,
    "/api/attachments/archive": archive_attachment_route,
    "/api/agents/clear-errors": clear_agent_errors_route,
    "/api/agents/message": send_agent_message_route,
    "/api/memory/suggest": suggest_memory_updates,
    "/api/memory/apply": apply_memory_candidate,
    "/api/memory/reject": reject_memory_candidate,
    "/api/memory/revert": revert_memory_candidate,
    "/api/ui-state": save_ui_state,
    "/api/conversation": save_conversation,
    "/api/config": save_config,
    "/api/telemetry/config": _telemetry.configure,
    "/api/telemetry/upload": lambda _payload: _telemetry.upload_pending(),
    "/api/telemetry/clear": lambda _payload: _telemetry.clear_local_data(),
    "/api/onboard/scan": scan_import_route,
    "/api/onboard/start": start_onboard_route,
    "/api/suggestions/accept": accept_suggestion_route,
    "/api/suggestions/reject": reject_suggestion_route,
}


# GET routes follow the same table-driven pattern as POST. Handlers receive the parsed
# query string as a dict of lists; they return JSON-able data or raise WorkspaceError.
GET_JSON_ROUTES = {
    "/api/workspace": lambda q: inspect_workspace(q.get("path", [""])[0]),
    "/api/agents": lambda q: _agents.list_agents(resolve_project_path(q.get("path", [""])[0])),
    "/api/agents/detail": lambda q: _agents.agent_detail(
        resolve_project_path(q.get("path", [""])[0]),
        q.get("module_id", [""])[0],
    ),
    "/api/snapshots": lambda q: list_snapshots_route(q.get("path", [""])[0]),
    "/api/attachments": lambda q: list_attachments_route(q.get("path", [""])[0]),
    "/api/memory/candidates": lambda q: list_memory_candidates_route(q.get("path", [""])[0]),
    "/api/git/status": lambda q: git_status_route(q.get("path", [""])[0]),
    "/api/git/diff": lambda q: git_diff_route(
        q.get("path", [""])[0],
        q.get("file", [""])[0],
    ),
    "/api/mcp/servers": lambda q: list_mcp_servers_route(q.get("path", [""])[0]),
    "/api/skills": lambda q: list_skills_route(q.get("path", [""])[0]),
    "/api/plugins": lambda q: list_plugins_route(q.get("path", [""])[0]),
    "/api/ui-layout/snapshot": lambda q: _ui_layout.read_layout_snapshot(
        resolve_project_path(q.get("path", [""])[0]),
        q.get("module_id", [""])[0],
    ),
    "/api/config": lambda q: read_public_global_config(),
    "/api/telemetry": lambda q: _telemetry.summary(),
    "/api/error-nodes": lambda q: {
        "nodes": _error_nodes.read_error_nodes(resolve_project_path(q.get("path", [""])[0]))
    },
}


# Code intelligence routes are contributed by main_routes_codeintel (the only
# module that imports the codeintel package). Merging here keeps the seam
# shallow: main.py holds no codeintel logic, only route-table composition.
POST_ROUTES = {**POST_ROUTES, **CODEINTEL_POST_ROUTES}
GET_JSON_ROUTES = {**GET_JSON_ROUTES, **CODEINTEL_GET_ROUTES}

# NDJSON stream protocol version. Bump only on incompatible event-shape
# changes; additive fields ride along without a bump.
NDJSON_SCHEMA_VERSION = 1


def envelop_event(event: dict[str, Any], seq: int) -> dict[str, Any]:
    """Attach the stream envelope (schema version, event id, sequence) to an event.

    Each NDJSON line becomes independently addressable: a client can detect a
    gap (dropped frame), resume from the last seen `seq`, and refuse events
    whose schema it does not understand. `event` is returned unchanged in shape
    — this only adds envelope keys, so older consumers that ignore unknown
    fields keep working.
    """
    return {
        "schema_version": NDJSON_SCHEMA_VERSION,
        "event_id": uuid.uuid4().hex,
        "seq": int(seq),
        **event,
    }


class DocuAgentHandler(SimpleHTTPRequestHandler):
    server_version = "DocuAgent/0.1"

    def log_message(self, format_string: str, *args: Any) -> None:
        print(f"[http] {self.address_string()} {format_string % args}")

    def send_json(self, payload: dict[str, Any], status: int = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _error_body(exc: WorkspaceError) -> dict[str, Any]:
        """One shape for every failure response: message plus optional receipt.

        `diagnostics` carries the structured receipt when the failure was raised with
        one (validate gates, contract checks); plain errors omit the key entirely so
        older clients see nothing new.
        """
        body: dict[str, Any] = {"ok": False, "error": str(exc)}
        payload = getattr(exc, "payload", None)
        if isinstance(payload, dict):
            body["diagnostics"] = payload
        return body

    def write_ndjson(self, events) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            for seq, event in enumerate(events, start=1):
                self.wfile.write(
                    (
                        json.dumps(envelop_event(event, seq), ensure_ascii=False)
                        + "\n"
                    ).encode("utf-8")
                )
                self.wfile.flush()
        except Exception as exc:
            # The 200 and headers are already on the wire, so the status can no longer
            # change. Ship the real reason as a final `error` event instead; the
            # frontend's NDJSON reader turns it back into a thrown error so the user
            # sees the actual failure rather than a generic "stream ended early".
            # Catch Exception (not only WorkspaceError) so a stray TypeError/OSError
            # inside a stream generator also surfaces instead of ending the stream
            # silently and producing "流式初始化未返回完成事件".
            message = str(exc) or exc.__class__.__name__
            try:
                self.wfile.write(
                    (
                        json.dumps(
                            envelop_event(
                                {"type": "error", "error": message},
                                seq + 1,
                            ),
                            ensure_ascii=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                self.wfile.flush()
            except Exception:
                pass

    def end_headers(self) -> None:
        # API 响应不缓存；静态文件要求每次校验（配合 Last-Modified 返回 304），
        # 避免浏览器长期缓存旧版 bundle。
        directive = "no-store" if self.path.startswith("/api/") else "no-cache"
        self.send_header("Cache-Control", directive)
        self.send_header(
            "Set-Cookie",
            (
                f"{SESSION_COOKIE_NAME}={SESSION_TOKEN}; "
                "HttpOnly; SameSite=Strict; Path=/"
            ),
        )
        super().end_headers()

    def read_payload(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0 or length > MAX_BODY_BYTES:
            raise WorkspaceError("请求内容为空或过大。")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise WorkspaceError("请求 JSON 格式无效。") from exc
        if not isinstance(payload, dict):
            raise WorkspaceError("请求必须是 JSON 对象。")
        return payload

    def _api_access_error(self) -> str | None:
        path = urllib.parse.urlparse(self.path).path
        if not path.startswith("/api/") or path in SESSION_EXEMPT_PATHS:
            return None
        bound_host = str(self.server.server_address[0])
        bound_port = int(self.server.server_address[1])
        origin = self.headers.get("Origin")
        if origin and not origin_allowed(origin, bound_host, bound_port):
            return "跨站请求被拒绝。"
        host_header = self.headers.get("Host")
        if host_header and not host_allowed(host_header, bound_host):
            return "请求来源主机不被允许。"
        if not has_session_cookie(self.headers):
            return "缺少本地会话令牌。"
        return None

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/session":
            self.send_json({"ok": True})
            return
        if parsed.path == "/api/health":
            self.send_json({"ok": True, "version": APP_VERSION})
            return

        access_error = self._api_access_error()
        if access_error:
            self.send_json(
                {"ok": False, "error": access_error},
                HTTPStatus.FORBIDDEN,
            )
            return

        handler = GET_JSON_ROUTES.get(parsed.path)
        if handler:
            query = urllib.parse.parse_qs(parsed.query)
            try:
                result = handler(query)
                self.send_json({"ok": True, "data": result})
            except WorkspaceError as exc:
                self.send_json(
                    self._error_body(exc), HTTPStatus.BAD_REQUEST
                )
            return

        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self) -> None:
        access_error = self._api_access_error()
        if access_error:
            self.send_json(
                {"ok": False, "error": access_error},
                HTTPStatus.FORBIDDEN,
            )
            return
        stream_path = urllib.parse.urlparse(self.path).path
        if stream_path in STREAM_ROUTE_PATHS:
            try:
                payload = self.read_payload()
                if stream_path == "/api/orchestrate/plan-stream":
                    events = stream_orchestrate_tasks_events(payload)
                elif stream_path == "/api/tasks/generate-wave-stream":
                    events = stream_task_wave_events(payload)
                elif stream_path == "/api/work/start-stream":
                    events = stream_task_wave_events(payload)
                elif stream_path == "/api/tasks/verify-stream":
                    events = stream_verify_task_events(payload)
                elif stream_path == "/api/bootstrap/stream-start":
                    events = stream_bootstrap_start_events(payload)
                elif stream_path == "/api/bootstrap/stream-answer":
                    events = stream_bootstrap_answer_events(payload)
                elif stream_path == "/api/onboard/stream":
                    events = stream_onboard_events(payload)
                else:
                    events = stream_architecture_edit_events(payload)
            except WorkspaceError as exc:
                self.send_json(self._error_body(exc), HTTPStatus.BAD_REQUEST)
                return
            self.write_ndjson(events)
            return

        handler = POST_ROUTES.get(urllib.parse.urlparse(self.path).path)
        if not handler:
            self.send_json({"ok": False, "error": "未知接口。"}, HTTPStatus.NOT_FOUND)
            return
        try:
            result = handler(self.read_payload())
            self.send_json({"ok": True, "data": result})
        except WorkspaceError as exc:
            self.send_json(self._error_body(exc), HTTPStatus.BAD_REQUEST)
        except OSError as exc:
            self.send_json(
                {"ok": False, "error": f"文件系统操作失败：{exc}"},
                HTTPStatus.INTERNAL_SERVER_ERROR,
            )


def find_port(host: str, requested: int) -> int:
    if requested:
        return requested
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def run_server(host: str, port: int, open_browser: bool) -> None:
    web_root = resolve_web_root()
    if not web_root.exists():
        raise SystemExit(f"Missing web root: {web_root}")
    actual_port = find_port(host, port)

    def handler(*args: Any, **kwargs: Any) -> DocuAgentHandler:
        return DocuAgentHandler(*args, directory=str(web_root), **kwargs)

    server = ThreadingHTTPServer((host, actual_port), handler)
    try:
        _telemetry.record_app_start()
    except Exception:
        # Local metrics must never prevent the workbench from starting.
        pass
    url = f"http://{host}:{server.server_address[1]}"
    surface = "graph workbench"
    print(f"DocuAgent is running at {url} ({surface})")
    if open_browser:
        threading.Timer(0.45, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping DocuAgent.")
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="DocuAgent local workbench")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run_server(args.host, args.port, not args.no_browser)


if __name__ == "__main__":
    main()
