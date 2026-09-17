# Purpose

Backend and workflow verification for DocuAgent's community source tree. Test cases use the standard library `unittest` API and are collected by the test-only `pytest` dependency.

# Entry Points & Flows

- From the repository root, set `PYTHONPATH=docuagent` and run `python -m pytest -q docuagent/tests`.
- Tests use temporary project directories and never write to user projects.

# Entries

- `test_community_boundary.py`: community startup without private code, fail-before-write extension boundaries and reopening generated projects.
- `test_project_discovery.py`: approval, project-only scope, safe metadata, bounded responses, retry behavior and selection intent. Commercial onboarding tests are outside the public source tree.

- `test_docuagent.py`: folder selection, bootstrap persistence, scaffold, model-result validation, provider mocks, revision, and confirmation.
- `test_full_flow_smoke.py`: One fixed-mock trip through initialize → plan → generate → diff → apply → verify → sync docs.
- `test_tasks.py`: Task planning, patch/hunk application, verification, nested directory-document maintenance, and sync gates.
- `test_task_jobs.py`: Project-scoped execution jobs, persisted cancellation, legacy compatibility and orphan reconciliation.
- `test_architecture_preflight.py`: Command executability, target ownership and documented-command consistency gates.
- `test_architecture_docs.py`: Architecture source fingerprints and docs-dirty propagation.
- `test_sandbox_flow.py`: Sandbox write-back, verification fallback, repair, and child-document maintenance.
- `test_orchestrate.py`: Advanced-orchestration normalization, cross-module scope checks, plan merge/persistence, task priorities, and streaming events.
- `test_memory.py`: Memory-candidate lifecycle for user profile, project standards, and project recipes.

# Where to Look

- Folder-selection and initialization regressions: `test_docuagent.py`
- Architecture result and dependency validation: `test_docuagent.py`
- Advanced orchestration and coordination records: `test_orchestrate.py`
- Provider and review-gate behavior: `test_docuagent.py`
- Production behavior under test: parent `../AI_ARCH.md` then `../docuagent.py`
