import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

import bootstrap
import docuagent
import llm_client
import main

PYTHON_COMMAND = "python" if os.name == "nt" else "python3"



class OriginAndSessionTest(unittest.TestCase):
    def test_loopback_origins_are_allowed(self) -> None:
        self.assertTrue(main.origin_allowed("http://127.0.0.1:8765", "127.0.0.1", 8765))
        self.assertTrue(main.origin_allowed("http://localhost:3100", "127.0.0.1", 8765))
        self.assertTrue(main.origin_allowed("http://[::1]:8765", "127.0.0.1", 8765))

    def test_foreign_and_null_origins_are_rejected(self) -> None:
        self.assertFalse(
            main.origin_allowed("http://evil.example:8765", "127.0.0.1", 8765)
        )
        self.assertFalse(main.origin_allowed("null", "127.0.0.1", 8765))

    def test_untrusted_loopback_port_is_rejected(self) -> None:
        self.assertFalse(
            main.origin_allowed("http://127.0.0.1:9999", "127.0.0.1", 8765)
        )
        self.assertFalse(
            main.origin_allowed("http://localhost:8080", "127.0.0.1", 8765)
        )

    def test_host_allowlist_blocks_dns_rebinding(self) -> None:
        self.assertTrue(main.host_allowed("127.0.0.1:8765", "127.0.0.1"))
        self.assertTrue(main.host_allowed("localhost:3100", "127.0.0.1"))
        self.assertFalse(main.host_allowed("evil.example:8765", "127.0.0.1"))

    def test_session_cookie_is_checked(self) -> None:
        headers = {
            "Cookie": (
                "other=1; "
                f"{main.SESSION_COOKIE_NAME}={main.SESSION_TOKEN}"
            )
        }
        self.assertTrue(main.has_session_cookie(headers))
        self.assertFalse(main.has_session_cookie({"Cookie": "other=1"}))


class ConfigMaskTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config_path = Path(self.tmp.name) / "config.json"

    def test_public_config_masks_api_key(self) -> None:
        with patch("main.GLOBAL_CONFIG_PATH", self.config_path):
            main.save_global_config({
                "enabled": True,
                "base_url": "https://api.example.com/v1",
                "model": "m",
                "api_key": "sk-1234567890abcdef",
            })
            public = main.read_public_global_config()
            saved = main.read_global_config()["api_key"]

        self.assertTrue(public["has_api_key"])
        self.assertEqual("sk-1****cdef", public["api_key"])
        self.assertEqual("sk-1234567890abcdef", saved)

    def test_blank_api_key_preserves_saved_key(self) -> None:
        with patch("main.GLOBAL_CONFIG_PATH", self.config_path):
            main.save_global_config({"api_key": "sk-saved"})
            main.save_global_config({"api_key": ""})
            self.assertEqual("sk-saved", main.read_global_config()["api_key"])

    def test_masked_api_key_preserves_saved_key(self) -> None:
        with patch("main.GLOBAL_CONFIG_PATH", self.config_path):
            main.save_global_config({"api_key": "sk-saved"})
            masked = main.mask_api_key("sk-saved")
            main.save_global_config({"api_key": masked})
            self.assertEqual("sk-saved", main.read_global_config()["api_key"])

    def test_clear_api_key_removes_saved_key(self) -> None:
        with patch("main.GLOBAL_CONFIG_PATH", self.config_path):
            main.save_global_config({"api_key": "sk-saved"})
            main.save_global_config({"api_key": "sk-saved", "clear_api_key": True})
            self.assertEqual("", main.read_global_config()["api_key"])

    def test_saved_key_fallback_never_exposes_key_to_frontend(self) -> None:
        payload = {
            "provider": {
                "enabled": True,
                "base_url": "https://api.example.com/v1",
                "model": "m",
                "api_key": "",
                "use_saved_key": True,
            }
        }
        with patch("llm_client.SAVED_KEY_PROVIDER", return_value="saved-secret"):
            provider = bootstrap.ProviderConfig.from_payload(payload)
        self.assertEqual("saved-secret", provider.api_key)

    def test_saved_key_is_not_merged_for_local_base_url(self) -> None:
        payload = {
            "provider": {
                "enabled": True,
                "base_url": "http://localhost:11434/v1",
                "model": "local",
                "api_key": "",
                "use_saved_key": True,
            }
        }
        with patch("llm_client.SAVED_KEY_PROVIDER", return_value="saved-secret"):
            provider = bootstrap.ProviderConfig.from_payload(payload)
        self.assertEqual("", provider.api_key)

    def test_role_provider_overrides_model(self) -> None:
        original = llm_client.ROLE_PROVIDER
        llm_client.ROLE_PROVIDER = lambda role: (
            {
                "base_url": "http://localhost:11434/v1",
                "model": "role-model",
                "api_key": "role-key",
            }
            if role == "subagent"
            else {}
        )
        try:
            provider = bootstrap.ProviderConfig.from_payload(
                {
                    "provider": {
                        "enabled": True,
                        "base_url": "http://localhost:11434/v1",
                        "model": "default-model",
                        "api_key": "default-key",
                    }
                },
                role="subagent",
            )
        finally:
            llm_client.ROLE_PROVIDER = original

        self.assertIsNotNone(provider)
        self.assertEqual("role-model", provider.model)
        self.assertEqual("role-key", provider.api_key)


class TerminalExecTest(unittest.TestCase):
    def test_rejects_unknown_command(self) -> None:
        with self.assertRaises(docuagent.WorkspaceError):
            docuagent.terminal_exec({"path": ".", "command": "rm -rf x"})

    def test_rejects_forbidden_markers(self) -> None:
        with self.assertRaises(docuagent.WorkspaceError):
            docuagent.terminal_exec(
                {"path": ".", "command": 'python -c "print(1);print(2)"'}
            )

    def test_rejects_cwd_outside_project(self) -> None:
        with self.assertRaises(docuagent.WorkspaceError):
            docuagent.terminal_exec(
                {"path": ".", "command": f"{PYTHON_COMMAND} -V", "cwd": ".."}
            )

    def test_runs_whitelisted_command(self) -> None:
        result = docuagent.terminal_exec({"path": ".", "command": f"{PYTHON_COMMAND} -V"})
        self.assertEqual(0, result["returncode"])
        self.assertIn("Python", result["stdout"])

    def test_output_is_truncated(self) -> None:
        result = docuagent.terminal_exec(
            {"path": ".", "command": f"{PYTHON_COMMAND} -c \"print('x' * 60000)\""}
        )
        self.assertLessEqual(len(result["stdout"]), 50_000)


class HttpAccessTest(unittest.TestCase):
    def setUp(self) -> None:
        def handler(*args, **kwargs):
            return main.DocuAgentHandler(
                *args,
                directory=str(main.resolve_web_root()),
                **kwargs,
            )

        self.server = main.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            daemon=True,
        )
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def test_api_rejects_request_without_session(self) -> None:
        with self.assertRaises(urllib.error.HTTPError) as raised:
            urllib.request.urlopen(f"{self.base}/api/config", timeout=5)
        self.assertEqual(403, raised.exception.code)

    def test_api_allows_same_origin_session_request(self) -> None:
        request = urllib.request.Request(
            f"{self.base}/api/config",
            headers={
                "Host": f"127.0.0.1:{self.server.server_address[1]}",
                "Cookie": f"{main.SESSION_COOKIE_NAME}={main.SESSION_TOKEN}",
            },
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertTrue(payload["ok"])
        self.assertIn("has_api_key", payload["data"])


if __name__ == "__main__":
    unittest.main()
