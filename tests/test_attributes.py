from otel_core.attributes import (
    chat_error_attrs,
    chat_request_attrs,
    chat_response_attrs,
    redact_text,
    tool_end_attrs,
)

PRE = {
    "session_id": "s1",
    "turn_id": "t1",
    "api_request_id": "t1:api:1",
    "model": "gpt-x",
    "provider": "openai",
    "base_url": "https://api.example.com/v1",
    "api_mode": "chat_completions",
    "api_call_count": 1,
    "message_count": 9,
    "started_at": 1000.0,
    "request": {
        "body": {
            "model": "gpt-x-wire",
            "messages": [
                {"role": "user", "content": "hello world"},
                {"role": "assistant", "content": "hi"},
            ],
        }
    },
}

POST = {
    **PRE,
    "ended_at": 1002.5,
    "finish_reason": "stop",
    "response_model": "gpt-x-wire",
    "usage": {
        "input_tokens": 10,
        "output_tokens": 4,
        "cache_read_tokens": 2,
        "cache_write_tokens": 0,
        "reasoning_tokens": 1,
    },
    "response": {
        "model": "gpt-x-wire",
        "finish_reason": "stop",
        "assistant_message": {"role": "assistant", "content": "the answer"},
        "usage": {},
    },
}


def test_redact_text_masks_secret_shapes():
    text = "key sk-abcdef1234567890 and pk-lf-zyxw98765432 Bearer tok123456 api_key=hunter2"
    out = redact_text(text)
    assert "sk-abcdef1234567890" not in out
    assert "pk-lf-zyxw98765432" not in out
    assert "tok123456" not in out
    assert "hunter2" not in out


def test_redact_text_truncates_after_redaction():
    long = "sk-" + "a" * 20000
    out = redact_text(long, max_chars=100)
    assert len(out) <= 120  # truncation marker allowance


def test_chat_request_attrs_metadata_only_when_capture_none():
    a = chat_request_attrs(PRE, "none")
    assert a["gen_ai.provider.name"] == "openai"
    assert a["gen_ai.request.model"] == "gpt-x-wire"
    assert a["hermes.api_request_id"] == "t1:api:1"
    assert a["hermes.api_mode"] == "chat_completions"
    assert not any(k.startswith("gen_ai.prompt") for k in a)


def test_chat_request_attrs_content_in_sanitized_mode():
    a = chat_request_attrs(PRE, "sanitized")
    assert a["gen_ai.prompt.0.role"] == "user"
    assert a["gen_ai.prompt.0.content"] == "hello world"
    assert a["gen_ai.prompt.count"] == 2


def test_chat_response_attrs_usage_mapping():
    a = chat_response_attrs(POST, "none")
    assert a["gen_ai.usage.input_tokens"] == 10
    assert a["gen_ai.usage.output_tokens"] == 4
    assert a["gen_ai.usage.cache_read.input_tokens"] == 2
    assert a["gen_ai.response.finish_reasons"] == ("stop",)
    assert a["gen_ai.response.model"] == "gpt-x-wire"
    assert "gen_ai.completion.content" not in a


def test_chat_response_attrs_completion_content_sanitized():
    a = chat_response_attrs(POST, "sanitized")
    assert a["gen_ai.completion.content"] == "the answer"


def test_chat_error_attrs_redacts_message_even_in_full_mode():
    err = {
        **PRE,
        "ended_at": 1001.0,
        "error": {"type": "RateLimitError", "message": "boom sk-abcdef1234567890"},
        "status_code": 429,
        "retryable": True,
    }
    a = chat_error_attrs(err, "full")
    assert a["error.type"] == "RateLimitError"
    assert "sk-abcdef1234567890" not in a["error.message"]
    assert a["hermes.status_code"] == 429
    assert a["hermes.retryable"] is True


def test_tool_end_attrs_status_and_duration():
    kw = {
        "tool_call_id": "call_1",
        "tool_name": "terminal",
        "duration_ms": 42,
        "status": "error",
        "error_type": "tool_error",
        "error_message": "exit 1",
    }
    a = tool_end_attrs(kw, "none")
    assert a["gen_ai.tool.name"] == "terminal"
    assert a["hermes.duration_ms"] == 42
    assert a["hermes.status"] == "error"
    assert a["error.type"] == "tool_error"
