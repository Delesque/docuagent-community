"""Port (interface) definitions for code intelligence.

These dataclasses and the CodeIntelPort protocol are the only contract the rest
of DocuAgent needs. Implementations (LSP bridge, null fallback) live behind this
interface so the transport/provider choice never leaks into callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, runtime_checkable


@dataclass
class Location:
    """A source location. Line/character are 0-based, LSP convention."""

    uri: str
    line: int
    character: int
    end_line: int
    end_character: int


@dataclass
class Symbol:
    """A document symbol (function, class, variable, ...)."""

    name: str
    kind: str
    line: int
    character: int
    detail: str = ""


@dataclass
class Diagnostic:
    """A diagnostic produced by a language server."""

    line: int
    character: int
    end_line: int
    end_character: int
    severity: int  # 1=Error, 2=Warning, 3=Info, 4=Hint
    message: str


@dataclass
class SymbolRecord:
    """One entry in the derived ``symbol_index``.

    This is the single source of truth for code intelligence: every symbol in
    the project is recorded here with enough metadata to drive editor
    navigation, agent tools and the architecture-diagram projection without
    re-parsing source. ``source_hash`` / ``last_seen`` / ``status`` come from
    the source-scanner reconciliation described in ROADMAP (3.3.4 / 3.7.2).
    """

    name: str
    kind: str  # "function" | "class" | "constant"
    file: str  # project-relative path
    line: int
    character: int
    signature: str = ""
    is_public: bool = False  # cross-module visible (module-level def / export)
    scope: str = ""  # "" = module-level; container name / "nested" for inner defs
    source_hash: str = ""
    last_seen: float = 0.0
    status: str = "active"  # active | stale | orphan | unregistered


@dataclass
class ReconcileReport:
    """Drift between the Contract Registry and the derived symbol_index."""

    stale: list[dict] = field(default_factory=list)  # contract points at a symbol no longer in its file
    orphan: list[dict] = field(default_factory=list)  # contract name has no matching symbol anywhere
    unregistered: list[dict] = field(default_factory=list)  # public symbol not yet in the registry


@dataclass
class Capabilities:
    """What the code-intel backend can currently do.

    `provider` is a short tag describing where the intelligence comes from:
    "" (nothing), "python-ast" (built-in, zero-dependency), "lsp" (external
    language server). Multiple providers combine with "+".
    """

    available: bool
    languages: list[str] = field(default_factory=list)
    features: list[str] = field(default_factory=list)
    provider: str = ""


@runtime_checkable
class CodeIntelPort(Protocol):
    """Abstraction over code intelligence providers."""

    def available(self) -> bool:
        ...

    def capabilities(self, project_root: Optional[str] = None) -> Capabilities:
        ...

    def goto_definition(
        self, project_root: str, path: str, line: int, col: int
    ) -> list[Location]:
        ...

    def find_references(
        self, project_root: str, path: str, line: int, col: int
    ) -> list[Location]:
        ...

    def hover(
        self, project_root: str, path: str, line: int, col: int
    ) -> Optional[str]:
        ...

    def document_symbols(
        self, project_root: str, path: str
    ) -> list[Symbol]:
        ...

    def rename(
        self, project_root: str, path: str, line: int, col: int, new_name: str
    ) -> dict:
        ...

    def diagnostics(self, project_root: str, path: str) -> list[Diagnostic]:
        ...

    def search(
        self, project_root: str, query: str, limit: int = 50
    ) -> list[SymbolRecord]:
        ...

    def reconcile(
        self, project_root: str, contracts: list[dict]
    ) -> ReconcileReport:
        ...

    def module_symbols(
        self, project_root: str, module_path: str
    ) -> list[SymbolRecord]:
        ...

    def stats(self, project_root: str) -> dict:
        ...


class NullCodeIntel:
    """Fallback used when no language server is available.

    Every operation degrades to an empty result so callers never have to branch
    on availability; the UI can show "code intelligence unavailable" from
    `capabilities().available`.
    """

    def available(self) -> bool:
        return False

    def capabilities(self) -> Capabilities:
        return Capabilities(available=False, languages=[], features=[], provider="")

    def goto_definition(self, *args, **kwargs) -> list[Location]:
        return []

    def find_references(self, *args, **kwargs) -> list[Location]:
        return []

    def hover(self, *args, **kwargs) -> Optional[str]:
        return None

    def document_symbols(self, *args, **kwargs) -> list[Symbol]:
        return []

    def rename(self, *args, **kwargs) -> dict:
        return {}

    def diagnostics(self, *args, **kwargs) -> list[Diagnostic]:
        return []

    def search(self, *args, **kwargs) -> list[SymbolRecord]:
        return []

    def reconcile(self, *args, **kwargs) -> ReconcileReport:
        return ReconcileReport(stale=[], orphan=[], unregistered=[])

    def module_symbols(self, *args, **kwargs) -> list[SymbolRecord]:
        return []

    def stats(self, *args, **kwargs) -> dict:
        return {}
