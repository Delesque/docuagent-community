import json
import unittest
from pathlib import Path

import docuagent


EXPECTED_POST_ROUTES = {
    "/api/discovery/propose",
    "/api/discovery/search",
    "/api/discovery/decision",
    "/api/discovery/read",
    "/api/dialog/folder",
    "/api/provider/test",
    "/api/provider/reachable",
    "/api/provider/models",
    "/api/bootstrap/start",
    "/api/bootstrap/mode",
    "/api/bootstrap/answer",
    "/api/bootstrap/revise",
    "/api/provenance/update",
    "/api/architecture/nodes",
    "/api/architecture/edges",
    "/api/architecture/reopen-review",
    "/api/bootstrap/confirm",
    "/api/bootstrap/finalize",
    "/api/architecture/edit",
    "/api/architecture/update-module-requirement",
    "/api/architecture/undo",
    "/api/architecture/clear-stale",
    "/api/orchestrate/plan",
    "/api/codeintel/architecture_projection",
    "/api/codeintel/registry_projection",
    "/api/codeintel/goto",
    "/api/codeintel/references",
    "/api/codeintel/hover",
    "/api/codeintel/symbols",
    "/api/codeintel/rename",
    "/api/codeintel/diagnostics",
    "/api/codeintel/search",
    "/api/codeintel/reconcile",
    "/api/tasks/cancel",
    "/api/tasks/generate-next",
    "/api/tasks/generate-wave",
    "/api/work/start",
    "/api/tasks/apply",
    "/api/tasks/apply-mode",
    "/api/doc-ignore",
    "/api/tasks/apply-partial",
    "/api/tasks/patch-edit",
    "/api/tasks/apply-hunks",
    "/api/tasks/hunks",
    "/api/file/diagnose",
    "/api/debug/file",
    "/api/context-cache",
    "/api/token-usage",
    "/api/terminal/exec",
    "/api/orchestrate/triage",
    "/api/orchestrate/triage-latest",
    "/api/tasks/reject",
    "/api/tasks/verify",
    "/api/tasks/retry",
    "/api/tasks/repair",
    "/api/tasks/resume",
    "/api/git/commit",
    "/api/micro-task/dispatch",
    "/api/micro-task/apply",
    "/api/mcp/tools",
    "/api/mcp/approve",
    "/api/mcp/call",
    "/api/mcp/servers",
    "/api/mcp/test",
    "/api/skills/import",
    "/api/skills/marketplace",
    "/api/skills/install",
    "/api/plugins/install",
    "/api/plugins/uninstall",
    "/api/plugins/toggle",
    "/api/plugins/marketplace",
    "/api/plugins/install-market",
    "/api/ui-layout/delta",
    "/api/ui-layout/delta-target",
    "/api/ui-layout/capture",
    "/api/tasks/sync-docs",
    "/api/tasks/retry-documentation",
    "/api/snapshots/restore",
    "/api/attachments/add",
    "/api/attachments/resolve",
    "/api/attachments/archive",
    "/api/agents/clear-errors",
    "/api/agents/message",
    "/api/memory/suggest",
    "/api/memory/apply",
    "/api/memory/reject",
    "/api/memory/revert",
    "/api/ui-state",
    "/api/conversation",
    "/api/config",
    "/api/telemetry/config",
    "/api/telemetry/upload",
    "/api/telemetry/clear",
    "/api/onboard/scan",
    "/api/onboard/start",
    "/api/suggestions/accept",
    "/api/suggestions/reject",
}

EXPECTED_STREAM_ROUTES = {
    "/api/orchestrate/plan-stream",
    "/api/tasks/generate-wave-stream",
    "/api/work/start-stream",
    "/api/tasks/verify-stream",
    "/api/bootstrap/stream-start",
    "/api/bootstrap/stream-answer",
    "/api/architecture/stream-edit",
    "/api/onboard/stream",
}


class HttpRoutesTest(unittest.TestCase):
    def test_post_routes_cover_documented_api(self) -> None:
        self.assertEqual(set(), EXPECTED_POST_ROUTES - set(docuagent.POST_ROUTES))
        self.assertEqual(set(), set(docuagent.POST_ROUTES) - EXPECTED_POST_ROUTES)

    def test_stream_routes_cover_documented_api(self) -> None:
        self.assertEqual(set(), EXPECTED_STREAM_ROUTES - set(docuagent.STREAM_ROUTE_PATHS))
        self.assertEqual(
            set(), set(docuagent.STREAM_ROUTE_PATHS) - EXPECTED_STREAM_ROUTES
        )

    def test_every_post_route_has_a_callable_handler(self) -> None:
        for path, handler in docuagent.POST_ROUTES.items():
            with self.subTest(path=path):
                self.assertTrue(callable(handler))

    def test_web_root_points_at_graph_workbench_build(self) -> None:
        self.assertEqual(docuagent.resolve_web_root(), docuagent.WEB_NEXT_ROOT)

    def test_release_version_matches_package_manifests(self) -> None:
        package_root = Path(__file__).resolve().parents[1]
        for relative in ("frontend/package.json", "desktop/package.json"):
            with self.subTest(package=relative):
                package = json.loads(
                    (package_root / relative).read_text(encoding="utf-8")
                )
                self.assertEqual(docuagent.APP_VERSION, package["version"])


if __name__ == "__main__":
    unittest.main()
