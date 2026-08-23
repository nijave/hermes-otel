"""Tests for otel_core.exporter: fail-open lazy OTLP tracer construction."""
import types

from otel_core import exporter
from otel_core.settings import Settings


def test_returns_none_without_endpoint():
    holder = types.SimpleNamespace()
    tracer = exporter.build_tracer(holder, Settings(endpoint=""))
    assert tracer is None
    assert holder.tracer is None


def test_lazy_init_failure_sentinel(monkeypatch):
    def boom(settings):
        raise ImportError("sdk missing")

    monkeypatch.setattr(exporter, "_build_sdk_provider", boom)
    holder = types.SimpleNamespace(_init_failed=False)
    tracer = exporter.build_tracer(holder, Settings(endpoint="http://x"))
    assert tracer is None
    assert holder._init_failed is True

    # second call must not retry the failed build
    calls = []

    def counting(settings):
        calls.append(1)
        raise ImportError("again")

    monkeypatch.setattr(exporter, "_build_sdk_provider", counting)
    tracer = exporter.build_tracer(holder, Settings(endpoint="http://x"))
    assert tracer is None
    assert not calls


def test_build_wires_batch_processor_with_headers(monkeypatch):
    monkeypatch.setenv("TOK", "v")
    captured = {}

    class FakeProvider:
        def __init__(self, **kw):
            captured.update(kw)
            captured["provider_obj"] = self
            self.flushed = False
            self.down = False

        def get_tracer(self, name):
            return object()

        def add_span_processor(self, proc):
            captured["proc"] = proc

        def force_flush(self, timeout_millis=None):
            self.flushed = True

        def shutdown(self):
            self.down = True

    class FakeExp:
        pass

    def make(endpoint, headers):
        captured["endpoint"] = endpoint
        captured["headers"] = dict(headers)
        captured["exp"] = FakeExp()
        return captured["exp"]

    monkeypatch.setattr(
        exporter,
        "_build_sdk_provider",
        lambda settings: (make, FakeProvider()),
    )
    holder = types.SimpleNamespace(_init_failed=False)
    s = Settings(
        endpoint="http://ep",
        headers_env={"authorization": "TOK"},
        service_name="svc",
        sample_rate=0.25,
    )
    tracer = exporter.build_tracer(holder, s)
    assert tracer is not None
    assert holder.tracer is tracer
    assert holder.provider is captured["provider_obj"]
    assert captured["endpoint"] == "http://ep"
    assert captured["headers"] == {"authorization": "v"}
    # real BatchSpanProcessor (SDK installed) wrapping the fake exporter instance
    assert captured["proc"].span_exporter is captured["exp"]
    exporter.flush_shutdown(holder)
    assert captured["provider_obj"].flushed and captured["provider_obj"].down
    assert holder.provider is None
    assert holder.tracer is None


def test_build_tracer_idempotent_when_already_built(monkeypatch):
    holder = types.SimpleNamespace(tracer=object(), provider=object(), _init_failed=False)

    def boom(settings):
        raise AssertionError("must not rebuild when tracer exists")

    monkeypatch.setattr(exporter, "_build_sdk_provider", boom)
    assert exporter.build_tracer(holder, Settings(endpoint="http://x")) is holder.tracer


def test_real_sdk_provider_wires_resource_and_sampler():
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    make, provider = exporter._build_sdk_provider(
        Settings(
            service_name="svc",
            sample_rate=0.25,
            resource_attributes={"env": "test"},
        )
    )
    assert provider._resource.attributes["service.name"] == "svc"
    assert provider._resource.attributes["env"] == "test"
    assert isinstance(provider.sampler, ParentBased)
    inner = provider.sampler._root
    assert isinstance(inner, TraceIdRatioBased)
    assert inner.rate == 0.25
    exp = make("http://ep", {"authorization": "v"})
    assert exp._endpoint == "http://ep"
    assert dict(exp._headers) == {"authorization": "v"}
