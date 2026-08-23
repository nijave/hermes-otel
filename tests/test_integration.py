"""End-to-end: real PluginManager discovery -> wired hooks -> span tree."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("opentelemetry.sdk")
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

@pytest.fixture
def wired(plugin_env, monkeypatch):
    """Load plugin through REAL discovery, swap in in-memory provider."""
    from hermes_cli.plugins import get_plugin_manager

    exp = InMemorySpanExporter()
    tp = TracerProvider()
    tp.add_span_processor(SimpleSpanProcessor(exp))

    # Same manager instance the module-level lifecycle dispatch uses,
    # so lazy re-discovery cannot re-exec the plugin module out from
    # under the monkeypatched seam.
    pm = get_plugin_manager()
    pm.discover_and_load()
    mod = sys.modules["hermes_plugins.hermes_otel"]
    monkeypatch.setattr(mod, "_build_tracer", lambda settings: tp.get_tracer("test"))
    mod._reset_for_tests()
    yield mod, exp
    mod._shutdown_for_tests()


PRE = {
    "session_id": "sess-1",
    "turn_id": "turn-1",
    "api_request_id": "turn-1:api:1",
    "model": "claude-x",
    "provider": "anthropic",
    "base_url": "https://api.anthropic.com",
    "api_mode": "messages",
    "api_call_count": 1,
    "started_at": 10.0,
    "request": {"body": {"model": "claude-x", "messages": [{"role": "user", "content": "hi"}]}},
}


def test_scripted_sequence_builds_expected_tree(wired):
    mod, exp = wired
    from hermes_cli.lifecycle import invoke_hook, has_hook

    assert has_hook("pre_api_request")

    invoke_hook("pre_llm_call", session_id="sess-1", turn_id="turn-1", platform="cli")
    invoke_hook("pre_api_request", **PRE)
    invoke_hook(
        "pre_tool_call",
        session_id="sess-1",
        turn_id="turn-1",
        task_id="task-1",
        tool_call_id="call_9",
        tool_name="terminal",
        args={"command": "ls"},
    )
    invoke_hook(
        "post_tool_call",
        session_id="sess-1",
        turn_id="turn-1",
        task_id="task-1",
        tool_call_id="call_9",
        tool_name="terminal",
        duration_ms=12,
        status="success",
        result='{"ok": true}',
    )
    invoke_hook(
        "post_api_request",
        **{
            **PRE,
            "ended_at": 11.0,
            "finish_reason": "stop",
            "response_model": "claude-x",
            "usage": {"input_tokens": 5, "output_tokens": 2},
            "response": {
                "model": "claude-x",
                "finish_reason": "stop",
                "assistant_message": {"role": "assistant", "content": "done"},
                "usage": {},
            },
        },
    )
    invoke_hook("on_session_finalize", session_id="sess-1", platform="cli", reason="complete")

    names = sorted(s.name for s in exp.get_finished_spans())
    assert names == [
        "chat claude-x",
        "execute_tool terminal",
        "invoke_agent claude-x",
        "invoke_agent hermes",
    ]


def test_no_content_attributes_by_default(wired):
    mod, exp = wired
    from hermes_cli.lifecycle import invoke_hook

    invoke_hook("pre_api_request", **PRE)
    invoke_hook("post_api_request", **{**PRE, "ended_at": 11.0, "usage": {}, "response": {}})
    invoke_hook("on_session_finalize", session_id="sess-1", platform="cli", reason="")
    chat = next(s for s in exp.get_finished_spans() if s.name.startswith("chat"))
    assert not any(k.startswith("gen_ai.prompt.") for k in chat.attributes)
    assert "gen_ai.completion.content" not in chat.attributes


def test_callback_never_raises(wired):
    mod, exp = wired
    from hermes_cli.lifecycle import invoke_hook

    invoke_hook("pre_api_request")  # missing everything
    invoke_hook("post_tool_call", unexpected="shape")
    invoke_hook("on_session_finalize", session_id=None)
    # No exception escaped into the caller. Per the committed TraceState
    # contract, missing fields degrade to "unknown" instead of raising or
    # dropping silently; pin that degradation shape here.
    names = sorted(s.name for s in exp.get_finished_spans())
    assert names == [
        "chat unknown",
        "execute_tool unknown",
        "invoke_agent hermes",
        "invoke_agent unknown",
    ]
