import threading
import time
import types
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytest.importorskip("opentelemetry.proto")
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

from otel_core import exporter
from otel_core.settings import Settings
from otel_core.spans import TraceState


class Sink(BaseHTTPRequestHandler):
    received = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        Sink.received.append(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):  # noqa: A002
        pass


@pytest.fixture
def sink():
    Sink.received = []
    server = HTTPServer(("127.0.0.1", 0), Sink)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}/v1/traces"
    server.shutdown()


def test_spans_arrive_over_real_otlp(sink):
    holder = types.SimpleNamespace(_init_failed=False)
    settings = Settings(endpoint=sink, service_name="wire-test")
    try:
        tracer = exporter.build_tracer(holder, settings)
        assert tracer is not None
        state = TraceState(tracer)
        pre = {
            "session_id": "ws",
            "turn_id": "wt",
            "api_request_id": "wt:api:1",
            "started_at": 1.0,
            "model": "m",
        }
        state.on_pre_api_request(pre)
        state.on_post_api_request({**pre, "ended_at": 2.0})
        state.finalize_session("ws")
        assert holder.provider.force_flush(timeout_millis=10000)

        deadline = time.monotonic() + 5.0
        while not Sink.received and time.monotonic() < deadline:
            time.sleep(0.05)

        req = ExportTraceServiceRequest()
        body = b"".join(Sink.received)
        assert body, "no OTLP bytes arrived"
        req.ParseFromString(body)
        names = []
        for rs in req.resource_spans:
            for ss in rs.scope_spans:
                for sp in ss.spans:
                    names.append(sp.name)
        assert "chat m" in names
        assert "invoke_agent hermes" in names
    finally:
        exporter.flush_shutdown(holder)
