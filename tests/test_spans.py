import logging
import threading

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from otel_core.spans import TraceState


@pytest.fixture
def exporter():
    exp = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exp))
    state = TraceState(tp.get_tracer("hermes-otel-test"))
    yield state, exp
    tp.shutdown()


def test_full_tree_parent_links(exporter):
    state, exp = exporter
    pre = {
        "session_id": "s1",
        "turn_id": "t1",
        "api_request_id": "t1:api:1",
        "started_at": 1000.0,
        "model": "m",
        "provider": "p",
    }
    state.on_pre_api_request(pre)
    state.on_post_api_request({**pre, "ended_at": 1001.0, "finish_reason": "stop"})
    state.finalize_session("s1")

    spans = {s.name: s for s in exp.get_finished_spans()}
    assert set(spans) == {"invoke_agent hermes", "invoke_agent m", "chat m"}
    chat = spans["chat m"]
    turn = spans["invoke_agent m"]
    root = spans["invoke_agent hermes"]
    # SDK ReadableSpan.parent is the parent's SpanContext, not the Span object.
    assert chat.parent == turn.context
    assert turn.parent == root.context
    assert chat.start_time == int(1000.0 * 1e9)
    assert chat.end_time == int(1001.0 * 1e9)
    assert root.attributes["gen_ai.conversation.id"] == "s1"


def test_api_error_marks_chat_span_error_status(exporter):
    state, exp = exporter
    pre = {"session_id": "s", "turn_id": "t", "api_request_id": "t:api:1", "started_at": 1.0}
    state.on_pre_api_request(pre)
    state.on_api_request_error(
        {**pre, "ended_at": 2.0, "error": {"type": "X", "message": "boom"}, "retryable": True}
    )
    state.finalize_session("s")
    chat = next(s for s in exp.get_finished_spans() if s.name.startswith("chat"))
    assert chat.status.status_code.name == "ERROR"


def test_lru_eviction_force_ends_and_warns(exporter, caplog):
    state, exp = exporter
    with caplog.at_level(logging.WARNING, logger="hermes_plugins.hermes_otel"):
        for i in range(300):
            sid = f"s{i}"
            state.on_pre_api_request(
                {"session_id": sid, "turn_id": f"t{i}", "api_request_id": f"t{i}:api:1", "started_at": 1.0}
            )
        # sessions s0..s43 evicted (cap 256); late event for evicted session:
        state.on_post_api_request(
            {
                "session_id": "s0",
                "turn_id": "t0",
                "api_request_id": "t0:api:1",
                "started_at": 1.0,
                "ended_at": 2.0,
            }
        )
        state.finalize_session("s299")
    assert state.evicted_warnings >= 44
    assert any("evicted incomplete session trace" in r.message for r in caplog.records)
    finished_roots = [
        s for s in exp.get_finished_spans() if s.name == "invoke_agent hermes"
    ]
    assert len(finished_roots) >= 44  # evicted trees were force-ended, not leaked


def test_out_of_order_close_attaches_to_live_turn(exporter):
    state, exp = exporter
    state.on_pre_api_request(
        {"session_id": "s", "turn_id": "t", "api_request_id": "t:api:1", "started_at": 1.0}
    )
    state.finalize_session("s")  # turn closes before tool result lands
    state.on_post_tool_call(
        {
            "session_id": "s",
            "tool_call_id": "c1",
            "tool_name": "terminal",
            "duration_ms": 5,
        }
    )
    tools = [s for s in exp.get_finished_spans() if s.name == "execute_tool terminal"]
    assert len(tools) == 1  # recorded against orphan/live ancestor path, no crash, no leak


def test_root_span_ignores_ambient_context(exporter):
    state, exp = exporter
    tp2 = TracerProvider()
    foreign = tp2.get_tracer("foreign").start_span("foreign-root")
    with otel_trace.use_span(foreign, end_on_exit=False):
        state.on_pre_llm_call({"session_id": "s1", "platform": "p"})
        state.on_post_tool_call(
            {"session_id": "s9", "tool_call_id": "c", "tool_name": "t", "duration_ms": 1}
        )
    foreign.end()
    state.finalize_session("s1")
    roots = [s for s in exp.get_finished_spans() if s.name == "invoke_agent hermes"]
    orphan = next(s for s in exp.get_finished_spans() if s.name == "execute_tool t")
    assert len(roots) == 1 and roots[0].parent is None
    assert orphan.parent is None
    tp2.shutdown()


def test_concurrent_sessions_no_lost_updates(exporter):
    state, exp = exporter
    n_threads, per_thread = 8, 30

    def worker(i):
        for j in range(per_thread):
            sid = f"s{i}"
            state.on_pre_api_request(
                {
                    "session_id": sid,
                    "turn_id": f"t{i}-{j}",
                    "api_request_id": f"t{i}-{j}:api:1",
                    "started_at": 1.0,
                }
            )
            state.on_post_api_request(
                {
                    "session_id": sid,
                    "turn_id": f"t{i}-{j}",
                    "api_request_id": f"t{i}-{j}:api:1",
                    "ended_at": 2.0,
                }
            )
            state.on_post_tool_call(
                {"session_id": sid, "tool_call_id": f"c{i}-{j}", "tool_name": "orphan"}
            )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for i in range(n_threads):
        state.finalize_session(f"s{i}")
    chats = [s for s in exp.get_finished_spans() if s.name.startswith("chat ")]
    assert len(chats) == n_threads * per_thread
    orphans = [s for s in exp.get_finished_spans() if s.name == "execute_tool orphan"]
    assert len(orphans) == n_threads * per_thread
