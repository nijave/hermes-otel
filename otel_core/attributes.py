"""Hook payload -> official GenAI semconv attributes + hermes.* extras. Pure functions."""
from __future__ import annotations

import re
from typing import Any

_REDACTIONS = [
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"pk-lf-[A-Za-z0-9_-]{8,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|token|password)\s*[=:]\s*\S+"),
]

# Wire-truth precedence handled by callers: prefer request.body.model over payload["model"].


def _wire_model(kwargs: dict[str, Any]) -> str | None:
    try:
        m = ((kwargs.get("request") or {}).get("body") or {}).get("model")
        return m or kwargs.get("response_model") or kwargs.get("model")
    except AttributeError:
        return kwargs.get("model")


def redact_text(text: str, max_chars: int = 8000) -> str:
    if not isinstance(text, str):
        return ""
    out = text
    for rx in _REDACTIONS:
        out = rx.sub("[REDACTED]", out)
    if len(out) > max_chars:
        out = out[:max_chars] + "…[truncated]"
    return out


def session_root_attrs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        "gen_ai.agent.name": "hermes",
        "gen_ai.conversation.id": kwargs.get("session_id"),
        "hermes.platform": kwargs.get("platform"),
    }


def turn_attrs(kwargs: dict[str, Any]) -> dict[str, Any]:
    model = _wire_model(kwargs) or kwargs.get("model")
    return {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.request.model": model,
        "gen_ai.conversation.id": kwargs.get("session_id"),
        "hermes.turn_id": kwargs.get("turn_id"),
        "hermes.parent_session_id": kwargs.get("parent_session_id"),
    }


def _prompt_attrs(kwargs: dict[str, Any], capture_mode: str) -> dict[str, Any]:
    if capture_mode == "none":
        return {}
    limit = None if capture_mode == "full" else 8000
    messages = ((kwargs.get("request") or {}).get("body") or {}).get("messages") or []
    out: dict[str, Any] = {}
    for i, msg in enumerate(messages[:200]):
        role = msg.get("role")
        content = msg.get("content")
        if role:
            out[f"gen_ai.prompt.{i}.role"] = role
        if content:
            text = content if isinstance(content, str) else str(content)
            out[f"gen_ai.prompt.{i}.content"] = (
                text if limit is None else redact_text(text, limit)
            )
    if out:
        out["gen_ai.prompt.count"] = min(len(messages), 200)
    return out


def chat_request_attrs(kwargs: dict[str, Any], capture_mode: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": kwargs.get("provider"),
        "gen_ai.request.model": _wire_model(kwargs),
        "gen_ai.conversation.id": kwargs.get("session_id"),
        "hermes.turn_id": kwargs.get("turn_id"),
        "hermes.api_request_id": kwargs.get("api_request_id"),
        "hermes.api_mode": kwargs.get("api_mode"),
        "hermes.api_call_count": kwargs.get("api_call_count"),
    }
    base_url = kwargs.get("base_url")
    if base_url:
        attrs["server.address"] = base_url
    attrs.update(_prompt_attrs(kwargs, capture_mode))
    return {k: v for k, v in attrs.items() if v is not None}


def chat_response_attrs(kwargs: dict[str, Any], capture_mode: str) -> dict[str, Any]:
    usage = kwargs.get("usage") or {}
    finish = kwargs.get("finish_reason")
    attrs: dict[str, Any] = {
        "gen_ai.response.model": kwargs.get("response_model") or kwargs.get("model"),
        "gen_ai.response.finish_reasons": (finish,) if finish else None,
        "gen_ai.usage.input_tokens": usage.get("input_tokens"),
        "gen_ai.usage.output_tokens": usage.get("output_tokens"),
        "gen_ai.usage.cache_read.input_tokens": usage.get("cache_read_tokens"),
        "gen_ai.usage.cache_write.input_tokens": usage.get("cache_write_tokens"),
        "gen_ai.usage.reasoning.output_tokens": usage.get("reasoning_tokens"),
    }
    assistant = (kwargs.get("response") or {}).get("assistant_message") or {}
    content = assistant.get("content")
    if capture_mode != "none" and content:
        attrs["gen_ai.completion.content"] = (
            content if capture_mode == "full" else redact_text(str(content))
        )
    return {k: v for k, v in attrs.items() if v is not None}


def chat_error_attrs(kwargs: dict[str, Any], capture_mode: str) -> dict[str, Any]:
    error = kwargs.get("error") or {}
    attrs: dict[str, Any] = {
        "error.type": error.get("type") or "unknown",
        "error.message": redact_text(str(error.get("message") or "")),
        "hermes.status_code": kwargs.get("status_code"),
        "hermes.retryable": kwargs.get("retryable"),
    }
    return {k: v for k, v in attrs.items() if v is not None}


def tool_start_attrs(kwargs: dict[str, Any], capture_mode: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "gen_ai.tool.name": kwargs.get("tool_name"),
        "gen_ai.tool.call.id": kwargs.get("tool_call_id"),
    }
    args = kwargs.get("args")
    if capture_mode == "sanitized" and isinstance(args, dict):
        attrs["hermes.tool.args_json"] = redact_text(repr(args))
    elif capture_mode == "full" and args is not None:
        attrs["hermes.tool.args_json"] = repr(args)
    return {k: v for k, v in attrs.items() if v is not None}


def tool_end_attrs(kwargs: dict[str, Any], capture_mode: str) -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "gen_ai.tool.name": kwargs.get("tool_name"),
        "gen_ai.tool.call.id": kwargs.get("tool_call_id"),
        "hermes.duration_ms": kwargs.get("duration_ms"),
        "hermes.status": kwargs.get("status"),
    }
    if kwargs.get("error_type"):
        attrs["error.type"] = kwargs["error_type"]
    if kwargs.get("error_message"):
        attrs["error.message"] = redact_text(str(kwargs["error_message"]))
    result = kwargs.get("result")
    if capture_mode == "sanitized" and result is not None:
        attrs["hermes.tool.result_preview"] = redact_text(str(result), 2000)
    elif capture_mode == "full" and result is not None:
        attrs["hermes.tool.result_preview"] = str(result)[:2000]
    return {k: v for k, v in attrs.items() if v is not None}


def subagent_start_attrs(kwargs: dict[str, Any], capture_mode: str = "none") -> dict[str, Any]:
    attrs: dict[str, Any] = {
        "hermes.child_session_id": kwargs.get("child_session_id"),
        "hermes.role": kwargs.get("role"),
    }
    goal = kwargs.get("goal")
    if capture_mode == "sanitized" and goal:
        attrs["hermes.goal"] = redact_text(str(goal))
    elif capture_mode == "full" and goal:
        attrs["hermes.goal"] = str(goal)
    return {k: v for k, v in attrs.items() if v is not None}


def subagent_stop_attrs(kwargs: dict[str, Any]) -> dict[str, Any]:
    return {
        k: v
        for k, v in {
            "hermes.child_session_id": kwargs.get("child_session_id"),
            "hermes.summary_chars": len(kwargs.get("summary") or ""),
            "hermes.duration_ms": kwargs.get("duration_ms"),
        }.items()
        if v is not None
    }
