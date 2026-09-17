"""Shared prompt standards and the default project standards document.

These rules are injected into the Architecture, Task Planning, Implementation, and
Doc Maintainer prompts so every model role follows the same software-safety and
engineering conventions. The project-level `standards.md` starts from
`DEFAULT_STANDARDS_MD` and can grow through the memory-sedimentation flow.
"""

DEFAULT_STANDARDS_MD = """# Project Standards

- Prefer small functions and modules with a single responsibility.
- Keep modules cohesive and dependencies explicit; avoid hidden global state.
- Every name, file, module, class, function, parameter, and return value must exist
  and be used. Never declare placeholders or reference undeclared names.
- Generate complete runnable code, not stubs, `TODO` markers, or partial signatures.
- Reuse existing project patterns instead of inventing parallel abstractions.
- Do not hardcode secrets, tokens, or machine-specific absolute paths.
- Keep patches inside the task's module and target files.
- Every navigable project directory must have an `AI_ARCH.md` that lists each direct
  file/subdirectory and its current responsibility. Read the directory document
  before editing; after any write, the mandatory Doc Maintainer gate refreshes the
  affected directory chain. A task cannot close while the document tree is missing
  or stale. Generated state, dependency caches, and historical archives are excluded
  by the document-tree policy.
"""

ARCHITECTURE_INTEGRITY_RULES = """Architecture integrity rules:
- Every module is one real capability a user can name; no invented or ghost modules.
- Module ids, paths, groups, and dependencies must be explicit and real.
- Module paths must be relative, inside the project, and never contain "..".
- Dependencies must be explicit via depends_on/edges; never imply hidden coupling.
- Keep modules loosely coupled and highly cohesive. A module owns one responsibility
  stateable in a single sentence; if its responsibility reads as a list of independent
  duties joined by "and", split it. Avoid god modules, duplicated boundaries, and
  unnecessary intermediate layers.
- When editing, preserve existing ids and paths unless the request changes them.
"""

TASK_INTEGRITY_RULES = """Task integrity rules:
- One task belongs to exactly one real architecture module via `module_id`.
- `target_files` must be relative, safe, and inside that module's path.
- Never invent files, modules, or framework layers that are not in the architecture.
- `depends_on` must reference task ids that exist in the same returned plan.
- Keep one task to one verifiable change; add verification commands when possible.
"""

IMPLEMENTATION_SAFETY_RULES = """Software safety and engineering standards:
- Prefer small functions and modules; keep responsibilities single and coupling low.
- Every name used in the code must be defined by the returned files or by an existing
  project module. Never reference undeclared variables, missing imports, or files that
  do not exist.
- Return complete runnable file content. No placeholders, `TODO`, bare `pass`, dummy
  stubs, or partial implementations.
- Avoid unused imports, unused local variables, dead code, and duplicated logic.
- Use explicit parameters and interfaces instead of implicit global mutable state.
- Before defining any new function, class, constant, command, or variable name, call
  `search_reuse` against the Project Contract Registry. If an equivalent export,
  command, shared-kernel symbol, recipe, or vocabulary term already exists, reuse it.
  If an existing interface is insufficient, request a contract delta instead of
  silently creating a duplicate implementation.
- Do not hardcode secrets, tokens, or machine-specific absolute paths.
- Validate inputs and use structured APIs; do not use unsafe `eval` or shell injection
  on untrusted data.
- Errors must be observable: never swallow exceptions with a bare `except: pass`.
- Never write files outside the trial sandbox; only the system's apply flow moves
  sandbox changes into the project.
- If the work leaves this module or requires an architecture change, return
  `needs_handoff` instead of expanding scope.
"""

DOC_INTEGRITY_RULES = """Document integrity rules:
- Only document modules, files, commands, and verification steps that actually exist.
- Never write "will be implemented", "may exist", or other invented facts.
- Sync only the affected paths; do not rewrite unrelated documentation.
- The root `AI_ARCH.md` and every non-excluded project directory's `AI_ARCH.md` are
  mandatory navigation documents. The Doc Maintainer must be invoked after
  initialization, after every applied write path, and before a task/workflow is
  marked complete. Missing or stale directory documents are a blocking error.
- Each directory document must account for every direct file and child directory,
  state the responsibility of each entry, and point the reader to the child's
  `AI_ARCH.md` when one exists.
"""
