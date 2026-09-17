"""The model-call seam the task pipeline goes through.

`tasks.py` is due to be split along the pipeline (plan / generate / patch / verify /
docs). That split has a hazard the module boundary alone does not reveal: the suite
controls the pipeline by patching `tasks.call_model_json`, `tasks.stream_json_model`
and `tasks.run_agent_tool_loop`, and those three names are used across four of the
five prospective segments.

`patch("tasks.call_model_json")` rebinds an attribute on the `tasks` module. A function
that has moved to `task_verify.py` looks the name up in *its own* module namespace, so
the patch would no longer reach it — and nothing would fail loudly: the test would stay
green while quietly issuing real model calls. A compatibility layer re-exporting the
moved functions does not help either, because re-export fixes imports, not attribute
lookup inside a relocated function body.

Routing every model call through this module makes the seam independent of which file a
function ends up in: the lookup happens here, per call, so patching `task_runtime` (or
the `tasks` aliases that forward to it) keeps working after the split.
"""

from __future__ import annotations

from typing import Any
import sys

import agent_tools as _agent_tools
import llm_client as _llm_client

_legacy_aliases: dict[str, Any] = {}


def install_legacy_providers(
    *,
    call_model_json: Any = None,
    stream_json_model: Any = None,
    run_agent_tool_loop: Any = None,
) -> None:
    """Register compatibility callbacks without importing the pipeline module.

    The callbacks are intentionally looked up at call time by the registering module,
    which preserves existing patch("tasks.<model call>") targets after extraction.
    """
    if call_model_json is not None:
        _legacy_aliases["call_model_json"] = call_model_json
    if stream_json_model is not None:
        _legacy_aliases["stream_json_model"] = stream_json_model
    if run_agent_tool_loop is not None:
        _legacy_aliases["run_agent_tool_loop"] = run_agent_tool_loop


def _patched_legacy(name: str) -> Any:
    """Return a replaced tasks attribute, but skip the original forwarding alias."""
    module = sys.modules.get("tasks")
    candidate = getattr(module, name, None) if module is not None else None
    if candidate is not None and candidate is not _legacy_aliases.get(name):
        return candidate
    return None


def call_model_json(*args: Any, **kwargs: Any) -> dict[str, Any]:
    patched = _patched_legacy("call_model_json")
    if patched is not None:
        return patched(*args, **kwargs)
    return _llm_client.call_model_json(*args, **kwargs)


def stream_json_model(*args: Any, **kwargs: Any):
    patched = _patched_legacy("stream_json_model")
    if patched is not None:
        return patched(*args, **kwargs)
    return _llm_client.stream_json_model(*args, **kwargs)


def run_agent_tool_loop(*args: Any, **kwargs: Any) -> dict[str, Any]:
    patched = _patched_legacy("run_agent_tool_loop")
    if patched is not None:
        return patched(*args, **kwargs)
    return _agent_tools.run_agent_tool_loop(*args, **kwargs)
