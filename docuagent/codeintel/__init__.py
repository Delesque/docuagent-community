"""DocuAgent code intelligence package.

All code-intelligence logic lives here so that existing modules
(main.py, docuagent.py, tasks.py, agent_tools.py, bootstrap.py) never need to
import this package. The only sanctioned integration point is
`main_routes_codeintel.py`, which imports from here and exposes route tables
that `main.py` merges. No third-party runtime dependency is required: the LSP
bridge speaks JSON-RPC over stdio with the standard library; language servers
are provided by the user environment and degrade gracefully when absent.
"""

from .ports import (
    Capabilities,
    CodeIntelPort,
    Diagnostic,
    Location,
    NullCodeIntel,
    Symbol,
)
from .service import CodeIntelService

__all__ = [
    "Capabilities",
    "CodeIntelPort",
    "Diagnostic",
    "Location",
    "NullCodeIntel",
    "Symbol",
    "CodeIntelService",
]
