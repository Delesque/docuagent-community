"""Optional onboarding extension boundary; no commercial implementation ships here."""

from __future__ import annotations

import importlib
import importlib.util
import os
from typing import Any

from core import WorkspaceError

__all__ = [
    "accept_suggestion", "build_onboard_model_input", "build_onboard_state",
    "call_onboard", "execute_onboard_tool", "normalize_doc_tree",
    "normalize_suggestions", "onboard_requires_loop", "reject_suggestion",
    "stream_onboard", "stream_onboard_loop", "validate_onboard_result",
    "write_suggestion_attachments",
]


def available() -> bool:
    """Installation plus explicit opt-in, not a license or security check."""
    return (
        os.environ.get("DOCUAGENT_ENABLE_COMMERCIAL") == "1"
        and importlib.util.find_spec("docuagent_pro") is not None
    )


def require_available() -> None:
    if not available():
        raise WorkspaceError(
            "当前安装未包含旧项目接入与重构扩展。已有成果仍可查看，新项目可继续交付。",
            payload={"code": "extension_unavailable", "capability": "project_reconstruction"},
        )


def __getattr__(name: str):
    if name not in __all__:
        raise AttributeError(name)

    def invoke(*args: Any, **kwargs: Any):
        require_available()
        try:
            provider = importlib.import_module("docuagent_pro.onboard")
        except ImportError as exc:
            raise WorkspaceError("旧项目扩展加载失败，请检查扩展安装。") from exc
        return getattr(provider, name)(*args, **kwargs)

    invoke.__name__ = name
    return invoke
