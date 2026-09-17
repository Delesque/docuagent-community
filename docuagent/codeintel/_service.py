"""Shared, long-lived code-intel service instance.

Both the HTTP route handlers (``main_routes_codeintel``) and the sub-agent tool
bridge (``agent_bridge``) talk to the *same* ``CodeIntelService`` so the project
index and any LSP sessions are built once and reused. Keeping the singleton here
(rather than inside either consumer) avoids two competing instances scanning the
project twice.
"""

from __future__ import annotations

from .service import CodeIntelService

SERVICE: CodeIntelService = CodeIntelService()
