from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_global_usage_files(tmp_path, monkeypatch):
    """Keep test runs from writing metrics into the developer's home directory."""
    monkeypatch.setenv(
        "DOCUAGENT_TELEMETRY_PATH",
        str(tmp_path / "telemetry.json"),
    )
    monkeypatch.setenv(
        "DOCUAGENT_USAGE_PATH",
        str(tmp_path / "token-usage.json"),
    )
    monkeypatch.delenv("DOCUAGENT_TELEMETRY_UPLOAD_URL", raising=False)
