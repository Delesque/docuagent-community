import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import project_discovery as discovery
from core import WorkspaceError
from workspace import atomic_write_json, managed_path


class ProjectDiscoveryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.payload = {"path": str(self.root)}

    def proposal(self):
        return discovery.propose({**self.payload, "query": "appointment booking application"})

    def response(self, **changes):
        item = {"full_name": "sample/booking", "description": "Booking app",
                "html_url": "javascript:ignored", "license": {"spdx_id": "MIT"}}
        item.update(changes)
        return io.BytesIO(json.dumps({"items": [item]}).encode())

    def test_proposal_never_contacts_github(self):
        with patch.object(discovery.urllib.request, "urlopen") as request:
            record = self.proposal()
        request.assert_not_called()
        self.assertEqual(record["status"], "proposed")
        self.assertEqual(discovery.read(self.payload)["id"], record["id"])

    def test_scope_and_paths_are_bounded(self):
        for extra in ({"scope": "module"}, {"module_id": "payments"}):
            with self.subTest(extra=extra), self.assertRaises(WorkspaceError):
                discovery.propose({**self.payload, "query": "booking", **extra})
        with self.assertRaises(WorkspaceError):
            discovery.search({**self.payload, "id": "../../secret", "approved": True})

    def test_consent_must_be_explicit_boolean(self):
        record = self.proposal()
        with patch.object(discovery.urllib.request, "urlopen") as request:
            for consent in (False, None, "true", 1):
                with self.subTest(consent=consent), self.assertRaises(WorkspaceError):
                    discovery.search({**self.payload, "id": record["id"], "approved": consent})
        request.assert_not_called()

    def test_only_approved_query_is_sent_and_results_do_not_claim_coverage(self):
        record = self.proposal()
        with patch.object(discovery.urllib.request, "urlopen", return_value=self.response()) as request:
            result = discovery.search({**self.payload, "id": record["id"], "approved": True,
                                       "query": "unapproved replacement", "url": "http://localhost"})
        url = request.call_args.args[0].full_url
        self.assertTrue(url.startswith("https://api.github.com/search/repositories?"))
        self.assertIn("appointment+booking+application", url)
        self.assertNotIn("unapproved", url)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["url"], "https://github.com/sample/booking")
        self.assertEqual(candidate["coverage"], "unverified")
        self.assertEqual(candidate["license"], "MIT")

    def test_missing_license_is_not_assumed_permissive(self):
        record = self.proposal()
        with patch.object(discovery.urllib.request, "urlopen", return_value=self.response(license=None)):
            result = discovery.search({**self.payload, "id": record["id"], "approved": True})
        self.assertEqual(result["candidates"][0]["license"], "unknown")

    def test_search_is_replayable_without_repeating_network(self):
        record = self.proposal()
        with patch.object(discovery.urllib.request, "urlopen", return_value=self.response()) as request:
            payload = {**self.payload, "id": record["id"], "approved": True}
            discovery.search(payload)
            discovery.search(payload)
        self.assertEqual(request.call_count, 1)

    def test_skipped_proposal_cannot_be_searched(self):
        record = self.proposal()
        discovery.decide({**self.payload, "id": record["id"], "decision": "skip"})
        with patch.object(discovery.urllib.request, "urlopen") as request:
            with self.assertRaises(WorkspaceError):
                discovery.search({**self.payload, "id": record["id"], "approved": True})
        request.assert_not_called()

    def test_failure_leaves_proposal_retryable(self):
        record = self.proposal()
        with patch.object(discovery.urllib.request, "urlopen", side_effect=OSError("offline")):
            with self.assertRaises(WorkspaceError):
                discovery.search({**self.payload, "id": record["id"], "approved": True})
        self.assertEqual(discovery.read(self.payload)["status"], "proposed")

    def test_oversized_or_malformed_results_are_rejected(self):
        record = self.proposal()
        for data in (b"x" * (discovery.MAX_RESPONSE_BYTES + 1), b"[]", b"not json"):
            with self.subTest(size=len(data)):
                with patch.object(discovery.urllib.request, "urlopen", return_value=io.BytesIO(data)):
                    with self.assertRaises(WorkspaceError):
                        discovery.search({**self.payload, "id": record["id"], "approved": True})

    def test_decision_records_intent_without_downloading_code(self):
        record = self.proposal()
        with patch.object(discovery.urllib.request, "urlopen", return_value=self.response()):
            discovery.search({**self.payload, "id": record["id"], "approved": True})
        with patch.object(discovery.urllib.request, "urlopen") as request:
            result = discovery.decide({**self.payload, "id": record["id"],
                "candidate": "sample/booking", "decision": "differentiate", "reason": "Need local storage"})
        request.assert_not_called()
        self.assertEqual(result["decisions"][0]["decision"], "differentiate")
        with self.assertRaises(WorkspaceError):
            discovery.decide({**self.payload, "id": record["id"], "candidate": "other/repo",
                              "decision": "evaluate_adoption", "reason": "x"})

    def test_planner_receives_only_overall_goal_and_summary(self):
        atomic_write_json(managed_path(self.root, "bootstrap.json"), {
            "answers": {"goal": "booking app"},
            "architecture": {"summary": "booking", "modules": [{"id": "private_module"}]},
        })
        with patch.object(discovery.ProviderConfig, "from_payload", return_value=object()):
            with patch.object(discovery, "call_model_json", return_value={"query": "booking app"}) as model:
                discovery.propose(self.payload)
        self.assertEqual(set(model.call_args.args[2]), {"goal", "summary"})
        self.assertNotIn("private_module", json.dumps(model.call_args.args[2]))
