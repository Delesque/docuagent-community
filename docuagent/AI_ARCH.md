# Purpose

`docuagent/` contains the local Python service and the workflow that turns a confirmed architecture into reviewable, verified project changes. The backend uses the Python standard library at runtime. The React frontend is built separately and served by the local service.

# Runtime Shape

1. `docuagent.py` starts the loopback HTTP service and exposes the compatibility import surface used by tests and scripts.
2. `main.py` assembles route modules, provider settings, session checks, terminal policy, and static frontend serving.
3. `bootstrap.py` runs the architecture interview and persists confirmed project state.
4. Architecture modules become dependency-ordered tasks through `task_plan.py`.
5. Implementation and repair agents work in isolated sandboxes, then return patches for review or controlled application.
6. Verification, contract checks, code indexing, and documentation synchronization close the delivery loop.

# Backend Map

## Project And Architecture State

- `workspace.py`: managed paths, atomic JSON/text writes, project inspection, migrations, UI state, and conversations.
- `bootstrap.py`: provider calls, interview modes, requirement provenance, architecture review, and initialization.
- `core.py`: architecture schema validation, graph normalization, dependency checks, and diagnostics.
- `scaffold.py`: initial project memory, standards, recipes, requirements, and `AI_ARCH.md` navigation documents.
- `contract_registry.py` and `contract_lint.py`: declared interfaces, contract deltas, and implementation checks.
- `workspace.py`, `memory.py`, and `standards.py`: graph notes, durable memory candidates, and shared agent rules.
- `telemetry.py`: local anonymous aggregate counters, explicit upload consent, batch upload and local clearing.

## Task Delivery

- `task_plan.py`: derives work items, dependencies, target files, and verification commands.
- `task_generate.py`: dispatches generation waves, creates sandboxes, restores checkpoints, and records generated patches.
- `task_jobs.py`: persists project-scoped execution jobs, routes cancellation by project/job/task, and distinguishes live work from orphaned `running` state.
- `architecture_preflight.py`: validates command executability, target-file ownership, and README/AI_ARCH command consistency before confirmation, initialization, and architecture revisions.
- `task_patch.py`: validates and applies full-file or partial review decisions.
- `task_verify.py`: verification, repair, stopped/failed checkpoint recovery, dependency checks, and lock cleanup.
- `task_docs.py`: root documentation synchronization and deterministic per-directory document maintenance.
- `task_state.py`: canonical task state plus agent-registry and error-node projections.
- `tasks.py`: compatibility facade over the task modules.
- `token_usage.py` and `context_cache.py`: global/project/module/feature token accounting and semantic context-cache rates.

## Agents And Isolation

- `agents.py`: module-addressed agent registry, workspace locks, bounded context, work logs, error memory, and sessions.
- `agent_tools.py`: native/fallback tool loop, read-before-write navigation checks, repeated-call reminders, compact step logs, cancellation, and checkpoint persistence.
- `sandbox.py`: Git worktree or copy sandbox creation, verification, stale-baseline protection, and controlled write-back.
- `snapshots.py`: project snapshots and rollback.
- `microtask.py`: short, reviewable UI-feedback tasks that reuse the same sandbox path.
- `orchestrate.py`: note-driven cross-module planning and error triage; it coordinates tasks but does not write project files.

Checkpoints contain the sandbox file changes and baseline hashes required to rebuild a fresh sandbox. Recovery accepts stopped (`pending`) or failed work only when a valid checkpoint exists, dependencies are complete, and workspace locks can be acquired. Cancellation takes effect after the current complete tool batch is saved.

## Code Intelligence And Integrations

- `codeintel/`: Python and generic symbol indexes, optional LSP bridges, impact analysis, and architecture projections. Source indexes refresh after file creation, modification, or deletion.
- `gitops.py`: local status, diff, and commit operations with snapshots before commit.
- `debug.py`: minimal Python `pdb` capture for a requested file and line.
- `mcp_server.py`, `mcp_client.py`, and `mcp_sdk_adapter.py`: local MCP server/client support with an optional SDK adapter.
- `skills.py` and `plugins.py`: bounded skill selection and explicitly enabled plugin tools.
- `project_discovery.py`: one whole-project GitHub query proposal, approval, safe repository metadata, and selection records. It never installs candidate source.
- `ui_layout.py` and `layout_delta.py`: local DOM layout capture and deterministic UI feedback context.

## Frontend And Desktop

- `frontend/`: React/Vite architecture workbench; see `frontend/AI_ARCH.md`.
- `desktop/`: Electron shell that starts the Python service and opens the built workbench.
- `tests/`: backend and workflow tests; see `tests/AI_ARCH.md`.
- `web-next/`: generated frontend output. It is built locally and is not part of the reviewed source export.

# State And Security Boundaries

Project-owned state lives under `.docuagent/`, including architecture, contracts, tasks, snapshots, documents, agent logs, locks, error nodes, discovery records, and UI state. Global provider configuration, token counters and anonymous telemetry live under the user's home directory. Global token files store project buckets by one-way path hash; anonymous telemetry never accepts project paths, prompts, code, model names or keys.

The HTTP service is designed for loopback use. API access uses same-origin session cookies plus Origin and Host checks. Saved provider keys are masked in responses. Anonymous telemetry is recorded locally by default but uploaded only after explicit consent; the upload endpoint must be HTTPS and is configured by `DOCUAGENT_TELEMETRY_UPLOAD_URL`. Worktrees constrain file application but do not isolate operating-system privileges; commands, MCP servers, plugins, and generated programs run with the user's permissions.

The optional existing-project reconstruction boundary is exposed through `onboard.py`. Its proprietary implementation is not present in the community source tree, and the community startup path must work without it.

# Task And Documentation Contract

- Tasks move through pending, running, review, applied, verifying, verified, failed, or blocked states. UI agent status is a projection of task state rather than a separate state machine.
- Manual review mode must leave the main project unchanged until a patch is accepted.
- Verification commands pass through `command_policy.py`; successful task state alone is not sufficient evidence of delivery.
- Documentation failure does not downgrade verified code tasks, but it sets the overall delivery status to blocked.
- The documentation agent receives the actual directory/file inventory, Python imports, `requires-python`, and contracts. The root `AI_ARCH.md` must link every real top-level directory and state the exact Python requirement when one exists.
- Every managed source directory requires an `AI_ARCH.md` unless excluded by the documented ignore policy.

# Maintainer Entry Points

- Server and routes: `docuagent.py`, `main.py`, `main_routes_*.py`
- Architecture interview: `bootstrap.py`, `core.py`
- Task workflow: `task_plan.py`, `task_generate.py`, `task_patch.py`, `task_verify.py`, `task_docs.py`
- Agent context and recovery: `agents.py`, `agent_tools.py`, `sandbox.py`
- Local telemetry, token and cache observability: `telemetry.py`, `token_usage.py`, `context_cache.py`
- Contracts and code intelligence: `contract_registry.py`, `contract_lint.py`, `codeintel/`
- Frontend: `frontend/AI_ARCH.md`
- Verification: `tests/AI_ARCH.md`
