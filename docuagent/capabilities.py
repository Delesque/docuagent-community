"""Explicit capability layer for sub-agent tool execution.

AI output is intent, not authorization. Before `agent_tools.execute_tool` runs a tool
it looks up the tool's required capability and requires that capability in the agent
context. `agents.build_agent_context` is the only production context factory and always
declares an explicit set; direct callers that omit the set fail closed.
"""

from __future__ import annotations

from typing import Any

from core import WorkspaceError

READ_PROJECT = "read_project"
READ_ARCHITECTURE = "read_architecture"
WRITE_SANDBOX = "write_sandbox"
RUN_VERIFICATION = "run_verification"
CREATE_SNAPSHOT = "create_snapshot"
APPLY_PATCH = "apply_patch"
COMMIT_GIT = "commit_git"
START_MCP = "start_mcp"
INSTALL_PLUGIN = "install_plugin"
CODE_INTEL = "code_intel"

CAPABILITIES = frozenset({
    READ_PROJECT,
    READ_ARCHITECTURE,
    WRITE_SANDBOX,
    RUN_VERIFICATION,
    CREATE_SNAPSHOT,
    APPLY_PATCH,
    COMMIT_GIT,
    START_MCP,
    INSTALL_PLUGIN,
    CODE_INTEL,
})

# Code intelligence (goto/references/search/hover for sub-agents) is opt-in: it is
# NOT in DEFAULT_SUBAGENT_CAPABILITIES, so a sub-agent only sees these tools when the
# operator explicitly enables them (DOCUAGENT_CODE_INTEL). This keeps the moat tooling
# off unless consciously turned on, and fails closed by default.
CODE_INTEL_ENV = "DOCUAGENT_CODE_INTEL"

# Implementation and Repair agents share the same minimal execution surface:
# scoped reads, architecture metadata, sandbox writes, and declared verification.
DEFAULT_SUBAGENT_CAPABILITIES = frozenset({
    READ_PROJECT,
    READ_ARCHITECTURE,
    WRITE_SANDBOX,
    RUN_VERIFICATION,
})


def require_capability(context: dict[str, Any], capability: str) -> None:
    declared = context.get("capabilities")
    if not isinstance(declared, (list, tuple, set, frozenset)):
        raise WorkspaceError("当前 Agent 没有声明能力范围。")
    if capability not in set(declared):
        raise WorkspaceError(f"当前 Agent 没有权限执行：{capability}")
