import json
import platform as host_platform
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import telemetry
from core import WorkspaceError


class TelemetryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.path = self.tmp / "telemetry.json"
        self.path_patch = patch(
            "telemetry._telemetry_path",
            return_value=self.path,
        )
        self.path_patch.start()
        self.env_patch = patch.dict("os.environ", {}, clear=False)
        self.env_patch.start()
        patch.dict(
            "os.environ",
            {telemetry.UPLOAD_URL_ENV: ""},
            clear=False,
        ).start()

    def tearDown(self) -> None:
        patch.stopall()

    def test_local_recording_is_default_and_aggregates_rates(self) -> None:
        telemetry.record_app_start()
        telemetry.record_job_started("generate-wave")
        telemetry.record_job_finished(
            "generate-wave",
            "failed",
            duration_ms=1200,
        )
        telemetry.record_job_started("verify")
        telemetry.record_job_finished("verify", "completed", duration_ms=800)
        telemetry.record_context_cache(8, 2)
        telemetry.record_provider_usage({
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "cache_hit_tokens": 70,
            "cache_miss_tokens": 30,
        })

        summary = telemetry.summary()
        self.assertTrue(summary["local_recording_enabled"])
        self.assertFalse(summary["consent"])
        self.assertEqual(1, summary["lifetime"]["app_starts"])
        self.assertEqual(2, summary["lifetime"]["jobs_started"])
        self.assertEqual(0.5, summary["failure_rate"])
        self.assertEqual(1, summary["lifetime"]["provider_calls"])
        self.assertEqual(0.7091, summary["cache_hit_rate"])
        self.assertEqual(1000, summary["average_job_duration_ms"])

    def test_consent_is_required_for_upload(self) -> None:
        telemetry.record_app_start()
        with self.assertRaisesRegex(WorkspaceError, "同意"):
            telemetry.upload_pending()
        self.assertEqual(1, telemetry.summary()["pending_events"])

    def test_consent_can_be_enabled_without_an_endpoint(self) -> None:
        result = telemetry.configure({"consent": True})
        self.assertTrue(result["consent"])
        self.assertFalse(result["upload_available"])
        with self.assertRaisesRegex(WorkspaceError, "尚未配置"):
            telemetry.upload_pending()

    def test_successful_upload_clears_only_pending_metrics(self) -> None:
        telemetry.configure({"consent": True})
        telemetry.record_app_start()
        captured: dict[str, object] = {}

        def fake_post(url: str, payload: dict[str, object]) -> None:
            captured.update({"url": url, "payload": payload})

        with (
            patch.dict(
                "os.environ",
                {telemetry.UPLOAD_URL_ENV: "https://metrics.example/v1/batch"},
                clear=False,
            ),
            patch.object(telemetry, "_post_payload", side_effect=fake_post),
        ):
            result = telemetry.upload_pending()

        self.assertTrue(result["uploaded"])
        summary = telemetry.summary()
        self.assertEqual(1, summary["lifetime"]["app_starts"])
        self.assertEqual(0, summary["pending_events"])
        payload = captured["payload"]
        self.assertEqual("docuagent-community", payload["product"])
        expected_platform = {
            "Windows": "windows",
            "Darwin": "macos",
        }.get(host_platform.system(), host_platform.system().lower())
        self.assertEqual(expected_platform, payload["platform"])
        self.assertIn("architecture", payload)
        metrics = payload["metrics"]
        self.assertEqual(1, metrics["app_starts"])
        serialized = json.dumps(payload, ensure_ascii=False).lower()
        for forbidden in (
            "project_root",
            "prompt_text",
            "api_key",
            "code_content",
            "hostname",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_invalid_consent_type_is_rejected(self) -> None:
        for value in (1, "true", None):
            with self.subTest(value=value), self.assertRaises(WorkspaceError):
                telemetry.configure({"consent": value})

    def test_clear_keeps_consent_choice(self) -> None:
        telemetry.configure({"consent": True})
        telemetry.record_app_start()
        summary = telemetry.clear_local_data()
        self.assertTrue(summary["consent"])
        self.assertEqual(0, summary["lifetime"]["app_starts"])


if __name__ == "__main__":
    unittest.main()
