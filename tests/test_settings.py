import os

from otel_core.settings import Settings


class FakeConfig:
    def __init__(self, store=None):
        self.store = store or {}

    def __call__(self, key, default=None):
        return self.store.get(key, default)


def test_defaults():
    s = Settings.from_config(FakeConfig())
    assert s.endpoint == ""
    assert s.capture_mode == "none"
    assert s.sample_rate == 1.0
    assert s.service_name == "hermes-agent"
    assert s.headers_env == {}


def test_reads_plugin_settings_keys():
    s = Settings.from_config(
        FakeConfig(
            {
                "endpoint": "http://localhost:4318/v1/traces",
                "capture_mode": "sanitized",
                "sample_rate": 0.5,
                "service_name": "my-hermes",
                "resource_attributes": {"deployment.environment": "lab"},
                "headers_env": {"authorization": "MY_OTLP_TOKEN"},
            }
        )
    )
    assert s.endpoint == "http://localhost:4318/v1/traces"
    assert s.capture_mode == "sanitized"
    assert s.sample_rate == 0.5
    assert s.headers_env == {"authorization": "MY_OTLP_TOKEN"}


def test_resolve_headers_indirects_env_names(monkeypatch):
    monkeypatch.setenv("MY_OTLP_TOKEN", "sekret-value")
    monkeypatch.delenv("MISSING_VAR", raising=False)
    s = Settings(
        endpoint="http://x",
        headers_env={"authorization": "MY_OTLP_TOKEN", "x-missing": "MISSING_VAR"},
    )
    resolved = s.resolve_headers()
    assert resolved == {"authorization": "sekret-value"}
    assert "x-missing" not in resolved


def test_invalid_capture_mode_falls_back_to_none():
    s = Settings.from_config(FakeConfig({"capture_mode": "everything"}))
    assert s.capture_mode == "none"


def test_sample_rate_clamped():
    assert Settings.from_config(FakeConfig({"sample_rate": 5})).sample_rate == 1.0
    assert Settings.from_config(FakeConfig({"sample_rate": -1})).sample_rate == 0.0
