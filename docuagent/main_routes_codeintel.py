"""Thin HTTP route handlers for code intelligence.

These handlers are the JSON boundary around the codeintel service. ALL real
logic lives in the `docuagent.codeintel` package; this module only translates
HTTP requests into service calls and is the ONLY place that imports the
package. `main.py` imports this module (following the existing
`main_routes_*` convention) and merges CODEINTEL_POST_ROUTES /
CODEINTEL_GET_ROUTES into the global route tables. No other existing module
imports codeintel.
"""

from __future__ import annotations

from typing import Any

from core import WorkspaceError
from workspace import resolve_project_path
from contract_registry import flatten_registry

from codeintel._service import SERVICE
from codeintel.ports import Capabilities

# Importing the bridge registers the code-intel sub-agent tools into agent_tools'
# external registry. Registration is a no-op unless DOCUAGENT_CODE_INTEL is set, so
# the capability stays opt-in and fails closed by default.
import codeintel.agent_bridge  # noqa: F401


def _service():
    # Thin accessor so the route handlers read naturally; the real instance is the
    # shared singleton defined in codeintel._service (also used by the agent bridge).
    return SERVICE


def _require(payload: dict[str, Any], key: str) -> Any:
    value = payload.get(key)
    if value in (None, ""):
        raise WorkspaceError(f"缺少 {key}。")
    return value


# ---- GET routes (receive parsed query dict) -------------------------------
def codeintel_capabilities_route(query: dict[str, Any]) -> dict[str, Any]:
    path = query.get("path", [""])[0]
    if not path:
        raise WorkspaceError("缺少 path。")
    root = resolve_project_path(path)
    caps: Capabilities = _service().capabilities(root)
    return {
        "available": caps.available,
        "languages": caps.languages,
        "features": caps.features,
        "provider": caps.provider,
    }


# ---- POST routes (receive parsed JSON payload) ----------------------------
def codeintel_goto_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    file = str(_require(payload, "file"))
    return [
        loc.__dict__
        for loc in _service().goto_definition(
            root, file, int(payload.get("line", 0)), int(payload.get("character", 0))
        )
    ]


def codeintel_references_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    file = str(_require(payload, "file"))
    return [
        loc.__dict__
        for loc in _service().find_references(
            root, file, int(payload.get("line", 0)), int(payload.get("character", 0))
        )
    ]


def codeintel_hover_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    file = str(_require(payload, "file"))
    text = _service().hover(
        root, file, int(payload.get("line", 0)), int(payload.get("character", 0))
    )
    return {"text": text}


def codeintel_symbols_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    file = str(_require(payload, "file"))
    return [sym.__dict__ for sym in _service().document_symbols(root, file)]


def codeintel_rename_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    file = str(_require(payload, "file"))
    new_name = str(_require(payload, "new_name"))
    return _service().rename(
        root, file, int(payload.get("line", 0)), int(payload.get("character", 0)), new_name
    )


def codeintel_diagnostics_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    file = str(_require(payload, "file"))
    return [d.__dict__ for d in _service().diagnostics(root, file)]


def codeintel_module_symbols_route(query: dict[str, Any]) -> dict[str, Any]:
    path = query.get("path", [""])[0]
    if not path:
        raise WorkspaceError("缺少 path。")
    module = query.get("module", [""])[0]
    if not module:
        raise WorkspaceError("缺少 module。")
    root = resolve_project_path(path)
    return [r.__dict__ for r in _service().module_symbols(root, module)]


def codeintel_stats_route(query: dict[str, Any]) -> dict[str, Any]:
    path = query.get("path", [""])[0]
    if not path:
        raise WorkspaceError("缺少 path。")
    root = resolve_project_path(path)
    return _service().stats(root)


def codeintel_search_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    query = str(_require(payload, "query"))
    limit = int(payload.get("limit", 50))
    return [r.__dict__ for r in _service().search(root, query, limit)]


def codeintel_reconcile_route(payload: dict[str, Any]) -> dict[str, Any]:
    root = resolve_project_path(str(_require(payload, "path")))
    contracts = payload.get("contracts")
    # Full registry dict -> flatten to typed items, run the typed reconcile (§3.7).
    if isinstance(contracts, dict):
        items = flatten_registry(contracts)
        return _service().reconcile_registry(root, items)
    # Legacy flat list contract ({name, file}).
    report = _service().reconcile(root, contracts or [])
    return {"stale": report.stale, "orphan": report.orphan, "unregistered": report.unregistered}


def codeintel_architecture_projection_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the derived symbol_index onto the architecture diagram (P3).

    Input: {path, modules: [{id, path}]}. Returns per-module public API + status
    and a global orphan count. The diagram only projects this aggregate — it
    never paints symbols as nodes.
    """
    root = resolve_project_path(str(_require(payload, "path")))
    modules = payload.get("modules") or []
    merge_registry = bool(payload.get("merge_registry", False))
    return _service().architecture_projection(root, modules, merge_registry=merge_registry)


def codeintel_registry_projection_route(payload: dict[str, Any]) -> dict[str, Any]:
    """Project the typed Contract Registry onto the architecture diagram (§3 step3).

    Input: {path}. Reads ``contracts.json`` from the workspace and returns registry
    nodes (six typed classes) + aggregate edges (owns / depends_on / uses). The
    diagram renders these as a collapsed cluster per module, expandable + filterable.
    """
    root = resolve_project_path(str(_require(payload, "path")))
    return _service().registry_projection(root)


CODEINTEL_GET_ROUTES = {
    "/api/codeintel/capabilities": codeintel_capabilities_route,
    "/api/codeintel/module_symbols": codeintel_module_symbols_route,
    "/api/codeintel/stats": codeintel_stats_route,
}

CODEINTEL_POST_ROUTES = {
    "/api/codeintel/goto": codeintel_goto_route,
    "/api/codeintel/references": codeintel_references_route,
    "/api/codeintel/hover": codeintel_hover_route,
    "/api/codeintel/symbols": codeintel_symbols_route,
    "/api/codeintel/rename": codeintel_rename_route,
    "/api/codeintel/diagnostics": codeintel_diagnostics_route,
    "/api/codeintel/search": codeintel_search_route,
    "/api/codeintel/reconcile": codeintel_reconcile_route,
    "/api/codeintel/architecture_projection": codeintel_architecture_projection_route,
    "/api/codeintel/registry_projection": codeintel_registry_projection_route,
}
